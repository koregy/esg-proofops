"""Local store/producer for company-activity industry topic reviews (GAP-010 A).

Revisions live in the run database's existing ``job_records`` table under their
own kinds, written only inside the job store's ``BEGIN IMMEDIATE`` transaction:

* ``industry_topic_universe`` (immutable, ``<seq>``) + ``industry_topic_universe_head``
* ``industry_topic_revision`` (immutable, ``<topic>:<seq>``) + ``industry_topic_head``

Every write is a compare-and-swap on the caller's ``expected_head`` (``None`` for
the first revision). Every read replays the whole chain (contiguous sequence,
prior hash, content hash, head anchor) and re-derives the run pins and every
evidence ref from the current run state, so a mutated original, stale input,
foreign tenant or re-hashed replacement is refused or goes stale; it can never
manufacture an approval. Nothing here touches claim, tag, decision or summary
records, and ``dry_run`` (the default) writes nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, replace

from proofops.application import industry_topic_review as topics
from proofops.application.evidence.span_citations import verify_source_ref
from proofops.application.industry_topic_review import IndustryReviewRejected
from proofops.domain.provenance import canonical_hash

UNIVERSE, UNIVERSE_HEAD = "industry_topic_universe", "industry_topic_universe_head"
REVISION, HEAD = "industry_topic_revision", "industry_topic_head"
# (tenant_id, run_id) -> dict(pins, graph, crosswalk); tests may inject.
RunLoader = Callable[[str, str], dict]


def run_loader(store, uploads, parser) -> RunLoader:
    """Read-only pins from a real local run: frozen snapshot, original, graph, rulepack."""

    def load(tenant_id: str, run_id: str) -> dict:
        from proofops.adapters.local.run_artifacts import load_run_graph, load_run_inputs
        from proofops.domain.rulepacks import RulePackSnapshot

        snapshot, source, _ = load_run_inputs(store, uploads, tenant_id=tenant_id, run_id=run_id)
        graph = load_run_graph(store, uploads, parser, tenant_id=tenant_id, run_id=run_id)
        try:
            crosswalk = RulePackSnapshot(**snapshot["rulepack"]).file_content(
                "sasb/industry_map.yaml"
            )
        except KeyError:
            # A pack without the crosswalk file pins None: every candidate stays
            # undetermined and nothing is excluded.
            crosswalk = None
        metadata = snapshot["document"].get("metadata") or {}
        return dict(
            pins=dict(
                tenant_id=tenant_id,
                run_id=run_id,
                document_version_id=source.document_version_id,
                object_version_id=source.object_version_id,
                source_sha256=source.sha256,
                input_hash=snapshot["input_hash"],
                parse_manifest_id=graph.parse_manifest_id,
                graph_sha256=canonical_hash(asdict(graph)),
                rulepack_sha256=snapshot["rulepack"]["sha256"],
                crosswalk_sha256=canonical_hash(crosswalk),
                industry=dict(
                    system=metadata.get("industry_system", "unknown"),
                    code=metadata.get("industry_code"),
                ),
            ),
            graph=graph,
            crosswalk=crosswalk,
        )

    return load


class LocalIndustryTopicStore:
    def __init__(self, jobs, loader: RunLoader):
        self.jobs, self._load = jobs, loader

    # --- state ------------------------------------------------------------------

    def _state(self, tenant_id: str, run_id: str) -> dict:
        self.jobs._scope(tenant_id, run_id)
        state = self._load(tenant_id, run_id)
        pins, graph = state["pins"], state["graph"]
        if (
            set(pins) != set(topics.PIN_KEYS)
            or (pins["tenant_id"], pins["run_id"]) != (tenant_id, run_id)
            or graph.tenant_id != tenant_id
            or graph.source_sha256 != pins["source_sha256"]
            or canonical_hash(asdict(graph)) != pins["graph_sha256"]
        ):
            raise IndustryReviewRejected("INDUSTRY_RUN_IDENTITY_MISMATCH")
        return state

    def _chain(self, db, tenant_id, run_id, kind, head_kind, head_key, prefix=""):
        """Full validated chain (oldest first); the head record anchors its tip."""
        head = self.jobs._raw(db, tenant_id, run_id, head_kind, head_key)
        # Exact prefix match in Python: SQL LIKE would treat "_" in topic ids as a wildcard.
        rows = sorted(
            (row[0], row[1])
            for row in db.execute(
                "SELECT record_id, value FROM job_records "
                "WHERE tenant_id=? AND run_id=? AND kind=?",
                (tenant_id, run_id, kind),
            )
            if row[0].startswith(prefix) and row[0][len(prefix) :].isdigit()
        )
        chain, prior = [], None
        for index, (key, raw) in enumerate(rows, start=1):
            body = json.loads(raw)
            topics.verify_revision_hash(body)
            if (
                key != f"{prefix}{index:010}"
                or body["seq"] != index
                or body["prior_sha256"] != prior
            ):
                raise IndustryReviewRejected("INDUSTRY_CHAIN_INVALID")
            chain.append(body)
            prior = body["revision_sha256"]
        anchored = json.loads(head)["revision_sha256"] if head is not None else None
        if anchored != prior:
            raise IndustryReviewRejected("INDUSTRY_CHAIN_INVALID")
        return chain

    def _universe(self, db, tenant_id, run_id):
        chain = self._chain(db, tenant_id, run_id, UNIVERSE, UNIVERSE_HEAD, "HEAD")
        return chain[-1] if chain else None

    def _topic_chain(self, db, tenant_id, run_id, topic_id):
        return self._chain(db, tenant_id, run_id, REVISION, HEAD, topic_id, prefix=f"{topic_id}:")

    @staticmethod
    def _cas(current, expected):
        if (current["revision_sha256"] if current else None) != expected:
            raise IndustryReviewRejected("INDUSTRY_HEAD_CONFLICT")

    def _append(self, db, tenant_id, run_id, kind, head_kind, head_key, record_key, body):
        self.jobs._put(db, tenant_id, run_id, kind, record_key, body, immutable=True)
        self.jobs._put(
            db,
            tenant_id,
            run_id,
            head_kind,
            head_key,
            dict(revision_sha256=body["revision_sha256"]),
        )

    # --- operations ---------------------------------------------------------------

    def declare_universe(self, tenant_id, run_id, universe, *, expected_head, dry_run=True):
        body = topics.validate_universe(universe)
        state = self._state(tenant_id, run_id)
        with self.jobs._transaction() as db:
            current = self._universe(db, tenant_id, run_id)
            self._cas(current, expected_head)
            topics.validate_universe_extension(current["universe"] if current else None, body)
            seq = current["seq"] + 1 if current else 1
            record = dict(
                schema="industry_topic_universe_revision_v1",
                pins=state["pins"],
                universe=body,
                universe_sha256=canonical_hash(body),
                seq=seq,
                prior_sha256=current["revision_sha256"] if current else None,
            )
            record["revision_sha256"] = canonical_hash(record)
            if not dry_run:
                self._append(
                    db, tenant_id, run_id, UNIVERSE, UNIVERSE_HEAD, "HEAD", f"{seq:010}", record
                )
        return dict(written=not dry_run, revision=record)

    def _evidence(self, state, tenant_id, specs):
        if not isinstance(specs, list):
            raise IndustryReviewRejected("INDUSTRY_EVIDENCE_INVALID")
        blocks = {b.source_id: b for b in state["graph"].blocks}
        refs = []
        for spec in specs:
            if not isinstance(spec, dict) or set(spec) != {
                "source_id",
                "char_start",
                "char_end",
                "quote",
            }:
                raise IndustryReviewRejected("INDUSTRY_EVIDENCE_INVALID")
            block = blocks.get(spec["source_id"])
            if block is None or block.winner is None:
                raise IndustryReviewRejected("INDUSTRY_EVIDENCE_NOT_VERIFIED")
            try:
                ref = replace(
                    block.source_ref(),
                    char_start=spec["char_start"],
                    char_end=spec["char_end"],
                    quote=spec["quote"],
                )
                checked = verify_source_ref(ref, state["graph"], tenant_id=tenant_id)
            except (ValueError, TypeError):
                raise IndustryReviewRejected("INDUSTRY_EVIDENCE_NOT_VERIFIED") from None
            if checked.verification_state != "verified":
                raise IndustryReviewRejected("INDUSTRY_EVIDENCE_NOT_VERIFIED")
            refs.append(asdict(checked))
        return refs

    def review(
        self,
        tenant_id,
        run_id,
        *,
        topic_id,
        decision,
        reason,
        evidence,
        reviewer,
        reviewed_at,
        expected_head,
        dry_run=True,
    ):
        state = self._state(tenant_id, run_id)
        refs = self._evidence(state, tenant_id, evidence)
        with self.jobs._transaction() as db:
            universe = self._universe(db, tenant_id, run_id)
            if universe is None or universe["pins"] != state["pins"]:
                raise IndustryReviewRejected("INDUSTRY_UNIVERSE_REQUIRED")
            if topic_id not in {t["topic_id"] for t in universe["universe"]["topics"]}:
                raise IndustryReviewRejected("INDUSTRY_TOPIC_NOT_IN_UNIVERSE")
            chain = self._topic_chain(db, tenant_id, run_id, topic_id)
            current = chain[-1] if chain else None
            self._cas(current, expected_head)
            body = topics.build_review(
                pins=state["pins"],
                universe_sha256=universe["universe_sha256"],
                topic_id=topic_id,
                seq=len(chain) + 1,
                prior_sha256=current["revision_sha256"] if current else None,
                decision=decision,
                reason=reason,
                evidence_refs=refs,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
            )
            if not dry_run:
                self._append(
                    db,
                    tenant_id,
                    run_id,
                    REVISION,
                    HEAD,
                    topic_id,
                    f"{topic_id}:{body['seq']:010}",
                    body,
                )
        return dict(written=not dry_run, revision=body)

    def approve(
        self,
        tenant_id,
        run_id,
        *,
        topic_id,
        approver,
        approved_at,
        reason,
        expected_head,
        dry_run=True,
    ):
        """Approve exactly ``expected_head``, which must be the current not_applicable
        review under the current pins and universe; a newer review makes it stale."""
        state = self._state(tenant_id, run_id)
        with self.jobs._transaction() as db:
            universe = self._universe(db, tenant_id, run_id)
            chain = self._topic_chain(db, tenant_id, run_id, topic_id)
            head = chain[-1] if chain else None
            self._cas(head, expected_head)
            if (
                universe is None
                or head is None
                or head["pins"] != state["pins"]
                or head["universe_sha256"] != universe["universe_sha256"]
            ):
                raise IndustryReviewRejected("INDUSTRY_APPROVAL_TARGET_STALE")
            body = topics.build_approval(
                pins=state["pins"],
                head=head,
                seq=len(chain) + 1,
                approver=approver,
                approved_at=approved_at,
                reason=reason,
            )
            self._verify_refs(state, tenant_id, head)
            if not dry_run:
                self._append(
                    db,
                    tenant_id,
                    run_id,
                    REVISION,
                    HEAD,
                    topic_id,
                    f"{topic_id}:{body['seq']:010}",
                    body,
                )
        return dict(written=not dry_run, revision=body)

    @staticmethod
    def _verify_refs(state, tenant_id, review):
        from proofops.domain.values import SourceRef

        for raw in review.get("evidence_refs", ()):
            ref = SourceRef(**raw)
            checked = verify_source_ref(ref, state["graph"], tenant_id=tenant_id)
            if checked.verification_state != "verified" or canonical_hash(
                asdict(checked)
            ) != canonical_hash(raw):
                raise IndustryReviewRejected("INDUSTRY_EVIDENCE_NOT_VERIFIED")

    def report(self, tenant_id, run_id) -> dict:
        """Replay every chain against current run state; project-only output."""
        state = self._state(tenant_id, run_id)
        with self.jobs._transaction() as db:
            universe = self._universe(db, tenant_id, run_id)
            chains = {}
            if universe is not None:
                for topic in universe["universe"]["topics"]:
                    chains[topic["topic_id"]] = self._topic_chain(
                        db, tenant_id, run_id, topic["topic_id"]
                    )
        for chain in chains.values():
            if chain and chain[-1]["pins"] == state["pins"]:
                reviews = [r for r in chain if r["schema"] == topics.REVIEW_SCHEMA]
                self._verify_refs(state, tenant_id, reviews[-1])
        declared = (
            universe["universe"]
            if universe is not None and universe["pins"] == state["pins"]
            else None
        )
        report = topics.build_report(
            pins=state["pins"],
            universe=declared,
            chains=chains if declared is not None else {},
            mapping=topics.crosswalk_mapping(state["crosswalk"]),
        )
        if universe is not None and declared is None:
            report = dict(report, universe_status="stale_inputs")
            report.pop("report_sha256")
            report["report_sha256"] = canonical_hash(report)
        return report
