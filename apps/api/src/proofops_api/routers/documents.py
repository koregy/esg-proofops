"""TASK-001 v1 boundary and explicit local-only multipart POST transport."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, Security
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyCookie
from proofops.adapters.parsing.report_sections import (
    MAX_PAGES,
    MAX_PDF_BYTES,
    POLICY_HASH,
    SectionInspectionError,
)
from proofops.adapters.parsing.report_sections_worker import (
    InspectionLimits,
    inspect_pdf_bytes_isolated,
)
from proofops.application.uploads import UploadService
from proofops.application.uploads_security import UploadRejected
from proofops_api.auth import (
    SESSION_COOKIE_NAME,
    AuthStore,
    _error_body,
    _error_response,
    _request_limit_response,
    _verify_csrf,
)
from proofops_api.middleware import RequestBodyTooLarge, read_bounded_json
from proofops_api.request_limits import READ_REQUESTS_PER_MINUTE, WRITE_REQUESTS_PER_MINUTE
from proofops_api.routers.registry import _authorize
from proofops_api.rulepacks import _ERROR_RESPONSES
from proofops_api.telemetry import current_request_id
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DocumentCreate(StrictDTO):
    company_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=200)]
    document_type: Literal["sustainability_report", "annual_report_section"]


class Document(StrictDTO):
    document_id: UUID
    company_id: UUID
    title: str
    document_type: Literal["sustainability_report", "annual_report_section"]
    latest_version_id: UUID | None = None
    revision: Annotated[int, Field(ge=1)]
    created_at: datetime


class UploadComplete(StrictDTO):
    sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=1, le=104857600)]


class VersionCreate(UploadComplete):
    filename: Annotated[str, Field(min_length=1, max_length=200)]
    report_year: Annotated[int, Field(ge=1900, le=2200)]
    industry_system: Literal["gics", "sasb", "custom", "unknown"]
    industry_code: str | None = None
    consolidation_scope: str | None = None
    period_start: Annotated[str, Field(json_schema_extra={"format": "date"})]
    period_end: Annotated[str, Field(json_schema_extra={"format": "date"})]
    rights_profile_id: UUID


class UploadTicket(StrictDTO):
    upload_id: UUID
    document_id: UUID
    post_url: str
    post_fields: dict[str, str]
    expires_at: datetime


class JobAccepted(StrictDTO):
    job_id: UUID
    resource_id: UUID
    status: Literal["queued", "running", "ready"]
    status_url: str


class DocumentVersion(StrictDTO):
    version_id: UUID
    document_id: UUID
    sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    report_year: Annotated[int, Field(ge=1900)]
    page_count: Annotated[int, Field(ge=1)] | None = None
    status: Literal["validating", "ready", "rejected"]
    created_at: datetime


class DocumentPage(StrictDTO):
    items: list[Document]
    next_cursor: str | None
    snapshot_epoch: Annotated[int, Field(ge=0)] | None


class DocumentVersionPage(StrictDTO):
    items: list[DocumentVersion]
    next_cursor: str | None
    snapshot_epoch: Annotated[int, Field(ge=0)] | None


Sha256 = Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
PageNumber = Annotated[int, Field(ge=1, le=MAX_PAGES)]
SectionRole = Literal["e_narrative", "esg_data", "appendix", "other", "unknown", "conflict"]


class ScopeProposalAnchor(StrictDTO):
    page: PageNumber
    title: Annotated[str, Field(max_length=200)]
    method: Literal[
        "outline", "named_destination", "large_heading_fallback", "toc_links", "toc_text_fallback"
    ]


class ScopeProposalSection(StrictDTO):
    start_page: PageNumber
    end_page: PageNumber
    role: SectionRole
    anchors: list[ScopeProposalAnchor]


class DocumentScopeProposal(StrictDTO):
    """Candidate-only E scope for one ready version; never an approved or full scope."""

    version_id: UUID
    document_id: UUID
    source_sha256: Sha256
    page_count: PageNumber
    status: Literal["candidate_only"]
    full_scope_declared: Literal[False]
    apply_as: Literal["declared_subset"]
    method: Literal["pdf_navigation", "large_heading_fallback", "toc_links", "toc_text_fallback"]
    proposed_pages: list[PageNumber]
    claim_candidate_pages: list[PageNumber]
    evidence_candidate_pages: list[PageNumber]
    unknown_pages: list[PageNumber]
    conflict_pages: list[PageNumber]
    other_candidate_pages: list[PageNumber]
    sections: list[ScopeProposalSection]
    issue_counts: dict[str, Annotated[int, Field(ge=1)]]
    policy_sha256: Sha256
    map_sha256: Sha256
    parser_versions: dict[str, str]
    coverage: Literal["unvalidated"]
    live_model: Literal["not_run"]
    table_figure_validation: Literal["not_run"]
    limitations: list[str]


def _scope_proposal(snapshot: dict, study: dict) -> dict:
    """Project the internal study to the bounded public DTO (no previews or paths)."""
    claims = sorted(study["claim_candidate_pages"])
    evidence = sorted(study["evidence_candidate_pages"])
    issue_counts: dict[str, int] = {}
    for issue in study["issues"]:
        issue_counts[issue["kind"]] = issue_counts.get(issue["kind"], 0) + 1
    return dict(
        version_id=snapshot["version_id"],
        document_id=snapshot["document_id"],
        source_sha256=study["source_sha256"],
        page_count=study["page_count"],
        status=study["status"],
        full_scope_declared=False,
        apply_as="declared_subset",
        method=study["method"],
        # Unknown/conflict pages stay out of the proposed subset but remain listed so a
        # reviewer can add them. Nothing in a proposal is evidence of absence.
        proposed_pages=sorted(set(claims) | set(evidence)),
        claim_candidate_pages=claims,
        evidence_candidate_pages=evidence,
        unknown_pages=sorted(study["unknown_pages"]),
        conflict_pages=sorted(study["conflict_pages"]),
        other_candidate_pages=sorted(study["other_candidate_pages"]),
        sections=[
            dict(
                start_page=section["start_page"],
                end_page=section["end_page"],
                role=section["role"],
                anchors=[
                    dict(page=a["page"], title=str(a["title"])[:200], method=a["method"])
                    for a in section["anchors"]
                ],
            )
            for section in study["sections"]
        ],
        issue_counts=issue_counts,
        policy_sha256=study["policy_sha256"],
        map_sha256=study["map_sha256"],
        parser_versions=study["parser_versions"],
        coverage=study["coverage"],
        live_model=study["live_model"],
        table_figure_validation=study["table_figure_validation"],
        limitations=study["limitations"],
    )


_SCOPE_FAILURES = {
    "SOURCE_SHA_MISMATCH": 409,
    "UPLOAD_INTEGRITY_MISMATCH": 409,
    "VERSION_NOT_READY": 409,
    "PAGE_COUNT_MISMATCH": 409,
    "SECTION_SOURCE_TOO_LARGE": 422,
    "SECTION_SOURCE_ENCRYPTED": 422,
    "SECTION_SOURCE_INVALID": 422,
    "SECTION_INSPECTION_FAILED": 422,
    "SECTION_INSPECTION_RESOURCE_LIMIT": 422,
    "SCOPE_INSPECTION_BUSY": 429,
    "SOURCE_UNAVAILABLE": 503,
    "SECTION_INSPECTION_TIMEOUT": 503,
    "SECTION_INSPECTION_UNAVAILABLE": 503,
}
# Retrying the same immutable source cannot change a deterministic outcome; only
# contention or a missing worker can clear on its own.
_SCOPE_RETRY_AFTER = {
    "SCOPE_INSPECTION_BUSY": 30,
    "SECTION_INSPECTION_UNAVAILABLE": 60,
    "SOURCE_UNAVAILABLE": 60,
}
_SCOPE_MESSAGES = {
    "SOURCE_SHA_MISMATCH": (
        "선택한 문서 버전의 원본 해시가 요청과 다릅니다. 문서 버전을 다시 불러오세요."
    ),
    "UPLOAD_INTEGRITY_MISMATCH": (
        "저장된 원본이 검증된 해시와 일치하지 않아 범위를 제안하지 않았습니다."
    ),
    "VERSION_NOT_READY": "검증이 끝난 문서 버전만 범위를 제안할 수 있습니다.",
    "PAGE_COUNT_MISMATCH": "원본 페이지 수가 검증 기록과 달라 범위를 제안하지 않았습니다.",
    "SECTION_SOURCE_TOO_LARGE": (
        f"범위 제안은 {MAX_PDF_BYTES // (1024 * 1024)}MiB·{MAX_PAGES}페이지 이하 문서만 지원합니다."
    ),
    "SECTION_SOURCE_ENCRYPTED": "암호화된 PDF는 범위를 제안할 수 없습니다.",
    "SECTION_SOURCE_INVALID": "원본을 읽을 수 없어 범위를 제안하지 않았습니다.",
    "SECTION_INSPECTION_FAILED": (
        "PDF 구조를 읽지 못해 범위를 제안하지 않았습니다. 페이지를 직접 지정하세요."
    ),
    "SECTION_INSPECTION_RESOURCE_LIMIT": (
        "문서 구조 확인이 메모리·출력 한도를 넘어 중단했습니다. 페이지를 직접 지정하세요."
    ),
    "SCOPE_INSPECTION_BUSY": "다른 범위 제안을 계산하고 있습니다. 잠시 후 다시 시도하세요.",
    "SOURCE_UNAVAILABLE": "원본 파일을 읽을 수 없어 범위를 제안하지 않았습니다.",
    "SECTION_INSPECTION_TIMEOUT": (
        "문서 구조 확인이 제한 시간을 넘어 중단했습니다. 같은 문서는 다시 시도해도 "
        "같을 수 있으니 페이지를 직접 지정하세요."
    ),
    "SECTION_INSPECTION_UNAVAILABLE": (
        "범위 제안 작업자를 시작할 수 없습니다. 잠시 후 다시 시도하거나 페이지를 직접 지정하세요."
    ),
}


def _scope_failure(code: str) -> JSONResponse:
    retry_after = _SCOPE_RETRY_AFTER.get(code)
    response = JSONResponse(
        status_code=_SCOPE_FAILURES[code],
        content=_error_body(
            code,
            _SCOPE_MESSAGES[code],
            current_request_id() or str(uuid4()),
            retryable=retry_after is not None,
        ),
    )
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _scope_limits_from_env() -> InspectionLimits:
    """Operator-tunable bounds; invalid values fail at startup, never silently widen."""
    return InspectionLimits(
        timeout_seconds=float(os.environ.get("SCOPE_PROPOSAL_TIMEOUT_SECONDS", "180")),
        memory_bytes=int(os.environ.get("SCOPE_PROPOSAL_MEMORY_MIB", "1024")) * 1024 * 1024,
    )


class _ScopeProposalCache:
    """Bounded per-process cache keyed by tenant, version, pinned SHA and policy hash.

    Versions and their bytes are immutable, so a hit can never serve another source.
    One inspection runs at a time per process; a concurrent request gets 429 instead
    of queueing unbounded CPU work.
    """

    def __init__(self, maximum: int = 16):
        self._items: OrderedDict[tuple, dict] = OrderedDict()
        self._maximum = maximum
        self._lock = threading.Lock()
        self.running = threading.Semaphore(1)

    def get(self, key: tuple) -> dict | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: tuple, value: dict) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._maximum:
                self._items.popitem(last=False)


def _request_schema(model: type[BaseModel]) -> dict:
    return {
        "x-minimum-role": "editor",
        "x-idempotency-required": True,
        "x-rate-limit": "10/min/user",
        "parameters": [
            {
                "name": "X-CSRF-Token",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            },
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "minLength": 16, "maxLength": 128},
            },
        ],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        },
    }


def _failure(exc: UploadRejected) -> JSONResponse:
    code = str(exc)
    status = (
        400
        if code == "INVALID_CURSOR"
        else 404
        if code == "NOT_FOUND"
        else 409
        if code
        in ("IDEMPOTENCY_CONFLICT", "VERSION_CONFLICT", "UPLOAD_EXPIRED", "UPLOAD_NOT_READY")
        else 422
    )
    return _error_response(
        status,
        "RESOURCE_NOT_FOUND" if code == "NOT_FOUND" else code,
        "요청한 문서 또는 업로드를 처리할 수 없습니다.",
    )


def build_documents_router(
    service: UploadService,
    auth_store: AuthStore,
    *,
    allowed_origin: str,
    app_env: str,
    model_adapter: str,
    clock: Any = None,
    scope_proposals: bool | None = None,
    scope_limits: InspectionLimits | None = None,
) -> APIRouter:
    """Fail closed unless explicitly mounted in local-synthetic composition.

    ``scope_proposals`` (default: env ``SCOPE_PROPOSAL_ENABLED`` != "0") mounts the
    read-only section-scope proposal route; disabling it is the documented rollback.
    ``scope_limits`` (default: env ``SCOPE_PROPOSAL_TIMEOUT_SECONDS``/``_MEMORY_MIB``) bounds
    the separate inspection process; PDF structure is never parsed in the API process.
    """
    if app_env != "local" or model_adapter != "synthetic" or not service.local_synthetic:
        raise ValueError("local-synthetic upload adapter cannot serve this environment")
    now_fn = clock if clock is not None else time.time
    if scope_proposals is None:
        scope_proposals = os.environ.get("SCOPE_PROPOSAL_ENABLED", "1") != "0"
    scope_cache = _ScopeProposalCache()
    if scope_proposals and scope_limits is None:
        scope_limits = _scope_limits_from_env()
    router = APIRouter(
        responses=_ERROR_RESPONSES,
        dependencies=[
            Security(
                APIKeyCookie(
                    name=SESSION_COOKIE_NAME, scheme_name="sessionCookie", auto_error=False
                )
            )
        ],
    )

    def authorize(request: Request, operation_id: str | None = None, write: bool = False):
        now = now_fn()
        auth = _authorize(request, auth_store, now, "editor" if write else "viewer")
        if isinstance(auth, JSONResponse):
            return auth
        if write:
            session = auth_store.sessions.get(auth.session_id)
            if session is None or not _verify_csrf(
                request, session.csrf_hash, allowed_origin=allowed_origin
            ):
                return _error_response(403, "CSRF_INVALID", "세션과 요청 출처를 확인하세요.")
        if operation_id is not None:
            limited = _request_limit_response(
                auth_store,
                user_sub=auth.user_sub,
                operation_id=operation_id,
                requests=WRITE_REQUESTS_PER_MINUTE if write else READ_REQUESTS_PER_MINUTE,
                now=now,
            )
            if limited is not None:
                return limited
        return auth

    async def payload(request: Request, model: type[BaseModel]) -> dict:
        key = request.headers.get("Idempotency-Key", "")
        if not 16 <= len(key) <= 128:
            raise UploadRejected("IDEMPOTENCY_KEY_INVALID")
        try:
            data = await read_bounded_json(request)
        except (RequestBodyTooLarge, ValueError):
            raise UploadRejected("VALIDATION_ERROR") from None
        return model.model_validate_json(json.dumps(data)).model_dump(
            mode="json", exclude_unset=True
        )

    @router.post(
        "/v1/documents",
        status_code=201,
        response_model=Document,
        operation_id="document_create",
        openapi_extra=_request_schema(DocumentCreate),
    )
    async def create_document(request: Request):
        auth = authorize(request, "document_create", True)
        if isinstance(auth, JSONResponse):
            return auth
        try:
            body = await payload(request, DocumentCreate)
            result = await run_in_threadpool(
                service.create_document,
                auth.tenant_id,
                body,
                request.headers["Idempotency-Key"],
                actor_sub=auth.user_sub,
            )
            return JSONResponse(
                result, status_code=201, headers={"ETag": f'"{result["revision"]}"'}
            )
        except ValidationError:
            return _failure(UploadRejected("VALIDATION_ERROR"))
        except UploadRejected as exc:
            return _failure(exc)

    read_contract = {
        "x-minimum-role": "viewer",
        "x-idempotency-required": False,
        "x-rate-limit": "120/min/user",
    }

    @router.get(
        "/v1/documents",
        response_model=DocumentPage,
        operation_id="documents_list",
        openapi_extra=read_contract,
    )
    def list_documents(
        request: Request,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        auth = authorize(request, "documents_list")
        if isinstance(auth, JSONResponse):
            return auth
        try:
            return JSONResponse(
                service.list_documents(auth.tenant_id, cursor=cursor, limit=limit, now=now_fn()),
                headers={"Cache-Control": "no-store"},
            )
        except UploadRejected as exc:
            return _failure(exc)

    @router.get(
        "/v1/documents/{document_id}",
        response_model=Document,
        operation_id="document_get",
        openapi_extra=read_contract,
    )
    def get_document(request: Request, document_id: UUID):
        auth = authorize(request, "document_get")
        if isinstance(auth, JSONResponse):
            return auth
        try:
            result = service.get_document(auth.tenant_id, str(document_id))
            return JSONResponse(result, headers={"ETag": f'"{result["revision"]}"'})
        except UploadRejected as exc:
            return _failure(exc)

    @router.get(
        "/v1/documents/{document_id}/versions",
        response_model=DocumentVersionPage,
        operation_id="versions_list",
        openapi_extra=read_contract,
    )
    def list_versions(
        request: Request,
        document_id: UUID,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        auth = authorize(request, "versions_list")
        if isinstance(auth, JSONResponse):
            return auth
        try:
            return JSONResponse(
                service.list_versions(
                    auth.tenant_id,
                    str(document_id),
                    cursor=cursor,
                    limit=limit,
                    now=now_fn(),
                ),
                headers={"Cache-Control": "no-store"},
            )
        except UploadRejected as exc:
            return _failure(exc)

    @router.post(
        "/v1/documents/{document_id}/versions",
        status_code=201,
        response_model=UploadTicket,
        operation_id="version_create",
        openapi_extra=_request_schema(VersionCreate),
    )
    async def initiate_upload(request: Request, document_id: UUID):
        auth = authorize(request, "version_create", True)
        if isinstance(auth, JSONResponse):
            return auth
        try:
            body = await payload(request, VersionCreate)
            result = await run_in_threadpool(
                service.initiate_upload,
                auth.tenant_id,
                str(document_id),
                body,
                request.headers["Idempotency-Key"],
                actor_sub=auth.user_sub,
            )
            return JSONResponse(result, status_code=201)
        except ValidationError:
            return _failure(UploadRejected("VALIDATION_ERROR"))
        except UploadRejected as exc:
            return _failure(exc)

    @router.post("/local/uploads/{upload_id}/content", include_in_schema=False)
    async def receive_content(request: Request, upload_id: UUID):
        auth = authorize(request, write=True)
        if isinstance(auth, JSONResponse):
            return auth
        try:
            ticket = service.check_receipt(auth.tenant_id, str(upload_id))
            maximum = service.limits.max_bytes + 65536  # bounded multipart envelope overhead
            size_header = request.headers.get("content-length")
            if size_header is not None and not 0 <= int(size_header) <= maximum:
                raise UploadRejected("UPLOAD_LIMIT_EXCEEDED")
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await request.receive()
                received += len(message.get("body", b""))
                if received > maximum:
                    # Starlette closes its temporary files on MultiPartException.
                    raise MultiPartException("UPLOAD_LIMIT_EXCEEDED")
                return message

            bounded_request = Request(request.scope, receive=bounded_receive)
            async with bounded_request.form(max_files=1, max_fields=1, max_part_size=512) as form:
                if len(form.multi_items()) != 2 or set(form) != {"ticket", "file"}:
                    raise UploadRejected("VALIDATION_ERROR")
                token, file = form["ticket"], form["file"]
                if not isinstance(token, str) or not secrets.compare_digest(
                    token.encode(), ticket["post_fields"]["ticket"].encode()
                ):
                    return _error_response(
                        403, "UPLOAD_TICKET_INVALID", "업로드 티켓을 확인하세요."
                    )
                if not isinstance(file, UploadFile) or file.content_type != "application/pdf":
                    raise UploadRejected("PDF_INVALID")
                content = await file.read(service.limits.max_bytes + 1)
                await run_in_threadpool(
                    service.receive_content,
                    auth.tenant_id,
                    str(upload_id),
                    content,
                    file.content_type,
                )
            return Response(status_code=204)
        except UploadRejected as exc:
            return _failure(exc)
        except (HTTPException, MultiPartException, ValueError):
            return _failure(UploadRejected("VALIDATION_ERROR"))

    @router.post(
        "/v1/uploads/{upload_id}/complete",
        status_code=202,
        response_model=JobAccepted,
        operation_id="upload_complete",
        openapi_extra=_request_schema(UploadComplete),
    )
    async def complete_upload(request: Request, upload_id: UUID):
        auth = authorize(request, "upload_complete", True)
        if isinstance(auth, JSONResponse):
            return auth
        try:
            body = await payload(request, UploadComplete)
            result = await run_in_threadpool(
                service.complete_upload,
                auth.tenant_id,
                str(upload_id),
                body,
                request.headers["Idempotency-Key"],
                actor_sub=auth.user_sub,
            )
            return JSONResponse(
                dict(
                    job_id=service.validation_job(auth.tenant_id, str(upload_id))["job_id"],
                    resource_id=result["version_id"],
                    status="ready",
                    status_url=f"/v1/versions/{result['version_id']}",
                ),
                status_code=202,
            )
        except ValidationError:
            return _failure(UploadRejected("VALIDATION_ERROR"))
        except UploadRejected as exc:
            return _failure(exc)

    @router.get(
        "/v1/versions/{version_id}",
        response_model=DocumentVersion,
        operation_id="version_get",
        openapi_extra={
            "x-minimum-role": "viewer",
            "x-idempotency-required": False,
            "x-rate-limit": "120/min/user",
        },
    )
    def get_version(request: Request, version_id: UUID):
        auth = authorize(request, "version_get")
        if isinstance(auth, JSONResponse):
            return auth
        try:
            return JSONResponse(service.get_version(auth.tenant_id, str(version_id)))
        except UploadRejected as exc:
            return _failure(exc)

    if scope_proposals:

        @router.get(
            "/v1/versions/{version_id}/scope-proposal",
            response_model=DocumentScopeProposal,
            operation_id="version_scope_proposal",
            openapi_extra={
                "x-minimum-role": "editor",
                "x-idempotency-required": False,
                "x-rate-limit": "6/min/user",
            },
        )
        async def propose_scope(
            request: Request,
            version_id: UUID,
            expected_sha256: Annotated[str, Query(pattern="^[0-9a-f]{64}$")],
        ):
            """Candidate E section scope from the server-held original of a ready version.

            Reads only the tenant's verified original by version id (no path or URL
            input), pins it to ``expected_sha256``, and never calls a model.
            """
            now = now_fn()
            auth = _authorize(request, auth_store, now, "editor")
            if isinstance(auth, JSONResponse):
                return auth
            limited = _request_limit_response(
                auth_store,
                user_sub=auth.user_sub,
                operation_id="version_scope_proposal",
                requests=6,
                now=now,
            )
            if limited is not None:
                return limited
            try:
                snapshot = service.version_snapshot(auth.tenant_id, str(version_id))
            except UploadRejected as exc:
                return _failure(exc)
            if snapshot.get("status") != "ready":
                return _scope_failure("VERSION_NOT_READY")
            if snapshot["sha256"] != expected_sha256:
                return _scope_failure("SOURCE_SHA_MISMATCH")
            key = (auth.tenant_id, str(version_id), snapshot["sha256"], POLICY_HASH)
            cached = scope_cache.get(key)
            if cached is not None:
                return JSONResponse(cached, headers={"Cache-Control": "no-store"})
            if not scope_cache.running.acquire(blocking=False):
                return _scope_failure("SCOPE_INSPECTION_BUSY")
            try:
                content = await run_in_threadpool(
                    service.read_original, auth.tenant_id, str(version_id)
                )
                study = await run_in_threadpool(
                    lambda: inspect_pdf_bytes_isolated(
                        content, expected_sha256=snapshot["sha256"], limits=scope_limits
                    )
                )
            except UploadRejected as exc:
                if str(exc) == "UPLOAD_INTEGRITY_MISMATCH":
                    return _scope_failure("UPLOAD_INTEGRITY_MISMATCH")
                return _failure(exc)
            except SectionInspectionError as exc:
                return _scope_failure(exc.code)
            except OSError:
                return _scope_failure("SOURCE_UNAVAILABLE")
            finally:
                scope_cache.running.release()
            if study["page_count"] != snapshot.get("page_count"):
                return _scope_failure("PAGE_COUNT_MISMATCH")
            result = DocumentScopeProposal.model_validate_json(
                json.dumps(_scope_proposal(snapshot, study))
            ).model_dump(mode="json")
            scope_cache.put(key, result)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return router
