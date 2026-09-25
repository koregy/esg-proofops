from dataclasses import replace
from hashlib import sha256

from proofops.application.ingest.graph_fusion import fuse_candidates
from proofops.domain.provenance import canonical_hash

from tests.acceptance.test_parsing import TENANT, candidate
from tests.integration.test_native_table_admission import fixture


def ref_for(block, quote):
    start = block.raw_text.index(quote)
    return block.source_ref(normalized_char_start=start, normalized_char_end=start + len(quote))


def cell(graph, text):
    return next(b for b in graph.blocks if b.kind == "table_cell" and b.raw_text == text)


def crop_ocr(page, box, **kwargs):
    return dict(status="read", text=page.crop(box).extract_text() or "", image_sha256="a" * 64)


def test_table_cell_and_row_quote_are_attested_inside_their_geometry(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    refs = (
        ref_for(cell(graph, "2024"), "2024"),
        ref_for(
            next(
                b for b in graph.blocks if b.kind == "table_row" and b.raw_text.startswith("Year")
            ),
            "2024",
        ),
    )

    receipt = verifier.attest_table_spans(graph, source, refs, tenant_id=TENANT)

    assert [record["status"] for record in receipt["records"]] == ["verified", "verified"]
    assert all(record["reason"] is None for record in receipt["records"])
    unsigned = dict(receipt)
    artifact_sha256 = unsigned.pop("artifact_sha256")
    assert receipt["policy"]["schema"] == "table_span_source_policy_v1"
    assert artifact_sha256 == canonical_hash(unsigned)


def test_wrong_text_other_column_duplicate_and_token_cut_stay_unresolved(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    value = cell(graph, "2024")
    valid = ref_for(value, "2024")
    cut = ref_for(value, "2024")
    cut = replace(cut, char_end=cut.char_start + 3, quote="202")
    other_column_batch = candidate(
        "other_column",
        [("C", "table_cell", "2024", (220, 740, 320, 770), ())],
    )
    other_column_batch = replace(other_column_batch, source_sha256=sha256(source).hexdigest())
    other_column_graph = fuse_candidates((other_column_batch,), tenant_id=TENANT)
    other_column = ref_for(cell(other_column_graph, "2024"), "2024")
    monkeypatch.setattr(
        verifier,
        "_rendered_cell",
        lambda *a, **k: dict(status="read", text="2025", image_sha256="b" * 64),
    )
    monkeypatch.setattr(
        verifier,
        "_rendered_text",
        lambda *a, **k: dict(status="read", text="2025", image_sha256="b" * 64),
    )
    wrong = verifier.attest_table_spans(graph, source, (valid,), tenant_id=TENANT)
    monkeypatch.undo()
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    misplaced = verifier.attest_table_spans(
        other_column_graph, source, (other_column,), tenant_id=TENANT
    )
    monkeypatch.setattr(
        verifier,
        "_rendered_text",
        lambda *a, **k: dict(status="read", text="2024 2024", image_sha256="c" * 64),
    )
    duplicate = verifier.attest_table_spans(graph, source, (valid,), tenant_id=TENANT)
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    truncated = verifier.attest_table_spans(graph, source, (cut,), tenant_id=TENANT)

    assert [
        receipt["records"][0]["status"] for receipt in (wrong, misplaced, duplicate, truncated)
    ] == ["unresolved"] * 4


def test_unique_native_quote_gets_a_tight_cell_crop_retry(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    value = cell(graph, "2024")
    ref = ref_for(value, "2024")
    monkeypatch.setattr(
        verifier,
        "_rendered_text",
        lambda *a, **k: dict(status="read", text="table columns out of order"),
    )
    seen = []

    def focused(page, box):
        seen.append(box)
        return dict(status="read", text="2024")

    monkeypatch.setattr(verifier, "_rendered_cell", focused)
    receipt = verifier.attest_table_spans(graph, source, (ref,), tenant_id=TENANT)

    assert receipt["records"][0]["status"] == "verified"
    assert len(seen) == 1
    assert value.bbox[0] <= seen[0][0] < seen[0][2] <= value.bbox[2]
    assert value.bbox[1] <= seen[0][1] < seen[0][3] <= value.bbox[3]


def test_missing_geometry_and_non_table_block_stay_unresolved(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    batch = graph.candidates[0]
    missing_geometry_batch = replace(
        batch,
        blocks=tuple(
            replace(block, source=replace(block.source, native_bbox=None))
            if block.source.source_native_id == "C01"
            else block
            for block in batch.blocks
        ),
    )
    missing_graph = fuse_candidates((missing_geometry_batch,), tenant_id=TENANT)
    missing_ref = ref_for(cell(missing_graph, "2024"), "2024")
    paragraph_batch = candidate(
        "paragraph",
        [("P1", "paragraph", "Year", (20, 30, 120, 60), ())],
    )
    paragraph_batch = replace(paragraph_batch, source_sha256=sha256(source).hexdigest())
    paragraph_graph = fuse_candidates((paragraph_batch,), tenant_id=TENANT)
    paragraph_ref = ref_for(paragraph_graph.blocks[0], "Year")

    missing = verifier.attest_table_spans(missing_graph, source, (missing_ref,), tenant_id=TENANT)
    non_table = verifier.attest_table_spans(
        paragraph_graph, source, (paragraph_ref,), tenant_id=TENANT
    )

    assert missing["records"][0]["status"] == "unresolved"
    assert non_table["records"][0]["status"] == "unresolved"
