"""REC-006 A no-timely-filing outcome under opt-in revision ``rec-002-006-v2``.

R00 section 12 REC-006 A: "complete official lookup with no timely same-period FS
follows explicit completed not_applicable reason". Output 1.1 cannot carry that
reason, so ``rec-002-006-v1`` keeps it ``blocked``; v2 returns output schema 1.2.

The packet is input 1.2: it names no financial filing at all (no ``rcept_no``, no
``financial_document_version``, no financial fact, source, document or quote).
Retrieval instants come only from the collector's clock in the trusted collector
record; a receipt can restate them verbatim but never supply or edit them.

Synthetic fixtures only: the real fixture bundle builder, the real collector over
a fake transport and fake clock, the real file reader, service, CLI and store. No
network, no model call, no DART key.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from proofops.adapters.dart.client import DartClient
from proofops.adapters.dart.filings import collect_filing_history
from proofops.adapters.local.reconciliation_store import (
    LocalReconciliationStore,
    ReconciliationRejected,
)
from proofops.adapters.reconciliation import FileSourceReader
from proofops.application.reconciliation import revision, service
from proofops.application.reconciliation.presentation import project_result
from proofops.application.reconciliation.schema import SchemaValidationError, validate_schema

from evaluation.reconciliation_cli import main as cli_main
from evaluation.reconciliation_fixtures import build_case

# ruff: noqa: F811 -- pytest fixtures imported from the anchored store tests
from tests.reconciliation.test_product_store import (  # noqa: F401
    auth,
    prepare_bundle,
    review_body,
    store,
    verified,
)
from tests.reconciliation.test_rec002_rec006 import (
    CORP,
    CORRECTION,
    LATE,
    ORIGINAL,
    UNRELATED,
    FakeTransport,
    _row,
)

EVALUATED = date(2026, 9, 29)
CONFIRMED = "2026-09-28"
# The collector clock reads this just before the first page request.
FETCHED = datetime(2025, 7, 15, 1, 0, tzinfo=UTC)
PERIOD_QUOTE = "보고 기간은 2024년 1월 1일부터 2024년 12월 31일까지입니다."
CONSOLIDATION_QUOTE = "재무 정보는 연결 기준으로 작성합니다."
# A family (annual report lineage) filing received only after the cutoff, and an
# unrelated timely filing: the search is complete and nothing timely is family.
NO_TIMELY_ROWS = [
    _row(LATE, "20250801", "사업보고서 (2024.12)"),
    _row(UNRELATED, "20250315", "주요사항보고서(자기주식취득결정)"),
]


class FakeClock:
    """Deterministic, timezone-aware collector clock; one second per reading."""

    def __init__(self, start: datetime | None) -> None:
        self.now = start

    def __call__(self) -> datetime:
        assert self.now is not None
        value, self.now = self.now, self.now + timedelta(seconds=1)
        return value


def collect(rows, *, fetched=FETCHED, page_count=2, **filters):
    transport = FakeTransport(rows, page_count)
    client = DartClient(api_key="test-key-not-real", transport=transport, max_retries=0)
    clock = FakeClock(fetched) if fetched is not None else None
    return collect_filing_history(
        client, CORP, "20240101", "20250930", page_count=page_count, clock=clock, **filters
    )


def confirm(receipt: dict, confirmed_on: str = CONFIRMED) -> dict:
    receipt = {key: value for key, value in receipt.items() if key != "confirmation"}
    receipt["confirmation"] = {
        "actor": "user-reviewer",
        "confirmed_on": confirmed_on,
        "receipt_sha256": revision.receipt_body_sha256(receipt),
    }
    return receipt


def rebuild_document(bundle: dict, root: Path, document_id: str) -> None:
    """Regenerate one document's bytes, hash and bindings from its packet refs."""
    refs = [s for s in bundle["packet"]["sources"] if s["document_id"] == document_id]
    text = "SYNTHETIC REC-006 NO-FILING FIXTURE ONLY\n"
    for ref in refs:
        start = len(text)
        text += ref["quote"]
        ref["locator"] = f"chars:{start}:{len(text)}"
        text += "\n"
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    artifact = bundle["artifacts"][document_id]
    (root / artifact["path"]).write_bytes(payload)
    artifact["sha256"] = digest
    document = bundle["documents"][document_id]
    document["artifact_sha256"] = digest
    for ref in refs:
        ref["artifact_sha256"] = digest
        binding = document["source_bindings"].setdefault(ref["source_id"], {"roles": []})
        binding.update(locator=ref["locator"], quote=ref["quote"])


def receipt_for(record: dict, identity: dict) -> dict:
    """The reviewer's receipt: it restates the collector search and names its hash."""
    present = {row["rcept_no"] for row in record["filings"]}
    classification = {
        LATE: {"lineage": "family", "basis": "reviewer: FY2024 annual report lineage"},
        UNRELATED: {"lineage": "unrelated", "basis": "reviewer: different report type"},
        ORIGINAL: {"lineage": "family", "basis": "reviewer: FY2024 annual report lineage"},
        CORRECTION: {"lineage": "family", "basis": "reviewer: correction of ORIGINAL"},
    }
    return {
        "revision": revision.REVISION_V2,
        "explanation_bindings": {},
        "filing_history": {
            "cutoff": {"date": identity["as_of_date"], "granularity": "date_inclusive"},
            "collection_sha256": revision.collection_sha256(record),
            "search": {
                key: deepcopy(record[key])
                for key in ("search_version", "endpoint", "request", "pages")
            },
            "classification": {k: v for k, v in classification.items() if k in present},
            "no_timely_filing": {
                "family_basis": "reviewer: FY2024 annual business report and its corrections",
                "period_start": identity["financial_period_start"],
                "period_end": identity["financial_period_end"],
                "consolidation": identity["consolidation"],
                "period_evidence_source_ids": ["sr-period"],
                "consolidation_evidence_source_ids": ["sr-consol"],
            },
        },
    }


