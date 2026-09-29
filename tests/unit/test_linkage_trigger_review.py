"""C3/C4 source-bound trigger review bridge (synthetic only).

No producer emits `currency_amount`/`revenue_share`. A C3/C4 trigger review is
the SCHEMA_GUIDE "trigger 판정 receipt": pinned to tenant/company/run/claim/
version/accepted tag revision, to the SHA-256 of the confirmed fact it reads and
to the claim's source hash, with every literal re-derived from a bound quote.
Every sentence, amount and classification here is a SYNTHETIC fixture; nothing
claims a real Kia (or other) commitment, revenue share or reconciliation result.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from proofops.application.linkage_exchange import (
    BlockedPacket,
    C3Context,
    C4Context,
    FinancialFact,
    FinancialSource,
)
from proofops.application.linkage_trigger_review import (
    C3CurrencyTriggerReview,
    C4RevenueTriggerReview,
    TriggerSourceBinding,
    check_trigger_review,
    fact_sha256,
    parse_amount_literal,
    trigger_review_from_dict,
    trigger_review_receipt,
    trigger_review_to_dict,
)
from proofops.domain.errors import DomainValidationError
from proofops.domain.reconciliation.common import validate_packet
from proofops.domain.reconciliation.engine import canonical_sha256, evaluate
from proofops.domain.rules.engine import ConfirmedFact

from tests.unit.test_linkage_exchange import (
    CLAIM,
    FIXTURE_CONTRACT_DIR,
    SR_SOURCE,
    TENANT,
    VERSION,
    _build,
    _claim,
    _confirmed_tags,
    _financial_context,
    _source_ref,
)

RUN = "run-synthetic-0001"
COMPANY = "fixture-company"
OTHER_TENANT = "99999999-9999-4999-8999-999999999999"
C3_QUOTE = "2030년까지 가상 친환경 설비투자 10조원을 집행한다."
C4_QUOTE = "가상친환경차 매출 비중은 22.5%입니다."
POLICY = json.loads((FIXTURE_CONTRACT_DIR / "example-policy.json").read_text(encoding="utf-8"))


def _claim_for(quote: str):
    return _claim(quote=quote, source_refs=(_source_ref(quote=quote),))


def _fact(quote: str, name: str = "target_metric", **overrides) -> ConfirmedFact:
    values = dict(
        name=name,
        state="present",
        evidence_refs=(_source_ref(quote=quote),),
        source_tenant_id=TENANT,
        citation_verified=True,
        binding_accepted=True,
        normalized_value=quote,
    )
    values.update(overrides)
    return ConfirmedFact(**values)


def _tags(fact: ConfirmedFact, track: str = "goal", **overrides):
    return _confirmed_tags(facts=(fact,), track=track, **overrides)


def _pins(fact: ConfirmedFact, **overrides) -> dict:
    values = dict(
        synthetic=True,
        tenant_id=TENANT,
        company_id=COMPANY,
        run_id=RUN,
        claim_id=CLAIM,
        document_version_id=VERSION,
        source_sha256="f" * 64,
        tag_revision=1,
        fact_name=fact.name,
        fact_value=fact.normalized_value,
        fact_sha256=fact_sha256(fact),
        source_id=SR_SOURCE,
        source_bindings=(TriggerSourceBinding(SR_SOURCE, fact.evidence_refs[0].quote),),
        review_id="synthetic-trigger-review-1",
        reviewed_by="synthetic-delegated-reviewer",
        reviewed_at="2026-09-29",
        review_origin="ai_delegated",
    )
    values.update(overrides)
    return values


def _c3_review(fact, **overrides) -> C3CurrencyTriggerReview:
    values = _pins(fact) | dict(
        amount_literal="10조원",
        investment_label="설비투자",
        currency="KRW",
        normalized_amount="10000000000000",
    )
    values.update(overrides)
    return C3CurrencyTriggerReview(**values)


def _c4_review(fact, **overrides) -> C4RevenueTriggerReview:
    values = _pins(fact) | dict(
        classification_label="가상친환경차",
        revenue_label="매출",
        share_literal="22.5%",
    )
    values.update(overrides)
    return C4RevenueTriggerReview(**values)


C3_CONTEXT = C3Context(
    currency="KRW",
    target_period_start="2024-01-01",
    target_period_end="2030-12-31",
    capex_period_start="2024-01-01",
    capex_period_end="2024-12-31",
    capex_account_ids=("synthetic-capex-account",),
    commitment_source_id=None,
    funding_plan_source_id=None,
)


def _c3_financial(**overrides):
    values = dict(
        financial=FinancialFact(
            raw="가상 유형자산의 취득 3조원",
            normalized="3000000000000",
            kind="currency_amount",
            unit="KRW",
            source_id="fs-scope",
        ),
        c3_context=C3_CONTEXT,
    )
    values.update(overrides)
    return _financial_context(**values)


def _c4_financial(context: C4Context | None = None):
    return _financial_context(
        financial=FinancialFact(
            raw="가상친환경차",
            normalized="가상친환경차",
            kind="classification",
            unit=None,
            source_id="fs-scope",
        ),
        c4_context=context or C4Context("가상친환경차", ("fs-scope",), ()),
    )


def _build_c3(fact=None, review=None, **overrides):
    fact = fact or _fact(C3_QUOTE)
    kwargs = dict(
        item="C3",
        tags=_tags(fact),
        claim=_claim_for(fact.evidence_refs[0].quote if fact.evidence_refs else C3_QUOTE),
        financial_context=_c3_financial(),
        trusted_company_id=COMPANY,
        run_id=RUN,
        trigger_review=review or _c3_review(fact),
    )
    kwargs.update(overrides)
    return _build(**kwargs)


def _build_c4(fact=None, review=None, **overrides):
    fact = fact or _fact(C4_QUOTE, name="quantitative_or_qualified_ordinal")
    kwargs = dict(
        item="C4",
        tags=_tags(fact, track="performance"),
        claim=_claim_for(fact.evidence_refs[0].quote),
        financial_context=_c4_financial(),
        trusted_company_id=COMPANY,
        run_id=RUN,
        trigger_review=review or _c4_review(fact),
    )
    kwargs.update(overrides)
    return _build(**kwargs)


def _reason(result) -> str:
    assert isinstance(result, BlockedPacket), result
    return result.reason


# --------------------------------------------------------------------------- #
# positive synthetic vectors
# --------------------------------------------------------------------------- #


def test_reviewed_c3_trigger_builds_a_valid_packet_and_receipt():
    fact = _fact(C3_QUOTE)
    review = _c3_review(fact)
    packet = _build_c3(fact, review)
    assert not isinstance(packet, BlockedPacket), packet
    validate_packet(packet)
    assert packet["sustainability"] == dict(
        raw=C3_QUOTE,
        normalized="10000000000000",
        kind="currency_amount",
        unit="KRW",
        source_id="sr-" + SR_SOURCE,
    )
    assert packet["claim"]["trigger_elements"] == ["currency_amount"]
    assert packet["claim"]["track"] == "goal"
    assert packet["comparability"] == "unknown"  # never decided by the bridge

    trigger = check_trigger_review(
        review,
        item="C3",
        claim=_claim_for(C3_QUOTE),
        tags=_tags(fact),
        tenant_id=TENANT,
        company_id=COMPANY,
        run_id=RUN,
        synthetic=True,
    )
    receipt = trigger_review_receipt(review, packet, trigger)
    assert receipt["packet_sha256"] == canonical_sha256(packet)
    assert receipt["review_sha256"] == canonical_sha256(trigger_review_to_dict(review))
    assert receipt["review_kind"] == "ai_delegated_trigger_review_not_independent_gold"
    assert receipt["derived"]["fact_sha256"] == fact_sha256(fact)
    assert "REC-003 CAPEX account mapping" in receipt["not_decided"]


def test_reviewed_c4_trigger_builds_a_valid_packet():
    packet = _build_c4()
    assert not isinstance(packet, BlockedPacket), packet
    validate_packet(packet)
    assert packet["sustainability"]["kind"] == "classification"
    assert packet["sustainability"]["normalized"] == "가상친환경차"
    assert packet["sustainability"]["raw"] == C4_QUOTE
    # The same fact is also the existing C2 trigger; every verified trigger is listed.
    assert packet["claim"]["trigger_elements"] == ["quantitative_value", "revenue_share"]


def test_reviewed_packets_pass_the_contract_schema(tmp_path):
    import importlib.util
    import sys

    sys.path.insert(0, str(FIXTURE_CONTRACT_DIR))
    spec = importlib.util.spec_from_file_location("validate", FIXTURE_CONTRACT_DIR / "validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    validator = module.load_schema_validators()["input"]
    for packet in (_build_c3(), _build_c4()):
        assert not list(validator.iter_errors(packet))


def test_review_json_roundtrip_is_strict():
    review = _c3_review(_fact(C3_QUOTE))
    data = json.loads(json.dumps(trigger_review_to_dict(review), ensure_ascii=False))
    assert trigger_review_from_dict(data) == review
    for broken in (
        data | {"extra": 1},
        {k: v for k, v in data.items() if k != "investment_label"},
        data | {"item": "C1"},
        data | {"review_origin": None},  # an unfilled draft never loads
        data | {"review_origin": "automatic"},
    ):
        with pytest.raises(DomainValidationError):
            trigger_review_from_dict(broken)


@pytest.mark.parametrize(
    "literal, expected",
    [
        ("10조원", ("10000000000000", "KRW")),
        ("1,234억 원", ("123400000000", "KRW")),
        ("2.5조원", ("2500000000000", "KRW")),
        ("500백만원", ("500000000", "KRW")),
        ("3,000 KRW", ("3000", "KRW")),
        ("$5 million", ("5000000", "USD")),
        ("USD 1.2 billion", ("1200000000", "USD")),
    ],
)
def test_amount_literals_parse_exactly(literal, expected):
    assert parse_amount_literal(literal) == expected


@pytest.mark.parametrize(
    "literal", ["10조", "1조 2,000억원", "0원", " 10조원", "10조원 이상", "십조원"]
)
def test_unsupported_amount_literals_raise(literal):
    with pytest.raises(DomainValidationError):
        parse_amount_literal(literal)


# --------------------------------------------------------------------------- #
# identity / CAS pins
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"tag_revision": 2}, "review_revision_stale"),
        ({"source_sha256": "e" * 64}, "review_source_hash_stale"),
        ({"fact_sha256": "0" * 64}, "review_fact_stale"),
        ({"tenant_id": OTHER_TENANT}, "review_tenant_mismatch"),
        ({"company_id": "another-company"}, "review_company_mismatch"),
        ({"run_id": "another-run"}, "review_run_mismatch"),
        ({"synthetic": False}, "review_synthetic_mismatch"),
    ],
)
def test_stale_or_foreign_review_pins_block(overrides, reason):
    fact = _fact(C3_QUOTE)
    assert _reason(_build_c3(fact, _c3_review(fact, **overrides))) == reason


def test_changed_accepted_fact_blocks_as_stale():
    reviewed = _fact(C3_QUOTE)
    changed = _fact(C3_QUOTE, normalized_value="2030년 목표")
    assert _reason(_build_c3(changed, _c3_review(reviewed))) == "review_fact_stale"


def test_newer_accepted_revision_blocks_a_review_of_the_old_one():
    fact = _fact(C3_QUOTE)
    result = _build_c3(fact, tags=_tags(fact, tag_revision=3))
    assert _reason(result) == "review_revision_stale"


def test_cross_tenant_claim_blocks_before_review():
    fact = _fact(C3_QUOTE)
    assert _reason(_build_c3(fact, tenant_id=OTHER_TENANT)) == "tenant_mismatch"


def test_unverified_or_foreign_evidence_fact_blocks():
    fact = _fact(C3_QUOTE, state="unknown", evidence_refs=(), citation_verified=False)
    review = _c3_review(_fact(C3_QUOTE))
    assert _reason(_build_c3(fact, review)) == "review_fact_not_verified"


def test_review_quote_must_equal_the_verified_evidence():
    fact = _fact(C3_QUOTE)
    tampered = (TriggerSourceBinding(SR_SOURCE, C3_QUOTE.replace("10조원", "20조원")),)
    review = _c3_review(fact, source_bindings=tampered)
    assert _reason(_build_c3(fact, review)) == "review_quote_mismatch"
    other = "88888888-8888-4888-8888-888888888888"
    review = _c3_review(fact, source_id=other, source_bindings=(TriggerSourceBinding(other, "x"),))
    assert _reason(_build_c3(fact, review)) == "review_source_not_fact_evidence"


def test_review_is_bound_to_its_item_and_never_duplicates_a_direct_trigger():
    fact = _fact(C3_QUOTE)
    assert _reason(_build_c3(fact, item="C4")) == "trigger_review_item_mismatch"
    direct = _fact(C3_QUOTE, name="currency_amount", normalized_value="10000000000000")
    tags = _confirmed_tags(facts=(fact, direct), track="goal")
    assert _reason(_build_c3(fact, tags=tags)) == "trigger_review_redundant"


def test_without_a_review_c3_and_c4_keep_the_existing_gap():
    fact = _fact(C3_QUOTE)
    result = _build_c3(fact, trigger_review=None)
    assert _reason(result) == "no_verified_trigger"


# --------------------------------------------------------------------------- #
# C3 semantics: never an arbitrary amount, never an ambiguous one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "quote, overrides, reason",
    [
        (
            "2030년까지 가상 설비투자 10조원 중 3조원을 집행한다.",
            {},
            "c3_amount_ambiguous",
        ),
        (
            "2030년까지 가상 매출 10조원과 설비투자 확대를 추진한다.",
            {},
            "c3_amount_meaning_ambiguous",
        ),
        (
            "2030년까지 가상 기부금 10조원을 조성한다.",
            {"investment_label": "기부금"},
            "c3_investment_meaning_not_in_source",
        ),
        (
            "가상 투자자 배당 10조원을 지급한다.",
            {"investment_label": "투자자"},
            "c3_investment_meaning_not_in_source",
        ),
        (C3_QUOTE, {"amount_literal": "20조원"}, "c3_amount_not_in_source"),
        (C3_QUOTE, {"normalized_amount": "1000000000000"}, "c3_amount_normalization_mismatch"),
        (C3_QUOTE, {"currency": "USD"}, "c3_amount_normalization_mismatch"),
        (
            "2030년까지 가상 설비투자 1조 2,000억원을 집행한다.",
            {"amount_literal": "2,000억원"},
            "c3_amount_ambiguous",
        ),
    ],
)
def test_c3_semantic_guards(quote, overrides, reason):
    fact = _fact(quote)
    assert _reason(_build_c3(fact, _c3_review(fact, **overrides))) == reason


def test_c3_requires_a_goal_claim():
    fact = _fact(C3_QUOTE)
    assert _reason(_build_c3(fact, tags=_tags(fact, track="performance"))) == (
        "c3_review_requires_goal_track"
    )


def test_c3_review_cannot_use_an_unlisted_fact():
    fact = _fact(C3_QUOTE, name="baseline_value")
    with pytest.raises(DomainValidationError):
        _c3_review(fact)


def test_capex_mapping_and_threshold_gates_stay_blocked_on_a_reviewed_packet():
    """REC-003/REC-004: the bridge types the trigger; the engine still refuses."""
    packet = _build_c3() | {"comparability": "comparable"}
    result = evaluate(packet, POLICY)  # mapping not approved
    assert (result["execution_state"], result["status"]) == ("blocked", None)
    assert result["reason_codes"] == ["c3_policy_unapproved"]

    mapped = POLICY | {
        "c3_account_mapping_approved": True,
        "allowed_capex_account_ids": ["synthetic-capex-account"],
    }
    result = evaluate(packet, mapped)  # threshold stays null, no commitment source
    assert result["reason_codes"] == ["c3_policy_unapproved"]
    assert mapped["c3_threshold"] is None

    unmapped = _build_c3(
        financial_context=_c3_financial(
            c3_context=dataclasses.replace(C3_CONTEXT, capex_account_ids=())
        )
    ) | {"comparability": "comparable"}
    assert evaluate(unmapped, mapped)["reason_codes"] == ["c3_policy_unapproved"]

    # The shipped builder never marks comparability; B's gate still blocks first.
    assert evaluate(_build_c3(), mapped)["reason_codes"] == ["comparability_unknown"]


# --------------------------------------------------------------------------- #
# C4 semantics: sales count is never revenue
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "quote, overrides, reason",
    [
        (
            "가상친환경차 판매 비중은 22.5%입니다.",
            {"revenue_label": "판매"},
            "c4_sales_count_not_revenue",
        ),
        ("가상친환경차 매출 비중 22.5%, 판매 대수 10만 대.", {}, "c4_sales_count_not_revenue"),
        ("Virtual EV sales volume share 22.5% of 매출", {}, "c4_sales_count_not_revenue"),
        (
            "가상친환경차 비중은 22.5%입니다.",
            {"revenue_label": "비중"},
            "c4_revenue_basis_not_in_source",
        ),
        ("가상친환경차 매출 비중은 2023년 15%에서 22.5%로 증가.", {}, "c4_share_ambiguous"),
        ("가상친환경차 매출이 증가했습니다.", {}, "c4_share_ambiguous"),
        (C4_QUOTE, {"classification_label": "가상전기차"}, "c4_classification_not_in_source"),
    ],
)
def test_c4_semantic_guards(quote, overrides, reason):
    fact = _fact(quote, name="quantitative_or_qualified_ordinal")
    assert _reason(_build_c4(fact, _c4_review(fact, **overrides))) == reason


def test_c4_classification_must_match_the_caller_context():
    fact = _fact(C4_QUOTE, name="quantitative_or_qualified_ordinal")
    result = _build_c4(
        fact, financial_context=_c4_financial(C4Context("다른 분류", ("fs-scope",), ()))
    )
    assert _reason(result) == "c4_classification_mismatch"


def test_c4_basis_rules_stay_with_the_engine():
    """REC-007: a reviewed trigger alone does not make the classification basis present."""
    packet = _build_c4() | {"comparability": "comparable"}
    result = evaluate(packet, POLICY)
    assert result["reason_codes"] == ["search_incomplete"]  # calculation basis not cited
    assert result["status"] is None


def test_financial_source_ids_remain_the_callers_and_unique():
    fact = _fact(C4_QUOTE, name="quantitative_or_qualified_ordinal")
    duplicate = _financial_context(
        financial=FinancialFact("x", "가상친환경차", "classification", None, "sr-" + SR_SOURCE),
        financial_sources=(
            FinancialSource("sr-" + SR_SOURCE, "fs-v1", "2" * 64, "physical_page=1", "x"),
        ),
        c4_context=C4Context("가상친환경차", (), ()),
    )
    assert _reason(_build_c4(fact, financial_context=duplicate)) == "duplicate_source_id"


def test_fixture_contract_dir_exists():
    assert Path(FIXTURE_CONTRACT_DIR, "input.schema.json").is_file()
