"""Tag checkpoint validation/replay; initial revisions belong to LocalSQLiteReviewStore.

No parallel revision tables. Checkpoints use the existing immutable artifact
pointer and retain the extraction checkpoint digest and frozen run inputs.
"""

import json
from dataclasses import asdict
from hashlib import sha256

from proofops.adapters.cache.aws import CacheNamespace, CacheRequest
from proofops.adapters.local.claim_store import LocalClaimStore
from proofops.application.budget import TokenUsage
from proofops.application.evidence.binding import ClaimContext
from proofops.application.evidence.retrieval import freeze_packet
from proofops.application.ports.jobs import JobMessage
from proofops.application.ports.models import ModelBinding
from proofops.application.reviews import ReviewInputs
from proofops.application.tagging.consensus import form_consensus
from proofops.application.tagging.relations import SYSTEM_PROMPT as RELATION_SYSTEM_PROMPT
from proofops.application.tagging.service import TaggingSettings, TagRun
from proofops.domain.provenance import canonical_hash
from proofops.domain.rulepacks import RulePackSnapshot
from proofops.domain.rules.engine import RuleContext, evaluate
from proofops.domain.values import SourceRef, llm_tags_from_dict


def tagging_settings(snapshot, *, preliminary=False, relation=False):
    if preliminary and relation:
        raise ValueError("TAGGING_PROFILE_MISMATCH")
    prefix = "relation" if relation else "preliminary" if preliminary else "tagging"
    if prefix + "_settings" not in snapshot:
        raise ValueError("TAGGING_PROFILE_MISMATCH")
    raw = snapshot[prefix + "_settings"]
    settings = TaggingSettings(**(raw | {"binding": ModelBinding(**raw["binding"])}))
    if snapshot.get("tagging_mode") == "upstage_local":
        from proofops.application.registry import artifact_sha256

        runtime = snapshot[prefix + "_runtime"]
        if (
            canonical_hash(asdict(settings)) != snapshot[prefix + "_settings_hash"]
            or artifact_sha256(runtime) != snapshot[prefix + "_runtime_artifact_hash"]
            or settings.binding.synthetic is not False
            or settings.binding.binding_id != runtime["runtime_binding_id"]
            or settings.binding.role != "tagger"
            or settings.model_id != runtime["model_id"]
            or runtime.get("tagging_settings_sha256") != canonical_hash(asdict(settings))
            or runtime.get("input_reservation_policy_sha256")
            != snapshot["input_reservation_policy_hash"]
            or canonical_hash(snapshot["input_reservation_policy"])
            != snapshot["input_reservation_policy_hash"]
        ):
            raise ValueError("TAGGING_PROFILE_MISMATCH")
        if relation and (
            settings.model_profile != "upstage-relation-source-quotes-v1"
            or settings.system_prompt != RELATION_SYSTEM_PROMPT
        ):
            raise ValueError("TAGGING_PROFILE_MISMATCH")
        return settings
    if preliminary or relation:
        raise ValueError("PRELIMINARY_PROFILE_UNSUPPORTED")
    runtime = snapshot["runtime"]
    if (
        canonical_hash(asdict(settings)) != snapshot["tagging_settings_hash"]
        or snapshot["tagging_mode"] != "local_synthetic"
        or not settings.binding.synthetic
        or settings.binding.binding_id != runtime["runtime_binding_id"]
        or settings.binding.role != runtime["role"]
        or (settings.model_id, settings.region) != (runtime["model_id"], runtime["endpoint_region"])
        or runtime["status"] != "approved"
        or type(settings.max_tokens) is not int
        or not 1 <= settings.max_tokens <= runtime["max_output_tokens"]
    ):
        raise ValueError("TAGGING_PROFILE_MISMATCH")
    return settings


