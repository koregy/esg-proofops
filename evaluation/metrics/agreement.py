"""Source-bound paired ratings; agreement is not model accuracy or approved gold."""

from __future__ import annotations

import re
from fractions import Fraction

from proofops.domain.provenance import canonical_hash

GRADES = ("E0", "E1", "E2", "E3")
_SHA = re.compile(r"[0-9a-f]{64}")


def score_agreement(packet: dict) -> dict:
    """Compute both weighting conventions without silently dropping missing ratings.

    Independence and reviewer identity are caller declarations, not authenticated
    human review. No result here approves a label, an evidence span or a rulepack.
    """
    if not isinstance(packet, dict) or set(packet) != {
        "schema",
        "dataset_id",
        "reviewers",
        "independence_declared",
        "cases",
    }:
        raise ValueError("AGREEMENT_PACKET_INVALID")
    if packet["schema"] != "paired-grade-ratings-v1":
        raise ValueError("AGREEMENT_SCHEMA_INVALID")
    if not isinstance(packet["dataset_id"], str) or not packet["dataset_id"].strip():
        raise ValueError("AGREEMENT_DATASET_REQUIRED")
    if packet["independence_declared"] is not True:
        raise ValueError("INDEPENDENT_RATINGS_REQUIRED")
    reviewers = packet["reviewers"]
    if not isinstance(reviewers, list) or len(reviewers) != 2:
        raise ValueError("TWO_REVIEWERS_REQUIRED")
    for reviewer in reviewers:
        if (
            not isinstance(reviewer, dict)
            or set(reviewer) != {"id", "kind"}
            or not isinstance(reviewer["id"], str)
            or not reviewer["id"].strip()
            or reviewer["kind"] not in {"human", "ai_delegated", "synthetic"}
        ):
            raise ValueError("REVIEWER_IDENTITY_INVALID")
    if reviewers[0]["id"] == reviewers[1]["id"]:
        raise ValueError("DISTINCT_REVIEWERS_REQUIRED")
    cases = packet["cases"]
    if not isinstance(cases, list) or len(cases) > 100_000:
        raise ValueError("AGREEMENT_CASES_INVALID")
    matrix = [[0] * 4 for _ in GRADES]
    seen = set()
    exclusions = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "claim_id",
            "claim_revision",
            "source_sha256",
            "claim_quote_sha256",
            "ratings",
        }:
            raise ValueError("AGREEMENT_CASE_INVALID")
        claim = case["claim_id"]
        if not isinstance(claim, str) or not claim.strip() or claim in seen:
            raise ValueError("AGREEMENT_CLAIM_ID_INVALID_OR_DUPLICATE")
        seen.add(claim)
        if type(case["claim_revision"]) is not int or case["claim_revision"] < 1:
            raise ValueError("AGREEMENT_REVISION_INVALID")
        for key in ("source_sha256", "claim_quote_sha256"):
            if not isinstance(case[key], str) or _SHA.fullmatch(case[key]) is None:
                raise ValueError("AGREEMENT_SOURCE_BINDING_REQUIRED")
        ratings = case["ratings"]
        if not isinstance(ratings, list) or len(ratings) != 2:
            raise ValueError("TWO_EXPLICIT_RATINGS_REQUIRED")
        if any(rating is not None and rating not in GRADES for rating in ratings):
            raise ValueError("AGREEMENT_GRADE_INVALID")
        if None in ratings:
            exclusions.append({"claim_id": claim, "reason": "missing_or_unresolved_rating"})
        else:
            matrix[GRADES.index(ratings[0])][GRADES.index(ratings[1])] += 1
    count = sum(map(sum, matrix))
    rows = [sum(row) for row in matrix]
    columns = [sum(matrix[i][j] for i in range(4)) for j in range(4)]
    scores = {}
    for name, power in (("linear", 1), ("quadratic", 2)):
        observed = sum(
            Fraction(abs(i - j) ** power, 3**power) * matrix[i][j]
            for i in range(4)
            for j in range(4)
        )
        expected = sum(
            Fraction(abs(i - j) ** power, 3**power) * rows[i] * columns[j]
            for i in range(4)
            for j in range(4)
        )
        scores[name] = {
            "value": float(1 - observed * count / expected) if expected else None,
            "denominator": count,
            "status": "computed"
            if expected
            else ("no_complete_pairs" if not count else "degenerate_marginals"),
        }
    return {
        "schema": "paired-grade-agreement-v1",
        "dataset_id": packet["dataset_id"],
        "input_sha256": canonical_hash(packet),
        "reviewers": [dict(reviewer) for reviewer in reviewers],
        "provenance": "reviewer_identity_and_independence_self_declared",
        "human_ratings_declared": all(r["kind"] == "human" for r in reviewers),
        "gold_approved": False,
        "grades": list(GRADES),
        "matrix_rows_reviewer_1_columns_reviewer_2": matrix,
        "total_cases": len(cases),
        "complete_pairs": count,
        "excluded_cases": exclusions,
        "paired_coverage": count / len(cases) if cases else None,
        "exact_agreement": sum(matrix[i][i] for i in range(4)) / count if count else None,
        "weighted_kappa": scores,
    }
