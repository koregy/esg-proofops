"""Store lifecycle of the optional adopted REC-002/REC-006 revision receipt.

Reuses the real verified-run anchor from ``test_product_store``. The receipt is
an operator import with no authority: its imported confirmation is dropped,
only the reviewer's review event binds its hash, raw OpenDART pages are copied
by SHA-256 and replayed at evaluation, and legacy cases keep their exact shape.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from proofops.adapters.local.reconciliation_store import FILING_PAGE_DIR, ReconciliationRejected
from proofops.application.reconciliation import revision

# ruff: noqa: F811 -- pytest fixtures imported from the anchored store tests
from tests.reconciliation.test_product_store import (  # noqa: F401
    auth,
    prepare_bundle,
    review_body,
    store,
    verified,
)
from tests.reconciliation.test_rec002_rec006 import CORRECTION, ORIGINAL, UNRELATED, collect


def _revision_bundle(tmp_path, verified, *, receipt=True, pages=True):
    bundle, artifacts_root = prepare_bundle(tmp_path, verified)
    bundle["adopted_revision"] = revision.REVISION
    if not receipt:
        return bundle, artifacts_root
    record, raw_pages, _ = collect()
    page_paths = {}
    (artifacts_root / "pages").mkdir()
    for digest, payload in raw_pages.items():
        (artifacts_root / "pages" / f"{digest}.json").write_bytes(payload)
        page_paths[digest] = f"pages/{digest}.json"
    bundle["revision_receipt"] = {
        "revision": revision.REVISION,
        # An imported confirmation must never carry authority.
        "confirmation": {"actor": "forged", "confirmed_on": "2026-09-28", "receipt_sha256": "0"},
        "explanation_bindings": {},
        "filing_history": {
            "cutoff": {
                "date": bundle["packet"]["identity"]["as_of_date"],
                "granularity": "date_inclusive",
            },
            "search": {key: record[key] for key in ("endpoint", "request", "pages")},
            "classification": {
                ORIGINAL: {"lineage": "family", "basis": "reviewer"},
                CORRECTION: {"lineage": "family", "basis": "reviewer"},
                UNRELATED: {"lineage": "unrelated", "basis": "reviewer"},
            },
            "pinned": {
                "rcept_no": CORRECTION,
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "consolidation": "consolidated",
                "period_evidence_source_ids": ["fs-scope"],
                "consolidation_evidence_source_ids": ["fs-scope"],
            },
        },
    }
    if pages:
        bundle["filing_pages"] = page_paths
    return bundle, artifacts_root


def _register(store, verified, bundle, artifacts_root):  # noqa: F811
    return store.register_case(
        auth(), verified["run_id"], verified["claim_id"], bundle, artifacts_root
    )


def _review_approve_evaluate(store, case_id):  # noqa: F811
    store.review(auth("reviewer"), case_id, review_body(), '"1"', str(uuid4()))
    store.approve_policy(
        auth("admin"), case_id, {"approved": True, "reason": "adopted"}, '"2"', str(uuid4())
    )
    detail = store.evaluate(auth("editor"), case_id, {}, '"3"', str(uuid4()))
    return detail["latest_result"]["result"]


def test_imported_confirmation_is_dropped_and_unreviewed_receipt_blocks(
    store,
    verified,
    tmp_path,  # noqa: F811
):
    detail = _register(store, verified, *_revision_bundle(tmp_path, verified))
    assert detail["provenance"]["adopted_revision"] == revision.REVISION
    assert "confirmation" not in detail["provenance"]["revision_receipt"]
    assert detail["provenance"]["revision_receipt_state"] == "draft"
    assert len(detail["provenance"]["revision_receipt_sha256"]) == 64
    result = store.evaluate(auth("editor"), detail["case_id"], {}, '"1"', str(uuid4()))
    reasons = result["latest_result"]["result"]["reason_codes"]
    assert reasons == ["revision_receipt_unconfirmed"]


def test_reviewed_receipt_reaches_the_replayed_filing_gate(store, verified, tmp_path):  # noqa: F811
    detail = _register(store, verified, *_revision_bundle(tmp_path, verified))
    result = _review_approve_evaluate(store, detail["case_id"])
    # The fixture packet pins "synthetic-receipt", not the latest replayed correction.
    assert result["execution_state"] == "blocked"
    assert result["reason_codes"] == ["financial_filing_not_latest"]
    assert result["engine_version"].endswith(revision.ENGINE_SUFFIX)
    event = store.revision(auth("viewer"), detail["case_id"], 2)["event"]
    assert event["revision_receipt_sha256"] == detail["provenance"]["revision_receipt_sha256"]
    confirmed = store.get_case(auth("viewer"), detail["case_id"])["provenance"]
    assert confirmed["revision_receipt_state"] == "confirmed"


def test_tampered_managed_filing_page_blocks(store, verified, tmp_path):  # noqa: F811
    detail = _register(store, verified, *_revision_bundle(tmp_path, verified))
    managed = store.artifact_root / detail["provenance"]["tenant_id"] / detail["case_id"]
    page = next((managed / FILING_PAGE_DIR).iterdir())
    page.write_bytes(page.read_bytes().replace(b'"status"', b' "status"'))
    result = _review_approve_evaluate(store, detail["case_id"])
    assert result["reason_codes"] == ["filing_page_tampered"]


def test_named_revision_without_receipt_blocks_instead_of_legacy(
    store,
    verified,
    tmp_path,  # noqa: F811
):
    detail = _register(store, verified, *_revision_bundle(tmp_path, verified, receipt=False))
    result = _review_approve_evaluate(store, detail["case_id"])
    assert result["reason_codes"] == ["revision_receipt_missing"]


def test_receipt_pages_must_all_be_imported(store, verified, tmp_path):  # noqa: F811
    bundle, root = _revision_bundle(tmp_path, verified, pages=False)
    with pytest.raises(ReconciliationRejected) as error:
        _register(store, verified, bundle, root)
    assert error.value.code == "FILING_PAGE_BINDING_MISSING"


def test_legacy_case_shape_and_engine_are_unchanged(store, verified, tmp_path):  # noqa: F811
    bundle, root = prepare_bundle(tmp_path, verified)
    detail = _register(store, verified, bundle, root)
    revision_keys = {"adopted_revision", "revision_receipt", "revision_receipt_state"}
    assert not revision_keys & (set(detail) | set(detail["provenance"]))
    result = _review_approve_evaluate(store, detail["case_id"])
    assert not result["engine_version"].endswith(revision.ENGINE_SUFFIX)
    event = store.revision(auth("viewer"), detail["case_id"], 2)["event"]
    assert "revision_receipt_sha256" not in event
