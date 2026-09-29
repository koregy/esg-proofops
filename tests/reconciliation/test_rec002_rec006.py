"""Adopted REC-002 / REC-006 revision (R00 section 12, 2026-09-28).

Synthetic fixtures only: no model call, no network, no DART key. OpenDART list
pages are produced by the real collector through a fake transport and replayed
from their raw bytes by the real application gate; the real pure domain engine
computes every completed status.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from proofops.adapters.dart.client import DartClient
from proofops.adapters.dart.filings import collect_filing_history
from proofops.adapters.reconciliation import FileSourceReader
from proofops.application.reconciliation import revision, service

ROOT = Path(__file__).parents[2]
CONTRACT = ROOT / "contracts" / "reconciliation"
CORP = "00000000"
ORIGINAL, UNRELATED, CORRECTION, LATE = (
    "20250310000100",
    "20250315000150",
    "20250320000200",
    "20250801000300",
)
SR_TEXT = {
    "sr-scope": "보고 대상 법인은 가상법인 A와 B입니다.",
    "sr-expl": "가상법인 C는 지분법 적용 대상이라 보고 범위에서 제외했습니다.",
    "sr-unrel": "이 보고서는 이사회 승인을 받았습니다.",
}
FS_TEXT = {
    "fs-scope": "연결 대상 법인은 가상법인 A, B, C입니다.",
    "fs-period": "당기 제10기 2024년 01월 01일부터 2024년 12월 31일까지",
    "fs-consol": "연결재무제표",
}


def _row(rcept_no: str, rcept_dt: str, report_nm: str) -> dict:
    return {
        "corp_code": CORP,
        "corp_name": "가상법인",
        "stock_code": "",
        "corp_cls": "E",
        "report_nm": report_nm,
        "rcept_no": rcept_no,
        "flr_nm": "가상법인",
        "rcept_dt": rcept_dt,
        "rm": "",
    }


DEFAULT_ROWS = [
    _row(LATE, "20250801", "[기재정정]사업보고서 (2024.12)"),
    _row(CORRECTION, "20250320", "[기재정정]사업보고서 (2024.12)"),
    _row(UNRELATED, "20250315", "주요사항보고서(자기주식취득결정)"),
    _row(ORIGINAL, "20250310", "사업보고서 (2024.12)"),
]


class FakeTransport:
    """Serves OpenDART list pages from a fixed listing; never touches the network."""

    def __init__(self, rows: list[dict], page_count: int, *, drift_on: int | None = None):
        self.rows, self.page_count, self.drift_on = rows, page_count, drift_on
        self.urls: list[str] = []

    def __call__(self, url, headers=None, timeout=10.0):
        self.urls.append(url)
        page_no = int(url.split("page_no=")[1].split("&")[0])
        if not self.rows:
            body = {"status": "013", "message": "조회된 데이타가 없습니다."}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
        total_page = -(-len(self.rows) // self.page_count)
        start = (page_no - 1) * self.page_count
        total_count = len(self.rows) + (1 if page_no == self.drift_on else 0)
        body = {
            "status": "000",
            "message": "정상",
            "page_no": page_no,
            "page_count": self.page_count,
            "total_count": total_count,
            "total_page": total_page,
            "list": self.rows[start : start + self.page_count],
        }
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()


def collect(rows=None, *, page_count=2, bgn_de="20240101", end_de="20250930", **kwargs):
    transport = FakeTransport(DEFAULT_ROWS if rows is None else rows, page_count, **kwargs)
    client = DartClient(api_key="test-key-not-real", transport=transport, max_retries=0)
    record, pages = collect_filing_history(client, CORP, bgn_de, end_de, page_count=page_count)
    return record, pages, transport


def _write_document(root: Path, name: str, segments: dict[str, str]) -> tuple[str, dict]:
    text, locators = "SYNTHETIC REC-002/006 FIXTURE ONLY\n", {}
    for source_id, quote in segments.items():
        start = len(text)
        text += quote
        locators[source_id] = f"chars:{start}:{len(text)}"
        text += "\n"
    payload = text.encode("utf-8")
    (root / f"{name}.txt").write_bytes(payload)
    return hashlib.sha256(payload).hexdigest(), locators


def confirm(receipt: dict) -> dict:
    receipt = {key: value for key, value in receipt.items() if key != "confirmation"}
    receipt["confirmation"] = {
        "actor": "user-reviewer",
        "confirmed_on": "2026-09-28",
        "receipt_sha256": revision.receipt_body_sha256(receipt),
    }
    return receipt


class Case:
    """One synthetic C1 boundary difference: SR {A,B} vs pinned FS {A,B,C}."""

    def __init__(
        self, tmp_path: Path, *, rows=None, fs_text=None, pinned=CORRECTION, published=None
    ):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path
        self.fs_text = dict(FS_TEXT, **(fs_text or {}))
        sr_digest, sr_loc = _write_document(tmp_path, "sr-v1", SR_TEXT)
        fs_digest, fs_loc = _write_document(tmp_path, "fs-v1", self.fs_text)
        self.digests = {"sr-v1": sr_digest, "fs-v1": fs_digest}
        self.artifacts = {
            name: {"path": f"{name}.txt", "format": "text", "sha256": digest}
            for name, digest in self.digests.items()
        }
        listing = DEFAULT_ROWS if rows is None else rows
        dates = {row["rcept_no"]: row["rcept_dt"] for row in listing}
        if published is None:
            published = revision.receipt_date(dates.get(pinned, "20250320")).isoformat()

        packet = json.loads((CONTRACT / "example-input.json").read_text(encoding="utf-8"))
        self.policy = json.loads((CONTRACT / "example-policy.json").read_text(encoding="utf-8"))
        packet["identity"].update(rcept_no=pinned, financial_published_at=published)
        self.candidates = {}
        packet["sources"] = []
        for document_id, texts, locators in (
            ("sr-v1", SR_TEXT, sr_loc),
            ("fs-v1", self.fs_text, fs_loc),
        ):
            for source_id, quote in texts.items():
                source = {
                    "source_id": source_id,
                    "document_id": document_id,
                    "artifact_sha256": self.digests[document_id],
                    "locator": locators[source_id],
                    "quote": quote,
                }
                if source_id in {"sr-expl", "sr-unrel"}:
                    self.candidates[source_id] = source
                else:
                    packet["sources"].append(source)
        packet["claim"]["quote"] = SR_TEXT["sr-scope"]
        packet["sustainability"].update(raw="가상법인 A, B", normalized='["A","B"]')
        packet["financial"].update(
            raw="가상법인 A, B, C", normalized='["A","B","C"]', source_id="fs-scope"
        )
        reviewed = [source["source_id"] for source in packet["sources"]] + list(self.candidates)
        packet["search"].update(
            state="complete",
            coverage_policy_id=self.policy["coverage_policy_id"],
            receipt_id="receipt",
            # The trusted coverage receipt, not the packet, lists search candidates.
            reviewed_source_ids=[source["source_id"] for source in packet["sources"]],
        )
        packet["explanation"]["search_complete"] = True
        self.packet = packet
        self.coverage = {
            "receipt": {
                "state": "complete",
                "coverage_policy_id": self.policy["coverage_policy_id"],
                "tenant_id": "fixture-tenant",
                "company_id": "fixture-company",
                "package_id": "fixture-package",
                "required_document_ids": ["sr-v1", "fs-v1"],
                "reviewed_source_ids": list(reviewed),
                "failed_document_ids": [],
            }
        }
        self.documents = {
            document_id: self._document(document_id, role, published)
            for document_id, role in (("sr-v1", "sustainability"), ("fs-v1", "financial"))
        }
        self.policies = {
            service.canonical_sha256(self.policy): {
                "approved": True,
                "approved_by": "trusted",
                "approved_on": "2026-09-28",
                "version": self.policy["version"],
                "source_policy_sha256": self.policy["source_policy_sha256"],
                "synthetic_only": True,
            }
        }
        record, self.pages, _ = collect(rows)
        classification = {
            ORIGINAL: {"lineage": "family", "basis": "reviewer: same annual report lineage"},
            CORRECTION: {"lineage": "family", "basis": "reviewer: correction of ORIGINAL"},
            UNRELATED: {"lineage": "unrelated", "basis": "reviewer: different report type"},
        }
        present = {row["rcept_no"] for row in listing}
        self.receipt = {
            "revision": revision.REVISION,
            "explanation_bindings": {"sr-expl": self.binding("sr-expl")},
            "filing_history": {
                "cutoff": {
                    "date": packet["identity"]["as_of_date"],
                    "granularity": "date_inclusive",
                },
                "search": {key: record[key] for key in ("endpoint", "request", "pages")},
                "classification": {
                    key: value for key, value in classification.items() if key in present
                },
                "pinned": {
                    "rcept_no": pinned,
                    "period_start": "2024-01-01",
                    "period_end": "2024-12-31",
                    "consolidation": "consolidated",
                    "period_evidence_source_ids": ["fs-period"],
                    "consolidation_evidence_source_ids": ["fs-consol"],
                },
            },
        }

    def _document(self, document_id: str, role: str, published: str) -> dict:
        financial = role == "financial"
        identity = self.packet["identity"]
        bindings = {}
        for source in [*self.packet["sources"], *self.candidates.values()]:
            if source["document_id"] != document_id:
                continue
            roles = []
            if source["source_id"] == "sr-scope":
                roles += ["claim", "sustainability_fact"]
            if source["source_id"] == "fs-scope":
                roles.append("financial_fact")
            if source["source_id"] in self.candidates:
                roles.append("explanation")
            bindings[source["source_id"]] = {
                "locator": source["locator"],
                "quote": source["quote"],
                "roles": roles,
            }
        fact = self.packet[role]
        return {
            "synthetic": True,
            "tenant_id": "fixture-tenant",
            "company_id": "fixture-company",
            "package_id": "fixture-package",
            "document_version_id": document_id,
            "document_role": role,
            "artifact_sha256": self.digests[document_id],
            "corp_code": CORP,
            "fiscal_year": 2024,
            "rcept_no": identity["rcept_no"] if financial else None,
            "consolidation": "consolidated",
            "published_at": published if financial else "2025-06-30",
            "available_on": published if financial else "2025-06-30",
            "as_of_date": identity["as_of_date"],
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "relevant_items": ["C1"],
            "decision_binding": {
                "item": self.packet["item"],
                "comparability": self.packet["comparability"],
                "claim": deepcopy(self.packet["claim"]),
                "c3_context": None,
                "c4_context": None,
                "claim_id": identity["claim_id"],
            },
            "source_bindings": bindings,
            "fact_bindings": {
                fact["source_id"]: {key: fact[key] for key in ("raw", "normalized", "kind", "unit")}
            },
        }

    def binding(self, source_id: str) -> dict:
        candidate = self.candidates[source_id]
        return {
            "claim_id": "fixture-claim",
            "item": "C1",
            "package_id": "fixture-package",
            "document_id": candidate["document_id"],
            "artifact_sha256": candidate["artifact_sha256"],
            "locator": candidate["locator"],
            "quote": candidate["quote"],
            "compared": {
                "sustainability_source_id": "sr-scope",
                "sustainability_normalized": '["A","B"]',
                "financial_source_id": "fs-scope",
                "financial_normalized": '["A","B","C"]',
            },
        }

    def only_reviewed(self, *candidate_ids: str) -> None:
        reviewed = [s["source_id"] for s in self.packet["sources"]] + list(candidate_ids)
        self.coverage["receipt"]["reviewed_source_ids"] = reviewed

    def run(self, *, candidates=("sr-expl", "sr-unrel"), receipt="confirmed", legacy=False):
        search = [deepcopy(self.candidates[name]) for name in candidates]
        kwargs = {}
        if not legacy:
            kwargs = {
                "adopted_revision": revision.REVISION,
                "revision_receipt": confirm(self.receipt) if receipt == "confirmed" else receipt,
                "filing_page_reader": self.pages.__getitem__,
            }
        return service.reconcile(
            deepcopy(self.packet),
            deepcopy(self.policy),
            source_reader=FileSourceReader(self.root, self.artifacts),
            explanation_search=lambda _packet: deepcopy(search),
            policy_registry=self.policies,
            coverage_registry=self.coverage,
            document_registry=self.documents,
            **kwargs,
        )


def blocked_by(result: dict, code: str) -> None:
    assert result["execution_state"] == "blocked"
    assert result["status"] is None
    assert code in result["reason_codes"], result["reason_codes"]
    assert result["engine_version"].endswith(revision.ENGINE_SUFFIX)


# --------------------------------------------------------------------------- #
# REC-002 explanation binding
# --------------------------------------------------------------------------- #


def test_bound_explanation_matches_and_unrelated_quote_is_excluded(tmp_path):
    result = Case(tmp_path).run()
    assert result["execution_state"] == "completed"
    assert result["status"] == "matched"
    assert result["explanation_source_id"] == "sr-expl"
    assert "difference_explained" in result["reason_codes"]
    assert "explanation_candidate_unbound" in result["reason_codes"]
    assert result["engine_version"] == "reconciliation-engine-1.2.0" + revision.ENGINE_SUFFIX


def test_unrelated_verified_quote_is_not_an_explanation(tmp_path):
    case = Case(tmp_path)
    case.only_reviewed("sr-unrel")
    result = case.run(candidates=("sr-unrel",))
    assert (result["execution_state"], result["status"]) == ("completed", "needs_explanation")
    assert result["explanation_source_id"] is None
    assert "explanation_candidate_unbound" in result["reason_codes"]


def test_legacy_path_is_unchanged_and_would_accept_the_unrelated_quote(tmp_path):
    # Demonstrates the adopted gap and that frozen replay semantics are untouched.
    case = Case(tmp_path)
    case.only_reviewed("sr-unrel")
    result = case.run(candidates=("sr-unrel",), legacy=True)
    assert (result["status"], result["explanation_source_id"]) == ("matched", "sr-unrel")
    assert result["engine_version"] == "reconciliation-engine-1.2.0"


def test_explanation_bound_to_another_difference_is_excluded(tmp_path):
    case = Case(tmp_path)
    case.receipt["explanation_bindings"]["sr-expl"]["compared"]["financial_normalized"] = (
        '["A","B","D"]'
    )
    case.only_reviewed("sr-expl")
    result = case.run(candidates=("sr-expl",))
    assert result["status"] == "needs_explanation"
    assert result["explanation_source_id"] is None


def test_explanation_bound_to_another_claim_or_package_is_excluded(tmp_path):
    for field, value in (("claim_id", "other-claim"), ("package_id", "other-package")):
        case = Case(tmp_path / field)
        case.receipt["explanation_bindings"]["sr-expl"][field] = value
        case.only_reviewed("sr-expl")
        assert case.run(candidates=("sr-expl",))["status"] == "needs_explanation"


def test_packet_asserted_unbound_explanation_blocks(tmp_path):
    case = Case(tmp_path)
    case.packet["sources"].append(deepcopy(case.candidates["sr-expl"]))
    case.packet["explanation"]["source_id"] = "sr-expl"
    case.only_reviewed()
    case.receipt["explanation_bindings"] = {}
    blocked_by(case.run(candidates=()), "explanation_binding_missing")
    case.receipt["explanation_bindings"] = {"sr-expl": case.binding("sr-expl")}
    assert case.run(candidates=())["status"] == "matched"


def test_incomplete_search_without_bound_quote_stays_blocked(tmp_path):
    case = Case(tmp_path)
    case.only_reviewed("sr-unrel")
    case.coverage["receipt"]["state"] = "incomplete"
    blocked_by(case.run(candidates=("sr-unrel",)), "search_incomplete")


def test_tampered_explanation_source_bytes_block(tmp_path):
    case = Case(tmp_path)
    path = tmp_path / "sr-v1.txt"
    path.write_bytes(path.read_bytes().replace("지분법".encode(), "원가법".encode()))
    result = case.run()
    assert result["execution_state"] == "blocked"
    assert result["status"] is None


def test_receipt_edited_after_confirmation_blocks(tmp_path):
    case = Case(tmp_path)
    tampered = confirm(case.receipt)
    tampered["explanation_bindings"]["sr-unrel"] = case.binding("sr-unrel")
    blocked_by(case.run(receipt=tampered), "revision_receipt_tampered")


def test_missing_or_unconfirmed_receipt_never_falls_back_to_legacy(tmp_path):
    case = Case(tmp_path)
    blocked_by(case.run(receipt=None), "revision_receipt_missing")
    blocked_by(case.run(receipt=deepcopy(case.receipt)), "revision_receipt_unconfirmed")
    with pytest.raises(ValueError, match="revision_receipt_without_revision"):
        service.reconcile(
            case.packet,
            case.policy,
            source_reader=FileSourceReader(case.root, case.artifacts),
            explanation_search=lambda _: [],
            revision_receipt=confirm(case.receipt),
        )


# --------------------------------------------------------------------------- #
# REC-006 latest corrected filing as of cutoff
# --------------------------------------------------------------------------- #


def test_stale_original_filing_is_blocked_when_a_correction_precedes_cutoff(tmp_path):
    case = Case(tmp_path, pinned=ORIGINAL)
    blocked_by(case.run(), "financial_filing_not_latest")


def test_correction_after_cutoff_is_preserved_but_not_pinned(tmp_path):
    rows = [
        _row(CORRECTION, "20250701", "[기재정정]사업보고서 (2024.12)"),
        _row(UNRELATED, "20250315", "주요사항보고서(자기주식취득결정)"),
        _row(ORIGINAL, "20250310", "사업보고서 (2024.12)"),
    ]
    case = Case(tmp_path, rows=rows, pinned=ORIGINAL)
    # The post-cutoff row is in the replayed listing but needs no classification.
    del case.receipt["filing_history"]["classification"][CORRECTION]
    assert case.run()["status"] == "matched"
    stale_pin = Case(tmp_path / "late", rows=rows, pinned=CORRECTION, published="2025-03-20")
    blocked_by(stale_pin.run(), "financial_filing_not_latest")


def test_unclassified_timely_filing_blocks(tmp_path):
    case = Case(tmp_path)
    del case.receipt["filing_history"]["classification"][UNRELATED]
    blocked_by(case.run(), "filing_classification_incomplete")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda s: s["pages"].pop(), "filing_search_incomplete"),
        (lambda s: s["request"].update(last_reprt_at="Y"), "filing_search_latest_only"),
        (lambda s: s["request"].update(end_de="20250601"), "filing_search_before_cutoff"),
        (lambda s: s["request"].update(bgn_de="20250101"), "filing_search_window_incomplete"),
        (lambda s: s["request"].update(corp_code="99999999"), "filing_search_identity_mismatch"),
    ],
)
def test_incomplete_or_mismatched_filing_search_blocks(tmp_path, mutate, code):
    case = Case(tmp_path)
    mutate(case.receipt["filing_history"]["search"])
    blocked_by(case.run(), code)


def test_tampered_filing_page_blocks(tmp_path):
    case = Case(tmp_path)
    digest = case.receipt["filing_history"]["search"]["pages"][1]["sha256"]
    case.pages[digest] = case.pages[digest].replace(b"20250310", b"20250319")
    blocked_by(case.run(), "filing_page_tampered")


def test_same_day_family_filings_are_ambiguous_not_ordered_by_number(tmp_path):
    rows = [
        _row(CORRECTION, "20250320", "[기재정정]사업보고서 (2024.12)"),
        _row(ORIGINAL, "20250320", "사업보고서 (2024.12)"),
    ]
    case = Case(tmp_path, rows=rows)
    blocked_by(case.run(), "filing_order_ambiguous")


def test_no_timely_family_filing_blocks_instead_of_not_applicable(tmp_path):
    case = Case(tmp_path, rows=[_row(LATE, "20250801", "사업보고서 (2024.12)")], pinned=ORIGINAL)
    blocked_by(case.run(), "financial_filing_not_available_as_of")


def test_pinned_period_must_be_written_in_a_verified_pinned_quote(tmp_path):
    case = Case(tmp_path, fs_text={"fs-period": "당기 제10기 2024년 01월 01일부터"})
    blocked_by(case.run(), "financial_period_unverified")
    title_only = Case(tmp_path / "title")
    title_only.receipt["filing_history"]["pinned"]["period_evidence_source_ids"] = ["fs-consol"]
    blocked_by(title_only.run(), "financial_period_unverified")


def test_consolidation_must_stay_verified(tmp_path):
    case = Case(tmp_path)
    case.receipt["filing_history"]["pinned"]["consolidation"] = "separate"
    blocked_by(case.run(), "financial_consolidation_unverified")
    case.receipt["filing_history"]["pinned"]["consolidation"] = "consolidated"
    case.receipt["filing_history"]["pinned"]["consolidation_evidence_source_ids"] = ["sr-scope"]
    blocked_by(case.run(), "financial_evidence_not_pinned")


def test_cutoff_must_be_explicit_and_equal_the_evaluation_as_of(tmp_path):
    case = Case(tmp_path)
    case.receipt["filing_history"]["cutoff"]["granularity"] = "instant"
    blocked_by(case.run(), "filing_cutoff_invalid")
    case.receipt["filing_history"]["cutoff"] = {
        "date": "2025-07-01",
        "granularity": "date_inclusive",
    }
    blocked_by(case.run(), "filing_cutoff_mismatch")


def test_source_native_dates_normalise_deterministically():
    assert revision.normalized_dates("2024년 1월 1일 ~ 2024.12.31 및 20240630") == {
        "2024-01-01",
        "2024-12-31",
        "2024-06-30",
    }
    assert revision.normalized_dates("사업보고서 (2024.12)") == set()
    assert revision.normalized_dates("2024-02-30") == set()


# --------------------------------------------------------------------------- #
# bounded OpenDART collector (fake transport only)
# --------------------------------------------------------------------------- #


def test_collector_walks_every_page_with_history_included():
    record, pages, transport = collect()
    assert record["state"] == "complete"
    assert [page["page_no"] for page in record["pages"]] == [1, 2]
    assert all("last_reprt_at=N" in url for url in transport.urls)
    assert {row["rcept_no"] for row in record["filings"]} == {
        ORIGINAL,
        UNRELATED,
        CORRECTION,
        LATE,
    }
    assert set(pages) == {page["sha256"] for page in record["pages"]}
    assert "test-key-not-real" not in json.dumps(record)


def test_collector_marks_bound_drift_and_failures_incomplete():
    transport = FakeTransport(DEFAULT_ROWS, 1)
    client = DartClient(api_key="k", transport=transport, max_retries=0)
    record, _ = collect_filing_history(
        client, CORP, "20240101", "20250930", page_count=1, max_pages=2
    )
    assert (record["state"], record["incomplete_reason"]) == ("incomplete", "page_bound_exceeded")
    record, _, _ = collect(drift_on=2)
    assert record["incomplete_reason"] == "pagination_drift"
    dup = [DEFAULT_ROWS[0], DEFAULT_ROWS[0]]
    record, _, _ = collect(dup)
    assert record["incomplete_reason"] == "duplicate_rcept_no"

    def failing(url, headers=None, timeout=10.0):
        return 500, {}, b"upstream error"

    client = DartClient(api_key="secret-never-copied", transport=failing, max_retries=0)
    record, _ = collect_filing_history(client, CORP, "20240101", "20250930")
    assert record["state"] == "incomplete"
    assert record["incomplete_reason"] == "request_failed:DartError"
    assert "secret-never-copied" not in json.dumps(record)


def test_collector_no_data_is_a_complete_empty_search():
    record, pages, _ = collect([])
    assert (record["state"], record["filings"], len(pages)) == ("complete", [], 1)
