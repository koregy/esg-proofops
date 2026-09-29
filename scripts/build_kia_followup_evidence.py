"""Export source-bound candidates for the 2026-09-29 Kia FY2024 (DOC-034) follow-up reply.

Inputs are local, private team files; none of them is copied or committed:

* the private Kia Sustainability Report 2025 PDF the reviewer sent with the reply
  (SHA-256 ``d0d814d9...``; 134 pages; ``*.pdf`` is gitignored),
* the reply document ``B_followup_회신_20260929.md`` (only its SHA-256 is recorded),
* the expert ``reconciliation.csv`` (SHA-256 ``df4b7817...``), read and never written,
* optionally the public SR PDF (``9ce8f379...``) for a per-page text equality note.

What it exports, under a new directory below ``<repo>/.local/``:

1. Literal quote checks for the reply's locations (p2 period, p35 1,178.5, p106
   revenue share 27.0 % and classification notes, p111 KCN, p130-131 inventory scope,
   p28 C04 goal), with the same readers and refusal rules as
   ``build_kia_submission_case.py`` (pdfplumber literal / unique whitespace span, else
   B's pypdf ``whitespace-v1``). A verified quote is a *candidate* only.
2. A ``derived`` arithmetic reproduction of the p35 Scope 1 subtotals from the p131
   site rows, with and without the office rows. Under R00 §12 RQ-02 this is never
   ``present``; the p35 "생산공장" coverage vs office inclusion conflict (E04) is kept.
3. A bounded same-line search for money amounts next to "투자" across every page, and
   the absence of any currency amount on the C04 goal page. ``c3_trigger_absent`` is
   kept distinct from ``c3_policy_unapproved``.
4. Per-case follow-up candidates beside the unchanged CSV expectations, and a policy
   role record: the R00 §12 approver is the project user; the reviewer is a source
   reviewer / interpretation proposer, not an adjudicator or policy approver.

It never writes gold, approves a policy, rewrites the CSV, calls a model, the network
or AWS, or resolves the overall C1 entity set.

Usage::

    PYTHONUTF8=1 .venv/Scripts/python.exe scripts/build_kia_followup_evidence.py \
        --private-sr-pdf "<ROOT>/esg-proofops/2025_기아_지속가능경영보고서.pdf" \
        --reply "<ROOT>/esg-proofops/B_followup_회신_20260929.md" \
        --reconciliation-csv "<ROOT>/esg-proofops/reconciliation.csv" \
        --public-sr-pdf .local/submission-20260929/kia-real/sources/kia-sr-2025-kr.pdf \
        --out .local/submission-20260929/kia-followup-evidence
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R00 = ROOT / "docs/R00_DOMAIN_DECISIONS.md"

PRIVATE_SR_SHA256 = "d0d814d98c4aeedbbdb2bf8631b8981ae5cde94dec32aa32c57510420274da1f"
PUBLIC_SR_SHA256 = "9ce8f379b0e858efc2cd1940c4cd3f4f56d67d027e4897afe345d10856aa4b0f"
CSV_SHA256 = "df4b7817184b3a5fcfcce948b3514b70335fbdd48c01172ea4c6d89869723017"
PAGE_COUNT = 134
KIND = "source_bound_followup_candidates_not_confirmed_not_gold"

# (check_id, physical page, literal quote). Quotes are the reply's locations, cut at
# the smallest literal the reply relies on; the reply's full p2 sentence is also tried
# so a reader line break inside it stays visible instead of being squeezed away.
CHECKS: tuple[tuple[str, int, str], ...] = (
    ("P2-PERIOD", 2, "2024년 1월 1일부터 2024년 12월 31일까지"),
    ("P2-EXCEPTION", 2, "성과의 경우 2025년 상반기까지 발생한 내용을 포함하고 있습니다."),
    (
        "P2-REPLY-FULL-SENTENCE",
        2,
        "본 보고서는 2024년 1월 1일부터 2024년 12월 31일까지의 경제·환경·사회·지배구조 측면의 "
        "성과와 활동을 담고 있으며, 일부 중대한 성과의 경우 2025년 상반기까지 발생한 내용을 "
        "포함하고 있습니다.",
    ),
    ("P2-REPORT-SCOPE", 2, "본 보고서의 보고 범위는 국내외 전 사업장의 ESG 경영 활동과"),
    (
        "P28-C04-GOAL",
        28,
        "기아는 2030년까지 에너지 효율화를 통해 ‘2019년 국내외 공장 Scope 1 & 2 배출량의 "
        "10% 감축’이라는 중장기 목표를 수립하고, 공장별 추진전략을 구체화하고 있습니다.",
    ),
    ("P35-COVERAGE", 35, "데이터 커버리지 : 국내+해외 생산공장"),
    # "구분 단위 2022 2023 2024" alone occurs twice on p35; bind it to the Scope 1 & 2 table.
    ("P35-HEADER", 35, "데이터 커버리지 : 국내+해외 생산공장 구분 단위 2022 2023 2024"),
    ("P35-TOTAL", 35, "총 배출량(Scope 1 & 2)1 천tCO₂eq 1,166.9 1,130.9 1,178.5"),
    ("P35-SCOPE1-DOMESTIC", 35, "국내 천tCO₂eq 266.0 244.6 238.6"),
    ("P35-SCOPE1-OVERSEAS", 35, "해외 천tCO₂eq 113.9 109.3 147.7"),
    ("P106-GHG-COVERAGE", 106, "데이터 커버리지 : 국내+해외 생산공장"),
    ("P106-TOTAL", 106, "총 배출량(Scope1 & 2)1 천tCOeq 1,166.9 1,130.9 1,178.5"),
    ("P106-REVENUE-BASIS", 106, "내부관리회계 기준"),
    ("P106-REVENUE-SHARE", 106, "친환경차 매출 비중 % 23.5 26.3 27.0"),
    (
        "P106-REVENUE-YEARS",
        106,
        "친환경차 매출 비중 지표명 단위 2022 2023 2024 친환경차 매출 비중 % 23.5 26.3 27.0",
    ),
    (
        "P106-CLASSIFICATION",
        106,
        "* ‘친환경차’는 EV(전기차), HEV(하이브리드), PHEV(플러그인 하이브리드)를 포함하며 "
        "‘전동화’는 EV(전기차)만을 포함",
    ),
    ("P106-SALES-BASIS", 106, "1. 도매 기준"),
    ("P106-SALES-TOTAL", 106, "총 합계1 대 492,593 598,846 644,685"),
    ("P106-HEV", 106, "HEV 대 254,327 311,671 382,764"),
    ("P106-PHEV", 106, "PHEV 대 81,766 88,861 67,797"),
    ("P106-EV", 106, "EV 대 156,500 198,314 194,124"),
    ("P111-KCN", 111, "KCN(기아 중국) 명 4,202 3,908 3,811"),
    ("P111-COVERAGE", 111, "데이터 커버리지 : 해외"),
    (
        "P130-INVENTORY",
        130,
        "로이드인증원(LRQA)은 기아(주)(이하 기아)로부터 2024 년도 온실가스 인벤토리",
    ),
    (
        "P130-OPERATIONAL-CONTROL",
        130,
        "기아의 주요 활동은 자동차 제조이며 "
        "온실가스 배출은 운영통제접근법을 사용하여 통합되었습니다.",
    ),
    ("P131-TITLE", 131, "2024년도 기아 Scope 1 및 Scope 2 온실가스 인벤토리 요약"),
    ("P131-CHINA", 131, "생산법인 China 43,743 119,623 97,284 1,726"),
    ("P131-OFFICE-DOMESTIC-NOTE", 131, "2. 국내 본사 및 판매지점 및 서비스센터"),
    ("P131-OFFICE-OVERSEAS-NOTE", 131, "3. 해외 판매법인"),
)

# p131 site rows: (label as printed, domestic?, office?). Scope 1 is the first number.
P131_SITES: tuple[tuple[str, bool, bool], ...] = (
    ("광명", True, False),
    ("광주", True, False),
    ("화성", True, False),
    ("국내사무소2", True, True),
    ("United States", False, False),
    ("Slovakia", False, False),
    ("China", False, False),
    ("Mexico", False, False),
    ("India", False, False),
    ("해외사무소3", False, True),
)
P35_SUBTOTALS = {"domestic": Decimal("238.6"), "overseas": Decimal("147.7")}
# Pages the reply reported for its money + "투자" row search.
REPLY_MONEY_PAGES = (49, 116)

MONEY_UNIT = re.compile(r"(?:억|조)\s*원")
CURRENCY = re.compile(
    r"\d[\d,.]*\s*(?:억|조|만|백만|천)?\s*원(?![가-힣])"  # amount + 원, not 원칙 etc.
    r"|(?:억|조)\s*원|KRW|USD|\$"
)


class InputRejected(ValueError):
    """An input does not match its pinned identity or the output is unsafe; nothing is built."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_module():
    """Reuse the Kia case builder's readers and anchoring rules instead of copying them."""
    path = ROOT / "scripts/build_kia_submission_case.py"
    spec = importlib.util.spec_from_file_location("build_kia_submission_case", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Pure checks over page texts (1-based physical pages)
# --------------------------------------------------------------------------- #


def run_checks(anchor, pages, pypdf_pages, checks=CHECKS) -> list[dict]:
    results = []
    for check_id, page, quote in checks:
        got = anchor(pages, pypdf_pages, page, quote)
        results.append(
            dict(
                check_id=check_id,
                physical_page=page,
                quote=quote,
                verified=bool(got.get("verified")),
                method=got.get("method"),
                reader=got.get("reader"),
                char_start=got.get("char_start"),
                char_end=got.get("char_end"),
                reason=got.get("reason"),
                candidate_state="present_candidate" if got.get("verified") else "unknown",
            )
        )
    return results


def _thousand(value: int) -> Decimal:
    return (Decimal(value) / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def p131_scope1_rows(page_text: str) -> dict:
    """First integer after each literal site label, only when the label's row is unique."""
    rows, refused = {}, {}
    for label, _domestic, _office in P131_SITES:
        pattern = re.compile(rf"(?:^|\s){re.escape(label)} (\d{{1,3}}(?:,\d{{3}})+) ")
        matches = list(pattern.finditer(page_text))
        if len(matches) == 1:
            rows[label] = int(matches[0].group(1).replace(",", ""))
        else:
            refused[label] = "row_missing" if not matches else "row_ambiguous"
    return dict(rows=rows, refused=refused)


def reproduce_p35_subtotals(page_text: str) -> dict:
    """Derived Scope 1 subtotals from p131; recorded as ``derived``, never ``present``."""
    parsed = p131_scope1_rows(page_text)
    if parsed["refused"]:
        return dict(state="unknown", reason="p131_rows_unreadable", refused=parsed["refused"])
    out = {}
    for region, domestic in (("domestic", True), ("overseas", False)):
        sites = [s for s in P131_SITES if s[1] is domestic]
        with_offices = sum(parsed["rows"][s[0]] for s in sites)
        without = sum(parsed["rows"][s[0]] for s in sites if not s[2])
        out[region] = dict(
            p35_value_kt=str(P35_SUBTOTALS[region]),
            with_offices_t=with_offices,
            with_offices_kt=str(_thousand(with_offices)),
            with_offices_equal=_thousand(with_offices) == P35_SUBTOTALS[region],
            without_offices_t=without,
            without_offices_kt=str(_thousand(without)),
            without_offices_equal=_thousand(without) == P35_SUBTOTALS[region],
        )
    offices_needed = all(
        v["with_offices_equal"] and not v["without_offices_equal"] for v in out.values()
    )
    return dict(
        state="derived",
        rows_t=parsed["rows"],
        regions=out,
        offices_required_to_reproduce_p35=offices_needed,
        boundary_conflict_e04="kept"
        if offices_needed
        else "not_asserted_reproduction_inconclusive",
        scope="Scope 1 domestic/overseas subtotals only; the 1,178.5 total is not reconciled",
        rounding="sum of p131 tCO2e / 1000, ROUND_HALF_UP to 0.1 thousand tCO2eq",
        note="RQ-02: derived arithmetic stays separate and is never promoted to present",
    )


def money_investment_search(pages: list[str]) -> dict:
    """Bounded full-document search: lines with '투자' and an 억/조 원 amount unit."""
    lines: list[dict] = []
    line_pages: set[int] = set()
    page_cooccurrence: list[int] = []
    for number, text in enumerate(pages, 1):
        for line in text.splitlines():
            if "투자" in line and MONEY_UNIT.search(line):
                lines.append(dict(physical_page=number, line=line.strip()))
                line_pages.add(number)
        if "투자" in text and MONEY_UNIT.search(text):
            page_cooccurrence.append(number)
    without_text = [n for n, text in enumerate(pages, 1) if not text.strip()]
    same_line_pages = sorted(line_pages)
    return dict(
        pages_searched=len(pages),
        pages_with_text=len(pages) - len(without_text),
        pages_without_text=without_text,
        search_complete=not without_text,
        reply_reported_pages=list(REPLY_MONEY_PAGES),
        pages_beyond_reply=[n for n in same_line_pages if n not in REPLY_MONEY_PAGES],
        reply_pages_not_found=[n for n in REPLY_MONEY_PAGES if n not in same_line_pages],
        same_line_pages=same_line_pages,
        same_line_matches=lines,
        same_page_cooccurrence=page_cooccurrence,
        reader="pdfplumber extract_text; no OCR, no table geometry",
    )


def currency_on_page(page_text: str) -> list[str]:
    return [m.group(0) for m in CURRENCY.finditer(page_text)]


# --------------------------------------------------------------------------- #
# Case candidates beside unchanged CSV expectations
# --------------------------------------------------------------------------- #


def _ok(checks: dict, *ids: str) -> bool:
    return all(checks[i]["verified"] for i in ids)


def case_candidates(checks: list[dict], reproduction: dict, money: dict, c04_currency) -> dict:
    by_id = {c["check_id"]: c for c in checks}
    period_ok = _ok(by_id, "P2-PERIOD", "P35-HEADER", "P35-TOTAL", "P130-INVENTORY", "P131-TITLE")
    kcn_ok = _ok(by_id, "P111-KCN", "P131-CHINA", "P130-OPERATIONAL-CONTROL")
    c4_ok = _ok(
        by_id, "P106-CLASSIFICATION", "P106-SALES-BASIS", "P106-SALES-TOTAL", "P106-REVENUE-SHARE"
    )
    common = dict(expectation_change="none", gold_written=False, policy_approved=False)
    return {
        "RC-DOC034-C01-C2-20250313001390": dict(
            common,
            item="C2",
            candidate="period_match_candidate" if period_ok else "unknown",
            evidence=["P2-PERIOD", "P35-HEADER", "P35-TOTAL", "P130-INVENTORY", "P131-TITLE"],
            p2_exception_applied=False,
            p2_exception_literal_verified=by_id["P2-EXCEPTION"]["verified"],
            p2_reply_full_sentence_verified=by_id["P2-REPLY-FULL-SENTENCE"]["verified"],
            open_interpretation="p130/p131 say '2024년도' but no inventory start/end date literal",
        ),
        "RC-DOC034-C01-C1-20250313001390": dict(
            common,
            item="C1",
            candidate="unresolved",
            overall_c1_resolved=False,
            reason="financial legal-entity set vs SR site/office set; no source-verified "
            "full mapping (REC-001) and the reviewer did not review the subsidiaries annex",
            boundary_reproduction=reproduction.get("offices_required_to_reproduce_p35"),
            boundary_conflict_e04=reproduction.get("boundary_conflict_e04", "kept"),
            evidence=["P2-REPORT-SCOPE", "P35-COVERAGE", "P106-GHG-COVERAGE", "P131-TITLE"],
        ),
        "RC-DOC034-C01-C1KCN-20250313001390": dict(
            common,
            item="C1",
            candidate="kcn_identity_and_explained_boundary_candidate" if kcn_ok else "unknown",
            scope="KCN only; the overall C1 stays unresolved",
            overall_c1_resolved=False,
            evidence=["P111-KCN", "P111-COVERAGE", "P131-CHINA", "P130-OPERATIONAL-CONTROL"],
            financial_side="XBRL note chars:78981:79520 (not re-read here)",
        ),
        "RC-DOC034-C04-C3-20250313001390": dict(
            common,
            item="C3",
            candidate=(
                "trigger_absent_candidate"
                if by_id["P28-C04-GOAL"]["verified"] and not c04_currency
                else "unknown"
            ),
            reason_code_candidate="c3_trigger_absent",
            distinct_from="c3_policy_unapproved",
            c04_page_currency_tokens=c04_currency,
            other_money_investment_lines=money["same_line_pages"],
            money_search_beyond_reply=money.get("pages_beyond_reply", []),
            money_search_complete=money.get("search_complete"),
            other_lines_related_to_c04=None,
            note="p49/p116 relation to C04 is the reviewer's reading, not a machine judgement; "
            "REC-004 threshold stays null",
        ),
        "DOC034-C03-C4-NEW": dict(
            common,
            item="C4",
            candidate="classification_basis_candidate" if c4_ok else "unknown",
            csv_row_written=False,
            evidence=[
                "P106-CLASSIFICATION",
                "P106-SALES-BASIS",
                "P106-SALES-TOTAL",
                "P106-HEV",
                "P106-PHEV",
                "P106-EV",
                "P106-REVENUE-SHARE",
                "P106-REVENUE-BASIS",
            ],
            open_question="C03 is a unit-sales claim; the p106 revenue share (27.0 %, internal "
            "management accounting) is closer to the C4 target and needs a new claim registration",
        ),
        "DOC034-REVENUE-2024-C4-CANDIDATE": dict(
            common,
            item="C4",
            candidate=(
                "revenue_share_claim_candidate"
                if _ok(by_id, "P106-REVENUE-SHARE", "P106-REVENUE-YEARS", "P106-REVENUE-BASIS")
                else "unknown"
            ),
            candidate_id="DOC034-REVENUE-2024-C4-CANDIDATE",
            source_sha256=PRIVATE_SR_SHA256,
            physical_page=106,
            proposed_track="performance",
            proposed_metric="친환경차 매출 비중",
            proposed_value="27.0",
            proposed_unit="%",
            proposed_year=2024,
            calculation_basis="내부관리회계 기준",
            evidence=["P106-REVENUE-SHARE", "P106-REVENUE-YEARS", "P106-REVENUE-BASIS"],
            product_claim_registered=False,
            remaining_gate=(
                "source geometry and accepted claim binding required for product registration"
            ),
            csv_row_written=False,
            classification_equivalence_to_regulatory_green_revenue=None,
        ),
    }


def policy_role_record(r00_text: str | None) -> dict:
    section = "## 12. 사용자 결정"
    found = bool(r00_text and section in r00_text)
    return dict(
        r00_section_12_present=found,
        approver_role="project user (프로젝트 책임자) per R00 §12",
        reviewer_role=(
            "source reviewer / interpretation proposer; not adjudicator, not policy approver"
        ),
        reviewer_participated_in_rec_adoption=False,
        b_f02_named_approver="기준·데이터 담당자; recorded as a different role from the reviewer",
        reviewer_requests=[
            "share R00 §12 text with the reviewer",
            "record decider, role, time and policy version of the 2026-09-28 adoption",
        ],
        expectation_revision="held: CUR/ASM expectations unchanged until the reviewer returns "
        "a change table against §12",
        adjudicator=None,
    )


# --------------------------------------------------------------------------- #
# IO
# --------------------------------------------------------------------------- #


def _safe_out(out: Path) -> Path:
    out = out.resolve()
    local = (ROOT / ".local").resolve()
    if out.exists():
        raise InputRejected(f"output directory already exists, refusing to overwrite: {out}")
    if ROOT.resolve() in out.parents and local not in out.parents:
        raise InputRejected("output inside the repository must be below the ignored .local/")
    return out


def _read_pinned(path: Path, expected: str, label: str) -> bytes:
    data = path.read_bytes()
    if sha256_bytes(data) != expected:
        raise InputRejected(f"{label} SHA-256 differs from the pinned identity")
    return data


def build(args) -> dict:
    out = _safe_out(Path(args.out))
    pdf = _read_pinned(Path(args.private_sr_pdf), PRIVATE_SR_SHA256, "private SR PDF")
    csv_path = Path(args.reconciliation_csv)
    csv_before = _read_pinned(csv_path, CSV_SHA256, "reconciliation.csv")
    reply_sha = sha256_bytes(Path(args.reply).read_bytes())

    case = _case_module()
    pages = case.sr_page_texts(pdf)
    if len(pages) != PAGE_COUNT:
        raise InputRejected("private SR page count differs from the pinned count")
    pypdf_pages = case.PypdfPages(pdf)

    checks = run_checks(case.anchor_quote, pages, pypdf_pages)
    reproduction = reproduce_p35_subtotals(pages[130])
    money = money_investment_search(pages)
    c04_currency = currency_on_page(pages[27])
    candidates = case_candidates(checks, reproduction, money, c04_currency)

    public = None
    if args.public_sr_pdf:
        pub = _read_pinned(Path(args.public_sr_pdf), PUBLIC_SR_SHA256, "public SR PDF")
        pub_pages = case.sr_page_texts(pub)
        public = {
            str(n): pub_pages[n - 1] == pages[n - 1] if n <= len(pub_pages) else None
            for n in sorted({c[1] for c in CHECKS})
        }

    csv_rows = list(csv.DictReader(io.StringIO(csv_before.decode("utf-8-sig"))))
    csv_after = csv_path.read_bytes()
    summary = dict(
        kind=KIND,
        private_sr_sha256=PRIVATE_SR_SHA256,
        pages=len(pages),
        reply_sha256=reply_sha,
        checks_total=len(checks),
        checks_verified=sum(c["verified"] for c in checks),
        refused={c["check_id"]: c["reason"] for c in checks if not c["verified"]},
        public_page_text_equal=public,
        reproduction=dict(
            state=reproduction["state"],
            offices_required_to_reproduce_p35=reproduction.get("offices_required_to_reproduce_p35"),
        ),
        money_investment_same_line_pages=money["same_line_pages"],
        money_search_beyond_reply=money["pages_beyond_reply"],
        pages_without_text=money["pages_without_text"],
        c04_page_currency_tokens=c04_currency,
        candidates={k: v["candidate"] for k, v in candidates.items()},
        overall_c1_resolved=False,
        csv=dict(
            sha256=CSV_SHA256,
            rows=len(csv_rows),
            unchanged=sha256_bytes(csv_after) == CSV_SHA256,
            expectations={
                r["case_id"]: dict(
                    execution_state=r["expected_execution_state"],
                    status=r["expected_status"] or None,
                    reason=r["reason_code"] or None,
                )
                for r in csv_rows
            },
        ),
        not_claimed=[
            "no gold label, policy approval, adjudication or CSV edit",
            "no confirmed tag, binding, native geometry or accepted revision",
            "no model, network, AWS or database call",
            "derived arithmetic is not present evidence; E04 conflict is not removed",
            "overall C1 entity set stays unresolved",
        ],
    )
    if not summary["csv"]["unchanged"]:
        raise InputRejected("reconciliation.csv changed during the run")

    r00_text = R00.read_text(encoding="utf-8") if R00.exists() else None
    files = {
        "summary.json": summary,
        "source-checks.json": checks,
        "p131-reproduction.json": reproduction,
        "money-investment-search.json": money,
        "case-candidates.json": candidates,
        "policy-roles.json": policy_role_record(r00_text),
    }
    out.mkdir(parents=True)
    manifest = {}
    for name, value in files.items():
        raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        (out / name).write_bytes(raw)
        manifest[name] = sha256_bytes(raw)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--private-sr-pdf", required=True)
    parser.add_argument("--reply", required=True)
    parser.add_argument("--reconciliation-csv", required=True)
    parser.add_argument("--public-sr-pdf")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        summary = build(args)
    except InputRejected as exc:
        print(json.dumps(dict(execution_state="blocked", reason=str(exc)), ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