def tag_pins(snapshot, extraction, extraction_hash):
    pins = dict(
        schema="local_tag_checkpoint_v1",
        synthetic=snapshot.get("tagging_mode") != "upstage_local",
        **{
            key: extraction[key]
            for key in (
                "tenant_id",
                "run_id",
                "document_version_id",
                "input_hash",
                "source_sha256",
                "object_version_id",
                "parse_manifest_id",
                "graph_sha256",
                "parse_checkpoint_sha256",
                "manifest_sha256",
                "rule_pack_sha256",
                "model_binding_hash",
                "extraction_profile",
                "extraction_profile_hash",
                "extraction_mode",
            )
        },
        claim_snapshot_sha256=extraction_hash,
        tagging_settings=snapshot.get("tagging_settings"),
        tagging_settings_hash=snapshot.get("tagging_settings_hash"),
        tagging_mode=snapshot.get("tagging_mode"),
        validation_profile="fast_preview",
        vision_status="not_run",
    )
    if snapshot.get("tagging_mode") == "upstage_local":
        pins.update(
            **{
                key: snapshot[key]
                for key in (
                    "preliminary_settings",
                    "preliminary_settings_hash",
                    "preliminary_runtime",
                    "preliminary_runtime_artifact_hash",
                    "tagging_runtime",
                    "tagging_runtime_artifact_hash",
                    "input_reservation_policy",
                    "input_reservation_policy_hash",
                    "rulepack_use",
                )
            }
        )
        relation_keys = (
            "relation_settings",
            "relation_settings_hash",
            "relation_runtime",
            "relation_runtime_artifact_hash",
        )
        if any(key in snapshot for key in relation_keys):
            if not all(key in snapshot for key in relation_keys):
                raise ValueError("TAG_CHECKPOINT_PIN_MISMATCH")
            pins.update({key: snapshot[key] for key in relation_keys})
    elif any(
        key in snapshot
        for key in (
            "relation_settings",
            "relation_settings_hash",
            "relation_runtime",
            "relation_runtime_artifact_hash",
        )
    ):
        raise ValueError("TAG_CHECKPOINT_PIN_MISMATCH")
    return pins


def validate_tag_commit(db, jobs, run, message, envelope, next_job):
    row = db.execute(
        "SELECT payload FROM run_snapshots WHERE tenant_id=? AND run_id=?",
        (message.tenant_id, message.run_id),
    ).fetchone()
    snapshot = json.loads(row[0]) if row else {}
    extraction_job = jobs._job(db, JobMessage(**run["extract_job"]))
    ref = extraction_job["artifact_ref"]
    raw = jobs._raw(db, message.tenant_id, message.run_id, "artifact", ref["key"])
    if raw is None or sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("TAG_EXTRACTION_CHECKPOINT_INVALID")
    extraction = json.loads(raw)
    expected = tag_pins(snapshot, extraction, ref["sha256"])
    claims = envelope["claims"]
    original_ids = [c["claim_id"] for c in extraction["discovery"]["claims"]]
    decided = sum(
        item["decision"] is not None and item["decision"]["decision_status"] == "decided"
        for item in claims
    )
    coverage = dict(
        extraction["coverage"],
        claims_decided=decided,
        claims_needs_review=len(claims) - decided,
        complete=False,
    )
    if (
        message.stage != "tag"
        or next_job is not None
        or message.input_hash != snapshot["input_hash"]
        or canonical_hash({k: v for k, v in snapshot.items() if k != "input_hash"})
        != message.input_hash
        or (message.tenant_id, message.run_id, message.document_version_id)
        != (snapshot["tenant_id"], snapshot["run_id"], snapshot["document"]["version_id"])
        or run["claim_snapshot_sha256"] != ref["sha256"]
        or any(envelope.get(k) != v for k, v in expected.items())
        or [item["claim_id"] for item in claims] != original_ids
        or envelope["coverage"] != coverage
        or envelope["stage_status"] not in {"blocked", "needs_review", "completed"}
        or envelope["downstream_status"] != "human_review"
        or any(item["tag_runs"] and item.get("review_inputs") is None for item in claims)
        or any(not item["tag_runs"] and item["decision"] is not None for item in claims)
    ):
        raise ValueError("TAG_CHECKPOINT_INVALID")
    # Only the caller holding this same transaction's fence can publish revisions.
    for item in claims:
        if item["tag_runs"]:
            review = item["review_inputs"]
            if (
                review["tag_runs"] != item["tag_runs"]
                or review["decision"] != item["decision"]
                or review["claim"]["claim_id"] != item["claim_id"]
            ):
                raise ValueError("TAG_REVIEW_INPUT_MISMATCH")
    return coverage