def to_no_filing(bundle: dict, root: Path) -> dict:
    """Turn a registered-package bundle into an input-1.2 case with no financial filing.

    The financial side is removed, not faked: no ``rcept_no``, no financial document
    version, fact, source, document or artifact. Period and consolidation evidence
    are verified quotes of the registered sustainability report.
    """
    bundle = deepcopy(bundle)
    packet = bundle["packet"]
    packet["schema_version"] = "1.2"
    identity = packet["identity"]
    financial_version = identity["financial_document_version"]
    sr_version = identity["sustainability_document_version"]
    identity.update(rcept_no=None, financial_published_at=None, financial_document_version=None)
    packet["financial"] = {
        "raw": None,
        "normalized": None,
        "kind": "unknown",
        "unit": None,
        "source_id": None,
    }
    packet["sources"] = [s for s in packet["sources"] if s["document_id"] != financial_version]
    packet["sources"] += [
        {"source_id": "sr-period", "document_id": sr_version, "quote": PERIOD_QUOTE},
        {"source_id": "sr-consol", "document_id": sr_version, "quote": CONSOLIDATION_QUOTE},
    ]
    packet["search"]["required_document_ids"] = [sr_version]
    del bundle["documents"][financial_version]
    del bundle["artifacts"][financial_version]
    rebuild_document(bundle, root, sr_version)
    return bundle


class NoFilingCase:
    def __init__(self, tmp_path: Path, *, rows=None, fetched=FETCHED, **filters) -> None:
        self.root = tmp_path / "artifacts"
        self.bundle = to_no_filing(build_case("c1-same-entities", self.root), self.root)
        self.collection, self.pages = collect(
            NO_TIMELY_ROWS if rows is None else rows, fetched=fetched, **filters
        )
        self.receipt = receipt_for(self.collection, self.bundle["packet"]["identity"])

    @property
    def history(self) -> dict:
        return self.receipt["filing_history"]

    def run(
        self,
        *,
        adopted=revision.REVISION_V2,
        receipt="confirmed",
        evaluated=EVALUATED,
        confirmed_on=CONFIRMED,
        collection="trusted",
        packet=None,
    ):
        bundle = self.bundle
        body = dict(self.receipt, revision=adopted)
        kwargs = {
            "adopted_revision": adopted,
            "revision_receipt": confirm(body, confirmed_on) if receipt == "confirmed" else receipt,
            "filing_page_reader": self.pages.__getitem__,
        }
        if adopted == revision.REVISION_V2:
            kwargs["evaluation_date"] = evaluated
            kwargs["filing_collection"] = (
                deepcopy(self.collection) if collection == "trusted" else collection
            )
        return service.reconcile(
            deepcopy(packet or bundle["packet"]),
            deepcopy(bundle["policy"]),
            source_reader=FileSourceReader(self.root, bundle["artifacts"]),
            explanation_search=lambda _packet: [],
            policy_registry=bundle["policies"],
            coverage_registry=bundle["coverage"],
            document_registry=bundle["documents"],
            **kwargs,
        )

    def input_1_1(self) -> dict:
        """The same case forced into input 1.1, which must name a financial version."""
        packet = deepcopy(self.bundle["packet"])
        packet["schema_version"] = "1.1"
        packet["identity"]["financial_document_version"] = "fs-v1"  # registers nothing
        return packet


def blocked_v2(result: dict, *codes: str) -> None:
    validate_schema("output-1.2", result)
    assert result["schema_version"] == "1.2"
    assert (result["execution_state"], result["status"]) == ("blocked", None)
    assert (result["not_applicable_reason"], result["filing_lookup"]) == (None, None)
    assert set(codes) <= set(result["reason_codes"]), result["reason_codes"]
    assert result["engine_version"].endswith(revision.ENGINE_SUFFIX_V2)


def absence_unproven(result: dict, code: str) -> None:
    blocked_v2(result, revision.NO_TIMELY_FILING, code)


# --------------------------------------------------------------------------- #
# positive: explicit reason and schema
# --------------------------------------------------------------------------- #


def test_complete_lookup_without_timely_filing_completes_not_applicable(tmp_path):
    case = NoFilingCase(tmp_path)
    validate_schema("input-1.2", case.bundle["packet"])
    result = case.run()
    validate_schema("output-1.2", result)
    assert result["schema_version"] == "1.2"
    assert (result["execution_state"], result["status"]) == ("completed", "not_applicable")
    assert result["not_applicable_reason"] == "financial_filing_not_available_as_of"
    assert result["reason_codes"] == ["financial_filing_not_available_as_of"]
    assert result["review_required"] is False
    assert (result["financial_value"], result["explanation_source_id"]) == (None, None)
    assert result["engine_version"] == (
        "reconciliation-application-rec006-1" + revision.ENGINE_SUFFIX_V2
    )
    lookup = result["filing_lookup"]
    assert lookup["outcome"] == "no_timely_filing"
    assert lookup["pinned_rcept_no"] is None
    assert lookup["collection_sha256"] == revision.collection_sha256(case.collection)
    # Instants come from the collector clock, one reading per page request.
    assert (lookup["retrieved_from"], lookup["retrieved_until"]) == (
        "2025-07-15T01:00:00Z",
        "2025-07-15T01:00:00Z",
    )
    assert lookup["cutoff"] == {"date": "2025-06-30", "granularity": "date_inclusive"}
    assert lookup["family_basis"].startswith("reviewer:")
    # rcept_no history is preserved, including the family filing received too late.
    assert lookup["filings"] == [
        {
            "rcept_no": UNRELATED,
            "rcept_dt": "2025-03-15",
            "report_nm": "주요사항보고서(자기주식취득결정)",
            "timely": True,
            "lineage": "unrelated",
        },
        {
            "rcept_no": LATE,
            "rcept_dt": "2025-08-01",
            "report_nm": "사업보고서 (2024.12)",
            "timely": False,
            "lineage": "family",
        },
    ]
    assert lookup["page_sha256"] == [p["sha256"] for p in case.collection["pages"]]


