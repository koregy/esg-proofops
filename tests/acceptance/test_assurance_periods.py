"""AT-007 period extension: explicit calendar date intervals, source-bound end to end.

Claim dimensions come from verified sub-spans of the claim's own atomic source
and the opinion fields from verified tagged spans via `extract_assurance`; no
field is set by hand except where a test explicitly checks a malformed literal.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from proofops.application.assurance import (
    claim_context_from_review_inputs,
    extract_assurance,
    match_assurance,
)
from proofops.application.claims import Claim, ExtractionProfile, ExtractionReceipt
from proofops.application.evidence.binding import ClaimContext as EvidenceContext
from proofops.application.ingest.graph_fusion import (
    CandidateBatch,
    CandidateBlock,
    fuse_candidates,
)
from proofops.application.ports.models import ModelBinding
from proofops.domain.documents import NativeSource, PageGeometry

TENANT = "11111111-1111-4111-8111-111111111111"
VERSION = "22222222-2222-4222-8222-222222222222"
MANIFEST = "33333333-3333-4333-8333-333333333333"
STATEMENT = "44444444-4444-4444-8444-444444444444"
RUN = "55555555-5555-4555-8555-555555555555"
CLAIM = "66666666-6666-4666-8666-666666666666"
BINDING = ModelBinding("synthetic-assurance", "assurance", True)

ISO_YEAR = "2025-01-01~2025-12-31"
KO_YEAR = "2025년 1월 1일부터 2025년 12월 31일까지"
DOT_YEAR = "2025.01.01 ~ 2025.12.31"


def _build(claim_period, statement_period, excluded_period=None):
    claim_text = f"예시법인은 {claim_period} 기간 서울 사업장의 Scope 1 배출량을 보고하였다."
    texts = {
        "claim": claim_text,
        "provider": "예시 보증기관",
        "standard": "ISAE 3000",
        "level": "제한적 보증",
        "period": statement_period,
        "metric": "Scope 1",
        "entity": "예시법인",
        "facility": "서울 사업장",
    }
    if excluded_period is not None:
        texts["excluded_period"] = excluded_period
    blocks = tuple(
        CandidateBlock(
            "paragraph",
            NativeSource(
                VERSION,
                MANIFEST,
                RUN,
                name,
                i + 1,
                None,
                (10, 10, 300, 40),
                "pdf_bottom_left_points",
                text,
                0,
                len(text),
            ),
            PageGeometry(600, 800, 0, (0, 0, 600, 800)),
        )
        for i, (name, text) in enumerate(texts.items())
    )
    batch = CandidateBatch(
        TENANT,
        VERSION,
        MANIFEST,
        "a" * 64,
        RUN,
        "synthetic",
        "fixture-v1",
        "synthetic",
        "b" * 64,
        blocks,
        (),
        synthetic=True,
    )
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    # Explicit synthetic human-verification fixture; never parser auto-approval.
    graph = replace(graph, blocks=tuple(replace(b, quality="verified") for b in graph.blocks))
    by_native = {b.sources[0].source_native_id: b for b in graph.blocks}

    def full(name):
        return (replace(by_native[name].source_ref(), verification_state="verified"),)

    tagged = {
        "provider": full("provider"),
        "standard_raw": full("standard"),
        "level": full("level"),
        "reporting_period": full("period"),
        "covered_metrics": full("metric"),
        "entities": full("entity"),
        "facilities": full("facility"),
    }
    if excluded_period is not None:
        tagged["excluded_periods"] = full("excluded_period")
    statement = extract_assurance(
        graph,
        tuple(r for refs in tagged.values() for r in refs),
        BINDING,
        tagged_fields=tagged,
        tenant_id=TENANT,
        statement_id=STATEMENT,
        model_sha256="c" * 64,
        prompt_sha256="d" * 64,
        replicate_id=1,
    )

    claim_block = by_native["claim"]

    def sub(quote):
        start = claim_block.normalized_text.index(quote)
        ref = claim_block.source_ref(
            normalized_char_start=start, normalized_char_end=start + len(quote)
        )
        assert ref.quote == quote
        return replace(ref, verification_state="verified")

    claim = Claim(
        claim_id=CLAIM,
        tenant_id=TENANT,
        document_version_id=VERSION,
        parse_manifest_id=MANIFEST,
        source_sha256="a" * 64,
        quote=claim_text,
        source_refs=(replace(claim_block.source_ref(), verification_state="verified"),),
        source_quality="verified",
        topic_ids=(),
        receipt=ExtractionReceipt(
            source_id=claim_block.source_id,
            packet_sha256="b" * 64,
            response_sha256=None,
            raw_response_json=None,
            profile=ExtractionProfile(
                model_sha256="c" * 64,
                prompt_sha256="d" * 64,
                rule_sha256="e" * 64,
                synthetic=True,
            ),
            status="committed",
        ),
    )
    dims = {
        "entity": sub("예시법인"),
        "metric": sub("Scope 1"),
        "reporting_period": sub(claim_period),
        "facility": sub("서울 사업장"),
    }
    review_inputs = SimpleNamespace(context=EvidenceContext(claim, dims), original=graph)
    ctx = claim_context_from_review_inputs(
        review_inputs, tenant_id=TENANT, document_version_id=VERSION, claim_id=CLAIM
    )
    return statement, ctx


@pytest.mark.parametrize(
    ("claim_period", "statement_period"),
    [
        (ISO_YEAR, ISO_YEAR),
        (ISO_YEAR, KO_YEAR),
        (KO_YEAR, DOT_YEAR),
        ("2025-04-01 – 2026-03-31", "2025년 4월 1일 ~ 2026년 3월 31일"),
    ],
)
def test_exact_same_interval_in_supported_spellings_is_covered(claim_period, statement_period):
    statement, ctx = _build(claim_period, statement_period)
    assert not statement.unresolved_fields
    # Stored literals are never rewritten.
    assert ctx.reporting_period == claim_period
    assert statement.reporting_period == statement_period
    result = match_assurance(statement, ctx)
    assert (result.metric_match, result.period_match, result.boundary_match) == (
        "yes",
        "yes",
        "yes",
    )
    assert result.status == "covered"
    assert result.evidence_refs == statement.source_refs


def test_contained_sub_period_is_not_promoted_to_covered():
    # A quarter inside an annual opinion: containment alone is not period evidence.
    statement, ctx = _build("2025-01-01~2025-03-31", ISO_YEAR)
    result = match_assurance(statement, ctx)
    assert result.period_match == "unknown"
    assert result.status == "undetermined"


@pytest.mark.parametrize(
    "claim_period",
    [
        "2024-01-01~2024-12-31",  # disjoint
        "2024-07-01~2025-06-30",  # partly outside the opinion period
        "2025-01-01~2026-03-31",  # extends past the opinion period
    ],
)
def test_claim_period_outside_opinion_period_is_not_covered(claim_period):
    statement, ctx = _build(claim_period, ISO_YEAR)
    result = match_assurance(statement, ctx)
    assert result.period_match == "no"
    assert result.status == "not_covered"


@pytest.mark.parametrize(
    ("claim_period", "statement_period"),
    [
        ("2025", ISO_YEAR),  # year label vs dates: fiscal/calendar not inferred
        (ISO_YEAR, "2025년"),
        ("2025-01-01", "2025-01-01"),  # single date is not an explicit interval
        ("2025-12-31~2025-01-01", "2025-12-31~2025-01-01"),  # reversed
        ("2025-02-30~2025-12-31", "2025-02-30~2025-12-31"),  # not a calendar date
        ("2025년 1월 1일부터 12월 31일까지", KO_YEAR),  # incomplete end date
        ("2025-01-01~2025-12-31 (1년)", ISO_YEAR),  # extra prose
        ("2023~2024", "2023~2024"),  # year range stays literal
    ],
)
def test_mixed_incomplete_or_malformed_periods_stay_unknown(claim_period, statement_period):
    statement, ctx = _build(claim_period, statement_period)
    result = match_assurance(statement, ctx)
    assert result.period_match == "unknown"
    assert result.status == "undetermined"


@pytest.mark.parametrize(
    "excluded",
    [
        ISO_YEAR,  # identical spelling
        KO_YEAR,  # alternate supported spelling of the same interval
        "2025.07.01 ~ 2025.12.31",  # overlaps part of the claim period
    ],
)
def test_explicit_interval_exclusion_dominates(excluded):
    statement, ctx = _build(ISO_YEAR, ISO_YEAR, excluded_period=excluded)
    assert statement.excluded_periods == (excluded,)
    result = match_assurance(statement, ctx)
    assert result.status == "not_covered"
    assert "explicit_exclusion" in result.reasons


def test_disjoint_interval_exclusion_does_not_block_exact_match():
    statement, ctx = _build(ISO_YEAR, ISO_YEAR, excluded_period="2024-01-01~2024-12-31")
    assert match_assurance(statement, ctx).status == "covered"


@pytest.mark.parametrize(
    ("claim_period", "excluded"),
    [
        (ISO_YEAR, "2025"),  # year exclusion vs interval claim: cannot compare
        (ISO_YEAR, "2025년 하반기"),  # unsupported exclusion literal
    ],
)
def test_incomparable_exclusion_stays_undetermined(claim_period, excluded):
    statement, ctx = _build(claim_period, ISO_YEAR, excluded_period=excluded)
    result = match_assurance(statement, ctx)
    assert result.status == "undetermined"
    assert "unresolved_statement" in result.reasons


def test_year_claim_with_interval_exclusion_stays_undetermined():
    statement, ctx = _build("2025년", "2025", excluded_period="2024-01-01~2024-12-31")
    assert ctx.reporting_period == "2025"
    assert match_assurance(statement, ctx).status == "undetermined"
