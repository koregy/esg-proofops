"""Opt-in, statement-pinned deterministic P4 (``assurance_covered``) fact derivation.

P4 is the output of the assurance matcher, never an LLM vote (tagging forces it to
unknown) and never a typed review value (review requires an equal prior fact). This
module is the only producer of that prior fact, and it works on a trusted review
revision instead of mutating a run or the frozen ``ReviewInputs`` snapshot:

* the request pins the policy, the exact loader snapshot, ONE published statement
  (id + semantic hash) and the whole claim source span;
* the statement is re-extracted from its stored tagged source refs against the
  review inputs' own original graph, so a statement from another graph, tenant,
  version or a mutated statement cannot be used;
* claim metric/period/entity/facility come only from the claim's own source-bound
  preliminary dimensions (``claim_context_from_review_inputs``);
* ``match_assurance`` compares one opinion at a time; explicit exclusions,
  unresolved statement fields, partial scope or unknown dimensions are never
  ``covered``, so no fact is produced and P4 stays ``unknown`` with the reasons.

The receipt is a pure function of (request, statement, inputs) and is recomputed on
every carried re-review; any difference is a replay mismatch.
"""

from __future__ import annotations

from dataclasses import asdict

from proofops.application.assurance import (
    MATCH_RULES,
    AssuranceStatement,
    claim_context_from_review_inputs,
    extract_assurance,
    match_assurance,
)
from proofops.application.evidence.span_citations import verify_source_ref
from proofops.domain.provenance import canonical_hash
from proofops.domain.rules.engine import ConfirmedFact

POLICY = "assurance-link-v1"
FACT = "assurance_covered"
ELEMENT = "P4"
POLICY_HASH = canonical_hash(
    {
        "policy": POLICY,
        "element": ELEMENT,
        "fact": FACT,
        "normalized_value": "covered",
        "source_scope": "global_bound",
        "statements_per_match": 1,
        "match_rules_sha256": canonical_hash(MATCH_RULES),
    }
)
REQUEST_KEYS = frozenset(
    {
        "policy",
        "policy_hash",
        "input_snapshot_sha256",
        "statement_id",
        "statement_semantic_hash",
        "claim_source_refs",
    }
)


class AssuranceLinkRejected(ValueError):
    """The request or statement is not usable; nothing may be published."""


def build_request(inputs, statement: AssuranceStatement) -> dict:
    """Trusted operator helper: pin the exact loader snapshot and statement."""
    return dict(
        policy=POLICY,
        policy_hash=POLICY_HASH,
        input_snapshot_sha256=canonical_hash(inputs.snapshot()),
        statement_id=statement.statement_id,
        statement_semantic_hash=statement.semantic_hash,
        claim_source_refs=[asdict(ref) for ref in inputs.context.claim.source_refs],
    )


def _validate_request(inputs, request) -> None:
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise AssuranceLinkRejected("ASSURANCE_REQUEST_INVALID")
    if (request["policy"], request["policy_hash"]) != (POLICY, POLICY_HASH):
        raise AssuranceLinkRejected("ASSURANCE_POLICY_MISMATCH")
    if request["input_snapshot_sha256"] != canonical_hash(inputs.snapshot()):
        raise AssuranceLinkRejected("ASSURANCE_STALE_INPUTS")
    claim = inputs.context.claim
    if not claim.source_refs or canonical_hash(request["claim_source_refs"]) != canonical_hash(
        [asdict(ref) for ref in claim.source_refs]
    ):
        raise AssuranceLinkRejected("WHOLE_CLAIM_REQUIRED")
    if inputs.packet.to_dict()["track"] != "performance":
        raise AssuranceLinkRejected("ASSURANCE_TRACK_MISMATCH")


def _reextract(statement: AssuranceStatement, graph) -> AssuranceStatement:
    """Rebuild the statement from its own tagged refs against ``graph`` (raises if absent)."""
    return extract_assurance(
        graph,
        statement.source_refs,
        statement.binding,
        tagged_fields={name: refs for name, refs in statement.tagged_fields if refs},
        tenant_id=statement.tenant_id,
        statement_id=statement.statement_id,
        model_sha256=statement.model_sha256,
        prompt_sha256=statement.prompt_sha256,
        replicate_id=statement.replicate_id,
    )


def _check_statement(inputs, request, statement) -> None:
    if not isinstance(statement, AssuranceStatement):
        raise AssuranceLinkRejected("ASSURANCE_STATEMENT_UNAVAILABLE")
    if (statement.statement_id, statement.semantic_hash) != (
        request["statement_id"],
        request["statement_semantic_hash"],
    ):
        raise AssuranceLinkRejected("ASSURANCE_STATEMENT_PIN_MISMATCH")
    original, claim = inputs.original, inputs.context.claim
    if (
        statement.tenant_id != claim.tenant_id
        or statement.tenant_id != original.tenant_id
        or statement.document_version_id != original.document_version_id
        or statement.parse_manifest_id != original.parse_manifest_id
        or statement.source_sha256 != original.source_sha256
        or statement.graph_sha256 != canonical_hash(asdict(original))
    ):
        raise AssuranceLinkRejected("ASSURANCE_STATEMENT_IDENTITY_MISMATCH")
    if statement.synthetic and not inputs.rule_context.local_synthetic:
        raise AssuranceLinkRejected("ASSURANCE_SYNTHETIC_STATEMENT")
    try:
        recomputed = _reextract(statement, original)
    except Exception as exc:  # the extractor raises for any forged/absent citation
        raise AssuranceLinkRejected("ASSURANCE_STATEMENT_SOURCE_REJECTED") from exc
    if recomputed.semantic_hash != statement.semantic_hash:
        raise AssuranceLinkRejected("ASSURANCE_STATEMENT_SOURCE_REJECTED")


