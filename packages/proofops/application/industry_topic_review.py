"""Company-activity industry topic review (R00 §12 GAP-010 A), project-only.

GAP-010 A: the versioned industry crosswalk only presents CANDIDATE industries.
Each topic is set applicable / not_applicable / undetermined only after a review
of the company's own activity evidence; a topic leaves the denominator only on an
explicit, approved not_applicable with a reason; unknown stays in the denominator.

This module is pure. It never invents a GICS/SASB mapping, never treats a
classification as an exclusion, and never infers satisfaction from applicability
or claim grades (``satisfaction`` stays ``null`` unless an independent verified
input is ever supplied). The topic universe is an operator-declared project scope,
not an official standards topic set, and the report is not standards compliance.
"""

from __future__ import annotations

import re
from datetime import datetime

from proofops.domain.applicability import (
    IndustryIdentity,
    IndustryMapping,
    IndustryMappingEntry,
    resolve_industry_applicability,
)
from proofops.domain.provenance import canonical_hash

POLICY = "company_activity_topic_review_v1"
UNIVERSE_SCHEMA = "operator_declared_topic_universe_v1"
REVIEW_SCHEMA = "industry_topic_review_revision_v1"
APPROVAL_SCHEMA = "industry_topic_approval_revision_v1"
REPORT_SCHEMA = "industry_topic_report_v1"
DECISIONS = frozenset({"applicable", "not_applicable", "undetermined"})
ACTOR_KINDS = frozenset({"human", "ai_delegated"})
PIN_KEYS = (
    "tenant_id",
    "run_id",
    "document_version_id",
    "object_version_id",
    "source_sha256",
    "input_hash",
    "parse_manifest_id",
    "graph_sha256",
    "rulepack_sha256",
    "crosswalk_sha256",
    "industry",
)
DISCLAIMER = (
    "Project-only company-activity topic review. The topic universe is operator-declared, "
    "not an official GICS/SASB topic set; no official mapping, legal Scope 3 deferral or "
    "standards compliance is asserted."
)
_TOPIC = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


class IndustryReviewRejected(ValueError):
    """The request cannot be recorded; nothing is written."""


def _text(value, low: int, high: int, code: str) -> str:
    if (
        not isinstance(value, str)
        or not low <= len(value.strip()) <= high
        or value != value.strip()
    ):
        raise IndustryReviewRejected(code)
    return value