def test_collector_clock_stamps_each_page_and_legacy_record_is_unchanged():
    record, _ = collect(NO_TIMELY_ROWS * 1 + [_row(ORIGINAL, "20250310", "x")], page_count=1)
    assert record["search_version"] == revision.FILING_SEARCH_VERSION_V2
    assert [page["retrieved_at"] for page in record["pages"]] == [
        "2025-07-15T01:00:00Z",
        "2025-07-15T01:00:01Z",
        "2025-07-15T01:00:02Z",
    ]
    legacy, _ = collect(NO_TIMELY_ROWS, fetched=None)
    assert legacy["search_version"] == "opendart-list-history-1"
    assert all(set(page) == {"page_no", "sha256"} for page in legacy["pages"])
    naive = DartClient(api_key="k", transport=FakeTransport(NO_TIMELY_ROWS, 2), max_retries=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        collect_filing_history(
            naive, CORP, "20240101", "20250930", clock=lambda: datetime(2025, 7, 15)
        )


def test_empty_official_listing_is_a_genuine_absence(tmp_path):
    case = NoFilingCase(tmp_path, rows=[])
    result = case.run()
    assert result["not_applicable_reason"] == "financial_filing_not_available_as_of"
    assert result["filing_lookup"]["filings"] == []


def test_v1_and_1_1_readers_still_see_blocked(tmp_path):
    case = NoFilingCase(tmp_path)
    # Input 1.2 exists only for v2; v1 and legacy keep the frozen input contract.
    with pytest.raises(SchemaValidationError):
        case.run(adopted=revision.REVISION)
    v1 = case.run(adopted=revision.REVISION, packet=case.input_1_1())
    validate_schema("output", v1)
    assert (v1["schema_version"], v1["execution_state"], v1["status"]) == ("1.1", "blocked", None)
    assert v1["reason_codes"] == ["financial_filing_not_available_as_of"]
    assert "not_applicable_reason" not in v1 and "filing_lookup" not in v1

    legacy = service.downgrade_to_1_1(case.run())
    validate_schema("output", legacy)
    assert (legacy["execution_state"], legacy["status"]) == ("blocked", None)
    assert legacy["reason_codes"] == ["financial_filing_not_available_as_of", "output_1_2_required"]
    with pytest.raises(ValueError, match="output_1_2_required"):
        service.downgrade_to_1_1(v1)


def test_schema_1_2_rejects_an_unexplained_or_unrecorded_not_applicable(tmp_path):
    result = NoFilingCase(tmp_path).run()
    for mutate in (
        lambda r: r.update(not_applicable_reason=None),
        lambda r: r.update(filing_lookup=None),
        lambda r: r["filing_lookup"].update(outcome="pinned"),
        lambda r: r["filing_lookup"].update(retrieved_from=None),
        lambda r: r["filing_lookup"].update(collection_sha256=None),
        lambda r: r.update(schema_version="1.1"),
        lambda r: r.update(execution_state="blocked", status=None),
    ):
        broken = deepcopy(result)
        mutate(broken)
        with pytest.raises(SchemaValidationError):
            validate_schema("output-1.2", broken)
    # The frozen 1.1 contract does not accept the 1.2 result at all.
    with pytest.raises(SchemaValidationError):
        validate_schema("output", result)


def test_input_1_2_differs_from_1_1_only_by_version_and_nullable_document(tmp_path):
    root = Path(__file__).parents[2]
    old = json.loads((root / "contracts/reconciliation/input.schema.json").read_text("utf-8"))
    new = json.loads((root / "contracts/reconciliation/input-1.2.schema.json").read_text("utf-8"))
    packaged = root / "packages/proofops/application/reconciliation/schemas"
    assert (packaged / "input-1.2.schema.json").read_bytes() == (
        root / "contracts/reconciliation/input-1.2.schema.json"
    ).read_bytes()
    new.pop("$comment")
    new["properties"]["schema_version"] = {"const": "1.1"}
    new["properties"]["identity"]["properties"]["financial_document_version"] = {
        "type": "string",
        "minLength": 1,
    }
    assert new == old


def test_projection_carries_the_reason_only_for_1_2(tmp_path):
    case = NoFilingCase(tmp_path)
    result = case.run()
    projection = project_result(result, case.bundle["packet"], case.bundle["documents"])
    assert projection["projection_schema_version"] == "reconciliation-presentation-2"
    assert (projection["execution_state"], projection["status"]) == (
        "completed",
        "not_applicable",
    )
    assert projection["not_applicable_reason"] == "financial_filing_not_available_as_of"
    assert projection["http_result_view"] == "output-1.1-downgrade"
    assert projection["filing_lookup"] == result["filing_lookup"]
    assert projection["result"] == result
    v1 = case.run(adopted=revision.REVISION, packet=case.input_1_1())
    old = project_result(v1, case.bundle["packet"], case.bundle["documents"])
    assert old["projection_schema_version"] == "reconciliation-presentation-1"
    assert not {"not_applicable_reason", "filing_lookup", "result", "http_result_view"} & set(old)


# --------------------------------------------------------------------------- #
# retrieval provenance: only the collector clock counts
# --------------------------------------------------------------------------- #


def test_receipt_cannot_supply_or_edit_retrieval_instants(tmp_path):
    case = NoFilingCase(tmp_path)
    case.history["search"]["pages"][0]["retrieved_at"] = "2025-08-01T00:00:00Z"
    absence_unproven(case.run(), "filing_lookup_collection_mismatch")

    case = NoFilingCase(tmp_path / "extra")
    case.history["search"]["retrieved_on"] = "2025-08-01"
    absence_unproven(case.run(), "filing_lookup_collection_mismatch")

    # Editing the collection itself changes its hash, which the receipt pins.
    case = NoFilingCase(tmp_path / "record")
    case.collection["pages"][0]["retrieved_at"] = "2025-08-01T00:00:00Z"
    case.history["search"]["pages"][0]["retrieved_at"] = "2025-08-01T00:00:00Z"
    absence_unproven(case.run(), "filing_lookup_collection_mismatch")


def test_missing_legacy_or_incomplete_collection_blocks(tmp_path):
    case = NoFilingCase(tmp_path)
    absence_unproven(case.run(collection=None), "filing_lookup_collection_missing")

    # A clockless (history-1) collection carries no retrieval instant at all.
    legacy = NoFilingCase(tmp_path / "legacy", fetched=None)
    absence_unproven(legacy.run(), "filing_lookup_collection_incomplete")

    incomplete = NoFilingCase(tmp_path / "incomplete")
    incomplete.collection.update(state="incomplete", incomplete_reason="page_bound_exceeded")
    incomplete.history["collection_sha256"] = revision.collection_sha256(incomplete.collection)
    absence_unproven(incomplete.run(), "filing_lookup_collection_incomplete")


def test_confirmation_and_evaluation_must_follow_retrieval(tmp_path):
    case = NoFilingCase(tmp_path)
    absence_unproven(
        case.run(confirmed_on="2025-07-14"), "filing_lookup_confirmed_before_retrieval"
    )
    absence_unproven(
        case.run(confirmed_on="2025-08-01", evaluated=date(2025, 7, 31)),
        "filing_lookup_evaluated_before_confirmation",
    )
    assert case.run(confirmed_on="2025-07-15", evaluated=date(2025, 7, 15))["status"] == (
        "not_applicable"
    )


def test_fetch_before_or_on_the_cutoff_day_blocks(tmp_path):
    early = NoFilingCase(tmp_path / "early", fetched=datetime(2025, 6, 1, tzinfo=UTC))
    absence_unproven(early.run(), "filing_lookup_before_cutoff")
    # Same-day ambiguity: a fetch during the cutoff day cannot exclude a later receipt.
    same_day = NoFilingCase(tmp_path / "same", fetched=datetime(2025, 6, 30, 23, 0, tzinfo=UTC))
    absence_unproven(same_day.run(), "filing_lookup_cutoff_day_open")
    future = NoFilingCase(tmp_path / "future", fetched=datetime(2026, 10, 1, tzinfo=UTC))
    absence_unproven(
        future.run(confirmed_on="2026-10-01", evaluated=EVALUATED),
        "filing_lookup_retrieval_future",
    )


def test_cutoff_in_the_future_blocks(tmp_path):
    case = NoFilingCase(tmp_path)
    blocked_v2(case.run(evaluated=date(2025, 6, 29)), "filing_cutoff_future")


# --------------------------------------------------------------------------- #
# negatives: an unknown source never becomes not_applicable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda h: h["search"]["pages"].pop(), "filing_search_incomplete"),
        (lambda h: h["search"]["request"].update(last_reprt_at="Y"), "filing_search_latest_only"),
        (lambda h: h["search"]["request"].update(end_de="20250601"), "filing_search_before_cutoff"),
        (
            lambda h: h["search"]["request"].update(bgn_de="20250101"),
            "filing_search_window_incomplete",
        ),
        (lambda h: h["classification"].pop(UNRELATED), "filing_classification_incomplete"),
    ],
)
def test_incomplete_collection_blocks(tmp_path, mutate, code):
    case = NoFilingCase(tmp_path)
    mutate(case.history)
    blocked_v2(case.run(), code)