def derive_assurance_fact(inputs, request, statement) -> tuple[ConfirmedFact | None, dict]:
    """Return the P4 fact only for a fully covered, source-verified single-statement match.

    Raises ``AssuranceLinkRejected`` for an invalid request or unusable statement.
    Otherwise returns ``(None, receipt)`` when coverage is not established; the
    receipt keeps the matcher reasons so the unresolved state stays explainable.
    """
    _validate_request(inputs, request)
    _check_statement(inputs, request, statement)
    claim, original = inputs.context.claim, inputs.original
    reasons: list[str] = []
    claim_refs = tuple(
        verify_source_ref(ref, original, tenant_id=claim.tenant_id) for ref in claim.source_refs
    )
    if any(ref.verification_state != "verified" for ref in claim_refs):
        reasons.append("claim_source_unverified")
    context = claim_context_from_review_inputs(
        inputs,
        tenant_id=claim.tenant_id,
        document_version_id=claim.document_version_id,
        claim_id=claim.claim_id,
    )
    if context is None:
        raise AssuranceLinkRejected("ASSURANCE_CLAIM_CONTEXT_INVALID")
    match = match_assurance(statement, context)
    reasons.extend(match.reasons)
    refs = tuple(
        verify_source_ref(ref, original, tenant_id=claim.tenant_id) for ref in match.evidence_refs
    )
    if not refs or any(
        ref.verification_state != "verified" or ref.location_quality != "located" for ref in refs
    ):
        reasons.append("statement_source_unverified")
    covered = match.status == "covered" and not reasons
    receipt = dict(
        policy=POLICY,
        policy_hash=POLICY_HASH,
        request=request,
        request_sha256=canonical_hash(request),
        statement=dict(
            statement_id=statement.statement_id,
            semantic_hash=statement.semantic_hash,
            graph_sha256=statement.graph_sha256,
            source_sha256=statement.source_sha256,
            synthetic=statement.synthetic,
            unresolved_fields=list(statement.unresolved_fields),
        ),
        claim_context=asdict(context),
        match=dict(
            status=match.status,
            level=match.level,
            provider=match.provider,
            metric_match=match.metric_match,
            period_match=match.period_match,
            boundary_match=match.boundary_match,
            rule_sha256=match.rule_sha256,
            statement_sha256=match.statement_sha256,
            reasons=list(match.reasons),
        ),
        evidence_refs=[asdict(ref) for ref in refs],
        status="covered" if covered else "unknown",
        reasons=sorted(set(reasons)),
    )
    receipt["receipt_sha256"] = canonical_hash(receipt)
    if not covered:
        return None, receipt
    fact = ConfirmedFact(
        FACT,
        "present",
        refs,
        claim.tenant_id,
        True,
        True,
        source_scope="global_bound",
        normalized_value="covered",
    )
    return fact, receipt


def p4_element(fact: ConfirmedFact) -> dict:
    """The only P4 body element a reviewer can submit for a derived fact."""
    return dict(
        element_id=ELEMENT,
        state="present",
        evidence_refs=[asdict(ref) for ref in fact.evidence_refs],
        normalized_value="covered",
        credited_from=None,
        reason_code=POLICY,
    )


def replay_assurance_receipt(inputs, prior: dict, statement) -> tuple[ConfirmedFact, dict]:
    """Recompute a carried receipt; it must still be covered and byte-identical."""
    if not isinstance(prior, dict) or not isinstance(prior.get("request"), dict):
        raise AssuranceLinkRejected("ASSURANCE_REPLAY_MISMATCH")
    stored = {k: v for k, v in prior.items() if k not in ("receipt_sha256", "carried_from")}
    if prior.get("receipt_sha256") != canonical_hash(stored):
        raise AssuranceLinkRejected("ASSURANCE_REPLAY_MISMATCH")
    request = dict(prior["request"], input_snapshot_sha256=canonical_hash(inputs.snapshot()))
    if request != prior["request"]:
        raise AssuranceLinkRejected("ASSURANCE_REPLAY_MISMATCH")
    fact, receipt = derive_assurance_fact(inputs, request, statement)
    if fact is None or receipt["receipt_sha256"] != prior["receipt_sha256"]:
        raise AssuranceLinkRejected("ASSURANCE_REPLAY_MISMATCH")
    return fact, receipt
