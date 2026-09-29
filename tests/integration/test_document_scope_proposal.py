"""Read-only section-scope proposal for a ready uploaded version (R03 production wiring).

Real PDFs go through the real upload service and HTTP router. The proposal reads only
the server-held verified original, is pinned to the version SHA, and never declares a
full scope or turns unknown/conflict pages into absence.
"""

from __future__ import annotations

import time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PdfWriter

TENANT = "11111111-1111-4111-8111-111111111111"
FOREIGN = "22222222-2222-4222-8222-222222222222"
RIGHTS = "33333333-3333-4333-8333-333333333333"
ROUTE = "/v1/versions/{}/scope-proposal"


def _sectioned_pdf() -> bytes:
    writer = PdfWriter()
    for _ in range(8):
        writer.add_blank_page(width=600, height=800)
    for title, index in [("Environment", 1), ("Social", 4), ("ESG DATA", 5), ("Appendix", 7)]:
        writer.add_named_destination(title, index)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _blank_pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=600, height=800)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _ready_version(tmp_path: Path, data: bytes, *, tenant: str = TENANT):
    from proofops.application.registry import Registry, artifact_sha256
    from proofops.application.uploads import UploadService

    registry = Registry.empty()
    company = registry.create_company(actor="fixture", tenant_id=tenant, legal_name="Synthetic Co")
    artifact = dict(tenant_id=tenant, rights_profile_id=RIGHTS, status="approved", version="v1")
    registry.with_option(
        tenant,
        "rights",
        RIGHTS,
        "Synthetic fixture only",
        status="approved",
        version="v1",
        artifact=artifact,
        sha256=artifact_sha256(artifact),
        approved_by="synthetic-fixture",
        approved_at="2026-09-09T00:00:00Z",
        local_synthetic=True,
    )
    service = UploadService(tmp_path / "uploads.sqlite", tmp_path / "objects", registry)
    document = service.create_document(
        tenant,
        dict(company_id=company.company_id, title="Report", document_type="sustainability_report"),
        str(uuid4()),
    )
    digest = sha256(data).hexdigest()
    body = dict(
        filename="report.pdf",
        size_bytes=len(data),
        sha256=digest,
        report_year=2025,
        industry_system="unknown",
        period_start="2025-01-01",
        period_end="2025-12-31",
        rights_profile_id=RIGHTS,
    )
    ticket = service.initiate_upload(tenant, document["document_id"], body, str(uuid4()))
    service.receive_content(tenant, ticket["upload_id"], data, "application/pdf")
    version = service.complete_upload(
        tenant, ticket["upload_id"], dict(sha256=digest, size_bytes=len(data)), str(uuid4())
    )
    return service, version