def test_filtered_lookup_blocks(tmp_path):
    # A type-filtered listing (e.g. A001 only) cannot show that no family filing
    # exists in another report type; no approved filter set says otherwise.
    for key, value in (("pblntf_ty", "A"), ("pblntf_detail_ty", "A001")):
        case = NoFilingCase(tmp_path / key, **{key: value})
        assert case.collection["request"][key] == value
        absence_unproven(case.run(), "filing_lookup_filtered")


def test_family_filing_on_the_cutoff_day_is_timely_not_absent(tmp_path):
    rows = [_row(ORIGINAL, "20250630", "사업보고서 (2024.12)")]
    result = NoFilingCase(tmp_path, rows=rows).run()
    blocked_v2(result, "financial_filing_not_latest")


def test_same_day_family_filings_stay_ambiguous(tmp_path):
    rows = [
        _row(CORRECTION, "20250320", "[기재정정]사업보고서 (2024.12)"),
        _row(ORIGINAL, "20250320", "사업보고서 (2024.12)"),
    ]
    blocked_v2(NoFilingCase(tmp_path, rows=rows).run(), "filing_order_ambiguous")


def test_stale_or_tampered_receipt_blocks(tmp_path):
    case = NoFilingCase(tmp_path)
    confirmed = confirm(dict(case.receipt))
    confirmed["filing_history"]["no_timely_filing"]["family_basis"] = "edited after review"
    blocked_v2(case.run(receipt=confirmed), "revision_receipt_tampered")
    # A receipt confirmed for v1 cannot authorise the v2 outcome.
    v1_receipt = confirm(dict(case.receipt, revision=revision.REVISION))
    blocked_v2(case.run(receipt=v1_receipt), "revision_receipt_invalid")
    blocked_v2(case.run(receipt=None), "revision_receipt_missing")


