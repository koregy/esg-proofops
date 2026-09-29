"""Publish source-verified assurance from a committed run and caller-owned probe.

This adapter is usable by product workers without importing evaluation tooling.
Model fields never bypass graph verification or immutable publication.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from proofops.adapters.local.assurance_store import LocalAssuranceStore
from proofops.adapters.local.run_artifacts import load_run_graph
from proofops.adapters.local.upstage_assurance import UpstageAssuranceExtractor
from proofops.application.assurance import extract_assurance
from proofops.application.assurance_producer import select_opinion_boundary
from proofops.application.ports.models import ModelBinding


def run_assurance_producer(
    *,
    store,
    uploads,
    parser,
    tenant_id: str,
    run_id: str,
    source_ids: Sequence[str],
    probe,
    receipts: Path | str,
    statement_id: str | None = None,
    request_id: str | None = None,
    binding: ModelBinding | None = None,
) -> dict:
    """Run the whole producer route once and publish the result.

    Returns a small, honest status dict — it never claims "covered" or
    "verified" beyond what `extract_assurance`/`LocalAssuranceStore.publish`
    themselves determined. On any failure (transport, schema, re-verification)
    this raises rather than returning a partial/guessed status; the caller
    must treat that as not_run, never as "no assurance found".
    """
    graph = load_run_graph(store, uploads, parser, tenant_id=tenant_id, run_id=run_id)
    boundary, _texts = select_opinion_boundary(graph, source_ids)
    extractor = UpstageAssuranceExtractor(probe, receipts)
    tagged_fields = extractor.extract_tagged_fields(
        graph, boundary, request_id=request_id or str(uuid4())
    )
    if not tagged_fields:
        return {
            "status": "not_run",
            "reason": "model returned no recognized assurance fields for this boundary",
            "statement_id": None,
            "semantic_hash": None,
        }
    # Preserve the declared opinion, including uncited qualifications/exclusions.
    # Model-selected fields alone cannot establish that the opinion was readable.
    blocks = {block.source_id: block for block in graph.blocks}
    selected_refs = tuple(blocks[source_id].source_ref() for source_id in boundary.source_ids)
    statement = extract_assurance(
        graph,
        selected_refs,
        binding or ModelBinding(f"upstage-{extractor.model_sha256[:12]}", "assurance", False),
        tagged_fields=tagged_fields,
        tenant_id=tenant_id,
        statement_id=statement_id or str(uuid4()),
        model_sha256=extractor.model_sha256,
        prompt_sha256=extractor.prompt_sha256,
        replicate_id=1,
    )
    assurance_store = LocalAssuranceStore(store, uploads, parser)
    semantic_hash = assurance_store.publish(tenant_id, run_id, statement)
    return {
        "status": "published",
        "statement_id": statement.statement_id,
        "semantic_hash": semantic_hash,
        "unresolved_fields": list(statement.unresolved_fields),
        "provider": statement.provider,
        "level": statement.level,
        "reporting_period": statement.reporting_period,
    }
