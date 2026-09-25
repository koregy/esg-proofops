"""Original-PDF attestation for exact table-cell and table-row source spans."""

import io
from dataclasses import asdict, replace
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import pdfplumber
from pdfminer.pdftypes import resolve1

from proofops.adapters.local import claim_source_verification as claim_verifier
from proofops.adapters.local.native_glyph_geometry import native_word_ink_geometry
from proofops.adapters.local.selected_cell_table_verification import _rendered_cell
from proofops.adapters.local.source_verification import _rendered_text
from proofops.application import claims as claim_validation
from proofops.application.evidence.citations import _normalized, verify_source_ref
from proofops.application.ingest.gri import _validate_graph
from proofops.domain.provenance import canonical_hash

_TABLE_KINDS = {"table_cell", "table_row"}
_MAX_ROW_CELLS = 40


def table_span_policy():
    """Versioned hashes for every reader, geometry, normalizer and quote guard."""
    local = Path(__file__).parent
    return dict(
        schema="table_span_source_policy_v1",
        verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        quote_guard_sha256=sha256(Path(claim_verifier.__file__).read_bytes()).hexdigest(),
        claim_validation_sha256=sha256(Path(claim_validation.__file__).read_bytes()).hexdigest(),
        cell_reader_sha256=sha256(
            local.joinpath("selected_cell_table_verification.py").read_bytes()
        ).hexdigest(),
        glyph_geometry_sha256=sha256(
            local.joinpath("native_glyph_geometry.py").read_bytes()
        ).hexdigest(),
        rendered_reader_sha256=sha256(
            local.joinpath("source_verification.py").read_bytes()
        ).hexdigest(),
        ocr_script_sha256=sha256(local.joinpath("native_ocr.swift").read_bytes()).hexdigest(),
        citation_sha256=sha256(
            Path(verify_source_ref.__code__.co_filename).read_bytes()
        ).hexdigest(),
        readers={name: version(name) for name in ("pdfplumber", "pdfminer.six", "pypdfium2")},
    )


def _candidate_bound(graph, candidate):
    return any(candidate in batch.blocks for batch in graph.candidates)


def _has_winner(block):
    return type(block.winner) is int and 0 <= block.winner < len(block.candidates)


def _inside(inner, outer):
    return (
        outer[0] - 0.001 <= inner[0] < inner[2] <= outer[2] + 0.001
        and outer[1] - 0.001 <= inner[1] < inner[3] <= outer[3] + 0.001
    )


def _quote_crop_box(reading, quote):
    text = reading["native_text"]
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        return None
    end = start + len(quote)
    words = [
        word for word in reading["words"] if word["char_start"] < end and word["char_end"] > start
    ]
    if not words:
        return None
    box = reading["bbox"]
    return (
        max(box[0], min(word["bbox"][0] for word in words) - 1),
        max(box[1], min(word["bbox"][1] for word in words) - 1),
        min(box[2], max(word["bbox"][2] for word in words) + 1),
        min(box[3], max(word["bbox"][3] for word in words) + 1),
    )


def _occurrences(text, quote):
    count, start = 0, 0
    while quote and (found := text.find(quote, start)) >= 0:
        count += 1
        start = found + 1
    return count


def _tight_render(page, reading, quote):
    original = reading["rendered"]
    if _occurrences(original.get("text", ""), quote):
        return
    box = _quote_crop_box(reading, quote)
    if box is None:
        return
    focused = _rendered_cell(page, box)
    reading["rendered_attempts"] = [original, focused]
    reading["rendered_bbox"] = box
    reading["rendered"] = focused


def _row_cells(graph, row):
    blocks = {block.source_id: block for block in graph.blocks}
    source_ids = [
        edge.source_id
        for edge in graph.edges
        if edge.relation == "table_parent" and edge.target_id == row.source_id
    ]
    cells = [blocks.get(source_id) for source_id in source_ids]
    if not cells or len(cells) > _MAX_ROW_CELLS or any(cell is None for cell in cells):
        return None
    if any(
        cell.kind != "table_cell"
        or not _has_winner(cell)
        or cell.quality not in {"verified", "unverified"}
        or cell.page_num != row.page_num
        or cell.bbox is None
        or not _inside(cell.bbox, row.bbox)
        or not _candidate_bound(graph, cell.candidates[cell.winner])
        for cell in cells
    ):
        return None
    columns = [cell.candidates[cell.winner].column_number for cell in cells]
    if any(type(column) is not int for column in columns) or len(set(columns)) != len(columns):
        return None
    ordered = sorted(zip(columns, cells, strict=True), key=lambda item: item[0])
    row_candidate = row.candidates[row.winner]
    if row_candidate.row_number is not None and any(
        cell.candidates[cell.winner].row_number != row_candidate.row_number for _, cell in ordered
    ):
        return None
    if "\t".join(cell.raw_text for _, cell in ordered) != row.raw_text:
        return None
    parents = [
        edge.target_id
        for edge in graph.edges
        if edge.relation == "table_parent" and edge.source_id == row.source_id
    ]
    if len(parents) != 1 or blocks.get(parents[0]) is None or blocks[parents[0]].kind != "table":
        return None
    return [cell for _, cell in ordered]


