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


def _replace_batch(source, graph, transform):
    batch = graph.candidates[0]
    changed = replace(
        batch,
        source_sha256=sha256(source).hexdigest(),
        blocks=tuple(transform(block) for block in batch.blocks),
    )
    return fuse_candidates((changed,), tenant_id=TENANT)


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


def test_oversized_cell_bbox_cannot_attest_only_its_matching_subspan(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()

    def widen(block):
        if block.source.source_native_id == "C01":
            return replace(block, source=replace(block.source, native_bbox=(120, 740, 320, 770)))
        return block

    graph = _replace_batch(source, graph, widen)
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    value = next(
        block
        for block in graph.blocks
        if block.kind == "table_cell"
        and any(item.source.source_native_id == "C01" for item in block.candidates)
    )

    receipt = verifier.attest_table_spans(
        graph, source, (ref_for(value, "2024"),), tenant_id=TENANT
    )

    assert receipt["records"][0]["status"] == "unresolved"


def test_table_bbox_disjoint_from_row_cannot_attest_cell_or_row(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    graph = _replace_batch(
        source,
        graph,
        lambda block: replace(block, source=replace(block.source, native_bbox=(330, 710, 550, 770)))
        if block.source.source_native_id == "T"
        else block,
    )
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    value = cell(graph, "2024")
    row = next(
        block
        for block in graph.blocks
        if block.kind == "table_row" and block.raw_text.startswith("Year")
    )

    receipt = verifier.attest_table_spans(
        graph,
        source,
        (ref_for(value, "2024"), ref_for(row, "2024")),
        tenant_id=TENANT,
    )

    assert [record["status"] for record in receipt["records"]] == ["unresolved", "unresolved"]


def test_cell_bbox_in_another_column_cannot_attest_same_text(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    assert source.count(b"(2025)") == 1
    source = source.replace(b"(2025)", b"(2024)")
    row_text = "Year\t2024\t2024"
    table_text = row_text + "\nMWh\t25\t79"

    def move_to_other_column(block):
        native_id = block.source.source_native_id
        if native_id == "C01":
            other = next(
                item for item in graph.candidates[0].blocks if item.source.source_native_id == "C02"
            )
            return replace(
                block,
                context=("different declared slot",),
                source=replace(block.source, native_bbox=other.source.native_bbox),
            )
        if native_id == "C02":
            return replace(block, source=replace(block.source, raw_text="2024"))
        if native_id == "R0":
            return replace(block, source=replace(block.source, raw_text=row_text))
        if native_id == "T":
            return replace(block, source=replace(block.source, raw_text=table_text))
        return block

    graph = _replace_batch(source, graph, move_to_other_column)
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    value = next(
        block
        for block in graph.blocks
        if block.kind == "table_cell"
        and any(item.source.source_native_id == "C01" for item in block.candidates)
    )

    receipt = verifier.attest_table_spans(
        graph, source, (ref_for(value, "2024"),), tenant_id=TENANT
    )

    assert receipt["records"][0]["status"] == "unresolved"


def test_cell_bbox_in_another_row_cannot_attest_same_text(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    row_text = "Year\t25\t2025"
    table_text = row_text + "\nMWh\t25\t79"

    def move_to_other_row(block):
        native_id = block.source.source_native_id
        if native_id == "C01":
            other = next(
                item for item in graph.candidates[0].blocks if item.source.source_native_id == "C11"
            )
            return replace(
                block,
                context=("different declared slot",),
                source=replace(
                    block.source,
                    native_bbox=other.source.native_bbox,
                    raw_text="25",
                    char_end=block.source.char_start + 2,
                ),
            )
        if native_id == "R0":
            return replace(
                block,
                source=replace(
                    block.source,
                    raw_text=row_text,
                    char_end=block.source.char_start + len(row_text),
                ),
            )
        if native_id == "T":
            return replace(
                block,
                source=replace(
                    block.source,
                    raw_text=table_text,
                    char_end=block.source.char_start + len(table_text),
                ),
            )
        return block

    graph = _replace_batch(source, graph, move_to_other_row)
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    value = next(
        block
        for block in graph.blocks
        if block.kind == "table_cell"
        and any(item.source.source_native_id == "C01" for item in block.candidates)
    )

    receipt = verifier.attest_table_spans(graph, source, (ref_for(value, "25"),), tenant_id=TENANT)

    assert receipt["records"][0]["status"] == "unresolved"


def test_row_without_index_rejects_a_child_with_a_conflicting_row_index(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()

    def mismatch_child(block):
        if block.source.source_native_id == "C01":
            return replace(block, row_number=1)
        return block

    graph = _replace_batch(source, graph, mismatch_child)
    monkeypatch.setattr(verifier, "_rendered_text", crop_ocr)
    row = next(
        block
        for block in graph.blocks
        if block.kind == "table_row" and block.raw_text.startswith("Year")
    )

    receipt = verifier.attest_table_spans(graph, source, (ref_for(row, "2024"),), tenant_id=TENANT)

    assert receipt["records"][0]["status"] == "unresolved"


def test_partial_rendered_cell_cannot_be_replaced_with_quote_crop(monkeypatch):
    from proofops.adapters.local import table_span_source_verification as verifier

    source, graph = fixture()
    value = cell(graph, "2024")
    ref = ref_for(value, "2024")
    monkeypatch.setattr(
        verifier,
        "_rendered_text",
        lambda *a, **k: dict(status="read", text="table columns out of order"),
    )
    receipt = verifier.attest_table_spans(graph, source, (ref,), tenant_id=TENANT)

    assert receipt["records"][0]["status"] == "unresolved"
    assert receipt["records"][0]["reason"] == "rendered_cell_text_mismatch"


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
