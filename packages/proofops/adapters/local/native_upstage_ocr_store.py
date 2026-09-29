"""Immutable, fenced job records for NEW-run native Upstage OCR requests.

Mirrors ``raster_job_store`` with separate record kinds and snapshot keys, so v1 raster
records, policies and checkpoints are never read or written here. A request is registered
before any reservation or network call. Only the lease that registered it can store the
provider receipt, and an exact retry never redispatches. Receipt checks reuse the v1
provider-receipt validation (model pin, request/response hashes, billing pages).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid5

from proofops.adapters.local.raster_job_store import _receipt as _validate_provider_receipt
from proofops.application.ports.jobs import LeaseLost
from proofops.application.runs import validate_upstage_ocr_snapshot
from proofops.domain.provenance import canonical_hash
from proofops.domain.values import _require_sha256, _require_strict_int, _require_uuid

REQUEST_KIND = "upstage_ocr_request"
RECEIPT_KIND = "upstage_ocr_receipt"
REQUEST_FIELDS = frozenset(
    {
        "schema",
        "tenant_id",
        "run_id",
        "job_id",
        "document_version_id",
        "parse_manifest_id",
        "input_hash",
        "source_sha256",
        "selected_pages",
        "mode",
        "max_pages",
        "max_calls",
        "submitted_pages",
        "eligible_source_ids",
        "requested_source_ids",
        "policy_sha256",
        "native_attestation_sha256",
        "correspondence",
        "request_id",
    }
)


def _snapshot(db, message):
    row = db.execute(
        "SELECT payload FROM run_snapshots WHERE tenant_id=? AND run_id=?",
        (message.tenant_id, message.run_id),
    ).fetchone()
    if row is None:
        raise ValueError("UPSTAGE_OCR_RUN_SNAPSHOT_MISSING")
    snapshot = json.loads(row[0])
    frozen = {key: value for key, value in snapshot.items() if key != "input_hash"}
    if (
        snapshot.get("tenant_id") != message.tenant_id
        or snapshot.get("run_id") != message.run_id
        or snapshot.get("input_hash") != message.input_hash
        or canonical_hash(frozen) != message.input_hash
        or "native_upstage_ocr_policy" not in snapshot
    ):
        raise ValueError("UPSTAGE_OCR_RUN_INPUT_MISMATCH")
    validate_upstage_ocr_snapshot(snapshot)
    return snapshot


def request_id_for(job_id: str, request: dict) -> str:
    unsigned = {key: value for key, value in request.items() if key != "request_id"}
    return str(uuid5(UUID(job_id), canonical_hash(unsigned)))


def validate_request(request, *, message, snapshot):
    """Shape and scope of a request against its run snapshot and job."""
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise ValueError("UPSTAGE_OCR_REQUEST_INVALID")
    for name in (
        "request_id",
        "tenant_id",
        "run_id",
        "job_id",
        "document_version_id",
        "parse_manifest_id",
    ):
        _require_uuid(name, request[name])
    for name in ("input_hash", "source_sha256", "policy_sha256", "native_attestation_sha256"):
        _require_sha256(name, request[name])
    policy = snapshot["native_upstage_ocr_policy"]
    expected_manifest = str(uuid5(UUID(message.run_id), "parse:" + message.input_hash))
    if (
        request["schema"] != "native_upstage_ocr_request_v1"
        or (request["tenant_id"], request["run_id"], request["job_id"], request["input_hash"])
        != (message.tenant_id, message.run_id, message.job_id, message.input_hash)
        or request["document_version_id"] != snapshot["document"]["version_id"]
        or request["parse_manifest_id"] != expected_manifest
        or request["source_sha256"] != snapshot["document"]["sha256"]
        or request["selected_pages"] != snapshot["selected_pages"]
        or any(request[key] != policy[key] for key in ("mode", "max_pages", "max_calls"))
        or request["policy_sha256"] != snapshot["native_upstage_ocr_policy_hash"]
    ):
        raise ValueError("UPSTAGE_OCR_REQUEST_SCOPE_MISMATCH")
    for name in ("max_pages", "max_calls", "submitted_pages"):
        if _require_strict_int(name, request[name]) < 1:
            raise ValueError("UPSTAGE_OCR_REQUEST_LIMIT_INVALID")
    correspondence = request["correspondence"]
    rows = correspondence.get("rows") if isinstance(correspondence, dict) else None
    requested, eligible = request["requested_source_ids"], request["eligible_source_ids"]
    if (
        not isinstance(rows, list)
        or correspondence.get("schema") != "raster_ocr_correspondence_v1"
        or any(
            correspondence.get(key) != request[key]
            for key in ("tenant_id", "document_version_id", "parse_manifest_id", "source_sha256")
        )
        or request["submitted_pages"] != len(rows)
        or len(rows) > request["max_pages"]
        or not isinstance(requested, list)
        or not isinstance(eligible, list)
        or any(not isinstance(item, str) for item in requested + eligible)
        or len(set(requested)) != len(requested)
        or eligible != sorted(set(eligible))
        or not set(requested) <= set(eligible)
        or [row.get("source_id") if isinstance(row, dict) else None for row in rows] != requested
        or any(
            not isinstance(row, dict)
            or row.get("submitted_page") != index
            or type(row.get("physical_page")) is not int
            or row["physical_page"] not in request["selected_pages"]
            for index, row in enumerate(rows, 1)
        )
        or type(correspondence.get("input_bytes")) is not int
        or correspondence["input_bytes"] < 1
    ):
        raise ValueError("UPSTAGE_OCR_REQUEST_CORRESPONDENCE_INVALID")
    _require_sha256("input_pdf_sha256", correspondence.get("input_pdf_sha256"))
    if request["request_id"] != request_id_for(message.job_id, request):
        raise ValueError("UPSTAGE_OCR_REQUEST_ID_INVALID")


def register_request(store, lease, request, *, now: int) -> bool:
    """Atomically register a prepared request; an exact retry is never redispatched."""
    with store._transaction() as db:
        store._time(now)
        message = lease.message
        if message.stage != "parse" or store._owned(db, lease, now) is None:
            raise LeaseLost("LEASE_LOST")
        snapshot = _snapshot(db, message)
        validate_request(request, message=message, snapshot=snapshot)
        key = request["request_id"]
        raw = store._raw(db, message.tenant_id, message.run_id, REQUEST_KIND, key)
        if raw is not None:
            if json.loads(raw)["request"] != request:
                raise ValueError("UPSTAGE_OCR_REQUEST_IMMUTABLE_MISMATCH")
            return False
        count = db.execute(
            "SELECT count(*) FROM job_records WHERE tenant_id=? AND run_id=? AND kind=?",
            (message.tenant_id, message.run_id, REQUEST_KIND),
        ).fetchone()[0]
        if count >= request["max_calls"]:
            raise ValueError("UPSTAGE_OCR_MAX_CALLS_EXCEEDED")
        store._put(
            db,
            message.tenant_id,
            message.run_id,
            REQUEST_KIND,
            key,
            dict(
                request=request,
                request_sha256=canonical_hash(request),
                owner=lease.owner,
                fencing_token=lease.fencing_token,
            ),
            immutable=True,
        )
        return True


def _registered(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"request", "request_sha256", "owner", "fencing_token"}
        or not isinstance(value.get("request"), dict)
        or value.get("request_sha256") != canonical_hash(value["request"])
        or not isinstance(value.get("owner"), str)
        or type(value.get("fencing_token")) is not int
    ):
        raise ValueError("UPSTAGE_OCR_REQUEST_INTEGRITY_MISMATCH")
    return value


def _rows(db, message):
    return [
        _registered(json.loads(row[0]))
        for row in db.execute(
            "SELECT value FROM job_records WHERE tenant_id=? AND run_id=? AND kind=? "
            "AND json_extract(CAST(value AS TEXT), '$.request.job_id')=? ORDER BY record_id",
            (message.tenant_id, message.run_id, REQUEST_KIND, message.job_id),
        )
    ]


def requests_for(store, message) -> tuple[dict, ...]:
    with store._transaction() as db:
        store._job(db, message)
        return tuple(_rows(db, message))


def _checked_wrapper(registered, wrapper):
    if (
        not isinstance(wrapper, dict)
        or set(wrapper) != {"request_sha256", "receipt", "receipt_sha256"}
        or wrapper["request_sha256"] != registered["request_sha256"]
        or wrapper["receipt_sha256"] != canonical_hash(wrapper["receipt"])
    ):
        raise ValueError("UPSTAGE_OCR_RECEIPT_INTEGRITY_MISMATCH")
    try:
        _validate_provider_receipt(registered["request"], wrapper["receipt"])
    except (KeyError, TypeError):
        raise ValueError("UPSTAGE_OCR_RECEIPT_INVALID") from None
    except ValueError as exc:
        raise ValueError(str(exc).replace("RASTER_", "UPSTAGE_OCR_")) from None
    return wrapper


def finish_request(store, lease, request_id: str, receipt: dict) -> None:
    """Keep an exact provider return only for the lease that registered its request."""
    _require_uuid("request_id", request_id)
    with store._transaction() as db:
        message = lease.message
        store._job(db, message)
        registered = _registered(
            store._get(db, message.tenant_id, message.run_id, REQUEST_KIND, request_id)
        )
        if registered["request"].get("job_id") != message.job_id or (
            registered["owner"],
            registered["fencing_token"],
        ) != (lease.owner, lease.fencing_token):
            raise LeaseLost("unknown upstage OCR request owner")
        wrapper = _checked_wrapper(
            registered,
            dict(
                request_sha256=registered["request_sha256"],
                receipt=receipt,
                receipt_sha256=canonical_hash(receipt),
            ),
        )
        raw = store._raw(db, message.tenant_id, message.run_id, RECEIPT_KIND, request_id)
        if raw is not None:
            if json.loads(raw) != wrapper:
                raise ValueError("UPSTAGE_OCR_RECEIPT_IMMUTABLE_MISMATCH")
            return
        store._put(
            db,
            message.tenant_id,
            message.run_id,
            RECEIPT_KIND,
            request_id,
            wrapper,
            immutable=True,
        )


def receipt_for(store, message, request_id: str) -> dict | None:
    _require_uuid("request_id", request_id)
    with store._transaction() as db:
        store._job(db, message)
        registered = _registered(
            store._get(db, message.tenant_id, message.run_id, REQUEST_KIND, request_id)
        )
        if registered["request"].get("job_id") != message.job_id:
            raise KeyError("resource not found")
        raw = store._raw(db, message.tenant_id, message.run_id, RECEIPT_KIND, request_id)
        return None if raw is None else _checked_wrapper(registered, json.loads(raw))


def stored_entries(store, message) -> tuple[list[tuple[dict, dict, str]], list[dict]]:
    """All registered requests for this job with their receipts, in registration order.

    Raises ``UPSTAGE_OCR_REQUEST_PENDING`` when any request has no stored receipt: an
    unsettled or unknown call never becomes a published or replayable result.
    """
    with store._transaction() as db:
        store._job(db, message)
        entries, refs = [], []
        for registered in _rows(db, message):
            request = registered["request"]
            raw = store._raw(
                db, message.tenant_id, message.run_id, RECEIPT_KIND, request["request_id"]
            )
            if raw is None:
                raise ValueError("UPSTAGE_OCR_REQUEST_PENDING")
            wrapper = _checked_wrapper(registered, json.loads(raw))
            entries.append((request, wrapper["receipt"], wrapper["receipt_sha256"]))
            refs.append(
                dict(
                    request_id=request["request_id"],
                    request_sha256=registered["request_sha256"],
                    receipt_sha256=wrapper["receipt_sha256"],
                )
            )
        return entries, refs


CHECKPOINT_KEYS = frozenset(
    {
        "native_paragraph_upstage_ocr_policy_sha256",
        "native_paragraph_upstage_ocr_artifacts",
        "native_paragraph_upstage_ocr_coverage",
    }
)
COVERAGE_KEYS = frozenset(
    {
        "eligible_source_ids",
        "requested_source_ids",
        "skipped_source_ids",
        "corroborated_source_ids",
        "unresolved_source_ids",
        "complete",
    }
)


def checkpoint_shape(envelope) -> bool:
    """True when the checkpoint carries a well-formed Upstage OCR section; False if absent."""
    present = {key for key in envelope if key.startswith("native_paragraph_upstage_ocr_")}
    if not present:
        return False
    coverage = envelope.get("native_paragraph_upstage_ocr_coverage")
    refs = envelope.get("native_paragraph_upstage_ocr_artifacts")
    if (
        present != CHECKPOINT_KEYS
        or envelope.get("schema") != "local_parser_checkpoint_v4"
        or any(
            key.startswith(("native_paragraph_windows_", "native_paragraph_typography_"))
            for key in envelope
        )
        or not isinstance(coverage, dict)
        or set(coverage) != COVERAGE_KEYS
        or type(coverage["complete"]) is not bool
        or any(
            not isinstance(coverage[k], list)
            or any(not isinstance(v, str) for v in coverage[k])
            or coverage[k] != sorted(set(coverage[k]))
            for k in COVERAGE_KEYS - {"complete"}
        )
        or not isinstance(refs, list)
        or len(refs) > 20
    ):
        raise ValueError("UPSTAGE_OCR_CHECKPOINT_INVALID")
    sets = {k: set(v) for k, v in coverage.items() if k != "complete"}
    if (
        not sets["corroborated_source_ids"]
        <= sets["requested_source_ids"]
        <= sets["eligible_source_ids"]
        or sets["skipped_source_ids"] != sets["eligible_source_ids"] - sets["requested_source_ids"]
        or sets["unresolved_source_ids"]
        != sets["eligible_source_ids"] - sets["corroborated_source_ids"]
        or coverage["complete"] != (not sets["skipped_source_ids"])
    ):
        raise ValueError("UPSTAGE_OCR_CHECKPOINT_INVALID")
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {
            "request_id",
            "request_sha256",
            "receipt_sha256",
        }:
            raise ValueError("UPSTAGE_OCR_CHECKPOINT_INVALID")
        _require_uuid("request_id", ref["request_id"])
        _require_sha256("request_sha256", ref["request_sha256"])
        _require_sha256("receipt_sha256", ref["receipt_sha256"])
    return True


def validate_checkpoint_bindings(db, store, message, snapshot, envelope):
    """Published pointers must equal this job's immutable records, in one transaction."""
    from proofops.adapters.local.native_upstage_ocr import live_policy_for

    enabled = "native_upstage_ocr_policy" in snapshot
    present = checkpoint_shape(envelope) if isinstance(envelope, dict) else False
    if enabled != present:
        raise ValueError("UPSTAGE_OCR_CHECKPOINT_POLICY_MISMATCH")
    if not enabled:
        return
    validate_upstage_ocr_snapshot(snapshot)
    policy = snapshot["native_upstage_ocr_policy"]
    if (
        policy != live_policy_for(policy)
        or envelope["native_paragraph_upstage_ocr_policy_sha256"]
        != snapshot["native_upstage_ocr_policy_hash"]
    ):
        raise ValueError("UPSTAGE_OCR_CHECKPOINT_POLICY_MISMATCH")
    native = envelope.get("native_paragraph_attestation")
    refs, requested = [], set()
    for registered in _rows(db, message):
        request = registered["request"]
        if request["native_attestation_sha256"] != canonical_hash(native) or requested & set(
            request["requested_source_ids"]
        ):
            raise ValueError("UPSTAGE_OCR_CHECKPOINT_REQUEST_MISMATCH")
        requested.update(request["requested_source_ids"])
        raw = store._raw(db, message.tenant_id, message.run_id, RECEIPT_KIND, request["request_id"])
        if raw is None:
            raise ValueError("UPSTAGE_OCR_REQUEST_PENDING")
        wrapper = _checked_wrapper(registered, json.loads(raw))
        refs.append(
            dict(
                request_id=request["request_id"],
                request_sha256=registered["request_sha256"],
                receipt_sha256=wrapper["receipt_sha256"],
            )
        )
    coverage = envelope["native_paragraph_upstage_ocr_coverage"]
    if (
        envelope["native_paragraph_upstage_ocr_artifacts"] != refs
        or len(refs) > policy["max_calls"]
        or coverage["requested_source_ids"] != sorted(requested)
    ):
        raise ValueError("UPSTAGE_OCR_CHECKPOINT_INPUT_MISMATCH")
