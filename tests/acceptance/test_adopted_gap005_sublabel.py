"""Adopted GAP-005 A (R00 section 12, 2026-09-28): sublabel boundary regressions.

The adopted text says "Only exact goal missing pair [G3,G4] in the section-7 example
maps to IMPL". The source section-7 example (PROJECT_DOMAIN_V2_ORIGINAL.md L608-613)
tags G3, G4, G5 and G6 all absent and explains E1 by the G3/G4 pair. The
coordinator ruled (2026-09-29) that this shorthand is ambiguous, so the engine keeps
the literal full example and does not broaden or invert it: IMPL only for that exact
enumerated combination; every other E1/E2 combination keeps its E/label with
sublabel=null and GAP-005. No new rule pack or policy is created for this.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.acceptance.test_rules import evaluate, fact, inputs

# Source section 7: G1/G2 present; G3..G6 verified absent; G7/G8 not triggered.
LITERAL_EXAMPLE = dict(
    baseline_year="absent",
    baseline_value="absent",
    scope="absent",
    org_boundary="absent",
    current_progress="absent",
    transition_plan="absent",
)


def _goal(**changes):
    return evaluate(*inputs("goal", **changes))


def test_literal_section_seven_example_maps_to_impl_and_keeps_e1_label():
    result = _goal(**LITERAL_EXAMPLE)
    assert (result.evidence_grade, result.label, result.sublabel) == ("E1", "INCOMPLETE", "IMPL")
    assert set(result.missing_elements) == {"G3", "G4", "G5", "G6"}
    assert "GAP-005" not in result.gap_ids


@pytest.mark.parametrize(
    "changes",
    [
        # Adopted shorthand pair alone: G3/G4 absent, G5/G6 present (not the example).
        dict(
            baseline_year="absent", baseline_value="absent", scope="absent", org_boundary="absent"
        ),
        # Only one element of the pair.
        dict(baseline_year="absent", baseline_value="absent"),
        dict(scope="absent", org_boundary="absent"),
        # Pair plus only one of G5/G6.
        {**LITERAL_EXAMPLE, "transition_plan": "present"},
        {**LITERAL_EXAMPLE, "current_progress": "present"},
        # Partial absence inside G3 (baseline value only).
        {**LITERAL_EXAMPLE, "baseline_year": "present"},
    ],
    ids=["pair-only", "g3-only", "g4-only", "no-g6", "no-g5", "g3-value-only"],
)
def test_other_e1_combinations_keep_grade_with_null_sublabel_and_gap005(changes):
    result = _goal(**changes)
    assert result.evidence_grade == "E1" and result.label == "INCOMPLETE"
    assert result.sublabel is None
    assert "GAP-005" in result.gap_ids


def test_e2_goal_gap_is_not_impl():
    result = _goal(current_progress="absent", transition_plan="absent")
    assert (result.evidence_grade, result.label, result.sublabel) == ("E2", "INCOMPLETE", None)
    assert "GAP-005" in result.gap_ids


def test_unresolved_example_element_is_never_promoted_to_absent_or_impl():
    result = _goal(**{**LITERAL_EXAMPLE, "current_progress": "unknown"})
    assert result.sublabel is None
    assert "G5" not in result.missing_elements


def test_triggered_conditional_element_leaves_the_exact_example():
    # Offset claim triggers G7, so excluded != {G7, G8}: not the enumerated example.
    tags, context = inputs("goal", **LITERAL_EXAMPLE)
    facts = tuple(
        fact(f.name, "present") if f.name == "offset_or_carbon_neutral_claim" else f
        for f in tags.facts
    )
    result = evaluate(replace(tags, facts=facts), context)
    assert result.sublabel is None


@pytest.mark.parametrize("track", ["performance", "management"])
def test_non_goal_tracks_never_get_impl(track):
    names = {
        "performance": ("comparison_baseline", "calculation_boundary"),
        "management": ("external_verification", "concrete_implementation_detail"),
    }[track]
    result = evaluate(*inputs(track, **{name: "absent" for name in names}))
    assert result.sublabel is None
    if result.evidence_grade in ("E1", "E2"):
        assert "GAP-005" in result.gap_ids


def test_complete_goal_e3_has_no_sublabel_and_no_gap005():
    result = _goal()
    assert (result.evidence_grade, result.sublabel) == ("E3", None)
    assert "GAP-005" not in result.gap_ids
