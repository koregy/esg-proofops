"""Retrieval evaluation must expose missed, partial, unreadable and scoped cases."""

import pytest

from evaluation.metrics.retrieval import RetrievalCase, score_retrieval


def case(claim, company="A", readable=True, ids=("method", "value")):
    return RetrievalCase(claim, company, "report-2025", "a" * 64, ids, readable)


def ranking(ids):
    return dict(document_version_id="report-2025", source_sha256="a" * 64, source_ids=ids)


def score(cases, rankings, **kwargs):
    return score_retrieval(
        cases, rankings, reviewer_kind="ai_delegated", reviewer_id="fixture-agent", **kwargs
    )


def test_partial_and_unreadable_results_do_not_inflate_end_to_end_recall():
    cases = (case("full"), case("partial"), case("missing", "B"), case("unreadable", "B", False))
    result = score(
        cases,
        {
            "full": ranking(["value", "method"]),
            "partial": ranking(["value", "unrelated"]),
            "unreadable": ranking(["value", "method"]),
        },
    )
    total = result["overall"]["10"]["end_to_end"]
    assert total == dict(
        claims=4,
        all_required_hit=1,
        partial_required_hit=1,
        no_required_hit=2,
        missing_rankings=1,
        unreadable_claims=1,
        evidence_hits=3,
        evidence_required=8,
        complete_recall=0.25,
        evidence_recall=3 / 8,
    )
    assert result["overall"]["10"]["readable_only"]["complete_recall"] == 1 / 3
    assert result["by_company"]["B"]["20"]["end_to_end"]["complete_recall"] == 0
    assert result["independent_accuracy_claim"] is False
    assert result["reviewer_kind"] == "ai_delegated"


def test_rank_cutoff_and_hashes_are_reproducible():
    cases = (case("one", ids=("required",)), case("two", readable=False))
    predictions = {"one": ranking([f"distractor-{i}" for i in range(10)] + ["required"])}
    result = score(cases, predictions)
    assert result["overall"]["10"]["end_to_end"]["all_required_hit"] == 0
    assert result["overall"]["20"]["end_to_end"]["all_required_hit"] == 1
    assert score(tuple(reversed(cases)), predictions) == result
    assert result["overall"]["20"]["readable_only"]["claims"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"document_version_id": "other"},
        {"source_sha256": "b" * 64},
        {"source_ids": ["method", "method"]},
        {"source_ids": [None]},
        {"untrusted": True},
    ],
)
def test_wrong_identity_and_invalid_rankings_are_refused(change):
    with pytest.raises(ValueError):
        score((case("one"),), {"one": ranking(["method"]) | change})


def test_no_readable_cases_returns_null_conditional_metric_and_keeps_full_denominator():
    result = score((case("one", readable=False),), {})
    assert result["overall"]["10"]["readable_only"]["complete_recall"] is None
    assert result["overall"]["10"]["end_to_end"]["complete_recall"] == 0
    with pytest.raises(ValueError):
        case("one", ids=())
    with pytest.raises(ValueError):
        score((case("one"),), {"foreign": ranking([])})
    with pytest.raises(ValueError):
        score((case("one"), case("one")), {})
    with pytest.raises(ValueError):
        score((case("one"),), {}, ks=(0,))


def test_cli_preserves_ai_origin_and_refuses_overwriting_result(tmp_path):
    import json
    from dataclasses import asdict

    from evaluation.retrieval_eval import main

    source, output = tmp_path / "input.json", tmp_path / "result.json"
    source.write_text(
        json.dumps(
            dict(
                reviewer_kind="ai_delegated",
                reviewer_id="fixture-agent",
                cases=[asdict(case("one"))],
                rankings={},
            )
        ),
        encoding="utf-8",
    )
    assert main(["--input", str(source), "--output", str(output)]) == 0
    before = output.read_bytes()
    result = json.loads(before)
    assert result["reviewer_kind"] == "ai_delegated"
    assert result["overall"]["20"]["end_to_end"]["missing_rankings"] == 1
    with pytest.raises(SystemExit) as error:
        main(["--input", str(source), "--output", str(output)])
    assert error.value.code == 2
    assert output.read_bytes() == before