def _row_segments(row, cells, ref):
    cursor = 0
    segments = []
    for index, cell in enumerate(cells):
        start, end = cursor, cursor + len(cell.raw_text)
        left, right = max(start, ref.char_start), min(end, ref.char_end)
        if left < right:
            segments.append((cell, row.raw_text[left:right]))
        cursor = end + (index < len(cells) - 1)
    if not segments or _normalized(" ".join(text for _, text in segments)) != _normalized(
        ref.quote
    ):
        return None
    return segments


def _page_reading(source, document, block, glyphs):
    if not _has_winner(block):
        return dict(status="unresolved", reason="source_invalid", bbox=None)
    candidate = block.candidates[block.winner]
    box, geometry = candidate.bbox, candidate.geometry
    unresolved = dict(status="unresolved", reason="geometry_unsupported", bbox=box)
    if (
        box is None
        or candidate.has_invalid_geometry
        or not 1 <= block.page_num <= len(document.pages)
    ):
        return unresolved
    page = document.pages[block.page_num - 1]
    if (
        page.rotation
        or geometry.rotation
        or tuple(page.bbox[:2]) != (0, 0)
        or tuple(geometry.crop_box[:2]) != (0, 0)
        or abs(page.width - geometry.width_pt) > 0.001
        or abs(page.height - geometry.height_pt) > 0.001
        or not (0 <= box[0] < box[2] <= page.width and 0 <= box[1] < box[3] <= page.height)
    ):
        return unresolved
    if block.page_num not in glyphs:
        words = page.extract_words()
        try:
            proof = native_word_ink_geometry(source, block.page_num, list(range(len(words))))
        except ValueError:
            proof = {}
        glyphs[block.page_num] = words, proof
    words, proof = glyphs[block.page_num]
    if not isinstance(proof.get("matched_words"), list):
        return dict(status="unresolved", reason="glyph_geometry_unresolved", bbox=box)
    boxes = {word["native_word_index"]: word["ink_bbox"] for word in proof["matched_words"]}
    missing = set(proof.get("unresolved_word_indices", ()))
    if set(boxes) | missing != set(range(len(words))) or any(
        words[i]["x1"] > box[0]
        and words[i]["x0"] < box[2]
        and words[i]["bottom"] > box[1]
        and words[i]["top"] < box[3]
        for i in missing
    ):
        return dict(status="unresolved", reason="glyph_geometry_unresolved", bbox=box)
    selected = []
    for index, word in enumerate(words):
        if index not in boxes:
            continue
        word_box = boxes[index]
        if (
            word_box[2] <= box[0]
            or word_box[0] >= box[2]
            or word_box[3] <= box[1]
            or word_box[1] >= box[3]
        ):
            continue
        if not word["upright"] or not _inside(word_box, box):
            return dict(status="unresolved", reason="clipped_or_rotated_words", bbox=box)
        selected.append(dict(index=index, text=word["text"], bbox=word_box))
    native_parts, offset = [], 0
    for word in selected:
        word["char_start"] = offset
        offset += len(word["text"])
        word["char_end"] = offset
        native_parts.append(word["text"])
        offset += 1
    native = " ".join(native_parts)
    rendered = _rendered_text(page, box, padding_px=6)
    return dict(
        status="read",
        reason=None,
        page=block.page_num,
        bbox=box,
        native_text=native,
        words=selected,
        rendered=rendered,
        glyph_geometry=proof,
    )


def _checked_ref(graph, ref, block, tenant_id):
    checked = replace(
        graph,
        blocks=tuple(
            replace(item, quality="verified") if item.source_id == ref.source_id else item
            for item in graph.blocks
        ),
    )
    return verify_source_ref(ref, checked, tenant_id=tenant_id).verification_state == "verified"


