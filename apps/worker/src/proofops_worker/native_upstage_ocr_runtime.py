"""Authorized NEW-run Upstage OCR requests for native paragraphs (parse stage only).

Mirrors ``raster_runtime`` for the separately versioned ``native_upstage_ocr`` policy.
It rechecks the lease, the frozen policy, the registry profiles and the local raster
preflight before rendering and again before handing off. It registers one immutable
request, and at most one dispatch goes through the unchanged ``UpstageParseProbe``
(shared ledger, USD reservation, no retries). An ambiguous call stays pending and is
never redispatched. Nothing here publishes or promotes; composition is offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from proofops.adapters.local import native_upstage_ocr_store as store_records
from proofops.adapters.local.native_upstage_ocr import (
    REQUEST_SCHEMA,
    live_policy_for,
)
from proofops.adapters.local.native_upstage_ocr_widget import eligible_words
from proofops.adapters.local.raster_ocr import prepare_raster_ocr
from proofops.adapters.local.run_artifacts import load_run_inputs
from proofops.adapters.local.upstage_parse import PARSE_MODEL_PINNED
from proofops.application.authorization import AuthContext
from proofops.application.ports.jobs import LeaseLost
from proofops.application.preflight import check_local_upstage_raster
from proofops.application.registry import artifact_sha256
from proofops.application.runs import validate_upstage_ocr_snapshot
from proofops.domain.provenance import canonical_hash


def prepare_authorized_upstage_ocr(runner, lease, graph, native, source_ids):
    message = lease.message
    if message.stage != "parse" or not runner.store.jobs.can_call(lease, now=int(runner.clock())):
        raise LeaseLost("LEASE_LOST")
    snapshot, source, profile = load_run_inputs(
        runner.store, runner.uploads, tenant_id=message.tenant_id, run_id=message.run_id
    )
    validate_upstage_ocr_snapshot(snapshot)
    if "native_upstage_ocr_policy" not in snapshot or (
        message.document_version_id,
        message.input_hash,
        graph.tenant_id,
        graph.document_version_id,
        graph.parse_manifest_id,
        graph.source_sha256,
    ) != (
        source.document_version_id,
        snapshot["input_hash"],
        message.tenant_id,
        source.document_version_id,
        profile.parse_manifest_id,
        source.sha256,
    ):
        raise ValueError("UPSTAGE_OCR_RUN_INPUT_MISMATCH")
    policy = snapshot["native_upstage_ocr_policy"]

    def authorize():
        if not runner.store.jobs.can_call(lease, now=int(runner.clock())):
            raise LeaseLost("LEASE_LOST")
        if policy != live_policy_for(policy):
            raise ValueError("UPSTAGE_OCR_EXECUTION_POLICY_CHANGED")
        auth = AuthContext("local-worker", message.tenant_id, "viewer", frozenset(), message.run_id)
        binding = snapshot["native_upstage_ocr_runtime"]
        for kind, frozen, identifier in (
            ("runtime", binding, "runtime_binding_id"),
            ("consent", snapshot["consent"], "consent_profile_id"),
            ("rights", snapshot["rights"], "rights_profile_id"),
        ):
            try:
                current = runner.uploads.registry.resolve_profile(auth, kind, frozen[identifier])
            except LookupError:
                raise ValueError("UPSTAGE_OCR_AUTHORIZATION_REVOKED") from None
            if artifact_sha256(current) != artifact_sha256(frozen):
                raise ValueError("UPSTAGE_OCR_AUTHORIZATION_CHANGED")
        gate = check_local_upstage_raster(
            binding=binding,
            consent=snapshot["consent"],
            auth=auth,
            checked_at=datetime.fromtimestamp(int(runner.clock()), UTC).isoformat(),
            source_sha256=source.sha256,
            document_rights=snapshot["document"]["metadata"]["rights_profile_id"],
            model_sha256=canonical_hash(
                dict(model=PARSE_MODEL_PINNED, provider="upstage", transport="UpstageParseProbe")
            ),
        )
        if not gate.ready:
            raise ValueError("UPSTAGE_OCR_PREFLIGHT_BLOCKED")

    authorize()
    if (
        not isinstance(source_ids, tuple)
        or not 1 <= len(source_ids) <= policy["max_pages"]
        or any(not isinstance(sid, str) for sid in source_ids)
        or len(set(source_ids)) != len(source_ids)
    ):
        raise ValueError("UPSTAGE_OCR_REQUEST_LIMIT_INVALID")
    blocks = {block.source_id: block for block in graph.blocks}
    if any(
        sid not in blocks or blocks[sid].page_num not in snapshot["selected_pages"]
        for sid in source_ids
    ):
        raise ValueError("UPSTAGE_OCR_PAGE_SCOPE_MISMATCH")
    from proofops.adapters.local.native_replay_cache import replay_cached

    replay_cached(native, graph, source.content, tenant_id=message.tenant_id)
    eligible = {
        sid
        for sid in eligible_words(
            snapshot, native, graph, source.content, tenant_id=message.tenant_id
        )
        if blocks[sid].page_num in snapshot["selected_pages"]
    }
    if not set(source_ids) <= eligible:
        raise ValueError("NATIVE_UPSTAGE_OCR_INELIGIBLE")
    data, correspondence = prepare_raster_ocr(
        graph, source.content, source_ids, tenant_id=message.tenant_id
    )
    request = dict(
        schema=REQUEST_SCHEMA,
        tenant_id=message.tenant_id,
        run_id=message.run_id,
        job_id=message.job_id,
        document_version_id=source.document_version_id,
        parse_manifest_id=profile.parse_manifest_id,
        input_hash=message.input_hash,
        source_sha256=source.sha256,
        selected_pages=snapshot["selected_pages"],
        mode=policy["mode"],
        max_pages=policy["max_pages"],
        max_calls=policy["max_calls"],
        submitted_pages=len(correspondence["rows"]),
        eligible_source_ids=sorted(eligible),
        requested_source_ids=list(source_ids),
        policy_sha256=snapshot["native_upstage_ocr_policy_hash"],
        native_attestation_sha256=canonical_hash(native),
        correspondence=correspondence,
    )
    request["request_id"] = store_records.request_id_for(message.job_id, request)
    # Rendering may outlive authority or policy; recheck before handing off.
    authorize()
    return data, request


def dispatch_authorized_upstage_ocr(runner, lease, graph, native, source_ids, *, probe, ledger):
    """One durable request, at most one dispatch; ambiguous requests stay pending."""
    from proofops.adapters.local.upstage_parse import UpstageParseProbe

    if (
        not isinstance(probe, UpstageParseProbe)
        or Path(probe.ledger).resolve() != Path(ledger).resolve()
    ):
        raise ValueError("UPSTAGE_OCR_LEDGER_MISMATCH")
    data, request = prepare_authorized_upstage_ocr(runner, lease, graph, native, source_ids)
    jobs = runner.store.jobs
    created = store_records.register_request(jobs, lease, request, now=int(runner.clock()))
    if created:
        # The shared transport reserves before HTTP. A crash or unknown response
        # leaves the registration in place and never authorizes a retry.
        jobs.heartbeat(lease, now=int(runner.clock()), lease_seconds=300)
        receipt = probe.parse(data, request_id=request["request_id"], mode=request["mode"])
        store_records.finish_request(jobs, lease, request["request_id"], receipt)
    saved = store_records.receipt_for(jobs, lease.message, request["request_id"])
    if saved is None:
        raise ValueError("UPSTAGE_OCR_REQUEST_PENDING")
    if not jobs.can_call(lease, now=int(runner.clock())):
        raise LeaseLost("LEASE_LOST")
    return request, saved
