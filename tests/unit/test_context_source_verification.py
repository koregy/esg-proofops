"""Routing and receipt compatibility for report-level source verification."""

from dataclasses import asdict, replace
from hashlib import sha256
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
from proofops.adapters.local import claim_source_verification, table_span_source_verification
from proofops.adapters.local.tag_store import LocalTagStore
from proofops.domain.provenance import canonical_hash

from tests.acceptance.test_citations import TENANT, snapshot


def _case(monkeypatch, table_kind="table_cell"):
    graph, _ = snapshot("GRI Index AA1000AS v3")
    paragraph = replace(graph.blocks[0], quality="unverified")
    candidate = replace(
        paragraph.candidates[0],
        kind=table_kind,
        source=replace(paragraph.candidates[0].source, source_native_id="table-cell"),
    )
    cell = replace(
        paragraph,
        source_id=str(uuid5(NAMESPACE_URL, "table-cell")),
        kind=table_kind,
        candidates=(candidate,),
    )
    other = replace(
        paragraph,
        source_id=str(uuid5(NAMESPACE_URL, "whole-table")),
        kind="table",
        candidates=(
            replace(
                candidate,
                kind="table",
                source=replace(candidate.source, source_native_id="whole-table"),
            ),
        ),
    )
    source = b"source PDF bytes"
    graph = replace(
        graph,
        source_sha256=sha256(source).hexdigest(),
        blocks=(paragraph, cell, other),
        candidates=(
            replace(
                graph.candidates[0],
                source_sha256=sha256(source).hexdigest(),
                blocks=(paragraph.candidates[0], candidate, other.candidates[0]),
            ),
        ),
    )
    inputs = SimpleNamespace(
        context=SimpleNamespace(
            claim=SimpleNamespace(tenant_id=TENANT, document_version_id=graph.document_version_id)
        ),
        original=graph,
    )
    store = LocalTagStore(None, SimpleNamespace(read_original=lambda *_: source), None)
    refs = tuple(block.source_ref() for block in (paragraph, cell, other))

    def receipt(kind, selected):
        result = dict(
            schema=kind,
            policy={"schema": kind},
            records=[{"ref": asdict(ref), "status": "verified"} for ref in selected],
        )
        result["artifact_sha256"] = canonical_hash(result)
        return result

    calls = []

    def paragraphs(_graph, _source, selected, *, tenant_id):
        calls.append(("paragraph", selected, tenant_id))
        return receipt("claim_source_attestation_v1", selected)

    def tables(_graph, _source, selected, *, tenant_id):
        calls.append(("table", selected, tenant_id))
        return receipt("table_span_source_attestation_v1", selected)

    monkeypatch.setattr(claim_source_verification, "attest_claim_spans", paragraphs)
    monkeypatch.setattr(table_span_source_verification, "attest_table_spans", tables)
    return store, inputs, refs, calls, receipt


def test_paragraph_receipt_is_identical_to_r38(monkeypatch):
    store, inputs, refs, calls, receipt = _case(monkeypatch)
    _, actual = store.verify_context_sources(inputs, (refs[0],))
    assert actual == receipt("claim_source_attestation_v1", (refs[0],))
    assert calls == [("paragraph", (refs[0],), TENANT)]


@pytest.mark.parametrize("table_kind", ("table_cell", "table_row"))
def test_table_and_mixed_receipts_replay_identically(monkeypatch, table_kind):
    store, inputs, refs, calls, _ = _case(monkeypatch, table_kind)
    scoped, table_only = store.verify_context_sources(inputs, (refs[1],))
    assert table_only["records"][0]["status"] == "verified"
    assert scoped.verified_spans[-1].source_id == refs[1].source_id
    _, first = store.verify_context_sources(inputs, refs[:2])
    _, replay = store.verify_context_sources(inputs, refs[:2])
    assert canonical_hash(first) == canonical_hash(replay)
    assert first["artifact_sha256"] == canonical_hash(
        {key: value for key, value in first.items() if key != "artifact_sha256"}
    )
    assert set(first["policy_hashes"]) == {"paragraph", "table"}
    assert [record["ref"]["source_id"] for record in first["records"]] == [
        refs[0].source_id,
        refs[1].source_id,
    ]
    assert calls[-2:] == [
        ("paragraph", (refs[0],), TENANT),
        ("table", (refs[1],), TENANT),
    ]


def test_whole_table_and_unverified_table_record_are_rejected(monkeypatch):
    store, inputs, refs, calls, _ = _case(monkeypatch)
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_REJECTED"):
        store.verify_context_sources(inputs, (refs[2],))
    assert calls == []

    def unresolved(_graph, _source, selected, *, tenant_id):
        return {"records": [{"status": "unresolved"}]}

    monkeypatch.setattr(table_span_source_verification, "attest_table_spans", unresolved)
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_REJECTED"):
        store.verify_context_sources(inputs, (refs[0], refs[1]))