def _aware(value, code: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise IndustryReviewRejected(code) from None
    if parsed.tzinfo is None:
        raise IndustryReviewRejected(code)
    return value


def validate_actor(actor, code: str = "INDUSTRY_ACTOR_INVALID") -> dict:
    """Who acted and under what authority. ``human`` is never defaulted: a caller
    must state it explicitly; the CLI defaults to ``ai_delegated``."""
    if (
        not isinstance(actor, dict)
        or set(actor) != {"kind", "id", "authority"}
        or actor["kind"] not in ACTOR_KINDS
    ):
        raise IndustryReviewRejected(code)
    _text(actor["id"], 1, 128, code)
    _text(actor["authority"], 5, 500, code)
    return dict(actor)


def validate_universe(body: dict) -> dict:
    keys = {"schema", "scope", "official_mapping", "topics", "declared_by", "declared_at", "reason"}
    if not isinstance(body, dict) or set(body) != keys or body["schema"] != UNIVERSE_SCHEMA:
        raise IndustryReviewRejected("INDUSTRY_UNIVERSE_INVALID")
    if body["scope"] != "operator_declared_project_topics" or body["official_mapping"] is not False:
        raise IndustryReviewRejected("INDUSTRY_UNIVERSE_NOT_PROJECT_SCOPED")
    topics = body["topics"]
    if not isinstance(topics, list) or not 1 <= len(topics) <= 200:
        raise IndustryReviewRejected("INDUSTRY_UNIVERSE_INVALID")
    seen = set()
    for topic in topics:
        if (
            not isinstance(topic, dict)
            or set(topic) != {"topic_id", "label"}
            or not isinstance(topic["topic_id"], str)
            or not _TOPIC.fullmatch(topic["topic_id"])
            or topic["topic_id"] in seen
        ):
            raise IndustryReviewRejected("INDUSTRY_UNIVERSE_INVALID")
        _text(topic["label"], 1, 200, "INDUSTRY_UNIVERSE_INVALID")
        seen.add(topic["topic_id"])
    validate_actor(body["declared_by"])
    _aware(body["declared_at"], "INDUSTRY_UNIVERSE_INVALID")
    _text(body["reason"], 10, 2000, "INDUSTRY_UNIVERSE_INVALID")
    return dict(body)


def validate_universe_extension(previous: dict | None, new: dict) -> None:
    """A later universe revision may only ADD topics. Every existing topic id must
    stay listed with the identical label: removal (even of an approved
    not_applicable topic, which stays listed and excluded) or redefinition would
    silently change the denominator or a topic's meaning without any review."""
    if previous is None:
        return
    current = {t["topic_id"]: t["label"] for t in new["topics"]}
    for topic in previous["topics"]:
        if topic["topic_id"] not in current:
            raise IndustryReviewRejected("INDUSTRY_UNIVERSE_SHRINK_REFUSED")
        if current[topic["topic_id"]] != topic["label"]:
            raise IndustryReviewRejected("INDUSTRY_TOPIC_REDEFINITION_REFUSED")


def crosswalk_mapping(content: dict) -> IndustryMapping | None:
    """The pinned rulepack crosswalk as a domain mapping. Empty or unverified input
    stays exactly that; nothing is filled in."""
    if not isinstance(content, dict):
        return None
    entries = tuple(
        IndustryMappingEntry(
            industry_system=entry["industry_system"],
            industry_code=entry["industry_code"],
            topic_id=entry["topic_id"],
            applicability=entry["applicability"],
            mandatory=entry.get("mandatory"),
        )
        for entry in content.get("mappings") or ()
    )
    status = content.get("verification_status")
    return IndustryMapping(
        version=str(content.get("version") or "unversioned"),
        verification_status=status if status in ("verified", "unverified") else "unverified",
        entries=entries,
    )


def candidate_industry(industry: dict, topic_id: str, mapping: IndustryMapping | None) -> dict:
    """A crosswalk result shown as a CANDIDATE only; it never sets the topic state."""
    identity = IndustryIdentity(industry["system"], industry.get("code"))
    result = resolve_industry_applicability(identity, topic_id, mapping)
    return dict(
        candidate_applicability=result.applicability,
        candidate_mandatory=result.mandatory,
        mapping_version=result.mapping_version,
        mapping_verified=mapping is not None and mapping.verification_status == "verified",
        role="candidate_only_never_excludes",
    )


def build_review(
    *,
    pins: dict,
    universe_sha256: str,
    topic_id: str,
    seq: int,
    prior_sha256: str | None,
    decision: str,
    reason: str,
    evidence_refs: list[dict],
    reviewer: dict,
    reviewed_at: str,
) -> dict:
    """``evidence_refs`` are SourceRef dicts the caller already re-verified against
    the pinned run graph and tenant; applicable/not_applicable need at least one."""
    if decision not in DECISIONS:
        raise IndustryReviewRejected("INDUSTRY_DECISION_INVALID")
    _text(reason, 20, 2000, "INDUSTRY_REASON_REQUIRED")
    if not isinstance(evidence_refs, list) or len(evidence_refs) > 20:
        raise IndustryReviewRejected("INDUSTRY_EVIDENCE_INVALID")
    if decision != "undetermined" and not evidence_refs:
        raise IndustryReviewRejected("INDUSTRY_EVIDENCE_REQUIRED")
    body = dict(
        schema=REVIEW_SCHEMA,
        policy=POLICY,
        pins=dict(pins),
        universe_sha256=universe_sha256,
        topic_id=topic_id,
        seq=seq,
        prior_sha256=prior_sha256,
        decision=decision,
        reason=reason,
        evidence_refs=evidence_refs,
        reviewer=validate_actor(reviewer),
        reviewed_at=_aware(reviewed_at, "INDUSTRY_TIME_INVALID"),
    )
    body["revision_sha256"] = canonical_hash(body)
    return body


def build_approval(
    *,
    pins: dict,
    head: dict,
    seq: int,
    approver: dict,
    approved_at: str,
    reason: str,
) -> dict:
    """Approve EXACTLY the current head review, and only a not_applicable one."""
    if head is None or head.get("schema") != REVIEW_SCHEMA:
        raise IndustryReviewRejected("INDUSTRY_APPROVAL_TARGET_INVALID")
    if head["decision"] != "not_applicable":
        raise IndustryReviewRejected("INDUSTRY_APPROVAL_ONLY_FOR_NOT_APPLICABLE")
    _text(reason, 20, 2000, "INDUSTRY_REASON_REQUIRED")
    approver = validate_actor(approver)
    reviewer = head["reviewer"]
    body = dict(
        schema=APPROVAL_SCHEMA,
        policy=POLICY,
        pins=dict(pins),
        universe_sha256=head["universe_sha256"],
        topic_id=head["topic_id"],
        seq=seq,
        prior_sha256=head["revision_sha256"],
        approves_revision_sha256=head["revision_sha256"],
        approver=approver,
        approved_at=_aware(approved_at, "INDUSTRY_TIME_INVALID"),
        reason=reason,
        # Provenance only: R00 requires an explicit approval, not an independent or
        # human approver. A same-actor approval is allowed and recorded as such.
        approval_provenance=dict(
            role="explicit_separate_approval_revision",
            same_actor_as_reviewer=(approver["kind"], approver["id"])
            == (reviewer["kind"], reviewer["id"]),
            reviewer=reviewer,
            independent_or_human_approval_claimed=False,
        ),
    )
    body["revision_sha256"] = canonical_hash(body)
    return body


def verify_revision_hash(body: dict) -> None:
    rest = {key: value for key, value in body.items() if key != "revision_sha256"}
    if canonical_hash(rest) != body.get("revision_sha256"):
        raise IndustryReviewRejected("INDUSTRY_REVISION_TAMPERED")


def topic_state(chain: list[dict], *, pins: dict, universe_sha256: str | None) -> dict:
    """Current state of one topic from its full validated chain (oldest first).

    Approved not_applicable requires the head to be an approval of the review
    immediately before it; any newer review makes an older approval stale.
    Revisions pinned to other run inputs or another universe are stale, so the
    topic falls back to undetermined and stays in the denominator.
    """
    if not chain:
        return dict(state="unreviewed", in_denominator=True, head_sha256=None)
    head = chain[-1]
    if head["pins"] != pins or head["universe_sha256"] != universe_sha256:
        return dict(state="stale_inputs", in_denominator=True, head_sha256=head["revision_sha256"])
    if head["schema"] == APPROVAL_SCHEMA:
        # Never trust position alone: the record immediately before must be the
        # exact not_applicable review this approval names, under the same pins.
        review = chain[-2] if len(chain) >= 2 else None
        if (
            review is None
            or review["schema"] != REVIEW_SCHEMA
            or review["decision"] != "not_applicable"
            or head.get("approves_revision_sha256") != review["revision_sha256"]
            or head["prior_sha256"] != review["revision_sha256"]
            or review["pins"] != head["pins"]
            or review["universe_sha256"] != head["universe_sha256"]
        ):
            raise IndustryReviewRejected("INDUSTRY_APPROVAL_CHAIN_INVALID")
        return dict(
            state="not_applicable_approved",
            in_denominator=False,
            head_sha256=head["revision_sha256"],
            review_sha256=review["revision_sha256"],
            reason=review["reason"],
            evidence_refs=review["evidence_refs"],
            approver=head["approver"],
            approval_provenance=head["approval_provenance"],
        )
    state = {
        "applicable": "applicable",
        "not_applicable": "not_applicable_pending_approval",
        "undetermined": "undetermined",
    }[head["decision"]]
    return dict(
        state=state,
        in_denominator=True,
        head_sha256=head["revision_sha256"],
        review_sha256=head["revision_sha256"],
        reason=head["reason"],
        evidence_refs=head["evidence_refs"],
    )


def build_report(
    *, pins: dict, universe: dict | None, chains: dict[str, list[dict]], mapping
) -> dict:
    """Project-only topic report; satisfaction is never inferred (always null)."""
    universe_sha = canonical_hash(universe) if universe is not None else None
    topics = []
    for topic in (universe or {}).get("topics", ()):
        state = topic_state(
            chains.get(topic["topic_id"], []), pins=pins, universe_sha256=universe_sha
        )
        topics.append(
            dict(
                topic_id=topic["topic_id"],
                label=topic["label"],
                **state,
                satisfaction=None,
                candidate=candidate_industry(pins["industry"], topic["topic_id"], mapping),
            )
        )
    counted = [t for t in topics if t["in_denominator"]]
    report = dict(
        schema=REPORT_SCHEMA,
        policy=POLICY,
        pins=dict(pins),
        universe_status="declared" if universe is not None else "universe_undeclared",
        universe_sha256=universe_sha,
        topics=topics,
        topic_count=len(topics),
        denominator_count=len(counted) if universe is not None else None,
        excluded_not_applicable_count=len(topics) - len(counted),
        undetermined_or_unreviewed_count=sum(
            t["state"]
            in ("undetermined", "unreviewed", "stale_inputs", "not_applicable_pending_approval")
            for t in topics
        ),
        applicable_count=sum(t["state"] == "applicable" for t in topics),
        satisfaction_rate=None,
        disclaimer=DISCLAIMER,
    )
    report["report_sha256"] = canonical_hash(report)
    return report


def summary_applicability(report: dict):
    """Optional later integration for ``summarize_snapshot(applicability=...)``.

    Returns ``None`` (the current summary behaviour) unless every applicable topic
    carries an independently verified boolean satisfaction, which this producer
    never supplies. Undetermined, unreviewed, stale and pending topics map to
    ``undetermined``; only approved not_applicable maps to ``N_A``.
    """
    from proofops.application.summaries import RequirementStatus

    if report.get("universe_status") != "declared":
        return None
    items = []
    for topic in report["topics"]:
        if topic["state"] == "applicable":
            if type(topic["satisfaction"]) is not bool:
                return None
            items.append(RequirementStatus(topic["topic_id"], "applicable", topic["satisfaction"]))
        elif topic["state"] == "not_applicable_approved":
            items.append(RequirementStatus(topic["topic_id"], "N_A", None))
        else:
            items.append(RequirementStatus(topic["topic_id"], "undetermined", None))
    return tuple(items)
