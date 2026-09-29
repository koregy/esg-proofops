"""numeric-link-v1: deterministic P6 fact derivation over a synthetic table graph.

The graph is the explicit synthetic parser output used by the numeric acceptance
tests (table cells + a narrative claim, source quality confirmed as in AT-012).
Observations are recomputed with ``normalize_tables``; nothing here is a model call.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from proofops.application.claims import ClaimScope, discover_atomic_claims
from proofops.application.evidence.span_citations import verify_source_ref
from proofops.application.ingest.graph_fusion import fuse_candidates
from proofops.application.tagging.numeric_link import (
    FACT,
    POLICY,
    SOURCE_SCOPE,
    NumericLinkRejected,
    build_request,
    derive_numeric_fact,
    p6_element,
    replay_numeric_receipt,
    verified_observations,
)
from proofops.domain.provenance import canonical_hash
from proofops_agent.extraction import SyntheticClaimExtractor

from tests.acceptance.test_numeric import MANIFEST, TENANT, VERSION, row
from tests.acceptance.test_parsing import candidate
from tests.acceptance.test_tables import table

RUN = "44444444-4444-4444-8444-444444444444"
HEADER = ["지표", "Scope", "사업장", "연도", "산정방식", "조직경계", "단위", "분모", "값"]


def numeric_case(table_value, reported, *, unit="tCO2e", rows=None):
    entries = rows or (row(table_value, unit=unit),)
    statement = f"서울 Scope 1 2025 시장기반 연결 배출량 {reported} tCO2e 공시."
    graph = fuse_candidates(
        (
            table([HEADER, *entries]),
            candidate("claim", [("C", "paragraph", statement, (1, 500, 590, 520), ())]),
        ),
        tenant_id=TENANT,
    )
    graph = replace(graph, blocks=tuple(replace(b, quality="verified") for b in graph.blocks))
    found = discover_atomic_claims(
        graph, ClaimScope(TENANT, VERSION, MANIFEST), extractor=SyntheticClaimExtractor()
    )
    claim = next(c for c in found.claims if reported in c.quote)
    refs = tuple(verify_source_ref(ref, graph, tenant_id=TENANT) for ref in claim.source_refs)
    return SimpleNamespace(
        original=graph, claims=(replace(claim, source_quality="verified", source_refs=refs),)
    )


def inputs_for(c, *, track="performance", snapshot_salt=""):
    claim = c.claims[0]
    return SimpleNamespace(
        run_id=RUN,
        context=SimpleNamespace(claim=claim),
        original=c.original,
        packet=SimpleNamespace(to_dict=lambda: {"track": track}, packet_sha256="a" * 64),
        rulepack=SimpleNamespace(sha256="b" * 64),
        snapshot=lambda: {"claim": claim.claim_id, "salt": snapshot_salt},
    )


def binding_for(inputs, reported, **changes):
    claim = inputs.context.claim
    observation = verified_observations(inputs)[0]
    ref = claim.source_refs[0]
    start = ref.quote.index(reported)
    number = verify_source_ref(
        replace(
            ref,
            quote=reported,
            char_start=ref.char_start + start,
            char_end=ref.char_start + start + len(reported),
        ),
        inputs.original,
        tenant_id=TENANT,
    )
    binding = dict(
        kind="comparison",
        observation_id=observation.observation_id,
        reported_value=reported,
        reported_value_ref=asdict(number),
        metric_raw=observation.metric_raw,
        scope=observation.scope,
        subject=observation.subject,
        scope2_basis=observation.scope2_basis,
        organizational_boundary=observation.organizational_boundary,
        unit=observation.unit_canonical,
        denominator=observation.denominator,
        reporting_period=observation.reporting_period,
        quantity_kind="absolute",
        dimension_refs={},
    )
    for field in (
        "metric_raw",
        "scope",
        "subject",
        "scope2_basis",
        "organizational_boundary",
        "unit",
        "denominator",
        "reporting_period",
    ):
        value = binding[field]
        if value is None:
            continue
        start = ref.quote.index(value)
        bound = verify_source_ref(
            replace(
                ref,
                quote=value,
                char_start=ref.char_start + start,
                char_end=ref.char_start + start + len(value),
            ),
            inputs.original,
            tenant_id=TENANT,
        )
        binding["dimension_refs"][field] = asdict(bound)
    binding.update(changes)
    return binding


def derive(table_value, reported, **changes):
    c = numeric_case(table_value, reported)
    inputs = inputs_for(c)
    request = build_request(inputs, binding_for(inputs, reported, **changes))
    return inputs, request, derive_numeric_fact(inputs, request)


def test_consistent_table_value_yields_present_computed_check_fact():
    _, _, (fact, receipt) = derive("100", "100")
    assert fact is not None and (fact.name, fact.state) == (FACT, "present")
    assert fact.source_scope == SOURCE_SCOPE and fact.normalized_value == "consistent"
    assert fact.evidence_refs and all(
        r.verification_state == "verified" for r in fact.evidence_refs
    )
    assert receipt["result"]["status"] == "consistent" and receipt["fact_state"] == "present"
    assert receipt["observation"]["quality"] == "verified"
    element = p6_element(fact)
    assert element["reason_code"] == POLICY and element["state"] == "present"


def test_inconsistent_table_value_yields_conflict_fact():
    _, _, (fact, receipt) = derive("100", "120")
    assert fact is not None and (fact.state, fact.normalized_value) == ("conflict", "inconsistent")
    assert receipt["fact_state"] == "conflict"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"unit": "tCO2"}, "unit"),
        ({"subject": "부산"}, "subject"),
        ({"organizational_boundary": "별도"}, "organizational_boundary"),
        ({"reporting_period": "2024"}, "reporting_period"),
        ({"metric_raw": "에너지"}, "metric_raw"),
    ],
    ids=["unit", "entity-subject", "boundary", "period", "metric"],
)
def test_declared_dimension_mismatch_is_refused_without_claim_grounding(changes, reason):
    with pytest.raises(NumericLinkRejected, match=f"NUMERIC_DIMENSION_UNGROUNDED:{reason}"):
        derive("100", "100", **changes)


def test_only_the_normalizer_scale_rule_applies_and_raw_unit_is_not_accepted():
    # "천 tCO2e" 0.1 is 100 tCO2e by the normalizer's explicit scale rule only.
    c = numeric_case("0.1", "100", unit="천 tCO2e")
    inputs = inputs_for(c)
    fact, receipt = derive_numeric_fact(inputs, build_request(inputs, binding_for(inputs, "100")))
    assert receipt["observation"]["unit_canonical"] == "tCO2e"
    assert fact is not None and receipt["result"]["status"] == "consistent"
    # Declaring the raw scaled unit is a different dimension, never converted.
    raw = binding_for(inputs, "100", unit="천 tCO2e")
    with pytest.raises(NumericLinkRejected, match="NUMERIC_DIMENSION_UNGROUNDED:unit"):
        derive_numeric_fact(inputs, build_request(inputs, raw))


def test_stale_snapshot_is_rejected():
    c = numeric_case("100", "100")
    inputs = inputs_for(c)
    request = build_request(inputs, binding_for(inputs, "100"))
    with pytest.raises(NumericLinkRejected, match="NUMERIC_STALE_INPUTS"):
        derive_numeric_fact(inputs_for(c, snapshot_salt="moved"), request)


def test_tampered_request_or_partial_claim_is_rejected():
    inputs, request, _ = derive("100", "100")
    partial = copy.deepcopy(request)
    partial["claim_source_refs"][0]["quote"] = "100"
    with pytest.raises(NumericLinkRejected, match="WHOLE_CLAIM_REQUIRED"):
        derive_numeric_fact(inputs, partial)
    forged = copy.deepcopy(request)
    forged["policy_hash"] = "0" * 64
    with pytest.raises(NumericLinkRejected, match="NUMERIC_POLICY_MISMATCH"):
        derive_numeric_fact(inputs, forged)
    unknown = copy.deepcopy(request)
    unknown["binding"]["observation_id"] = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(NumericLinkRejected, match="NUMERIC_OBSERVATION_UNAVAILABLE"):
        derive_numeric_fact(inputs, unknown)
    extra = copy.deepcopy(request)
    extra["binding"]["tolerance"] = "5%"
    with pytest.raises(NumericLinkRejected, match="NUMERIC_BINDING_INVALID"):
        derive_numeric_fact(inputs, extra)


def test_tampered_reported_value_span_is_not_computable():
    c = numeric_case("100", "100")
    inputs = inputs_for(c)
    binding = binding_for(inputs, "100")
    binding["reported_value_ref"]["quote"] = "120"
    binding["reported_value"] = "120"
    fact, receipt = derive_numeric_fact(inputs, build_request(inputs, binding))
    assert fact is None and receipt["result"]["status"] == "not_computable"


def test_tampered_table_source_is_not_computable():
    c = numeric_case("100", "100")
    inputs = inputs_for(c)
    request = build_request(inputs, binding_for(inputs, "100"))
    # Break the table value block's verified quality in the replayed graph.
    value_id = verified_observations(inputs)[0].source_refs[-1].source_id
    blocks = tuple(
        replace(b, quality="unverified") if b.source_id == value_id else b
        for b in c.original.blocks
    )
    tampered = inputs_for(
        SimpleNamespace(claims=c.claims, original=replace(c.original, blocks=blocks))
    )
    # An unverified table cell never yields a usable observation: refused or undecided.
    try:
        fact, receipt = derive_numeric_fact(tampered, request)
    except NumericLinkRejected as exc:
        assert str(exc) in ("NUMERIC_OBSERVATION_UNAVAILABLE", "NUMERIC_STALE_INPUTS")
    else:
        assert fact is None and receipt["result"]["status"] == "not_computable"


def test_self_comparison_against_the_claims_own_block_is_refused():
    c = numeric_case("100", "100")
    inputs = inputs_for(c)
    claim = inputs.context.claim
    observation = verified_observations(inputs)[0]
    # Pretend the claim IS the table cell: its own block can never prove itself.
    fake_claim = replace(claim, source_refs=claim.source_refs + (observation.source_refs[-1],))
    fake = inputs_for(SimpleNamespace(claims=(fake_claim,), original=c.original))
    request = build_request(fake, binding_for(inputs, "100"))
    with pytest.raises(NumericLinkRejected, match="NUMERIC_SELF_COMPARISON"):
        derive_numeric_fact(fake, request)


def test_non_performance_track_is_refused():
    c = numeric_case("100", "100")
    inputs = inputs_for(c, track="goal")
    request = build_request(inputs, binding_for(inputs, "100"))
    with pytest.raises(NumericLinkRejected, match="NUMERIC_TRACK_MISMATCH"):
        derive_numeric_fact(inputs, request)


def test_two_observation_wrong_row_cannot_self_select_a_match():
    c = numeric_case("100", "100", rows=(row("100", subject="서울"), row("100", subject="부산")))
    inputs = inputs_for(c)
    observations = verified_observations(inputs)
    assert len(observations) == 2
    wrong = binding_for(inputs, "100", observation_id=observations[1].observation_id)
    fact, receipt = derive_numeric_fact(inputs, build_request(inputs, wrong))
    assert fact is None
    assert receipt["result"]["status"] == "not_comparable"
    assert receipt["result"]["reason"] == "dimension_mismatch"
    # Declaring the wrong row's entity instead is not a reviewed claim binding.
    wrong["subject"] = "부산"
    with pytest.raises(NumericLinkRejected, match="NUMERIC_DIMENSION_UNGROUNDED:subject"):
        derive_numeric_fact(inputs, build_request(inputs, wrong))


def test_validated_claim_context_conflict_overrides_a_selected_claim_span():
    c = numeric_case("100", "100")
    inputs = inputs_for(c)
    source = inputs.context.claim.source_refs[0]
    start = source.quote.index("2025")
    conflicting = verify_source_ref(
        replace(source, quote="2025", char_start=start, char_end=start + 4),
        inputs.original,
        tenant_id=TENANT,
    )
    inputs.context.dimensions = {"entity": conflicting}
    with pytest.raises(NumericLinkRejected, match="NUMERIC_CONTEXT_CONFLICT:subject"):
        derive_numeric_fact(inputs, build_request(inputs, binding_for(inputs, "100")))


def test_real_synthetic_pdf_table_pipeline_holds_unreviewed_cells_and_tamper(tmp_path):
    from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser, ParseFailure
    from proofops.application.ingest.graph_fusion import ParserProfile
    from proofops.application.ingest.normalize import normalize_tables

    from tests.acceptance.test_parsing import JAVA, MANIFEST, pdf, source

    raw = pdf(table=True)
    artifact = source(raw)
    graph = OpenDataLoaderParser(tmp_path / "parser").parse(
        artifact,
        ParserProfile(MANIFEST, physical_pages=(2,), java_executable=JAVA),
        tenant_id=TENANT,
    )
    assert any(b.kind == "table_cell" and b.raw_text == "1234 tCO2e" for b in graph.blocks)
    observations = normalize_tables(graph, tenant_id=TENANT).observations
    assert observations and all(o.quality != "verified" for o in observations)
    # A different PDF under the same declared source digest cannot be replayed.
    changed = replace(artifact, content=raw + b"%tampered\n")
    with pytest.raises(ParseFailure, match="SOURCE_INTEGRITY_MISMATCH"):
        OpenDataLoaderParser(tmp_path / "tampered").parse(
            changed,
            ParserProfile(MANIFEST, physical_pages=(2,), java_executable=JAVA),
            tenant_id=TENANT,
        )


def test_replay_is_byte_identical_and_detects_changed_source():
    inputs, request, (fact, receipt) = derive("100", "100")
    replayed, again = replay_numeric_receipt(inputs, receipt)
    assert replayed == fact and again["receipt_sha256"] == receipt["receipt_sha256"]
    edited = dict(receipt, fact_state="present-forged")
    with pytest.raises(NumericLinkRejected, match="NUMERIC_REPLAY_MISMATCH"):
        replay_numeric_receipt(inputs, edited)
    moved = inputs_for(
        SimpleNamespace(claims=(inputs.context.claim,), original=inputs.original), snapshot_salt="x"
    )
    with pytest.raises(NumericLinkRejected, match="NUMERIC_REPLAY_MISMATCH"):
        replay_numeric_receipt(moved, receipt)
    # A claimed boolean/state is never trusted: the receipt hash covers the result.
    assert receipt["receipt_sha256"] == canonical_hash(
        {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    )


@pytest.mark.parametrize("state", ["present", "conflict", None])
def test_p6_fact_never_changes_grade_or_label(state):
    from proofops.domain.rules.engine import ConfirmedFact

    from tests.acceptance.test_rules import evaluate, fact, inputs

    tags, context = inputs()
    facts = [f for f in tags.facts if f.name != FACT]
    baseline = evaluate(replace(tags, facts=tuple(facts)), context)
    if state is not None:
        ref = fact("comparison_baseline").evidence_refs
        facts.append(
            ConfirmedFact(
                FACT,
                state,
                ref,
                TENANT,
                True,
                True,
                source_scope=SOURCE_SCOPE,
                normalized_value="consistent" if state == "present" else "inconsistent",
            )
        )
    result = evaluate(replace(tags, facts=tuple(facts)), context)
    assert (result.evidence_grade, result.label) == (baseline.evidence_grade, baseline.label)
    assert result.evidence_grade == "E3"