def test_tampered_filing_page_or_source_blocks(tmp_path):
    case = NoFilingCase(tmp_path)
    digest = case.collection["pages"][0]["sha256"]
    case.pages[digest] = case.pages[digest].replace(b"20250801", b"20250601")
    blocked_v2(case.run(), "filing_page_tampered")
    source = NoFilingCase(tmp_path / "source")
    path = source.root / source.bundle["artifacts"]["sr-v1"]["path"]
    path.write_bytes(path.read_bytes().replace("12월 31일".encode(), "12월 30일".encode()))
    assert source.run()["execution_state"] == "blocked"


def test_cross_tenant_registry_blocks(tmp_path):
    case = NoFilingCase(tmp_path)
    case.bundle["documents"]["sr-v1"]["tenant_id"] = "other-tenant"
    blocked_v2(case.run(), "document_identity_mismatch")


def test_unreviewed_family_or_period_provenance_blocks(tmp_path):
    case = NoFilingCase(tmp_path)
    del case.history["no_timely_filing"]
    absence_unproven(case.run(), "no_timely_filing_unreviewed")

    case = NoFilingCase(tmp_path / "blank")
    case.history["no_timely_filing"]["family_basis"] = " "
    absence_unproven(case.run(), "no_timely_filing_unreviewed")

    case = NoFilingCase(tmp_path / "period")
    case.history["no_timely_filing"]["period_end"] = "2024-06-30"
    absence_unproven(case.run(), "financial_period_unverified")

    # Dates must be written in the verified quote; a quote without them is not one.
    case = NoFilingCase(tmp_path / "title")
    case.history["no_timely_filing"]["period_evidence_source_ids"] = ["sr-consol"]
    absence_unproven(case.run(), "financial_period_unverified")

    case = NoFilingCase(tmp_path / "consolidation")
    case.history["no_timely_filing"]["consolidation"] = "separate"
    absence_unproven(case.run(), "financial_consolidation_unverified")

    case = NoFilingCase(tmp_path / "unknown")
    case.history["no_timely_filing"]["period_evidence_source_ids"] = ["not-a-source"]
    absence_unproven(case.run(), "financial_evidence_missing")


def test_a_named_financial_filing_contradicts_the_absence(tmp_path):
    for field, value in (("rcept_no", ORIGINAL), ("financial_published_at", "2025-03-10")):
        case = NoFilingCase(tmp_path / field)
        case.bundle["packet"]["identity"][field] = value
        absence_unproven(case.run(), "financial_filing_identity_conflict")
    # No placeholder document version can stand in for "no filing", in 1.2 or 1.1.
    case = NoFilingCase(tmp_path / "version")
    case.bundle["packet"]["identity"]["financial_document_version"] = "fs-v1"
    absence_unproven(case.run(), "financial_filing_identity_conflict")
    absence_unproven(case.run(packet=case.input_1_1()), "financial_filing_identity_conflict")
    case = NoFilingCase(tmp_path / "pinned")
    case.history["pinned"] = {"rcept_no": LATE}
    absence_unproven(case.run(), "financial_filing_identity_conflict")


def test_input_1_2_without_financial_document_never_reaches_the_engine(tmp_path):
    # With a timely family filing there is no absence to prove, and a packet that
    # names no financial document has nothing to compare: blocked, not crashed.
    rows = [_row(ORIGINAL, "20250310", "사업보고서 (2024.12)")]
    case = NoFilingCase(tmp_path, rows=rows)
    case.history["pinned"] = {"rcept_no": ORIGINAL}
    result = case.run()
    assert result["execution_state"] == "blocked"


