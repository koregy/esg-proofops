"""Opt-in, receipt-pinned deterministic P6 (``numerical_check``) fact derivation.

P6 ("본문 수치와 표 일치") is the output of the pure numeric check, never an LLM vote
(tagging forces it to ``unknown`` with ``DETERMINISTIC_CHECK_REQUIRED:P6``) and never
a typed review value (review requires an equal prior fact). This module is the
producer of that prior fact, mirroring ``assurance-link-v1``:

* the request pins the policy, the exact loader snapshot, the whole claim span and
  ONE explicitly accepted comparison binding (observation id, reported value span,
  metric/scope/subject/scope2 basis/organizational boundary/unit/denominator/
  reporting period/quantity kind). Nothing is inferred: the domain check requires
  every declared dimension to equal the table observation exactly, with no unit
  conversion beyond the normalizer's own scale rule;
* observations are recomputed from the replayed original graph with
  ``normalize_tables`` (tables only, so a narrative span is never an observation)
  and promoted to ``verified`` only when every own source ref re-verifies; the pure
  domain check re-validates all of it again;
* an observation sourced from the claim's own block is refused (no self-proof);
* ``consistent`` -> ``numerical_check`` present (normalized ``consistent``);
  ``inconsistent`` -> ``conflict`` (normalized ``inconsistent``), following the
  GAP-003 row of 31장 (a verified same-scope mismatch marks the numeric fact
  conflict). ``not_computable``/``not_comparable``/``needs_review`` produce no fact:
  P6 stays ``unknown``, never ``absent``, and the receipt keeps the reason.

P6 is a §4.5 additional element (R00 §7 A-2): it never changes E/label/range.
The receipt is a pure function of (request, inputs) and is recomputed on every
carried re-review or reader check; any difference is a replay mismatch.
"""

from __future__ import annotations

from dataclasses import asdict, replace

from proofops.application.evidence.span_citations import verify_source_ref
from proofops.application.ingest.normalize import normalize_tables
from proofops.application.numeric_analysis import analyze_numeric_consistency
from proofops.domain.numeric import ClaimBinding
from proofops.domain.provenance import canonical_hash
from proofops.domain.rules.engine import ConfirmedFact
from proofops.domain.values import _source_ref_from_dict

POLICY = "numeric-link-v1"
FACT = "numerical_check"
ELEMENT = "P6"
SOURCE_SCOPE = "computed_check"
STATE_BY_STATUS = {"consistent": "present", "inconsistent": "conflict"}
POLICY_HASH = canonical_hash(
    {
        "policy": POLICY,
        "element": ELEMENT,
        "fact": FACT,
        "source_scope": SOURCE_SCOPE,
        "kinds": ["comparison"],
        "observations": "normalize_tables(replayed original); verified refs only",
        "self_comparison": "refused",
        "state_by_status": STATE_BY_STATUS,
        "undecided_statuses": "no fact; P6 unknown; never absent",
        "grade_effect": "none (R00 §7 A-2 additional element)",
        "dimension_grounding": (
            "every declared dimension equals a verified span inside the claim or a "
            "validated ClaimContext dimension; the caller never picks the row"
        ),
    }
)
REQUEST_KEYS = frozenset(
    {"policy", "policy_hash", "input_snapshot_sha256", "claim_source_refs", "binding"}
)
BINDING_KEYS = frozenset(
    {
        "kind",
        "observation_id",
        "reported_value",
        "reported_value_ref",
        "metric_raw",
        "scope",
        "subject",
        "scope2_basis",
        "organizational_boundary",
        "unit",
        "denominator",
        "reporting_period",
        "quantity_kind",
        "dimension_refs",
    }
)
# Declared dimensions that must each be grounded in the claim itself.
GROUNDED_FIELDS = (
    "metric_raw",
    "scope",
    "subject",
    "scope2_basis",
    "organizational_boundary",
    "unit",
    "denominator",
    "reporting_period",
)
# Validated ClaimContext roles that may ground the same field (numeric_analysis roles).
CONTEXT_ROLES = {
    "metric": "metric_raw",
    "entity": "subject",
    "reporting_period": "reporting_period",
    "scope": "scope",
    "boundary": "organizational_boundary",
}


