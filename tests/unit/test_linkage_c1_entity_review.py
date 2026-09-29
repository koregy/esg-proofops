"""C1 caller-reviewed entity_set bridge (synthetic only).

A confirmed ``org_boundary`` fact can carry the evidence sentence rather than a
typed entity_set. SCHEMA_GUIDE permits a name -> ID mapping only with the source
text and an approval record, so ``C1EntitySetReview`` is that record, pinned to
one tenant/company/run/claim/version/tag revision and to verified evidence.
Every entity ID and label here is a SYNTHETIC fixture ("가상법인"); nothing here
claims a real company boundary, Kia or otherwise, or a semantic C1 result.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from proofops.application.linkage_exchange import (
    BlockedPacket,
    C1EntitySetReview,
    ReviewedEntity,
    ReviewedSourceBinding,
    c1_review_receipt,
)
from proofops.domain.errors import DomainValidationError
from proofops.domain.reconciliation.common import validate_packet
from proofops.domain.reconciliation.engine import canonical_sha256
from proofops.domain.rules.engine import ConfirmedFact

from tests.unit.test_linkage_exchange import (
    CLAIM,
    SR_SOURCE,
    TENANT,
    VERSION,
    _build,
    _confirmed_tags,
    _financial_context,
    _source_ref,
)

RUN = "run-synthetic-0001"
QUOTE = "공시 대상 법인은 가상법인 A와 B입니다."
OTHER_TENANT = "99999999-9999-4999-8999-999999999999"
OTHER_SOURCE = "88888888-8888-4888-8888-888888888888"


def _sentence_tags(**overrides):
    fact = dict(
        name="org_boundary",
        state="present",
        evidence_refs=(_source_ref(),),
        source_tenant_id=TENANT,
        citation_verified=True,
        binding_accepted=True,
        normalized_value=QUOTE,
    )
    fact.update(overrides)
    return _confirmed_tags(facts=(ConfirmedFact(**fact),))


def _review(**overrides) -> C1EntitySetReview:
    values = dict(
        synthetic=True,
        tenant_id=TENANT,
        company_id="fixture-company",
        run_id=RUN,
        claim_id=CLAIM,
        document_version_id=VERSION,
        tag_revision=1,
        fact_name="org_boundary",
        fact_value=QUOTE,
        kind="entity_set",
        entities=(
            ReviewedEntity("A", "가상법인 A", SR_SOURCE),
            ReviewedEntity("B", "B", SR_SOURCE),
        ),
        source_bindings=(ReviewedSourceBinding(SR_SOURCE, QUOTE),),
        review_id="synthetic-review-1",
        reviewed_by="synthetic-reviewer",
        reviewed_at="2026-09-29",
        review_origin="ai_delegated",
    )
    values.update(overrides)
    return C1EntitySetReview(**values)


def _reviewed_build(review=None, **overrides):
    kwargs = dict(tags=_sentence_tags(), run_id=RUN, c1_entity_set_review=review or _review())
    kwargs.update(overrides)
    return _build(**kwargs)


def test_reviewed_sentence_becomes_a_typed_entity_set_bound_to_its_evidence():
    packet = _reviewed_build()
    assert not isinstance(packet, BlockedPacket), packet
    validate_packet(packet)
    assert packet["synthetic"] is True
    assert packet["sustainability"] == dict(
        raw=QUOTE, normalized='["A","B"]', kind="entity_set", unit=None, source_id="sr-" + SR_SOURCE
    )
    assert "sr-" + SR_SOURCE in {s["source_id"] for s in packet["sources"]}
    # strict1.1 keys only; the approval record lives in a separate receipt.
    assert "review" not in packet and "c1_entity_set_review" not in json.dumps(packet)
    receipt = c1_review_receipt(_review(), packet)
    assert receipt["packet_sha256"] == canonical_sha256(packet)
    assert receipt["review"]["tag_revision"] == 1 and receipt["synthetic"] is True
    assert receipt["review_kind"] == "ai_delegated_entity_set_review_not_independent_gold"
    human = c1_review_receipt(_review(review_origin="human"), packet)
    assert human["review_kind"] == "human_entity_set_review"


def test_without_a_review_the_sentence_is_still_refused_and_says_why():
    result = _build(tags=_sentence_tags())
    assert isinstance(result, BlockedPacket)
    assert result.reason == "invalid_reconciliation_packet"
    assert "C1EntitySetReview" in result.detail


@pytest.mark.parametrize(
    ("overrides", "build_overrides", "reason"),
    [
        ({"tenant_id": OTHER_TENANT}, {}, "review_tenant_mismatch"),
        ({"company_id": "other-company"}, {}, "review_company_mismatch"),
        ({"run_id": "other-run"}, {}, "review_run_mismatch"),
        ({}, {"run_id": None}, "review_run_mismatch"),
        ({"claim_id": OTHER_TENANT}, {}, "review_claim_mismatch"),
        ({"document_version_id": OTHER_TENANT}, {}, "review_version_mismatch"),
        ({"tag_revision": 2}, {}, "review_revision_stale"),
        ({"synthetic": False}, {}, "review_synthetic_mismatch"),
        ({"fact_value": "공시 대상 법인은 가상법인 A입니다."}, {}, "review_fact_value_mismatch"),
        ({"fact_name": "organizational_boundary"}, {}, "review_fact_not_verified_trigger"),
        (
            {"source_bindings": (ReviewedSourceBinding(OTHER_SOURCE, QUOTE),)},
            {},
            "review_source_not_trigger_evidence",
        ),
        (
            {"source_bindings": (ReviewedSourceBinding(SR_SOURCE, QUOTE + " 및 가상법인 C"),)},
            {},
            "review_quote_mismatch",
        ),
        (
            {"entities": (ReviewedEntity("C", "가상법인 C", SR_SOURCE),)},
            {},
            "review_entity_label_not_in_source",
        ),
        (
            {"entities": (ReviewedEntity("A", "가상법인 A", OTHER_SOURCE),)},
            {},
            "review_entity_label_not_in_source",
        ),
    ],
)
def test_any_unpinned_or_unbound_review_blocks(overrides, build_overrides, reason):
    result = _reviewed_build(_review(**overrides), **build_overrides)
    assert isinstance(result, BlockedPacket)
    assert result.reason == reason


def test_cross_tenant_claim_blocks_before_the_review_is_considered():
    result = _reviewed_build(_review(tenant_id=OTHER_TENANT), tenant_id=OTHER_TENANT)
    assert isinstance(result, BlockedPacket)
    assert result.reason == "tenant_mismatch"


def test_review_cannot_open_an_unverified_or_non_present_fact():
    unverified = _confirmed_tags(
        facts=(ConfirmedFact(name="org_boundary", state="unknown", normalized_value=QUOTE),)
    )
    result = _reviewed_build(tags=unverified)
    assert isinstance(result, BlockedPacket)
    assert result.reason == "no_verified_trigger"


def test_review_cannot_contradict_an_already_typed_value():
    typed = _sentence_tags(normalized_value='["A"]')
    result = _reviewed_build(_review(fact_value='["A"]'), tags=typed)
    assert isinstance(result, BlockedPacket)
    assert result.reason == "review_conflicts_typed_value"


def test_review_applies_only_to_c1():
    result = _reviewed_build(item="C3")
    assert isinstance(result, BlockedPacket)
    assert result.reason in {"no_matching_item_trigger", "invalid_c1_entity_set_review"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "facility_set"},  # implementation_scope has no producer
        {"fact_name": "scope"},  # generic GHG scope is never aliased
        {"fact_name": "implementation_scope"},
        {"entities": ()},
        {"entities": (ReviewedEntity("A", "A", SR_SOURCE), ReviewedEntity("A", "B", SR_SOURCE))},
        {"source_bindings": ()},
        {"tag_revision": True},
        {"reviewed_at": "yesterday"},
        {"review_origin": "expert"},  # no invented legal/accounting approval origin
        {"review_origin": None},
    ],
)
def test_malformed_or_scope_alias_review_is_rejected_at_construction(overrides):
    with pytest.raises((DomainValidationError, ValueError)):
        _review(**overrides)


def test_entity_labels_must_be_trimmed_literal_text():
    with pytest.raises(DomainValidationError):
        ReviewedEntity("A", " 가상법인 A", SR_SOURCE)


def test_real_financial_context_requires_a_non_synthetic_review():
    real = _financial_context(synthetic=False)
    result = _reviewed_build(financial_context=real, trusted_company_id="fixture-company")
    assert isinstance(result, BlockedPacket)
    assert result.reason == "review_synthetic_mismatch"
    packet = _reviewed_build(
        _review(synthetic=False), financial_context=real, trusted_company_id="fixture-company"
    )
    assert not isinstance(packet, BlockedPacket)
    assert packet["synthetic"] is False  # still byte-verified by the CLI before output


def test_review_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        _review().run_id = "x"  # type: ignore[misc]
