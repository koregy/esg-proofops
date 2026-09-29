"""Opt-in widening of ``native_upstage_ocr`` eligibility by the static pushbutton gate.

``native_upstage_ocr`` and its store are hash-pinned by every stored Upstage OCR
policy, so they stay byte-identical. A run opts in only through the snapshot pair
``native_upstage_ocr_widget_visibility`` / ``..._hash``, frozen at run creation.
Without it every function here delegates to the unchanged v1 behaviour. With it,
eligibility is the v1 set plus the records ``native_widget_visibility`` marks
eligible, and corroboration uses the same exact ``normalized_quote_fold_v1``
comparison against those records' native words.
"""

from __future__ import annotations

from dataclasses import replace

from proofops.adapters.local import native_upstage_ocr as v1
from proofops.adapters.local import raster_ocr
from proofops.application.evidence import citations
from proofops.domain.provenance import canonical_hash

KEY = "native_upstage_ocr_widget_visibility"
HASH_KEY = KEY + "_hash"


def widget_policy(snapshot):
    """The frozen opt-in policy, or None; a changed verifier fails closed."""
    from proofops.adapters.local.native_widget_visibility import (
        native_widget_visibility_policy,
    )

    if KEY not in snapshot and HASH_KEY not in snapshot:
        return None
    policy = snapshot.get(KEY)
    if (
        "native_upstage_ocr_policy" not in snapshot
        or canonical_hash(policy) != snapshot.get(HASH_KEY)
        or policy != native_widget_visibility_policy()
    ):
        raise ValueError("UPSTAGE_OCR_WIDGET_POLICY_CHANGED")
    return policy


def eligible_words(snapshot, native, graph, source, *, tenant_id):
    """``{source_id: native words}`` for every Upstage-eligible paragraph on selected pages."""
    from proofops.adapters.local.native_widget_visibility import (
        attest_widget_visibility,
        widget_eligible_words,
    )

    words = v1.native_words(native)
    eligible = {sid: words[sid] for sid in v1.eligible_upstage_sources(native)}
    if widget_policy(snapshot) is not None:
        proof = attest_widget_visibility(native, graph, source, tenant_id=tenant_id)
        widened = widget_eligible_words(proof)
        if set(widened) & set(eligible):
            raise ValueError("NATIVE_UPSTAGE_OCR_INELIGIBLE")
        eligible.update(widened)
    pages = {block.source_id: block.page_num for block in graph.blocks}
    return {
        sid: text for sid, text in eligible.items() if pages.get(sid) in snapshot["selected_pages"]
    }


def _corroborate(native, graph, source, entries, *, tenant_id, words):
    """v1 ``corroborate`` with the widened eligible words; same exact comparison."""
    from proofops.adapters.local.native_replay_cache import replay_cached

    baseline = replay_cached(native, graph, source, tenant_id=tenant_id)
    verified_before = {b.source_id for b in baseline.blocks if b.quality == "verified"}
    blocks = {block.source_id: block for block in graph.blocks}
    corroborated = set()
    for request, receipt, receipt_sha256 in entries:
        correspondence = request["correspondence"]
        rows = raster_ocr.replay_raster_ocr(
            correspondence,
            receipt,
            graph,
            source,
            request_sha256=canonical_hash(correspondence),
            receipt_sha256=receipt_sha256,
            tenant_id=tenant_id,
        )
        for row in rows:
            source_id = row["source_id"]
            block = blocks.get(source_id)
            if (
                source_id not in words
                or source_id in verified_before
                or block is None
                or block.kind != "paragraph"
                or citations._normalized(words[source_id]) != citations._normalized(block.raw_text)
            ):
                raise ValueError("NATIVE_UPSTAGE_OCR_INELIGIBLE")
            if v1.matches(words[source_id], row["external_ocr"]):
                corroborated.add(source_id)
    result = replace(
        baseline,
        blocks=tuple(
            replace(block, quality="verified") if block.source_id in corroborated else block
            for block in baseline.blocks
        ),
    )
    return result, corroborated


def compose_checkpoint(snapshot, message, native, graph, source, entries, refs):
    """v1 ``compose_checkpoint`` unless the run opted in; then the same checks, widened."""
    from proofops.adapters.local.native_upstage_ocr_store import validate_request

    if widget_policy(snapshot) is None:
        return v1.compose_checkpoint(snapshot, message, native, graph, source, entries, refs)
    policy = snapshot.get("native_upstage_ocr_policy")
    if (
        not isinstance(policy, dict)
        or policy != v1.live_policy_for(policy)
        or canonical_hash(policy) != snapshot.get("native_upstage_ocr_policy_hash")
    ):
        raise ValueError("UPSTAGE_OCR_POLICY_CHANGED")
    if (
        not isinstance(native, dict)
        or native.get("tenant_id") != message.tenant_id
        or native.get("source_sha256") != snapshot["document"]["sha256"]
        or native.get("parse_manifest_id") != graph.parse_manifest_id
        or graph.source_sha256 != snapshot["document"]["sha256"]
    ):
        raise ValueError("UPSTAGE_OCR_CHECKPOINT_INPUT_INVALID")
    words = eligible_words(snapshot, native, graph, source, tenant_id=message.tenant_id)
    eligible = set(words)
    if len(entries) > policy["max_calls"] or len(entries) != len(refs):
        raise ValueError("UPSTAGE_OCR_MAX_CALLS_EXCEEDED")
    requested: set[str] = set()
    for request, _receipt, _receipt_sha in entries:
        validate_request(request, message=message, snapshot=snapshot)
        if (
            request["native_attestation_sha256"] != canonical_hash(native)
            or request["correspondence"].get("graph_sha256") != native.get("input_graph_sha256")
            or request["eligible_source_ids"] != sorted(eligible)
            or requested & set(request["requested_source_ids"])
        ):
            raise ValueError("UPSTAGE_OCR_CHECKPOINT_REQUEST_MISMATCH")
        requested.update(request["requested_source_ids"])
    result, corroborated = _corroborate(
        native, graph, source, entries, tenant_id=message.tenant_id, words=words
    )
    coverage = {
        "eligible_source_ids": sorted(eligible),
        "requested_source_ids": sorted(requested),
        "skipped_source_ids": sorted(eligible - requested),
        "corroborated_source_ids": sorted(corroborated),
        "unresolved_source_ids": sorted(eligible - corroborated),
        "complete": requested == eligible,
    }
    return result, coverage, list(refs), snapshot["native_upstage_ocr_policy_hash"]
