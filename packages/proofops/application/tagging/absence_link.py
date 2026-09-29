"""Opt-in, receipt-pinned ``absent`` facts from full-document search coverage (R00 §12).

``unknown -> absent`` is allowed only after the whole registered document's scope,
readability and search log are complete AND an explicit whole-corpus review
(human or AI-delegated) concluded ``absent_confirmed``. The producer
(``application.evidence.search_coverage`` + ``LocalSearchCoverageStore``) owns both
checks; this module is the consumer that turns a replayed, re-validated producer
result into ``ConfirmedFact(state="absent", search_coverage_verified=True)``.

What never proves absence here: a claim-local retrieval packet, zero lexical hits,
a regex, a client boolean or a caller-supplied hash. The request carries only
content addresses; the trusted port replays the receipt from the current PDF, graph,
claim and run snapshot and re-reads the stored review. GAP-004 attribution is
unchanged: this module only ever emits ``absent`` and never credits a value found
elsewhere. Base elements that are ``present`` or ``conflict`` are never overridden.

Compound elements (G3 baseline year+value, G4 scope+boundary, P1, P3, ...): the
producer's review is element-level, so partial absence cannot be represented. An
item must list every primitive it asserts absent (the element's full mapping), and
any base primitive that is not ``unknown`` refuses the item; nothing is inferred
from one missing component. P4 (deterministic assurance coverage) and P6
(deterministic numeric check) are excluded: their not-covered/undetermined results
stay ``unknown`` and are never turned into absence.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

from proofops.domain.provenance import canonical_hash
from proofops.domain.rules.engine import MAPPINGS, ConfirmedFact

POLICY = "search-absence-link-v1"
RECEIPT_SCHEMA = "search_coverage_receipt_v1"
POLICY_HASH = canonical_hash(
    {
        "policy": POLICY,
        "receipt_schema": RECEIPT_SCHEMA,
        "review_schema": "search_absence_review_v1",
        "required_decision": "absent_confirmed",
        "required_prerequisites": "search_prerequisites_complete",
        "fact_state": "absent",
        "search_coverage_verified": True,
        "base_states_overridable": ["unknown"],
        "attribution": "GAP-004 unchanged; absence only",
        "compound": "all primitives explicit; any non-unknown base primitive refuses",
        "receipt_primitives": "receipt.element_primitives == item.absent_facts (required)",
        "excluded_elements": ["P4", "P6"],
    }
)
EXCLUDED_ELEMENTS = frozenset({"P4", "P6"})
REQUEST_KEYS = frozenset(
    {
        "policy",
        "policy_hash",
        "input_snapshot_sha256",
        "claim_id",
        "claim_source_refs",
        "rulepack_sha256",
        "track",
        "items",
    }
)
ITEM_KEYS = frozenset({"element_id", "absent_facts", "receipt_sha256", "review_sha256"})


class AbsenceLinkRejected(ValueError):
    """The request, receipt or review cannot support an absence; nothing is published."""


class SearchCoverageEvidence(Protocol):
    """Trusted port; ``LocalSearchCoverageStore`` implements it (replays from state)."""

    def replay(self, tenant_id: str, run_id: str, receipt_sha256: str) -> tuple[dict, dict]: ...

    def absence_prerequisite(
        self,
        tenant_id: str,
        run_id: str,
        claim_id: str,
        element: str,
        receipt_sha256: str,
        review_sha256: str | None = None,
    ) -> dict: ...


def _sha(value) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def build_request(inputs, track: str, items: list[dict]) -> dict:
    """Trusted operator helper: pin loader snapshot, claim, rulepack and content addresses."""
    return dict(
        policy=POLICY,
        policy_hash=POLICY_HASH,
        input_snapshot_sha256=canonical_hash(inputs.snapshot()),
        claim_id=inputs.context.claim.claim_id,
        claim_source_refs=[asdict(ref) for ref in inputs.context.claim.source_refs],
        rulepack_sha256=inputs.rulepack.sha256,
        track=track,
        items=[dict(item) for item in items],
    )


def _validate_request(inputs, request) -> list[dict]:
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise AbsenceLinkRejected("ABSENCE_REQUEST_INVALID")
    if (request["policy"], request["policy_hash"]) != (POLICY, POLICY_HASH):
        raise AbsenceLinkRejected("ABSENCE_POLICY_MISMATCH")
    claim = inputs.context.claim
    if request["input_snapshot_sha256"] != canonical_hash(inputs.snapshot()):
        raise AbsenceLinkRejected("ABSENCE_STALE_INPUTS")
    if request["claim_id"] != claim.claim_id or not claim.source_refs:
        raise AbsenceLinkRejected("ABSENCE_CLAIM_MISMATCH")
    if canonical_hash(request["claim_source_refs"]) != canonical_hash(
        [asdict(ref) for ref in claim.source_refs]
    ):
        raise AbsenceLinkRejected("WHOLE_CLAIM_REQUIRED")
    if request["rulepack_sha256"] != inputs.rulepack.sha256:
        raise AbsenceLinkRejected("ABSENCE_RULEPACK_MISMATCH")
    track = request["track"]
    if track not in MAPPINGS:
        raise AbsenceLinkRejected("ABSENCE_REQUEST_INVALID")
    items = request["items"]
    if not isinstance(items, list) or not items:
        raise AbsenceLinkRejected("ABSENCE_REQUEST_INVALID")
    seen = set()
    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != ITEM_KEYS
            or item["element_id"] not in MAPPINGS[track]
            or item["element_id"] in EXCLUDED_ELEMENTS
            or item["element_id"] in seen
            or item["absent_facts"] != sorted(MAPPINGS[track][item["element_id"]])
            or not _sha(item["receipt_sha256"])
            or not _sha(item["review_sha256"])
        ):
            raise AbsenceLinkRejected("ABSENCE_REQUEST_INVALID")
        seen.add(item["element_id"])
    return items


def _base_unknown(inputs, track, element_id) -> bool:
    """Every base primitive and the base candidate element must still be unknown."""
    base = next(
        (e for e in inputs.consensus.candidate_elements if e.element_id == element_id), None
    )
    if base is not None and base.state != "unknown":
        return False
    confirmed = inputs.consensus.confirmed_tags
    facts = {f.name: f for f in confirmed.facts} if confirmed is not None else {}
    return all(
        name not in facts or facts[name].state == "unknown" for name in MAPPINGS[track][element_id]
    )


def _receipt_identity(inputs, receipt: dict, element_id: str) -> dict:
    claim, original = inputs.context.claim, inputs.original
    expected = dict(
        schema=RECEIPT_SCHEMA,
        tenant_id=claim.tenant_id,
        run_id=inputs.run_id,
        claim_id=claim.claim_id,
        claim_revision=claim.revision,
        claim_source_ids=sorted({ref.source_id for ref in claim.source_refs}),
        element=element_id,
        document_version_id=original.document_version_id,
        source_sha256=original.source_sha256,
        parse_manifest_id=original.parse_manifest_id,
        graph_sha256=canonical_hash(asdict(original)),
        rulepack_sha256=inputs.rulepack.sha256,
        element_state_effect="none",
    )
    try:
        actual = {key: receipt[key] for key in expected}
    except (KeyError, TypeError):
        raise AbsenceLinkRejected("ABSENCE_RECEIPT_IDENTITY_MISMATCH") from None
    if actual != expected:
        raise AbsenceLinkRejected("ABSENCE_RECEIPT_IDENTITY_MISMATCH")
    return dict(
        expected,
        input_hash=receipt.get("input_hash"),
        object_version_id=receipt.get("object_version_id"),
        attribution_scope=receipt.get("attribution_scope"),
    )


def derive_absences(inputs, request, evidence: SearchCoverageEvidence):
    """Return ``(facts_by_element, receipt)``; any unsupported item refuses everything."""
    items = _validate_request(inputs, request)
    track = request["track"]
    claim = inputs.context.claim
    if evidence is None:
        raise AbsenceLinkRejected("ABSENCE_EVIDENCE_UNAVAILABLE")
    facts: dict[str, tuple[ConfirmedFact, ...]] = {}
    records = []
    for item in items:
        element_id = item["element_id"]
        if not _base_unknown(inputs, track, element_id):
            # Present/conflict/absent candidates and primitives stay exactly as they are.
            raise AbsenceLinkRejected("ABSENCE_BASE_NOT_UNKNOWN")
        try:
            receipt, _ = evidence.replay(claim.tenant_id, inputs.run_id, item["receipt_sha256"])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise AbsenceLinkRejected(f"ABSENCE_EVIDENCE_REJECTED:{exc}") from exc
        _receipt_identity(inputs, receipt, element_id)
        # The producer names the primitives its whole-corpus review asserted absent;
        # they must be exactly this item's primitives (required, never inferred).
        if receipt.get("element_primitives") != item["absent_facts"]:
            raise AbsenceLinkRejected("ABSENCE_PRIMITIVES_MISMATCH")
        if (
            receipt.get("artifact_sha256") != item["receipt_sha256"]
            or receipt.get("search_prerequisites_complete") is not True
        ):
            # Incomplete scope/readability/log: stays unknown whatever a review says.
            raise AbsenceLinkRejected("ABSENCE_SEARCH_INCOMPLETE")
        try:
            prerequisite = evidence.absence_prerequisite(
                claim.tenant_id,
                inputs.run_id,
                claim.claim_id,
                element_id,
                item["receipt_sha256"],
                item["review_sha256"],
            )
        except AbsenceLinkRejected:
            raise
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise AbsenceLinkRejected(f"ABSENCE_EVIDENCE_REJECTED:{exc}") from exc
        identity = _receipt_identity(inputs, receipt, element_id)
        if (
            receipt.get("artifact_sha256") != item["receipt_sha256"]
            or prerequisite.get("receipt_sha256") != item["receipt_sha256"]
            or prerequisite.get("review_sha256") != item["review_sha256"]
            or receipt.get("search_prerequisites_complete") is not True
            or prerequisite.get("search_prerequisites_complete") is not True
            or prerequisite.get("incomplete_pages") != []
        ):
            raise AbsenceLinkRejected("ABSENCE_SEARCH_INCOMPLETE")
        if prerequisite.get("element_state_candidate") != "absent":
            raise AbsenceLinkRejected("ABSENCE_NOT_CONFIRMED")
        facts[element_id] = tuple(
            ConfirmedFact(
                name,
                "absent",
                (),
                claim.tenant_id,
                False,
                False,
                True,
                "local_claim",
                None,
            )
            for name in MAPPINGS[track][element_id]
        )
        records.append(
            dict(
                element_id=element_id,
                absent_facts=list(item["absent_facts"]),
                receipt_sha256=item["receipt_sha256"],
                review_sha256=item["review_sha256"],
                receipt_identity=identity,
                element_state_candidate="absent",
            )
        )
    receipt = dict(
        policy=POLICY,
        policy_hash=POLICY_HASH,
        request=request,
        request_sha256=canonical_hash(request),
        items=records,
    )
    receipt["receipt_sha256"] = canonical_hash(receipt)
    return facts, receipt


def replay_absence_receipt(inputs, prior: dict, evidence):
    """Recompute a carried receipt; stale inputs, receipts or reviews are refused."""
    if not isinstance(prior, dict) or not isinstance(prior.get("request"), dict):
        raise AbsenceLinkRejected("ABSENCE_REPLAY_MISMATCH")
    stored = {k: v for k, v in prior.items() if k not in ("receipt_sha256", "carried_from")}
    if prior.get("receipt_sha256") != canonical_hash(stored):
        raise AbsenceLinkRejected("ABSENCE_REPLAY_MISMATCH")
    try:
        facts, receipt = derive_absences(inputs, prior["request"], evidence)
    except AbsenceLinkRejected as exc:
        raise AbsenceLinkRejected(f"ABSENCE_REPLAY_MISMATCH:{exc}") from exc
    if receipt["receipt_sha256"] != prior["receipt_sha256"]:
        raise AbsenceLinkRejected("ABSENCE_REPLAY_MISMATCH")
    return facts, receipt


def absent_element(element_id: str) -> dict:
    """The only body element a reviewer can submit for a derived absence."""
    return dict(
        element_id=element_id,
        state="absent",
        evidence_refs=[],
        normalized_value=None,
        credited_from=None,
        reason_code=POLICY,
    )