def _client(service, *, tenant=TENANT, role="editor", scope_proposals=True, scope_limits=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from proofops.adapters.local.auth_store import InMemoryMembershipStore, InMemorySessionStore
    from proofops.application.authorization import MembershipRecord, SessionRecord
    from proofops_api.auth import AuthStore
    from proofops_api.routers.documents import build_documents_router

    store = AuthStore(sessions=InMemorySessionStore(), memberships=InMemoryMembershipStore())
    store.sessions.put_with_token(
        SessionRecord(
            "scope-session",
            "user",
            tenant,
            store.hash_csrf("csrf"),
            time.time() + 3600,
            time.time() + 3600,
            False,
        ),
        "csrf",
    )
    store.memberships.put(MembershipRecord(tenant, "user", role, "active"))
    app = FastAPI()
    app.include_router(
        build_documents_router(
            service,
            store,
            allowed_origin="http://testserver",
            app_env="local",
            model_adapter="synthetic",
            scope_proposals=scope_proposals,
            scope_limits=scope_limits,
        )
    )
    client = TestClient(app)
    client.cookies.set("__Host-proofops_session", "scope-session")
    return client


def _assert_contract(name: str, payload: dict) -> None:
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker

    schemas = yaml.safe_load(Path("contracts/openapi.yaml").read_text(encoding="utf-8"))[
        "components"
    ]["schemas"]
    Draft202012Validator(
        dict(schemas[name], components={"schemas": schemas}), format_checker=FormatChecker()
    ).validate(payload)


def test_authorized_proposal_is_pinned_candidate_subset_with_uncertainty(tmp_path):
    from proofops.adapters.parsing.report_sections import POLICY_HASH

    data = _sectioned_pdf()
    service, version = _ready_version(tmp_path, data)
    with _client(service) as client:
        response = client.get(
            ROUTE.format(version["version_id"]), params={"expected_sha256": version["sha256"]}
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        _assert_contract("DocumentScopeProposal", body)
        assert body["version_id"] == version["version_id"]
        assert body["source_sha256"] == sha256(data).hexdigest() == version["sha256"]
        assert body["page_count"] == 8 and body["policy_sha256"] == POLICY_HASH
        assert body["status"] == "candidate_only" and body["full_scope_declared"] is False
        assert body["apply_as"] == "declared_subset"
        assert body["claim_candidate_pages"] == [2, 3, 4]
        assert body["evidence_candidate_pages"] == [2, 3, 4, 6, 7, 8]
        assert body["proposed_pages"] == [2, 3, 4, 6, 7, 8]
        # Unknown and other pages are reported for review, never silently dropped.
        assert body["unknown_pages"] == [1] and body["other_candidate_pages"] == [5]
        assert body["live_model"] == "not_run" and body["coverage"] == "unvalidated"
        assert any("never evidence absence" in text for text in body["limitations"])
        # No server path or page text leaks through the public projection.
        assert "source_path" not in body and "objects" not in response.text
        assert "destination_preview" not in response.text
        # A cached repeat returns the identical pinned map.
        again = client.get(
            ROUTE.format(version["version_id"]), params={"expected_sha256": version["sha256"]}
        )
        assert again.json() == body
    service.close()


def test_unauthenticated_viewer_and_cross_tenant_are_refused(tmp_path):
    service, version = _ready_version(tmp_path, _sectioned_pdf())
    url = ROUTE.format(version["version_id"])
    params = {"expected_sha256": version["sha256"]}
    with _client(service) as client:
        client.cookies.clear()
        assert client.get(url, params=params).status_code == 401
    with _client(service, role="viewer") as viewer:
        assert viewer.get(url, params=params).status_code == 403
    with _client(service, tenant=FOREIGN) as foreign:
        response = foreign.get(url, params=params)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    service.close()


def test_stale_sha_malformed_sha_and_missing_version_fail_closed(tmp_path):
    service, version = _ready_version(tmp_path, _sectioned_pdf())
    url = ROUTE.format(version["version_id"])
    with _client(service) as client:
        stale = client.get(url, params={"expected_sha256": "0" * 64})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "SOURCE_SHA_MISMATCH"
        assert client.get(url).status_code == 422
        assert client.get(url, params={"expected_sha256": "../../etc/passwd"}).status_code == 422
        # A path-like or URL-like identifier never reaches the filesystem.
        assert client.get("/v1/versions/..%2F..%2Fsecret/scope-proposal").status_code in (
            404,
            422,
        )
        missing = client.get(ROUTE.format(uuid4()), params={"expected_sha256": version["sha256"]})
        assert missing.status_code == 404
    service.close()


def test_pending_upload_and_not_ready_snapshot_are_refused(tmp_path, monkeypatch):
    service, version = _ready_version(tmp_path, _sectioned_pdf())
    data = _blank_pdf()
    document_id = version["document_id"]
    ticket = service.initiate_upload(
        TENANT,
        document_id,
        dict(
            filename="next.pdf",
            size_bytes=len(data),
            sha256=sha256(data).hexdigest(),
            report_year=2026,
            industry_system="unknown",
            period_start="2026-01-01",
            period_end="2026-12-31",
            rights_profile_id=RIGHTS,
        ),
        str(uuid4()),
    )
    service.receive_content(TENANT, ticket["upload_id"], data, "application/pdf")
    pending_version = service._get(TENANT, "upload", ticket["upload_id"])["version_id"]
    with _client(service) as client:
        # Not yet accepted: no version record exists, so nothing is inspected.
        pending = client.get(
            ROUTE.format(pending_version), params={"expected_sha256": sha256(data).hexdigest()}
        )
        assert pending.status_code == 404
        snapshot = service.version_snapshot(TENANT, version["version_id"])
        monkeypatch.setattr(
            service, "version_snapshot", lambda *_: dict(snapshot, status="validating")
        )
        not_ready = client.get(
            ROUTE.format(version["version_id"]), params={"expected_sha256": version["sha256"]}
        )
        assert not_ready.status_code == 409
        assert not_ready.json()["error"]["code"] == "VERSION_NOT_READY"
    service.close()


def test_tampered_original_is_integrity_failure_not_a_proposal(tmp_path):
    service, version = _ready_version(tmp_path, _sectioned_pdf())
    original = tmp_path / "objects" / "original" / TENANT / f"{version['version_id']}.pdf"
    original.chmod(0o644)
    original.write_bytes(_blank_pdf(8))
    with _client(service) as client:
        response = client.get(
            ROUTE.format(version["version_id"]), params={"expected_sha256": version["sha256"]}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "UPLOAD_INTEGRITY_MISMATCH"
    service.close()


def test_inspection_failures_have_stable_codes(tmp_path, monkeypatch):
    from proofops.adapters.parsing.report_sections import (
        MAX_PDF_BYTES,
        SectionInspectionError,
        inspect_pdf_bytes,
    )

    with pytest.raises(SectionInspectionError) as too_large:
        inspect_pdf_bytes(b"%PDF" + b"0" * MAX_PDF_BYTES)
    assert too_large.value.code == "SECTION_SOURCE_TOO_LARGE"
    with pytest.raises(SectionInspectionError) as garbage:
        inspect_pdf_bytes(b"%PDF-1.7 not really a pdf")
    assert garbage.value.code == "SECTION_INSPECTION_FAILED"
    data = _blank_pdf(3)
    with pytest.raises(SectionInspectionError) as stale:
        inspect_pdf_bytes(data, expected_sha256="0" * 64)
    assert stale.value.code == "SOURCE_SHA_MISMATCH"

    service, version = _ready_version(tmp_path, _sectioned_pdf())
    import proofops_api.routers.documents as documents

    def fail(*_args, **_kwargs):
        raise SectionInspectionError("SECTION_SOURCE_TOO_LARGE", "limit")

    monkeypatch.setattr(documents, "inspect_pdf_bytes_isolated", fail)
    with _client(service) as client:
        response = client.get(
            ROUTE.format(version["version_id"]), params={"expected_sha256": version["sha256"]}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SECTION_SOURCE_TOO_LARGE"
        assert "limit" not in response.json()["error"]["message"]
    service.close()


def test_route_can_be_disabled_and_is_declared_in_contract(tmp_path):
    import yaml

    service, version = _ready_version(tmp_path, _sectioned_pdf())
    with _client(service, scope_proposals=False) as client:
        response = client.get(
            ROUTE.format(version["version_id"]), params={"expected_sha256": version["sha256"]}
        )
        assert response.status_code == 404
        assert (
            "/v1/versions/{version_id}/scope-proposal"
            not in client.get("/openapi.json").json()["paths"]
        )
    with _client(service) as client:
        mounted = client.get("/openapi.json").json()["paths"][
            "/v1/versions/{version_id}/scope-proposal"
        ]["get"]
    declared = yaml.safe_load(Path("contracts/openapi.yaml").read_text(encoding="utf-8"))["paths"][
        "/v1/versions/{version_id}/scope-proposal"
    ]["get"]
    assert mounted["operationId"] == declared["operationId"] == "version_scope_proposal"
    assert mounted["x-minimum-role"] == declared["x-minimum-role"] == "editor"
    assert mounted["security"] == declared["security"] == [{"sessionCookie": []}]
    service.close()


def test_evaluation_wrapper_keeps_path_api_and_product_never_imports_evaluation():
    import re

    import proofops.adapters.parsing.report_sections as adapter

    import evaluation.report_sections as legacy

    assert legacy.build_map is adapter.build_map and legacy.POLICY_HASH == adapter.POLICY_HASH
    runtime_import = re.compile(r"^\s*(?:from|import)\s+evaluation\b", re.M)
    root = Path(__file__).resolve().parents[2]
    for base in ("packages/proofops", "apps/api/src", "apps/worker/src"):
        for source in (root / base).rglob("*.py"):
            assert not runtime_import.search(source.read_text(encoding="utf-8")), source
