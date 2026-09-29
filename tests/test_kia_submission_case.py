"""Offline checks for scripts/build_kia_submission_case.py (real Kia submission case).

The unit checks use only the committed public fixture. The end-to-end check runs the
builder on the real local inputs and is skipped where those files are absent.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_kia_submission_case.py"
FIXTURE = ROOT / "fixtures/submission-kia"


def _module():
    spec = importlib.util.spec_from_file_location("build_kia_submission_case", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kia = _module()


def _scope():
    return json.loads((FIXTURE / "dart-consolidation-scope.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# quote re-anchoring: literal, unique whitespace span, or refusal
# --------------------------------------------------------------------------- #


def test_reanchor_accepts_single_literal_and_refuses_multiple_literals():
    page = "총 배출량 1,178.5 천tCO₂eq\n데이터 커버리지 : 국내+해외 생산공장"
    hit = kia.reanchor(page, "1,178.5")
    assert hit["verified"] and hit["method"] == "literal"
    assert page[hit["char_start"] : hit["char_end"]] == "1,178.5"
    assert kia.reanchor("27% ... 27%", "27%") == {"verified": False, "reason": "literal_ambiguous"}


def test_reanchor_whitespace_span_must_be_unique_and_is_returned_literally():
    page = "기아는 제품, 사업장, 공급망의 탄소배출량을 통합\n관리하는 시스템"
    hit = kia.reanchor(page, "탄소배출량을 통합 관리하는")
    assert hit["verified"] and hit["method"] == "whitespace_reanchored"
    assert hit["quote"] == "탄소배출량을 통합\n관리하는"
    assert kia.reanchor("a  b\nx a\tb", "a b")["reason"] == "whitespace_match_ambiguous"
    assert (
        kia.reanchor("경제·\n환경", "경제·환경")["reason"] == "only_matches_with_whitespace_removed"
    )
    assert kia.reanchor("abc", "xyz")["reason"] == "quote_not_on_page"
    assert kia.reanchor("abc", "  ")["reason"] == "empty_quote"


def test_anchor_quote_falls_back_to_b_pypdf_rule_but_never_past_ambiguity():
    plumber = ["기아는 이사회 산하\n조직도 지속가능경영위원회를 중심으로"]  # interleaved columns
    pypdf = ["기아는 이사회 산하 지속가능경영위원회를\n중심으로 관리"]
    hit = kia.anchor_quote(plumber, pypdf, 1, "기아는 이사회 산하 지속가능경영위원회를 중심으로")
    assert hit["verified"] and hit["method"] == "pypdf_whitespace_v1" and hit["reader"] == "pypdf"
    assert hit["pdfplumber_reason"] == "quote_not_on_page"
    ambiguous = kia.anchor_quote(["% %"], ["%"], 1, "%")
    assert ambiguous == {"verified": False, "reason": "literal_ambiguous"}


# --------------------------------------------------------------------------- #
# DART consolidation scope
# --------------------------------------------------------------------------- #


def test_financial_entities_are_literal_codes_of_the_verified_annex():
    scope = _scope()
    entities = kia.financial_entities(scope["annex_source"]["quote"], scope["annex_rows"])
    ids = [e["entity_id"] for e in entities]
    assert len(ids) == 25 and len(set(ids)) == 25
    assert {"KIA-SUB:KaGA", "KIA-SUB:KaSK", "KIA-SUB:KMX", "KIA-SUB:KIN", "KIA-PARENT"} <= set(ids)
    # The joint venture KCN is not a consolidated subsidiary row.
    assert "KIA-SUB:KCN" not in ids
    for entity in entities:
        assert entity["source_label"] in scope["annex_source"]["quote"]


def test_financial_entities_refuse_a_row_without_one_literal_code():
    scope = _scope()
    rows = [["Kia Unnamed Subsidiary"] + scope["annex_rows"][0][1:]]
    with pytest.raises(kia.InputRejected):
        kia.financial_entities(scope["annex_source"]["quote"], rows)
    with pytest.raises(kia.InputRejected):
        kia.financial_entities("no codes here", scope["annex_rows"][:1])


def test_summary_total_row_year_end_count_matches_the_detail_rows():
    scope = _scope()
    assert kia.summary_count(scope["summary_total_row"]["quote"]) == len(scope["annex_rows"]) == 24
    assert kia.summary_count("td\t합계\ntd\t24") is None


# --------------------------------------------------------------------------- #
# candidates stay candidates; projection never builds a packet
# --------------------------------------------------------------------------- #


def _record(boundary_quote, *, state="present"):
    check = dict(verified=True, quote=boundary_quote, method="literal")
    return dict(
        a_claim_id="X",
        claim_quote_check=dict(verified=True),
        elements=[
            dict(
                fact="org_boundary",
                candidate_state="present_candidate" if state == "present" else "unknown",
                quote_checks=[check],
            ),
            dict(fact="baseline_value", candidate_state="unknown", quote_checks=[]),
        ],
    )


def test_gate_projection_blocks_c1_when_boundary_names_no_financial_entity():
    entities = kia.financial_entities(_scope()["annex_source"]["quote"], _scope()["annex_rows"])
    projection = kia._gate_projection(_record("국내외 공장"), entities)
    assert projection["C1"]["first_gate"] == "c1_boundary_quote_names_no_financial_entity"
    assert {v["execution_state"] for v in projection.values()} == {"blocked"}
    assert all(v["run_gate"].startswith("run_not_published") for v in projection.values())
    assert projection["C3"]["first_gate"] == "no_c3_trigger_candidate"


def test_gate_projection_names_literal_entity_codes_only():
    entities = kia.financial_entities(_scope()["annex_source"]["quote"], _scope()["annex_rows"])
    coded = kia._gate_projection(_record("생산법인 (KaGA) 기준"), entities)["C1"]["first_gate"]
    assert coded == "c1_entity_review_possible:(KaGA)"
    # A country or plant label is never mapped to an entity.
    country = kia._gate_projection(_record("United States 28,534"), entities)["C1"]
    assert country["first_gate"] == "c1_boundary_quote_names_no_financial_entity"
    unknown = kia._gate_projection(_record("(KaGA)", state="unknown"), entities)["C1"]
    assert unknown["first_gate"] == "no_c1_trigger_candidate"


# --------------------------------------------------------------------------- #
# end to end on the real local inputs
# --------------------------------------------------------------------------- #

PROJECT = Path(
    os.environ.get("KIA_CASE_PROJECT_ROOT", r"C:\Users\bigch\Desktop\korea x aws\project")
)
SOURCES = ROOT / ".local/submission-20260929/kia-real/sources"
REAL = dict(
    sr_pdf=SOURCES / "kia-sr-2025-kr.pdf",
    a_snapshot=SOURCES / "a-submission-kia-2025.json",
    dart_financial_dir=PROJECT / "esg-proofops/developer-b-kia-financial-20260922",
    dart_annex_dir=PROJECT / "output/developer-b-kia-annex-20260929",
    reconciliation_csv=PROJECT / "esg-proofops/reconciliation.csv",
)


@pytest.mark.skipif(
    not all(p.exists() for p in REAL.values()), reason="real Kia inputs not present"
)
def test_real_inputs_build_candidates_and_refuse_overwrite(tmp_path):
    out = tmp_path / "run"
    argv = [f"--{k.replace('_', '-')}={v}" for k, v in REAL.items()] + [f"--out={out}"]
    assert kia.main(argv) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["sr_sha256"].startswith("9ce8f379")
    assert summary["sr_pages"] == 134
    assert summary["dart"]["sources_verified"] == summary["dart"]["sources_total"] == 12
    assert summary["dart"]["count_consistent"] is True
    assert summary["gate_projection"]["packets_built"] == 0
    candidates = json.loads((out / "review-candidates.json").read_text(encoding="utf-8"))
    states = {e["candidate_state"] for r in candidates["records"] for e in r["elements"]}
    assert "present" not in states and "absent" not in states
    context = json.loads((out / "financial-context-C1.json").read_text(encoding="utf-8"))
    assert context["synthetic"] is False and context["financial"]["kind"] == "entity_set"
    assert len(json.loads(context["financial"]["normalized"])) == 25
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {p.name for p in out.iterdir()} - {"manifest.json"}
    assert kia.main(argv) == 2  # never overwrites


def test_pinned_input_mismatch_blocks(tmp_path):
    bogus = tmp_path / "sr.pdf"
    bogus.write_bytes(b"%PDF-1.7 not the report")
    args = dict(REAL, sr_pdf=bogus)
    if not all(Path(p).exists() for k, p in args.items() if k != "sr_pdf"):
        pytest.skip("real Kia inputs not present")
    argv = [f"--{k.replace('_', '-')}={v}" for k, v in args.items()] + [f"--out={tmp_path / 'o'}"]
    assert kia.main(argv) == 2
    assert not (tmp_path / "o").exists()