def test_legacy_and_v1_pinned_outputs_are_unchanged_by_v2(tmp_path):
    from tests.reconciliation.test_rec002_rec006 import Case
    from tests.reconciliation.test_rec002_rec006 import confirm as confirm_v1

    case = Case(tmp_path)
    v1 = case.run()
    expected_keys = json.loads(
        (Path(__file__).parents[2] / "contracts/reconciliation/example-output.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(v1) == set(expected_keys)
    receipt = dict(case.receipt, revision=revision.REVISION_V2)
    v2 = service.reconcile(
        deepcopy(case.packet),
        deepcopy(case.policy),
        source_reader=FileSourceReader(case.root, case.artifacts),
        explanation_search=lambda _packet: [deepcopy(c) for c in case.candidates.values()],
        policy_registry=case.policies,
        coverage_registry=case.coverage,
        document_registry=case.documents,
        adopted_revision=revision.REVISION_V2,
        revision_receipt=confirm_v1(deepcopy(receipt)),
        filing_page_reader=case.pages.__getitem__,
        evaluation_date=EVALUATED,
    )
    validate_schema("output-1.2", v2)
    lookup = v2["filing_lookup"]
    assert (lookup["outcome"], lookup["pinned_rcept_no"]) == ("pinned", CORRECTION)
    # No collection named: the pinned path does not claim retrieval instants.
    assert (lookup["collection_sha256"], lookup["retrieved_from"]) == (None, None)
    assert [f["rcept_no"] for f in lookup["filings"]] == [ORIGINAL, UNRELATED, CORRECTION, LATE]
    assert v2["not_applicable_reason"] is None
    same = service.downgrade_to_1_1(v2)
    same["engine_version"] = same["engine_version"].replace(
        revision.ENGINE_SUFFIX_V2, revision.ENGINE_SUFFIX
    )
    assert same == v1
    for extra in ({"evaluation_date": EVALUATED}, {"filing_collection": {}}):
        with pytest.raises(ValueError, match="evaluation_date_requires_revision_v2"):
            service.reconcile(
                case.packet,
                case.policy,
                source_reader=FileSourceReader(case.root, case.artifacts),
                explanation_search=lambda _: [],
                adopted_revision=revision.REVISION,
                revision_receipt=confirm_v1(case.receipt),
                **extra,
            )


def test_contract_example_is_the_real_1_2_output(tmp_path):
    example = Path(__file__).parents[2] / "contracts/reconciliation/example-output-1.2.json"
    expected = json.loads(example.read_text(encoding="utf-8"))
    validate_schema("output-1.2", expected)
    assert NoFilingCase(tmp_path).run() == expected


# --------------------------------------------------------------------------- #
# CLI and store carry the 1.2 result without touching old outputs
# --------------------------------------------------------------------------- #


def _write_cli_inputs(case: NoFilingCase, directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True)
    pages = directory / "pages"
    pages.mkdir()
    for digest, payload in case.pages.items():
        (pages / f"{digest}.json").write_bytes(payload)
    files = {"pages": pages}
    values = dict(case.bundle, collection=case.collection)
    for name in (
        "packet",
        "policy",
        "documents",
        "artifacts",
        "policies",
        "coverage",
        "collection",
    ):
        files[name] = directory / f"{name}.json"
        files[name].write_text(json.dumps(values[name], ensure_ascii=False), "utf-8")
    files["receipt"] = directory / "receipt.json"
    receipt = json.dumps(confirm(dict(case.receipt)), ensure_ascii=False)
    files["receipt"].write_text(receipt, "utf-8")
    return files


def _cli(files: dict[str, Path], out: Path, *extra: str, packet: Path | None = None) -> int:
    return cli_main(
        [
            "--packet", str(packet or files["packet"]),
            "--policy", str(files["policy"]),
            "--documents", str(files["documents"]),
            "--artifact-index", str(files["artifacts"]),
            "--artifacts", str(out.parent.parent / "artifacts"),
            "--policy-registry", str(files["policies"]),
            "--coverage-registry", str(files["coverage"]),
            "--output", str(out / "result.json"),
            "--projection", str(out / "projection.json"),
            *extra,
        ]
    )  # fmt: skip


def test_cli_writes_1_2_only_when_v2_is_requested(tmp_path):
    case = NoFilingCase(tmp_path)
    files = _write_cli_inputs(case, tmp_path / "inputs")
    names = ("v2", "v2-no-collection", "v1", "legacy", "stray")
    outs = {name: tmp_path / "out" / name for name in names}
    for directory in outs.values():
        directory.mkdir(parents=True)
    pages = ("--filing-pages", str(files["pages"]))
    v2_args = (
        "--adopted-revision", revision.REVISION_V2,
        "--evaluation-date", EVALUATED.isoformat(),
        "--revision-receipt", str(files["receipt"]),
        *pages,
    )  # fmt: skip
    assert _cli(files, outs["v2"], *v2_args, "--filing-collection", str(files["collection"])) == 0
    result = json.loads((outs["v2"] / "result.json").read_text("utf-8"))
    validate_schema("output-1.2", result)
    assert result["not_applicable_reason"] == "financial_filing_not_available_as_of"
    assert result["filing_lookup"]["retrieved_from"] == "2025-07-15T01:00:00Z"
    projection = json.loads((outs["v2"] / "projection.json").read_text("utf-8"))
    assert projection["projection_schema_version"] == "reconciliation-presentation-2"
    assert projection["not_applicable_reason"] == "financial_filing_not_available_as_of"

    # Without the trusted collector record the CLI cannot prove the absence.
    assert _cli(files, outs["v2-no-collection"], *v2_args) == 0
    blocked = json.loads((outs["v2-no-collection"] / "result.json").read_text("utf-8"))
    assert "filing_lookup_collection_missing" in blocked["reason_codes"]

    packet_1_1 = tmp_path / "inputs" / "packet-1.1.json"
    packet_1_1.write_text(json.dumps(case.input_1_1(), ensure_ascii=False), "utf-8")
    receipt_v1 = tmp_path / "inputs" / "receipt-v1.json"
    receipt_v1.write_text(
        json.dumps(confirm(dict(case.receipt, revision=revision.REVISION)), ensure_ascii=False),
        "utf-8",
    )
    v1_args = ("--adopted-revision", revision.REVISION, "--revision-receipt", str(receipt_v1))
    assert _cli(files, outs["v1"], *v1_args, *pages, packet=packet_1_1) == 0
    v1 = json.loads((outs["v1"] / "result.json").read_text("utf-8"))
    validate_schema("output", v1)
    assert (v1["execution_state"], v1["reason_codes"]) == (
        "blocked",
        ["financial_filing_not_available_as_of"],
    )

    # Without the flag the CLI is the frozen legacy path: input 1.2 is refused.
    assert _cli(files, outs["legacy"]) == 2
    assert not (outs["legacy"] / "result.json").exists()
    # Revision inputs without naming the revision are refused, never ignored.
    assert _cli(files, outs["stray"], "--filing-collection", str(files["collection"])) == 2
    assert not (outs["stray"] / "result.json").exists()


FIXED_STORE_CLOCK = datetime(2025, 7, 15, 1, 0, tzinfo=UTC)


@pytest.fixture
def clocked_store(verified, tmp_path):  # noqa: F811
    service_ = verified["service"]
    return LocalReconciliationStore(
        service_.store.path,
        tmp_path / "managed",
        run_store=service_.store,
        claims=verified["claims"],
        tags=verified["tags"],
        clock=FakeClock(FIXED_STORE_CLOCK),
    )


def _store_collect(store, rows=None, tenant_auth=None):  # noqa: F811
    client = DartClient(
        api_key="test-key-not-real",
        transport=FakeTransport(NO_TIMELY_ROWS if rows is None else rows, 2),
        max_retries=0,
    )
    return store.collect_filing_history(
        tenant_auth or auth(), client, CORP, "20240101", "20250930", page_count=2
    )


def _store_bundle(tmp_path, verified, collected):  # noqa: F811
    bundle, root = prepare_bundle(tmp_path, verified)
    converted = to_no_filing(bundle, root)
    converted["adopted_revision"] = revision.REVISION_V2
    converted["revision_receipt"] = receipt_for(
        collected["record"], converted["packet"]["identity"]
    )
    return converted, root


def _evaluate(store, case_id):  # noqa: F811
    store.review(auth("reviewer"), case_id, review_body(), '"1"', str(uuid4()))
    store.approve_policy(
        auth("admin"), case_id, {"approved": True, "reason": "adopted"}, '"2"', str(uuid4())
    )
    return store.evaluate(auth("editor"), case_id, {}, '"3"', str(uuid4()))


def test_store_collects_with_its_clock_and_carries_1_2(clocked_store, verified, tmp_path):  # noqa: F811
    store = clocked_store
    collected = _store_collect(store)
    record = collected["record"]
    assert record["pages"][0]["retrieved_at"] == "2025-07-15T01:00:00Z"
    assert collected["collection_sha256"] == revision.collection_sha256(record)
    bundle, root = _store_bundle(tmp_path, verified, collected)
    # No financial document, artifact, version or rcept_no is imported for this case.
    identity = bundle["packet"]["identity"]
    assert (identity["financial_document_version"], identity["rcept_no"]) == (None, None)
    assert "filing_pages" not in bundle
    detail = store.register_case(auth(), verified["run_id"], verified["claim_id"], bundle, root)
    case_id = detail["case_id"]
    assert detail["provenance"]["adopted_revision"] == revision.REVISION_V2
    assert detail["provenance"]["revision_receipt_state"] == "draft"

    evaluated = _evaluate(store, case_id)
    # The unchanged HTTP DTOs (strict result 1.1) still accept the stored case.
    from proofops_api.routers.reconciliation import CaseDetail, RevisionSnapshot

    CaseDetail.model_validate(evaluated)
    RevisionSnapshot.model_validate(store.revision(auth("viewer"), case_id, 4))
    latest = evaluated["latest_result"]
    validate_schema("output", latest["result"])
    assert latest["result"]["reason_codes"] == [
        "financial_filing_not_available_as_of",
        "output_1_2_required",
    ]
    # The full 1.2 result travels in the open, versioned projection.
    projection = latest["projection"]
    assert projection["projection_schema_version"] == "reconciliation-presentation-2"
    assert projection["http_result_view"] == "output-1.1-downgrade"
    assert projection["not_applicable_reason"] == "financial_filing_not_available_as_of"
    versioned = projection["result"]
    validate_schema("output-1.2", versioned)
    assert (versioned["execution_state"], versioned["status"]) == ("completed", "not_applicable")
    lookup = versioned["filing_lookup"]
    assert lookup["collection_sha256"] == collected["collection_sha256"]
    assert lookup["retrieved_from"] == "2025-07-15T01:00:00Z"
    assert {f["rcept_no"] for f in lookup["filings"]} == {LATE, UNRELATED}
    assert store.revision(auth("viewer"), case_id, 4)["projection"]["result"] == versioned
    with pytest.raises(ReconciliationRejected):
        store.get_case(auth("viewer", tenant=str(uuid4())), case_id)


def test_store_refuses_edited_times_and_unknown_or_foreign_collections(
    clocked_store,
    verified,
    tmp_path,  # noqa: F811
):
    store = clocked_store
    collected = _store_collect(store)

    bundle, root = _store_bundle(tmp_path / "edited", verified, collected)
    search = bundle["revision_receipt"]["filing_history"]["search"]
    search["pages"][0]["retrieved_at"] = "2025-08-01T00:00:00Z"
    detail = store.register_case(auth(), verified["run_id"], verified["claim_id"], bundle, root)
    result = _evaluate(store, detail["case_id"])["latest_result"]["projection"]["result"]
    assert "filing_lookup_collection_mismatch" in result["reason_codes"]
    assert result["status"] is None

    unknown, root = _store_bundle(tmp_path / "unknown", verified, collected)
    unknown["revision_receipt"]["filing_history"]["collection_sha256"] = "0" * 64
    with pytest.raises(ReconciliationRejected) as error:
        store.register_case(auth(), verified["run_id"], verified["claim_id"], unknown, root)
    assert error.value.code == "FILING_COLLECTION_NOT_FOUND"

    # Collections are tenant-scoped: another tenant's collection is not found.
    foreign = _store_collect(store, tenant_auth=auth("admin", tenant=str(uuid4())))
    other, root = _store_bundle(tmp_path / "foreign", verified, foreign)
    with pytest.raises(ReconciliationRejected) as error:
        store.register_case(auth(), verified["run_id"], verified["claim_id"], other, root)
    assert error.value.code == "FILING_COLLECTION_NOT_FOUND"

    # A collection-bound receipt never imports page files and is v2-only.
    extra, root = _store_bundle(tmp_path / "pages", verified, collected)
    extra["filing_pages"] = {collected["record"]["pages"][0]["sha256"]: "x.json"}
    with pytest.raises(ReconciliationRejected) as error:
        store.register_case(auth(), verified["run_id"], verified["claim_id"], extra, root)
    assert error.value.code == "VALIDATION_ERROR"


def test_store_collection_rows_are_immutable(clocked_store):
    store = clocked_store
    _store_collect(store)
    import sqlite3

    with pytest.raises(sqlite3.DatabaseError):
        with store.jobs._transaction() as db:
            db.execute("UPDATE reconciliation_filing_collection SET record=x'00'")


def test_legacy_store_case_shape_is_unchanged(store, verified, tmp_path):  # noqa: F811
    bundle, root = prepare_bundle(tmp_path, verified)
    detail = store.register_case(auth(), verified["run_id"], verified["claim_id"], bundle, root)
    latest = _evaluate(store, detail["case_id"])["latest_result"]
    assert latest["projection"]["projection_schema_version"] == "reconciliation-presentation-1"
    assert "not_applicable_reason" not in latest["result"]


def test_collection_table_is_an_additive_migration_of_an_existing_database(
    store,
    verified,
    tmp_path,  # noqa: F811
):
    bundle, root = prepare_bundle(tmp_path, verified)
    detail = store.register_case(auth(), verified["run_id"], verified["claim_id"], bundle, root)
    # Simulate a database created before this revision: no collection table.
    with store.jobs._transaction() as db:
        for action in ("update", "delete"):
            db.execute(f"DROP TRIGGER reconciliation_filing_collection_no_{action}")
        db.execute("DROP TABLE reconciliation_filing_collection")
    reopened = LocalReconciliationStore(
        store.run_store.path,
        store.artifact_root,
        run_store=store.run_store,
        claims=store.claims,
        tags=store.tags,
    )
    with reopened.jobs._transaction() as db:
        assert db.execute("SELECT version FROM reconciliation_schema").fetchall() == [(1,)]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master").fetchall()}
    assert "reconciliation_filing_collection" in tables
    assert reopened.get_case(auth("viewer"), detail["case_id"]) == store.get_case(
        auth("viewer"), detail["case_id"]
    )


def _collection_state(store):  # noqa: F811
    with store.jobs._transaction() as db:
        rows = db.execute("SELECT COUNT(*) FROM reconciliation_filing_collection").fetchone()[0]
        cases = db.execute("SELECT COUNT(*) FROM reconciliation_case").fetchone()[0]
    files = sorted(str(path) for path in store.artifact_root.rglob("*") if path.is_file())
    return rows, cases, files


def test_failed_collection_or_proof_writes_nothing(clocked_store, verified, tmp_path):  # noqa: F811
    store = clocked_store
    before = _collection_state(store)
    store._clock = lambda: datetime(2025, 7, 15, 1, 0)  # naive: not a trusted instant
    with pytest.raises(ValueError, match="timezone-aware"):
        _store_collect(store)

    def failing(url, headers=None, timeout=10.0):
        return 500, {}, b"upstream error"

    store._clock = FakeClock(FIXED_STORE_CLOCK)
    client = DartClient(api_key="secret-never-copied", transport=failing, max_retries=0)
    collected = store.collect_filing_history(auth(), client, CORP, "20240101", "20250930")
    # A failed fetch is still stored as evidence, but only as an incomplete record.
    assert collected["record"]["state"] == "incomplete"
    assert "secret-never-copied" not in json.dumps(collected)
    rows_after_incomplete = _collection_state(store)[0]
    assert rows_after_incomplete == before[0] + 1

    good = _store_collect(store)
    snapshot = _collection_state(store)
    unknown, root = _store_bundle(tmp_path / "unknown", verified, good)
    unknown["revision_receipt"]["filing_history"]["collection_sha256"] = "f" * 64
    with pytest.raises(ReconciliationRejected):
        store.register_case(auth(), verified["run_id"], verified["claim_id"], unknown, root)
    assert _collection_state(store) == snapshot

    # An incomplete collection cannot prove the absence.
    incomplete, root = _store_bundle(tmp_path / "incomplete", verified, collected)
    detail = store.register_case(auth(), verified["run_id"], verified["claim_id"], incomplete, root)
    result = _evaluate(store, detail["case_id"])["latest_result"]["projection"]["result"]
    assert result["status"] is None
    assert result["reason_codes"] == ["filing_search_incomplete"]
