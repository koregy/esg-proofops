"""Source-bound C3/C4 trigger review bridge for the linkage exchange (R08a).

No tagging producer emits the contract's `currency_amount` (C3) or
`revenue_share` (C4) trigger facts. SCHEMA_GUIDE ("기존 태그 → 신규 trigger 변환")
says the conversion is never a tag rename: the source fact AND a trigger decision
receipt must be kept. This module is that receipt path, mirroring the C1
`C1EntitySetReview` bridge in `linkage_exchange`:

* a review is pinned to one tenant, company, run, claim, document version, the
  ACCEPTED tag revision, the SHA-256 of the exact confirmed fact it reads
  (`fact_sha256`) and the claim's source bytes hash (`source_sha256`); any drift is
  a stale-review block (compare-and-set), never a repair;
* it may only read a confirmed `present` fact that carries verified evidence and
  an accepted binding, and only through that fact's own evidence quotes;
* every semantic literal (amount, investment wording, classification, revenue
  wording, share) must be an exact substring of one bound quote, and the value
  is re-derived here by a deterministic parser, not taken from the reviewer.

Deliberately refused, never guessed:

* C3: an amount without explicit investment/expenditure wording in the same
  quote; a quote carrying more than one currency amount, or revenue/sales
  wording (the amount's meaning is then ambiguous); a non-goal claim; any
  unsupported literal shape (e.g. compound `1조 2,000억원`).
* C4: any sale-count wording (`판매`, `대수`, `units sold`, `sales volume` ...),
  since a sales-volume share is not a revenue share; a quote without explicit
  revenue wording, with no or several percentages, or whose classification label
  differs from the caller's `c4_context.classification_name`.

It does not map financial accounts to CAPEX (REC-003), set a C3 threshold
(REC-004) or decide classification correctness (REC-007): those stay on the
policy/engine side and remain blocked there until separately approved. An
AI-delegated review is recorded as such (R00 §6) and is never presented as
human, legal or accounting approval or as independent gold.

Pure module: no filesystem, no network, no model call.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from datetime import date
from decimal import Decimal
from typing import Any

from proofops.application.claims import Claim
from proofops.domain.errors import DomainValidationError
from proofops.domain.rules.engine import ConfirmedFact, ConfirmedTags
from proofops.domain.values import _require_sha256, _require_uuid

TRIGGER_REVIEW_RECEIPT_SCHEMA = "linkage-trigger-review-1"
TRIGGER_REVIEW_ORIGINS = {
    "human": "human_trigger_review",
    "ai_delegated": "ai_delegated_trigger_review_not_independent_gold",
}
# Existing primitives whose confirmed sentence MAY state the trigger. The review,
# not the fact name, decides; an unlisted fact can never be reviewed into one.
C3_BASE_FACT_NAMES = ("target_metric", "transition_plan")
C4_BASE_FACT_NAMES = ("target_metric", "current_progress", "quantitative_or_qualified_ordinal")
TRIGGER_BY_ITEM = {"C3": "currency_amount", "C4": "revenue_share"}

_NUMBER = r"(?P<num>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.(?P<frac>[0-9]+))?"
_KR_MULTIPLIERS = {
    None: 1,
    "천": 10**3,
    "만": 10**4,
    "백만": 10**6,
    "천만": 10**7,
    "억": 10**8,
    "십억": 10**9,
    "조": 10**12,
}
_EN_MULTIPLIERS = {None: 1, "thousand": 10**3, "million": 10**6, "billion": 10**9}
_SUFFIX_AMOUNT = re.compile(
    _NUMBER + r"\s*(?P<mult>조|십억|억|천만|백만|만|천)?\s*(?P<cur>원|KRW|달러|USD)(?![A-Za-z])"
)
_PREFIX_AMOUNT = re.compile(
    r"(?P<cur>\$|USD\s?)" + _NUMBER + r"(?:\s*(?P<mult>thousand|million|billion))?(?![A-Za-z])"
)
_CURRENCY = {"원": "KRW", "KRW": "KRW", "달러": "USD", "USD": "USD", "$": "USD"}
_INVESTMENT = re.compile(r"투자(?!자)|CAPEX|Capex|capex|capital expenditure|자본적\s*지출|지출")
_AMOUNT_MEANING_CONFLICT = re.compile(r"매출|수익|판매|revenue|Revenue|sales|Sales")
_REVENUE = re.compile(r"매출|수익|revenue|Revenue")
_SALE_COUNT = re.compile(
    r"판매|대수|units?\s+sold|unit\s+sales|sales\s+volume|volume", re.IGNORECASE
)
_PERCENT = re.compile(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?\s*%")


def parse_amount_literal(literal: str) -> tuple[str, str]:
    """Exact `(normalized Decimal string, ISO currency)` for one supported literal.

    Supported: `<number>[조|십억|억|천만|백만|만|천]<원|KRW|달러|USD>` and
    `$|USD <number> [thousand|million|billion]`. Anything else raises.
    """
    if not isinstance(literal, str):
        raise DomainValidationError("amount literal must be text")
    stripped = literal.strip()
    match = _SUFFIX_AMOUNT.fullmatch(stripped)
    multipliers: dict[str | None, int] = _KR_MULTIPLIERS
    if match is None:
        match = _PREFIX_AMOUNT.fullmatch(stripped)
        multipliers = _EN_MULTIPLIERS
    if match is None or stripped != literal:
        raise DomainValidationError(f"unsupported amount literal: {literal!r}")
    number = match["num"].replace(",", "") + (f".{match['frac']}" if match["frac"] else "")
    value = Decimal(number) * multipliers[match["mult"]]
    if value <= 0:
        raise DomainValidationError("amount must be positive")
    normalized = format(value.normalize(), "f")
    return normalized, _CURRENCY[match["cur"].strip()]


_SCALED_NUMBER = re.compile(r"[0-9][0-9,.]*\s*(?:조|십억|억|천만|백만|만|천)")


def _amount_spans(text: str) -> list[tuple[int, int]]:
    """Currency amounts, plus any scaled number outside them (e.g. `1조` in `1조 2,000억원`)."""
    spans = {m.span() for m in _SUFFIX_AMOUNT.finditer(text)}
    spans |= {m.span() for m in _PREFIX_AMOUNT.finditer(text)}
    for match in _SCALED_NUMBER.finditer(text):
        if not any(start <= match.start() and match.end() <= end for start, end in spans):
            spans.add(match.span())
    return sorted(spans)


def fact_sha256(fact: ConfirmedFact) -> str:
    """CAS identity of one confirmed fact, including every evidence ref field."""
    from proofops.domain.reconciliation.engine import canonical_sha256

    return canonical_sha256(asdict(fact))


@dataclass(frozen=True, slots=True)
class TriggerSourceBinding:
    """One exact evidence ref (raw SourceRef.source_id + quote) the reviewer read."""

    source_id: str
    quote: str

    def __post_init__(self) -> None:
        _require_uuid("review source_id", self.source_id)
        if not isinstance(self.quote, str) or not self.quote:
            raise DomainValidationError("review source quote required")


def _literal(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise DomainValidationError(f"review {name} must be trimmed nonempty text")


@dataclass(frozen=True, slots=True)
class _TriggerReviewPins:
    synthetic: bool
    tenant_id: str
    company_id: str
    run_id: str
    claim_id: str
    document_version_id: str
    source_sha256: str
    tag_revision: int
    fact_name: str
    fact_value: str
    fact_sha256: str
    source_id: str
    source_bindings: tuple[TriggerSourceBinding, ...]
    review_id: str
    reviewed_by: str
    reviewed_at: str
    review_origin: str

    def _validate_pins(self, allowed_facts: tuple[str, ...]) -> None:
        if self.review_origin not in TRIGGER_REVIEW_ORIGINS:
            raise DomainValidationError(
                f"review_origin must be one of {sorted(TRIGGER_REVIEW_ORIGINS)}"
            )
        if type(self.synthetic) is not bool:
            raise DomainValidationError("review synthetic flag must be boolean")
        for name in ("tenant_id", "claim_id", "document_version_id", "source_id"):
            _require_uuid(f"review {name}", getattr(self, name))
        for name in ("source_sha256", "fact_sha256"):
            _require_sha256(f"review {name}", getattr(self, name))
        for name in ("company_id", "run_id", "fact_value", "review_id", "reviewed_by"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise DomainValidationError(f"review {name} required")
        if type(self.tag_revision) is not int or self.tag_revision < 1:
            raise DomainValidationError("review tag_revision must be a positive int")
        if self.fact_name not in allowed_facts:
            raise DomainValidationError(f"review fact_name must be one of {allowed_facts}")
        date.fromisoformat(self.reviewed_at)
        bindings = tuple(self.source_bindings)
        if not bindings or any(not isinstance(b, TriggerSourceBinding) for b in bindings):
            raise DomainValidationError("review source_bindings must be nonempty")
        if len({b.source_id for b in bindings}) != len(bindings):
            raise DomainValidationError("review source_bindings must be unique")
        if self.source_id not in {b.source_id for b in bindings}:
            raise DomainValidationError("review source_id must be one of its source_bindings")
        object.__setattr__(self, "source_bindings", bindings)

    def bound_quote(self) -> str:
        return next(b.quote for b in self.source_bindings if b.source_id == self.source_id)


@dataclass(frozen=True, slots=True)
class C3CurrencyTriggerReview(_TriggerReviewPins):
    """Reviewer asserts ONE literal investment/expenditure amount in one bound quote."""

    amount_literal: str
    investment_label: str
    currency: str
    normalized_amount: str

    item = "C3"

    def __post_init__(self) -> None:
        self._validate_pins(C3_BASE_FACT_NAMES)
        for name in ("amount_literal", "investment_label", "currency", "normalized_amount"):
            _literal(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class C4RevenueTriggerReview(_TriggerReviewPins):
    """Reviewer asserts ONE revenue share of one named classification in one bound quote."""

    classification_label: str
    revenue_label: str
    share_literal: str

    item = "C4"

    def __post_init__(self) -> None:
        self._validate_pins(C4_BASE_FACT_NAMES)
        for name in ("classification_label", "revenue_label", "share_literal"):
            _literal(name, getattr(self, name))


TriggerReview = C3CurrencyTriggerReview | C4RevenueTriggerReview
_REVIEW_CLASSES = {"C3": C3CurrencyTriggerReview, "C4": C4RevenueTriggerReview}


def trigger_review_from_dict(data: dict[str, Any]) -> TriggerReview:
    """Strict JSON loader: exact keys for the declared item, no extras, no defaults."""
    if not isinstance(data, dict) or data.get("item") not in _REVIEW_CLASSES:
        raise DomainValidationError("trigger review item must be C3 or C4")
    cls = _REVIEW_CLASSES[data["item"]]
    expected = {f.name for f in fields(cls)} | {"item"}
    if set(data) != expected:
        raise DomainValidationError(
            f"trigger review keys differ: missing {sorted(expected - set(data))}, "
            f"unexpected {sorted(set(data) - expected)}"
        )
    values = {k: v for k, v in data.items() if k not in ("item", "source_bindings")}
    if not isinstance(data["source_bindings"], list):
        raise DomainValidationError("review source_bindings must be a list")
    return cls(
        **values,
        source_bindings=tuple(TriggerSourceBinding(**b) for b in data["source_bindings"]),
    )


def trigger_review_to_dict(review: TriggerReview) -> dict[str, Any]:
    return dict(
        asdict(review),
        item=review.item,
        source_bindings=[asdict(b) for b in review.source_bindings],
    )


@dataclass(frozen=True, slots=True)
class ReviewedTrigger:
    """The single trigger a checked review yields; fed to `build_packet` only."""

    fact_name: str
    trigger_element: str
    evidence_refs: tuple
    normalized_value: str
    unit: str | None
    fact_sha256: str


def _derive_c3(review: C3CurrencyTriggerReview, quote: str) -> tuple[str, str] | ReviewedValue:
    if review.amount_literal not in quote or quote.count(review.amount_literal) != 1:
        return "c3_amount_not_in_source", "amount literal must occur exactly once in the quote"
    spans = _amount_spans(quote)
    if len(spans) != 1:
        return (
            "c3_amount_ambiguous",
            f"bound quote carries {len(spans)} currency amounts; one amount per reviewed quote",
        )
    start = quote.index(review.amount_literal)
    if spans[0] != (start, start + len(review.amount_literal)):
        return "c3_amount_ambiguous", "amount literal is not the quote's whole currency amount"
    if _AMOUNT_MEANING_CONFLICT.search(quote):
        return (
            "c3_amount_meaning_ambiguous",
            "quote also carries revenue/sales wording; the amount is not provably an investment",
        )
    if review.investment_label not in quote or not _INVESTMENT.search(review.investment_label):
        return (
            "c3_investment_meaning_not_in_source",
            "an explicit investment/expenditure label must appear literally in the same quote",
        )
    try:
        normalized, currency = parse_amount_literal(review.amount_literal)
    except DomainValidationError as exc:
        return "c3_amount_unsupported", str(exc)
    if (normalized, currency) != (review.normalized_amount, review.currency):
        return (
            "c3_amount_normalization_mismatch",
            f"literal parses to {normalized} {currency}, review states "
            f"{review.normalized_amount} {review.currency}",
        )
    return ReviewedValue(normalized, currency)


def _derive_c4(
    review: C4RevenueTriggerReview, quote: str, classification_name: str | None
) -> tuple[str, str] | ReviewedValue:
    if _SALE_COUNT.search(quote):
        return (
            "c4_sales_count_not_revenue",
            "quote states sales/unit volume wording; a sales-count share is never a revenue share",
        )
    if review.revenue_label not in quote or not _REVENUE.search(review.revenue_label):
        return (
            "c4_revenue_basis_not_in_source",
            "an explicit revenue label (매출/수익/revenue) must appear literally in the quote",
        )
    if review.classification_label not in quote:
        return "c4_classification_not_in_source", "classification label is not in the quote"
    percents = [m.group() for m in _PERCENT.finditer(quote)]
    if len(percents) != 1 or percents[0] != review.share_literal:
        return (
            "c4_share_ambiguous",
            f"quote must carry exactly one percentage equal to the share literal (found "
            f"{percents})",
        )
    if classification_name is not None and classification_name != review.classification_label:
        return (
            "c4_classification_mismatch",
            "c4_context.classification_name differs from the reviewed source label",
        )
    return ReviewedValue(review.classification_label, None)


@dataclass(frozen=True, slots=True)
class ReviewedValue:
    normalized: str
    unit: str | None


def check_trigger_review(
    review: TriggerReview,
    *,
    item: str,
    claim: Claim,
    tags: ConfirmedTags,
    tenant_id: str,
    company_id: str,
    run_id: str | None,
    synthetic: bool,
    classification_name: str | None = None,
) -> ReviewedTrigger | tuple[str, str]:
    """Return the reviewed trigger, or a `(reason, detail)` refusal. Never repairs."""
    if not isinstance(review, C3CurrencyTriggerReview | C4RevenueTriggerReview):
        return "invalid_trigger_review", "expected a C3/C4 trigger review"
    if review.item != item:
        return "trigger_review_item_mismatch", f"{review.item} review cannot build {item}"
    pins = (
        ("review_synthetic_mismatch", review.synthetic, synthetic),
        ("review_tenant_mismatch", review.tenant_id, tenant_id),
        ("review_company_mismatch", review.company_id, company_id),
        ("review_run_mismatch", review.run_id, run_id),
        ("review_claim_mismatch", review.claim_id, claim.claim_id),
        ("review_version_mismatch", review.document_version_id, claim.document_version_id),
        ("review_source_hash_stale", review.source_sha256, claim.source_sha256),
        ("review_revision_stale", review.tag_revision, tags.tag_revision),
    )
    for reason, pinned, trusted in pins:
        if pinned != trusted:
            return reason, f"review pins {pinned!r} but the trusted run has {trusted!r}"
    if item == "C3" and tags.track != "goal":
        return "c3_review_requires_goal_track", "C3 investment triggers apply to goal claims only"
    matches = [f for f in tags.facts if f.name == review.fact_name]
    if len(matches) != 1:
        return (
            "review_fact_not_unique",
            f"{review.fact_name} must occur exactly once on the accepted head",
        )
    fact = matches[0]
    if not (
        fact.state == "present"
        and fact.citation_verified
        and fact.binding_accepted
        and fact.source_tenant_id == tenant_id
        and fact.evidence_refs
    ):
        return (
            "review_fact_not_verified",
            f"{review.fact_name} is not a confirmed present fact with verified evidence",
        )
    if fact_sha256(fact) != review.fact_sha256 or fact.normalized_value != review.fact_value:
        return "review_fact_stale", "the confirmed fact changed since review; re-review it"
    evidence = {ref.source_id: ref for ref in fact.evidence_refs}
    for binding in review.source_bindings:
        ref = evidence.get(binding.source_id)
        if ref is None:
            return (
                "review_source_not_fact_evidence",
                f"review source {binding.source_id} is not an evidence ref of {review.fact_name}",
            )
        if ref.quote != binding.quote:
            return "review_quote_mismatch", f"quote for {binding.source_id} differs from evidence"
        if ref.document_version_id != claim.document_version_id:
            return "version_mismatch", "evidence ref belongs to another document version"
    quote = review.bound_quote()
    derived = (
        _derive_c3(review, quote)
        if isinstance(review, C3CurrencyTriggerReview)
        else _derive_c4(review, quote, classification_name)
    )
    if not isinstance(derived, ReviewedValue):
        return derived
    bound_ids = {b.source_id for b in review.source_bindings}
    # The quote holding the literal leads, so it becomes the packet's raw value.
    refs = sorted(
        (ref for ref in fact.evidence_refs if ref.source_id in bound_ids),
        key=lambda ref: ref.source_id != review.source_id,
    )
    return ReviewedTrigger(
        fact_name=fact.name,
        trigger_element=TRIGGER_BY_ITEM[item],
        evidence_refs=tuple(refs),
        normalized_value=derived.normalized,
        unit=derived.unit,
        fact_sha256=review.fact_sha256,
    )


def trigger_review_receipt(
    review: TriggerReview, packet: dict[str, Any], trigger: ReviewedTrigger
) -> dict[str, Any]:
    """Separate approval-record envelope; strict1.1 packets never carry extra fields."""
    from proofops.domain.reconciliation.engine import canonical_sha256

    body = trigger_review_to_dict(review)
    return dict(
        receipt_schema_version=TRIGGER_REVIEW_RECEIPT_SCHEMA,
        item=review.item,
        packet_sha256=canonical_sha256(packet),
        review_sha256=canonical_sha256(body),
        synthetic=review.synthetic,
        review_kind=TRIGGER_REVIEW_ORIGINS[review.review_origin],
        review=body,
        derived=dict(
            trigger_element=trigger.trigger_element,
            normalized=trigger.normalized_value,
            unit=trigger.unit,
            fact_sha256=trigger.fact_sha256,
        ),
        not_decided=[
            "REC-003 CAPEX account mapping",
            "REC-004 C3 threshold",
            "REC-007 classification correctness",
            "comparability and explanation search",
        ],
    )
