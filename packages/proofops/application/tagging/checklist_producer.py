"""Checklist-item producer for ``project_checklist_completeness_v1`` (GAP-001 · A, R00 §12).

The consumer already exists: ``ReviewService.resolve_ai_delegated_review(
safe_harbor_review=...)`` (``reviews._review_safe_harbor``) replays every reference
and records an immutable receipt. This module is the missing *producer*: it turns an
explicit operator/AI-delegated decision into that exact request, selecting evidence
only by content address from the replayed immutable packet. It never writes.

What it refuses (nothing is produced):

* a claim whose frozen packet and every guarded tag-run header do not name the same
  configured safe-harbor category (non-safe-harbor claims have no checklist);
* a run whose pinned rule pack does not carry the checklist policy;
* a stale loader snapshot, another claim or run, or missing/extra checklist ids;
* ``absent``: whole-document absence needs the search-coverage producer and absence
  consumer contract (R00 §12 common guard); this producer does not grant it;
* ``present``/``conflict`` without evidence, ``unknown`` with evidence, and any
  reference not in the packet's ``local_claim``/``same_table`` scope or not
  re-verified against the original document.

The preview is documentation completeness only: ``true`` only when every fixed item
is verified ``present``, otherwise ``null`` (``false`` needs verified absence, which
is not produced here). Evidence grade and label stay ``null``, ``legal_effect`` stays
``not_determined`` and GAP-001 stays unresolved. No human, gold or legal approval.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from proofops.application.evidence.span_citations import verify_source_ref
from proofops.domain.provenance import canonical_hash
from proofops.domain.rules.safe_harbor import CHECKLIST_POLICY_V1
from proofops.domain.values import _source_ref_from_dict

PRODUCER = "safe-harbor-checklist-producer-v1"
PRODUCED_STATES = ("present", "unknown", "conflict")
ALLOWED_SCOPES = ("local_claim", "same_table")
DECISION_KEYS = frozenset(
    {"producer", "input_snapshot_sha256", "run_id", "claim_id", "category", "items"}
)
ITEM_KEYS = frozenset({"state", "ref_sha256", "reason"})
PRODUCER_HASH = canonical_hash(
    {
        "producer": PRODUCER,
        "policy": CHECKLIST_POLICY_V1,
        "states": list(PRODUCED_STATES),
        "absence": "not produced; requires search-coverage producer + absence consumer",
        "ref_scopes": list(ALLOWED_SCOPES),
        "evidence": "packet refs by canonical hash, re-verified against the original",
        "completeness": "true iff all fixed items verified present; else null",
        "grade_label": "null",
        "legal_effect": "not_determined",
    }
)


class ChecklistProducerRejected(ValueError):
    """The decision cannot become a checklist review; nothing is produced."""


def _packet_refs(packet: dict) -> dict[str, tuple[dict, str]]:
    # Same index as ``reviews._packet_ref_index`` (the consumer re-checks it).
    refs: dict[str, tuple[dict, str]] = {}
    for raw in packet.get("claim_source_refs", []):
        refs[canonical_hash(raw)] = (raw, "local_claim")
    for candidate in packet.get("evidence_candidates", []):
        scope = candidate.get("source_scope")
        for raw in candidate.get("source_refs", []):
            refs.setdefault(canonical_hash(raw), (raw, scope))
    return refs


def _category(inputs) -> tuple[str, tuple[str, ...]]:
    config = inputs.rulepack.file_content("regulatory/safe_harbor.yaml")
    if config.get("reasonable_basis_boolean_mapping") != CHECKLIST_POLICY_V1:
        raise ChecklistProducerRejected("CHECKLIST_POLICY_PACK_REQUIRED")
    packet = inputs.packet.to_dict()
    category = packet.get("safe_harbor_category")
    headers = {run.guarded.safe_harbor_category for run in inputs.tag_runs if run.guarded}
    checklists = config.get("category_checklists") or {}
    if category is None or category not in checklists:
        raise ChecklistProducerRejected("NOT_SAFE_HARBOR_CLAIM")
    if headers != {category}:
        raise ChecklistProducerRejected("SAFE_HARBOR_CATEGORY_MISMATCH")
    expected = checklists[category]
    if (
        not isinstance(expected, list)
        or not expected
        or any(not isinstance(name, str) or not name for name in expected)
        or len(set(expected)) != len(expected)
    ):
        raise ChecklistProducerRejected("CHECKLIST_CONFIG_INVALID")
    return category, tuple(expected)


def decision_template(inputs) -> dict:
    """Everything an operator needs to decide; every item starts ``unknown``."""
    category, expected = _category(inputs)
    packet = inputs.packet.to_dict()
    candidates = []
    for digest, (raw, scope) in sorted(_packet_refs(packet).items()):
        if scope in ALLOWED_SCOPES:
            candidates.append(
                dict(
                    ref_sha256=digest,
                    scope=scope,
                    page_num=raw.get("page_num"),
                    quote=raw.get("quote"),
                )
            )
    return dict(
        decision=dict(
            producer=PRODUCER,
            input_snapshot_sha256=canonical_hash(inputs.snapshot()),
            run_id=inputs.run_id,
            claim_id=inputs.context.claim.claim_id,
            category=category,
            items={name: dict(state="unknown", ref_sha256=[], reason="") for name in expected},
        ),
        selectable_refs=candidates,
        claim_quote=[ref.quote for ref in inputs.context.claim.source_refs],
        note="present/conflict need >=1 selected packet ref; absent is not produced",
    )


def _text(value: Any) -> bool:
    return isinstance(value, str) and 5 <= len(value.strip()) <= 1000


def build_checklist_review(inputs, decision: dict, *, source_authority: str):
    """Return ``(safe_harbor_review_request, producer_receipt)`` or refuse."""
    category, expected = _category(inputs)
    claim = inputs.context.claim
    if not isinstance(decision, dict) or set(decision) != DECISION_KEYS:
        raise ChecklistProducerRejected("CHECKLIST_DECISION_INVALID")
    if decision["producer"] != PRODUCER:
        raise ChecklistProducerRejected("CHECKLIST_DECISION_INVALID")
    if decision["input_snapshot_sha256"] != canonical_hash(inputs.snapshot()):
        raise ChecklistProducerRejected("CHECKLIST_STALE_INPUTS")
    if decision["run_id"] != inputs.run_id or decision["claim_id"] != claim.claim_id:
        raise ChecklistProducerRejected("CHECKLIST_CLAIM_MISMATCH")
    if decision["category"] != category:
        raise ChecklistProducerRejected("SAFE_HARBOR_CATEGORY_MISMATCH")
    if not _text(source_authority):
        raise ChecklistProducerRejected("CHECKLIST_DECISION_INVALID")
    items = decision["items"]
    if not isinstance(items, dict) or set(items) != set(expected):
        raise ChecklistProducerRejected("CHECKLIST_ITEMS_MISMATCH")
    refs_by_hash = _packet_refs(inputs.packet.to_dict())
    facts, verified = [], {}
    for name in expected:
        item = items[name]
        if not isinstance(item, dict) or set(item) != ITEM_KEYS or not _text(item["reason"]):
            raise ChecklistProducerRejected("CHECKLIST_DECISION_INVALID")
        state, selected = item["state"], item["ref_sha256"]
        if state == "absent":
            raise ChecklistProducerRejected("CHECKLIST_ABSENCE_NOT_ENABLED")
        if state not in PRODUCED_STATES or not isinstance(selected, list):
            raise ChecklistProducerRejected("CHECKLIST_DECISION_INVALID")
        if len(set(map(str, selected))) != len(selected):
            raise ChecklistProducerRejected("CHECKLIST_DECISION_INVALID")
        if (state == "unknown") != (not selected):
            raise ChecklistProducerRejected("CHECKLIST_EVIDENCE_STATE_MISMATCH")
        raw_refs = []
        for digest in selected:
            match = refs_by_hash.get(digest) if isinstance(digest, str) else None
            if match is None or match[1] not in ALLOWED_SCOPES:
                raise ChecklistProducerRejected("CHECKLIST_SOURCE_REJECTED")
            checked = verify_source_ref(
                _source_ref_from_dict(match[0]), inputs.original, tenant_id=claim.tenant_id
            )
            if checked.verification_state != "verified":
                raise ChecklistProducerRejected("CHECKLIST_SOURCE_REJECTED")
            raw_refs.append(dict(match[0]))
            verified[digest] = asdict(checked)
        facts.append(
            dict(
                name=name,
                state=state,
                evidence_refs=raw_refs,
                search_coverage_verified=False,
                reason=item["reason"].strip(),
            )
        )
    request = dict(
        policy=CHECKLIST_POLICY_V1,
        input_snapshot_sha256=decision["input_snapshot_sha256"],
        category=category,
        source_authority=source_authority.strip(),
        facts=facts,
    )
    states = [fact["state"] for fact in facts]
    receipt = dict(
        producer=PRODUCER,
        producer_hash=PRODUCER_HASH,
        decision_sha256=canonical_hash(decision),
        request_sha256=canonical_hash(request),
        identity=dict(
            tenant_id=claim.tenant_id,
            run_id=inputs.run_id,
            claim_id=claim.claim_id,
            document_version_id=claim.document_version_id,
            source_sha256=inputs.original.source_sha256,
            parse_manifest_id=inputs.original.parse_manifest_id,
            packet_sha256=inputs.packet.packet_sha256,
            rulepack_sha256=inputs.rulepack.sha256,
            input_snapshot_sha256=decision["input_snapshot_sha256"],
        ),
        verified_refs=verified,
        preview=dict(
            reasonable_basis_documented=True if set(states) == {"present"} else None,
            evidence_grade=None,
            label=None,
            legal_effect="not_determined",
            mapping_status="unresolved",
            gap_ids=["GAP-001"],
        ),
    )
    return request, receipt