class LocalTagStore:
    def __init__(self, store, uploads, parser):
        self.store, self.uploads, self.parser = store, uploads, parser
        self.claims = LocalClaimStore(store, uploads, parser)

    def verify_context_sources(self, inputs, refs):
        """Source-only supplementary span attestations; never mutate frozen inputs."""
        from dataclasses import replace

        from proofops.adapters.local.claim_source_verification import (
            attest_claim_spans,
            claim_source_policy,
        )
        from proofops.adapters.local.table_span_source_verification import (
            attest_table_spans,
            table_span_policy,
        )
        from proofops.application.evidence.span_citations import (
            span_verified_graph,
            verify_source_ref,
        )

        claim, graph = inputs.context.claim, inputs.original
        if not 1 <= len(refs) <= 6:
            raise ValueError("CONTEXT_SOURCE_LIMIT")
        source = self.uploads.read_original(claim.tenant_id, claim.document_version_id)
        if sha256(source).hexdigest() != graph.source_sha256:
            raise ValueError("CONTEXT_SOURCE_MISMATCH")
        kinds = {block.source_id: block.kind for block in graph.blocks}
        if any(
            kinds.get(ref.source_id) not in {"paragraph", "table_cell", "table_row"} for ref in refs
        ):
            raise ValueError("CONTEXT_SOURCE_REJECTED")
        unresolved = tuple(
            r
            for r in refs
            if verify_source_ref(r, graph, tenant_id=claim.tenant_id).verification_state
            != "verified"
        )
        paragraphs = tuple(ref for ref in unresolved if kinds[ref.source_id] == "paragraph")
        tables = tuple(ref for ref in unresolved if kinds[ref.source_id] != "paragraph")
        if tables:
            paragraph_receipt = (
                attest_claim_spans(graph, source, paragraphs, tenant_id=claim.tenant_id)
                if paragraphs
                else None
            )
            table_receipt = attest_table_spans(graph, source, tables, tenant_id=claim.tenant_id)
            if (
                (paragraph_receipt and len(paragraph_receipt["records"]) != len(paragraphs))
                or len(table_receipt["records"]) != len(tables)
                or any(
                    record["status"] != "verified"
                    for item in (paragraph_receipt, table_receipt)
                    if item
                    for record in item["records"]
                )
            ):
                raise ValueError("CONTEXT_SOURCE_REJECTED")
            receipts = {"paragraph": paragraph_receipt, "table": table_receipt}
            records = {
                kind: iter(item["records"] if item else ()) for kind, item in receipts.items()
            }
            ordered = [
                next(records["paragraph" if kinds[ref.source_id] == "paragraph" else "table"])
                for ref in unresolved
            ]
            receipt = dict(
                schema="context_source_attestation_v1",
                tenant_id=claim.tenant_id,
                document_version_id=graph.document_version_id,
                parse_manifest_id=graph.parse_manifest_id,
                source_sha256=graph.source_sha256,
                policy_hashes={
                    "paragraph": canonical_hash(claim_source_policy()),
                    "table": canonical_hash(table_span_policy()),
                },
                receipts=receipts,
                records=ordered,
            )
            receipt["artifact_sha256"] = canonical_hash(receipt)
        else:
            # Keep R38 paragraph-only receipts byte-for-byte compatible on replay.
            receipt = attest_claim_spans(graph, source, unresolved, tenant_id=claim.tenant_id)
        if len(receipt["records"]) != len(unresolved) or any(
            record["status"] != "verified" for record in receipt["records"]
        ):
            raise ValueError("CONTEXT_SOURCE_REJECTED")
        scoped = span_verified_graph(
            graph,
            (
                *getattr(graph, "verified_spans", ()),
                *(replace(r, verification_state="verified") for r in unresolved),
            ),
            canonical_hash(
                dict(
                    prior=getattr(graph, "span_receipt_sha256", ""),
                    supplemental=receipt["artifact_sha256"],
                )
            ),
        )
        return scoped, receipt

    def _load_snapshot_with_evidence(self, tenant_id, run_id):
        # Share the evidence replay used to verify the tag checkpoint pins.
        run = self.store.jobs.get_run(tenant_id, run_id)
        message = JobMessage(**run["tag_job"])
        payload = self.store.jobs.read_checkpoint(message)
        if payload is None or sha256(payload).hexdigest() != run["tag_snapshot_sha256"]:
            raise ValueError("TAG_CHECKPOINT_HASH_MISMATCH")
        envelope = json.loads(payload)
        evidence = self.claims.load_evidence(tenant_id, run_id)
        extraction = evidence[0]
        snapshot = self.store.snapshot(tenant_id, run_id)
        expected = tag_pins(snapshot, extraction, run["claim_snapshot_sha256"])
        if any(envelope.get(k) != v for k, v in expected.items()):
            raise ValueError("TAG_CHECKPOINT_PIN_MISMATCH")
        return envelope, evidence

    def load_snapshot(self, tenant_id, run_id):
        return self._load_snapshot_with_evidence(tenant_id, run_id)[0]

    def load_inputs(self, tenant_id, run_id, claim_id):
        envelope, evidence = self._load_snapshot_with_evidence(tenant_id, run_id)
        item = next((item for item in envelope["claims"] if item["claim_id"] == claim_id), None)
        if item is None or item.get("review_inputs") is None:
            raise KeyError("tagged claim not published")
        raw = item["review_inputs"]
        _, discovery, graph = evidence
        claim = next(c for c in discovery.claims if c.claim_id == claim_id)
        rulepack = RulePackSnapshot(**self.store.snapshot(tenant_id, run_id)["rulepack"])
        packet = freeze_packet({k: v for k, v in raw["packet"].items() if k != "packet_sha256"})
        original_packet = freeze_packet(
            {k: v for k, v in raw["original_packet"].items() if k != "packet_sha256"}
        )
        context = ClaimContext(
            claim, {k: SourceRef(**v) if v else None for k, v in raw["dimensions"].items()}
        )
        runs = []
        for entry in raw["tag_runs"]:
            request = entry["request"]
            runs.append(
                TagRun(
                    **(
                        entry
                        | dict(
                            request=CacheRequest(
                                **(request | {"namespace": CacheNamespace(**request["namespace"])})
                            ),
                            usage=TokenUsage(**entry["usage"]) if entry["usage"] else None,
                            guarded=llm_tags_from_dict(entry["guarded"])
                            if entry["guarded"]
                            else None,
                            errors=tuple(entry["errors"]),
                            source_scopes=tuple(tuple(v) for v in entry["source_scopes"]),
                            binding_hashes=tuple(tuple(v) for v in entry["binding_hashes"]),
                        )
                    )
                )
            )
        consensus = form_consensus(
            tuple(runs),
            packet=packet,
            rulepack=rulepack,
            tenant_id=tenant_id,
            tag_revision=raw["tag_revision"],
        )
        rule_context = RuleContext(**raw["rule_context"])
        decision = (
            evaluate(consensus.confirmed_tags, rule_context, rulepack)
            if consensus.confirmed_tags
            and envelope.get("rulepack_use") != "candidate_tagging_reference_only"
            else None
        )
        inputs = ReviewInputs(
            run_id,
            context,
            graph,
            rulepack,
            rule_context,
            packet,
            original_packet,
            tuple(runs),
            consensus,
            {
                sid: {k: SourceRef(**v) if v else None for k, v in values.items()}
                for sid, values in raw["relation_tags"].items()
            },
            raw["tag_revision"],
            decision,
        )
        inputs.validate()
        if canonical_hash(inputs.snapshot()) != canonical_hash(raw):
            raise ValueError("TAG_REPLAY_MISMATCH")
        return inputs