def attest_table_spans(graph, source_pdf_bytes, refs, *, tenant_id):
    """Attest exact cell/row quotes using original native glyphs and a cropped OCR read."""
    _validate_graph(graph, tenant_id)
    if (
        not isinstance(source_pdf_bytes, bytes)
        or len(source_pdf_bytes) > 100 * 1024 * 1024
        or sha256(source_pdf_bytes).hexdigest() != graph.source_sha256
    ):
        raise ValueError("TABLE_SPAN_SOURCE_MISMATCH")
    blocks = {block.source_id: block for block in graph.blocks}
    records, readings, glyphs = [], {}, {}
    with pdfplumber.open(io.BytesIO(source_pdf_bytes)) as document:
        interactive = "OCProperties" in document.doc.catalog or (
            "AcroForm" in document.doc.catalog
            and not claim_verifier._pushbuttons_only(resolve1(document.doc.catalog.get("AcroForm")))
        )
        for ref in refs:
            record = dict(ref=asdict(ref), status="unresolved", reason="source_invalid")
            records.append(record)
            block = blocks.get(ref.source_id)
            if (
                block is None
                or block.kind not in _TABLE_KINDS
                or block.quality not in {"verified", "unverified"}
                or not _has_winner(block)
                or not _candidate_bound(graph, block.candidates[block.winner])
                or not _checked_ref(graph, ref, block, tenant_id)
            ):
                if block is not None and block.kind not in _TABLE_KINDS:
                    record["reason"] = "unsupported_block_kind"
                continue
            box = block.bbox
            if box is None:
                record["reason"] = "geometry_unsupported"
                continue
            if not 1 <= block.page_num <= len(document.pages):
                record["reason"] = "page_unavailable"
                continue
            if interactive or claim_verifier._appearance_overlaps(
                document.pages[block.page_num - 1], box
            ):
                record["reason"] = "interactive_visibility_requires_review"
                continue
            if block.source_id not in readings:
                if block.kind == "table_cell":
                    readings[block.source_id] = _page_reading(
                        source_pdf_bytes, document, block, glyphs
                    )
                else:
                    cells = _row_cells(graph, block)
                    readings[block.source_id] = (
                        dict(status="unresolved", reason="row_structure_unresolved")
                        if cells is None
                        else dict(
                            status="read",
                            cells=[
                                dict(
                                    source_id=cell.source_id,
                                    raw_text=cell.raw_text,
                                    reading=_page_reading(source_pdf_bytes, document, cell, glyphs),
                                )
                                for cell in cells
                            ],
                        )
                    )
            reading = readings[block.source_id]
            quote = _normalized(ref.quote)
            if block.kind == "table_cell":
                if reading["status"] != "read":
                    record["reason"] = reading["reason"]
                elif not claim_verifier._unique_quote(reading["native_text"], quote):
                    record["reason"] = "native_quote_unresolved"
                else:
                    _tight_render(document.pages[block.page_num - 1], reading, quote)
                    rendered_ok = claim_verifier._unique_quote(
                        reading["rendered"].get("text", ""), quote
                    )
                    record.update(
                        status="verified" if rendered_ok else "unresolved",
                        reason=None if rendered_ok else "rendered_quote_unresolved",
                    )
                record["reading_sha256"] = canonical_hash(reading)
                continue
            cells = _row_cells(graph, block)
            segments = _row_segments(block, cells, ref) if cells is not None else None
            if segments is None or reading["status"] != "read":
                record["reason"] = "row_structure_unresolved"
                record["reading_sha256"] = canonical_hash(reading)
                continue
            by_id = {item["source_id"]: item["reading"] for item in reading["cells"]}
            if any(by_id[cell.source_id]["status"] != "read" for cell in cells):
                record["reason"] = "cell_reading_unresolved"
                record["reading_sha256"] = canonical_hash(reading)
                continue
            page = document.pages[block.page_num - 1]
            for cell, part in segments:
                _tight_render(page, by_id[cell.source_id], _normalized(part))
            if any(by_id[cell.source_id]["rendered"].get("status") != "read" for cell in cells):
                record["reason"] = "cell_reading_unresolved"
                record["reading_sha256"] = canonical_hash(reading)
                continue
            native_row = " ".join(by_id[cell.source_id]["native_text"] for cell in cells)
            rendered_row = " ".join(
                by_id[cell.source_id]["rendered"].get("text", "") for cell in cells
            )
            if not claim_verifier._unique_quote(native_row, quote):
                record["reason"] = "native_quote_unresolved"
            elif not claim_verifier._unique_quote(rendered_row, quote):
                record["reason"] = "rendered_quote_unresolved"
            elif any(
                not claim_verifier._unique_quote(by_id[cell.source_id]["native_text"], part)
                or not claim_verifier._unique_quote(
                    by_id[cell.source_id]["rendered"].get("text", ""), part
                )
                for cell, part in segments
            ):
                record["reason"] = "row_cell_quote_unresolved"
            else:
                record.update(status="verified", reason=None)
            record["reading_sha256"] = canonical_hash(reading)
    receipt = dict(
        schema="table_span_source_attestation_v1",
        tenant_id=tenant_id,
        document_version_id=graph.document_version_id,
        parse_manifest_id=graph.parse_manifest_id,
        source_sha256=graph.source_sha256,
        graph_sha256=canonical_hash(asdict(graph)),
        policy=table_span_policy(),
        records=records,
        readings=readings,
    )
    receipt["artifact_sha256"] = canonical_hash(receipt)
    return receipt