class NumericLinkRejected(ValueError):
    """The request cannot support a P6 derivation; nothing may be published."""


def verified_observations(inputs):
    """Table observations of the replayed graph; ``verified`` only if every ref verifies."""
    original, tenant = inputs.original, inputs.context.claim.tenant_id
    result = []
    for item in normalize_tables(original, tenant_id=tenant).observations:
        refs = tuple(
            verify_source_ref(ref, original, tenant_id=tenant)
            for ref in item.source_refs
            if ref.quote.strip()
        )
        usable = (
            item.quality == "unverified"
            and refs
            and all(
                ref.verification_state == "verified" and ref.location_quality == "located"
                for ref in refs
            )
        )
        result.append(
            replace(item, source_refs=refs, quality="verified" if usable else item.quality)
        )
    return tuple(result)


def build_request(inputs, binding: dict) -> dict:
    """Trusted operator helper: pin the loader snapshot, whole claim and one binding."""
    return dict(
        policy=POLICY,
        policy_hash=POLICY_HASH,
        input_snapshot_sha256=canonical_hash(inputs.snapshot()),
        claim_source_refs=[asdict(ref) for ref in inputs.context.claim.source_refs],
        binding=dict(binding),
    )


def _validate(inputs, request) -> dict:
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise NumericLinkRejected("NUMERIC_REQUEST_INVALID")
    if (request["policy"], request["policy_hash"]) != (POLICY, POLICY_HASH):
        raise NumericLinkRejected("NUMERIC_POLICY_MISMATCH")
    if request["input_snapshot_sha256"] != canonical_hash(inputs.snapshot()):
        raise NumericLinkRejected("NUMERIC_STALE_INPUTS")
    claim = inputs.context.claim
    if not claim.source_refs or canonical_hash(request["claim_source_refs"]) != canonical_hash(
        [asdict(ref) for ref in claim.source_refs]
    ):
        raise NumericLinkRejected("WHOLE_CLAIM_REQUIRED")
    if claim.source_quality != "verified":
        raise NumericLinkRejected("NUMERIC_CLAIM_SOURCE_REQUIRED")
    if inputs.packet.to_dict()["track"] != "performance":
        raise NumericLinkRejected("NUMERIC_TRACK_MISMATCH")
    binding = request["binding"]
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
    if binding["kind"] != "comparison" or binding["quantity_kind"] not in ("absolute", "intensity"):
        raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
    for key in BINDING_KEYS - {"reported_value_ref", "kind", "quantity_kind", "dimension_refs"}:
        value = binding[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
    if not all(binding[k] for k in ("observation_id", "reported_value", "metric_raw", "unit")):
        raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
    if not binding["reporting_period"]:
        raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
    if not isinstance(binding["reported_value_ref"], dict):
        raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
    if not isinstance(binding["dimension_refs"], dict) or set(binding["dimension_refs"]) - set(
        GROUNDED_FIELDS
    ):
        raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
    return binding


def _grounded_dimensions(inputs, spec) -> dict:
    """Every declared dimension must be the claim's own verified literal.

    A declared value is accepted only when an explicit span inside the claim's own
    atomic source (re-verified against the original) or a validated ClaimContext
    dimension carries exactly that text. Otherwise the caller could choose which
    table row to compare against, so the request is refused.
    """
    from proofops.application.assurance import _validated_dimension_text

    claim, original = inputs.context.claim, inputs.original
    context_values: dict[str, set[str]] = {}
    dimensions = getattr(inputs.context, "dimensions", None) or {}
    for role, field in CONTEXT_ROLES.items():
        ref = dimensions.get(role) if hasattr(dimensions, "get") else None
        text = (
            _validated_dimension_text(
                ref, claim=claim, original=original, tenant_id=claim.tenant_id
            )
            if ref is not None
            else None
        )
        if text:
            context_values.setdefault(field, set()).add(text)
    grounded: dict = {}
    for field in GROUNDED_FIELDS:
        value, raw = spec[field], spec["dimension_refs"].get(field)
        if field in context_values and value not in context_values[field]:
            # A caller cannot bypass an already validated claim dimension by
            # selecting another literal span from the same narrative paragraph.
            raise NumericLinkRejected(f"NUMERIC_CONTEXT_CONFLICT:{field}")
        if value is None:
            if raw is not None:
                raise NumericLinkRejected("NUMERIC_BINDING_INVALID")
            continue
        if raw is not None:
            try:
                ref = _source_ref_from_dict(raw)
            except (TypeError, ValueError, KeyError) as exc:
                raise NumericLinkRejected("NUMERIC_BINDING_INVALID") from exc
            text = _validated_dimension_text(
                ref, claim=claim, original=original, tenant_id=claim.tenant_id
            )
            if text == value:
                checked = verify_source_ref(ref, original, tenant_id=claim.tenant_id)
                grounded[field] = dict(source="claim_span", ref=asdict(checked))
                continue
        elif value in context_values.get(field, ()):
            grounded[field] = dict(source="claim_context", text=value)
            continue
        raise NumericLinkRejected(f"NUMERIC_DIMENSION_UNGROUNDED:{field}")
    return grounded


def derive_numeric_fact(inputs, request) -> tuple[ConfirmedFact | None, dict]:
    """Return the P6 fact only for a decided, source-verified comparison.

    Raises ``NumericLinkRejected`` for an invalid/stale request, an observation that
    no longer exists in the replayed graph, or a self-comparison. Otherwise returns
    ``(None, receipt)`` when the check is undecided; the receipt keeps the reason.
    """
    spec = _validate(inputs, request)
    grounded = _grounded_dimensions(inputs, spec)
    claim, original = inputs.context.claim, inputs.original
    observations = verified_observations(inputs)
    observation = next(
        (o for o in observations if o.observation_id == spec["observation_id"]), None
    )
    if observation is None:
        raise NumericLinkRejected("NUMERIC_OBSERVATION_UNAVAILABLE")
    claim_sources = {ref.source_id for ref in claim.source_refs}
    if {ref.source_id for ref in observation.source_refs} & claim_sources or {
        block.source_id for block in observation.source_blocks
    } & claim_sources:
        raise NumericLinkRejected("NUMERIC_SELF_COMPARISON")
    try:
        number_ref = _source_ref_from_dict(spec["reported_value_ref"])
    except (TypeError, ValueError, KeyError) as exc:
        raise NumericLinkRejected("NUMERIC_BINDING_INVALID") from exc
    number_ref = verify_source_ref(number_ref, original, tenant_id=claim.tenant_id)
    binding = ClaimBinding(
        claim_id=claim.claim_id,
        tenant_id=claim.tenant_id,
        document_version_id=claim.document_version_id,
        parse_manifest_id=original.parse_manifest_id,
        kind="comparison",
        observation_ids=(observation.observation_id,),
        reported_value=spec["reported_value"],
        metric_raw=spec["metric_raw"],
        scope=spec["scope"],
        subject=spec["subject"],
        scope2_basis=spec["scope2_basis"],
        organizational_boundary=spec["organizational_boundary"],
        unit=spec["unit"],
        denominator=spec["denominator"],
        reporting_period=spec["reporting_period"],
        quantity_kind=spec["quantity_kind"],
        source_refs=tuple(claim.source_refs),
        reported_value_ref=number_ref,
        binding_accepted=True,
    )
    report = analyze_numeric_consistency(
        tenant_id=claim.tenant_id,
        original=original,
        observations=observations,
        bindings=(binding,),
        claims=(claim,),
    )
    outcome = next(o for o in report.outcomes if o.claim_id == claim.claim_id)
    result = outcome.result
    state = STATE_BY_STATUS.get(outcome.status)
    refs = ()
    if result is not None:
        refs = tuple(
            verify_source_ref(ref, original, tenant_id=claim.tenant_id)
            for ref in result.source_refs
        )
    if state is not None and (
        not refs
        or any(r.verification_state != "verified" or r.location_quality != "located" for r in refs)
    ):
        state = None  # a decided result must stay fully source-verified
    receipt = dict(
        policy=POLICY,
        policy_hash=POLICY_HASH,
        request=request,
        request_sha256=canonical_hash(request),
        identity=dict(
            tenant_id=claim.tenant_id,
            run_id=inputs.run_id,
            claim_id=claim.claim_id,
            document_version_id=claim.document_version_id,
            parse_manifest_id=original.parse_manifest_id,
            source_sha256=original.source_sha256,
            graph_sha256=canonical_hash(asdict(original)),
            packet_sha256=inputs.packet.packet_sha256,
            rulepack_sha256=inputs.rulepack.sha256,
        ),
        grounded_dimensions=grounded,
        observation=dict(
            observation_id=observation.observation_id,
            table_id=observation.table_id,
            quality=observation.quality,
            value_raw=observation.value_raw,
            unit_canonical=observation.unit_canonical,
            reporting_period=observation.reporting_period,
            metric_raw=observation.metric_raw,
            source_refs=[asdict(ref) for ref in observation.source_refs],
        ),
        result=dict(
            status=outcome.status,
            reason=outcome.reason,
            reported_value=result.reported_value if result else None,
            computed_value=result.computed_value if result else None,
            exact_ratio=list(result.exact_ratio) if result and result.exact_ratio else None,
            holds=list(outcome.holds),
        ),
        evidence_refs=[asdict(ref) for ref in refs] if state else [],
        fact_state=state or "unknown",
        grade_effect="none",
    )
    receipt["receipt_sha256"] = canonical_hash(receipt)
    if state is None:
        return None, receipt
    fact = ConfirmedFact(
        FACT,
        state,
        refs,
        claim.tenant_id,
        True,
        True,
        source_scope=SOURCE_SCOPE,
        normalized_value=outcome.status,
    )
    return fact, receipt


def p6_element(fact: ConfirmedFact) -> dict:
    """The only P6 body element a reviewer can submit for a derived fact."""
    return dict(
        element_id=ELEMENT,
        state=fact.state,
        evidence_refs=[asdict(ref) for ref in fact.evidence_refs],
        normalized_value=fact.normalized_value,
        credited_from=None,
        reason_code=POLICY,
    )


def replay_numeric_receipt(inputs, prior: dict) -> tuple[ConfirmedFact, dict]:
    """Recompute a carried receipt against the current source and snapshot."""
    if not isinstance(prior, dict) or not isinstance(prior.get("request"), dict):
        raise NumericLinkRejected("NUMERIC_REPLAY_MISMATCH")
    stored = {k: v for k, v in prior.items() if k not in ("receipt_sha256", "carried_from")}
    if prior.get("receipt_sha256") != canonical_hash(stored):
        raise NumericLinkRejected("NUMERIC_REPLAY_MISMATCH")
    request = dict(prior["request"], input_snapshot_sha256=canonical_hash(inputs.snapshot()))
    if request != prior["request"]:
        raise NumericLinkRejected("NUMERIC_REPLAY_MISMATCH")
    fact, receipt = derive_numeric_fact(inputs, request)
    if fact is None or receipt["receipt_sha256"] != prior["receipt_sha256"]:
        raise NumericLinkRejected("NUMERIC_REPLAY_MISMATCH")
    return fact, receipt
