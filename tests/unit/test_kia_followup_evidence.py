"""Offline checks for scripts/build_kia_followup_evidence.py (Kia DOC-034 follow-up reply).

Unit checks use synthetic page text only. The end-to-end check runs on the private
reviewer PDF and is skipped where that file is absent; the PDF is never committed.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import os
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build_kia_followup_evidence.py"


def _module():
    spec = importlib.util.spec_from_file_location("build_kia_followup_evidence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P131 = "\n".join(
    [
        "광명 50,728 85,505 85,505 2,771",
        "생산법인 광주 62,025 83,070 83,070 2,946",
        "화성 122,686 280,639 280,639 8,320",
        "국내사무소2 3,159 22,003 22,003 521 출장 - 항공, 철도, 버스, 개인차량 23,518",
        "United States 28,534 53,391 53,391 1,057",
        "Slovakia 27,724 32,631 3,394 1,077",
        "생산법인 China 43,743 119,623 97,284 1,726",
        "Mexico 19,567 51,282 44,622 795",
        "India 11,954 115,229 115,229 782",
        "해외사무소3 16,179 7,121 7,121 117",
    ]
)


def _all_verified(module, **overrides):
    checks = [
        dict(check_id=c[0], verified=overrides.get(c[0], True), reason=None) for c in module.CHECKS
    ]
    return checks


def test_reproduction_needs_offices_and_stays_derived():
    m = _module()
    got = m.reproduce_p35_subtotals(P131)
    assert got["state"] == "derived"
    assert got["regions"]["domestic"]["with_offices_kt"] == "238.6"
    assert got["regions"]["domestic"]["without_offices_kt"] == "235.4"
    assert got["regions"]["overseas"]["with_offices_kt"] == "147.7"
    assert got["regions"]["overseas"]["without_offices_kt"] == "131.5"
    assert got["offices_required_to_reproduce_p35"] is True
    assert got["boundary_conflict_e04"] == "kept"
    assert "total is not reconciled" in got["scope"]


def test_reproduction_refuses_missing_or_ambiguous_rows():
    m = _module()
    missing = m.reproduce_p35_subtotals(P131.replace("India 11,954", "India -"))
    assert missing["state"] == "unknown"
    assert missing["refused"] == {"India": "row_missing"}
    doubled = m.reproduce_p35_subtotals(P131 + "\n화성 1,000 2 3 4")
    assert doubled["state"] == "unknown"
    assert doubled["refused"] == {"화성": "row_ambiguous"}


def test_money_search_is_same_line_and_reports_unreadable_pages():
    m = _module()
    pages = [
        "환경 농축RTO 설치(투자 220억 원) 및 운영",
        "",
        "투자 계획\n매출 3조 원",
        "국내 전기차 투자 보증 지원 프로그램2 억 원 - - 5,000",
    ]
    got = m.money_investment_search(pages)
    assert got["same_line_pages"] == [1, 4]
    assert got["same_page_cooccurrence"] == [1, 3, 4]
    assert got["pages_without_text"] == [2]
    assert got["search_complete"] is False
    assert got["pages_searched"] == 4
    assert got["reply_pages_not_found"] == [49, 116]


def test_currency_on_goal_page_ignores_words_ending_in_won():
    m = _module()
    assert m.currency_on_page("공장별 지원 원칙과 10% 감축 목표") == []
    assert m.currency_on_page("투자 220억 원") == ["220억 원"]
    assert m.currency_on_page("총 5,000 원") == ["5,000 원"]


def test_candidates_never_resolve_overall_c1_or_write_gold():
    m = _module()
    checks = _all_verified(m)
    rep = m.reproduce_p35_subtotals(P131)
    got = m.case_candidates(checks, rep, dict(same_line_pages=[49, 116]), [])
    for value in got.values():
        assert value["gold_written"] is False
        assert value["policy_approved"] is False
        assert value["expectation_change"] == "none"
    assert got["RC-DOC034-C01-C1-20250313001390"]["candidate"] == "unresolved"
    assert got["RC-DOC034-C01-C1-20250313001390"]["overall_c1_resolved"] is False
    kcn = got["RC-DOC034-C01-C1KCN-20250313001390"]
    assert kcn["candidate"] == "kcn_identity_and_explained_boundary_candidate"
    assert kcn["overall_c1_resolved"] is False
    assert got["RC-DOC034-C01-C2-20250313001390"]["p2_exception_applied"] is False


def test_c04_trigger_absence_is_distinct_from_unapproved_and_needs_no_money():
    m = _module()
    rep = m.reproduce_p35_subtotals(P131)
    money = dict(same_line_pages=[49, 116])
    got = m.case_candidates(_all_verified(m), rep, money, [])["RC-DOC034-C04-C3-20250313001390"]
    assert got["candidate"] == "trigger_absent_candidate"
    assert got["reason_code_candidate"] == "c3_trigger_absent"
    assert got["distinct_from"] == "c3_policy_unapproved"
    assert got["other_lines_related_to_c04"] is None
    with_money = m.case_candidates(_all_verified(m), rep, money, ["220억 원"])
    assert with_money["RC-DOC034-C04-C3-20250313001390"]["candidate"] == "unknown"


def test_revenue_candidate_requires_its_own_year_header_and_does_not_replace_sales():
    m = _module()
    checks = _all_verified(m)
    key = "DOC034-REVENUE-2024-C4-CANDIDATE"
    args = (m.reproduce_p35_subtotals(P131), dict(same_line_pages=[]), [])
    cases = m.case_candidates(checks, *args)
    assert cases[key]["candidate"] == "revenue_share_claim_candidate"
    assert cases[key]["source_sha256"] == m.PRIVATE_SR_SHA256
    assert cases[key]["product_claim_registered"] is False
    assert cases[key]["classification_equivalence_to_regulatory_green_revenue"] is None
    assert "DOC034-C03-C4-NEW" in cases
    for row in checks:
        if row["check_id"] == "P106-REVENUE-YEARS":
            row["verified"] = False
    assert m.case_candidates(checks, *args)[key]["candidate"] == "unknown"


def test_unverified_quote_turns_candidate_unknown_not_absent():
    m = _module()
    rep = m.reproduce_p35_subtotals(P131)
    checks = _all_verified(m, **{"P111-KCN": False, "P2-PERIOD": False})
    got = m.case_candidates(checks, rep, dict(same_line_pages=[]), [])
    assert got["RC-DOC034-C01-C1KCN-20250313001390"]["candidate"] == "unknown"
    assert got["RC-DOC034-C01-C2-20250313001390"]["candidate"] == "unknown"


def test_run_checks_keeps_refusal_reason_and_unknown_state():
    m = _module()

    def anchor(pages, _pypdf, page, quote):
        if quote in pages[page - 1]:
            return dict(verified=True, method="literal", char_start=0, char_end=len(quote))
        return dict(verified=False, reason="quote_not_on_page")

    got = m.run_checks(anchor, ["abc", "xyz"], None, (("A", 1, "abc"), ("B", 2, "abc")))
    assert got[0]["candidate_state"] == "present_candidate"
    assert got[1]["candidate_state"] == "unknown"
    assert got[1]["reason"] == "quote_not_on_page"


def test_policy_roles_separate_reviewer_from_approver():
    m = _module()
    got = m.policy_role_record("## 12. 사용자 결정 — ...")
    assert got["r00_section_12_present"] is True
    assert got["reviewer_participated_in_rec_adoption"] is False
    assert got["adjudicator"] is None
    assert "not policy approver" in got["reviewer_role"]
    assert m.policy_role_record(None)["r00_section_12_present"] is False


def test_output_guard_refuses_tracked_paths_and_existing_dirs(tmp_path):
    m = _module()
    with pytest.raises(m.InputRejected):
        m._safe_out(ROOT / "docs/kia-followup")
    with pytest.raises(m.InputRejected):
        m._safe_out(tmp_path)
    assert m._safe_out(tmp_path / "new") == (tmp_path / "new").resolve()


def test_pinned_identity_mismatch_blocks_before_output(tmp_path):
    m = _module()
    fake = tmp_path / "x.pdf"
    fake.write_bytes(b"%PDF-1.4 not the private report")
    reply = tmp_path / "reply.md"
    reply.write_text("reply", encoding="utf-8")
    args = Namespace(
        private_sr_pdf=str(fake),
        reply=str(reply),
        reconciliation_csv=str(fake),
        public_sr_pdf=None,
        out=str(tmp_path / "out"),
    )
    with pytest.raises(m.InputRejected, match="private SR PDF"):
        m.build(args)
    assert not (tmp_path / "out").exists()


PRIVATE_DIR = Path(
    os.environ.get("KIA_FOLLOWUP_DIR", "C:/Users/bigch/Desktop/korea x aws/project/esg-proofops")
)
PRIVATE_PDF = PRIVATE_DIR / "2025_기아_지속가능경영보고서.pdf"


@pytest.mark.skipif(not PRIVATE_PDF.exists(), reason="private Kia follow-up PDF not present")
def test_private_inputs_export_candidates_and_leave_csv_unchanged(tmp_path):
    m = _module()
    csv_path = PRIVATE_DIR / "reconciliation.csv"
    before = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    args = Namespace(
        private_sr_pdf=str(PRIVATE_PDF),
        reply=str(PRIVATE_DIR / "B_followup_회신_20260929.md"),
        reconciliation_csv=str(csv_path),
        public_sr_pdf=None,
        out=str(tmp_path / "out"),
    )
    summary = m.build(args)
    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == before
    assert summary["csv"]["unchanged"] is True
    assert summary["overall_c1_resolved"] is False
    assert summary["reproduction"]["offices_required_to_reproduce_p35"] is True
    assert summary["c04_page_currency_tokens"] == []
    # The reply reported p49/p116 only; a literal same-line search also finds the
    # education-investment budget rows, and p134 has no text layer.
    assert summary["money_investment_same_line_pages"] == [49, 62, 113, 116]
    assert summary["money_search_beyond_reply"] == [62, 113]
    assert summary["pages_without_text"] == [134]
    assert set(summary["refused"]) == {"P2-REPLY-FULL-SENTENCE"}
    assert summary["candidates"]["RC-DOC034-C01-C2-20250313001390"] == "period_match_candidate"
    assert summary["candidates"]["RC-DOC034-C04-C3-20250313001390"] == "trigger_absent_candidate"
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert len(rows) == summary["csv"]["rows"] == 8
    assert not any(p.suffix == ".pdf" for p in (tmp_path / "out").iterdir())
