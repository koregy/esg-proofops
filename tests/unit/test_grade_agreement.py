import json

import pytest

from evaluation.agreement_eval import main
from evaluation.metrics.agreement import score_agreement


def packet(pairs):
    return {
        "schema": "paired-grade-ratings-v1",
        "dataset_id": "synthetic-agreement-test",
        "reviewers": [{"id": "r1", "kind": "synthetic"}, {"id": "r2", "kind": "synthetic"}],
        "independence_declared": True,
        "cases": [
            {
                "claim_id": f"c{i}",
                "claim_revision": 1,
                "source_sha256": "a" * 64,
                "claim_quote_sha256": "b" * 64,
                "ratings": list(pair),
            }
            for i, pair in enumerate(pairs)
        ],
    }


def test_hand_calculated_partial_agreement():
    result = score_agreement(packet([("E0", "E0"), ("E0", "E1"), ("E1", "E1"), ("E1", "E1")]))
    assert result["exact_agreement"] == 0.75
    assert result["weighted_kappa"]["linear"]["value"] == 0.5
    assert result["weighted_kappa"]["quadratic"]["value"] == 0.5
    assert result["gold_approved"] is False
    assert result["human_ratings_declared"] is False


def test_weighting_conventions_and_pair_symmetry():
    pairs = [("E0", "E0"), ("E1", "E2"), ("E3", "E3")]
    result = score_agreement(packet(pairs))
    # Observed linear distance sum 1/3; expected cross-product sum 13/3.
    assert result["weighted_kappa"]["linear"]["value"] == pytest.approx(10 / 13)
    assert result["weighted_kappa"]["quadratic"]["value"] == pytest.approx(26 / 29)
    reverse = score_agreement(packet([(b, a) for a, b in pairs]))
    assert reverse["weighted_kappa"] == result["weighted_kappa"]


def test_perfect_and_systematically_opposite_ratings():
    same = score_agreement(packet([("E0", "E0"), ("E3", "E3")]))
    opposite = score_agreement(packet([("E0", "E3"), ("E3", "E0")]))
    for weight in ("linear", "quadratic"):
        assert same["weighted_kappa"][weight]["value"] == 1
        assert opposite["weighted_kappa"][weight]["value"] == -1


def test_missing_pairs_remain_visible_in_coverage_and_degenerate_is_not_perfect():
    result = score_agreement(packet([("E2", "E2"), (None, "E1"), (None, None)]))
    assert result["total_cases"] == 3
    assert result["complete_pairs"] == 1
    assert result["paired_coverage"] == 1 / 3
    assert len(result["excluded_cases"]) == 2
    assert result["exact_agreement"] == 1
    assert result["weighted_kappa"]["linear"]["value"] is None
    assert result["weighted_kappa"]["linear"]["status"] == "degenerate_marginals"
    empty = score_agreement(packet([]))
    assert empty["weighted_kappa"]["linear"]["status"] == "no_complete_pairs"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["reviewers"][1].update(id="r1"),
        lambda p: p.update(independence_declared=False),
        lambda p: p["cases"].append(dict(p["cases"][0])),
        lambda p: p["cases"][0].update(source_sha256="unbound"),
        lambda p: p["cases"][0].update(claim_revision=True),
        lambda p: p["cases"][0].update(ratings=["E4", "E0"]),
        lambda p: p["cases"][0].update(ratings=["E0"]),
    ],
)
def test_unbound_or_unpaired_data_refused(mutation):
    data = packet([("E0", "E0")])
    mutation(data)
    with pytest.raises(ValueError):
        score_agreement(data)


def test_cli_preserves_input_and_refuses_overwrite(tmp_path):
    source, out = tmp_path / "in.json", tmp_path / "out.json"
    source.write_text(json.dumps(packet([("E0", "E0"), ("E3", "E3")])))
    original = source.read_bytes()
    assert main(["--input", str(source), "--output", str(out)]) == 0
    assert json.loads(out.read_text())["weighted_kappa"]["linear"]["value"] == 1
    with pytest.raises(SystemExit):
        main(["--input", str(source), "--output", str(out)])
    assert source.read_bytes() == original


def test_cli_duplicate_keys_refused_without_output(tmp_path):
    source, out = tmp_path / "in.json", tmp_path / "out.json"
    source.write_text('{"schema": "first", "schema": "second"}')
    with pytest.raises(SystemExit):
        main(["--input", str(source), "--output", str(out)])
    assert not out.exists()
