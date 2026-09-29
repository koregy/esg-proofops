"""Offline Upstage Document Parse receipt -> candidate batch conversion.

Synthetic PDFs and receipts only; no provider call. Pins the hash chain, the subset ->
physical page mapping, normalized-coordinate projection, explicit unknown categories,
table-level-only geometry, and the absence of any citation approval.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from proofops.adapters.local.upstage_parse import PARSE_MODEL_PINNED
from proofops.adapters.parsing.upstage_candidates import (
    UpstageCandidateError,
    convert_upstage_parse,
)
from proofops.application.ingest.graph_fusion import (
    SourceArtifact,
    candidates_from_snapshot,
    fuse_candidates,
)
from proofops.domain.provenance import canonical_hash
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, NumberObject

TENANT = "11111111-1111-4111-8111-111111111111"
DOCUMENT = "22222222-2222-4222-8222-222222222222"
VERSION = "33333333-3333-4333-8333-333333333333"
MANIFEST = "44444444-4444-4444-8444-444444444444"
PAGES = (2, 4)


def _pdf(count: int, rotate: int = 0) -> bytes:
    writer = PdfWriter()
    for number in range(1, count + 1):
        page = writer.add_blank_page(width=800, height=600)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 10 Td (page {number}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        if rotate:
            page[NameObject("/Rotate")] = NumberObject(rotate)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _subset(original: bytes, pages=PAGES) -> bytes:
    reader, writer = PdfReader(io.BytesIO(original)), PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page - 1])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _box(x0, y0, x1, y1):
    return [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]


def _elements():
    return [
        dict(
            id=0,
            page=1,
            category="paragraph",
            content=dict(text="Scope 1 12,345 tCO2e"),
            coordinates=_box(0.1, 0.1, 0.5, 0.2),
        ),
        dict(
            id=1,
            page=1,
            category="heading1",
            content=dict(text="Climate"),
            coordinates=_box(0.1, 0.02, 0.3, 0.06),
        ),
        dict(
            id=2,
            page=2,
            category="table",
            content=dict(html="<table/>", text="| a | 1 |"),
            coordinates=_box(0.2, 0.5, 0.9, 0.9),
        ),
        dict(
            id=3,
            page=2,
            category="header",
            content=dict(text="Kia"),
            coordinates=_box(0.0, 0.0, 0.1, 0.03),
        ),
        dict(id=4, page=2, category="list", content=dict(text="- a\n- b"), coordinates=[]),
    ]


def _receipt(subset: bytes, elements=None, mode="standard", pages=len(PAGES)):
    raw = dict(
        api="2.0",
        model=PARSE_MODEL_PINNED,
        usage={"pages": pages, mode: list(range(1, pages + 1))},
        elements=_elements() if elements is None else elements,
    )
    return dict(
        model=PARSE_MODEL_PINNED,
        provider_model=PARSE_MODEL_PINNED,
        provider_model_hash=hashlib.sha256(PARSE_MODEL_PINNED.encode()).hexdigest(),
        mode=mode,
        pages=pages,
        usage=raw["usage"],
        response_sha256=canonical_hash(raw),
        request_sha256=canonical_hash(
            dict(
                model=PARSE_MODEL_PINNED,
                mode=mode,
                pdf_sha256=hashlib.sha256(subset).hexdigest(),
                pages=pages,
                bytes_len=len(subset),
            )
        ),
        raw_response=raw,
    )


def _source(content: bytes) -> SourceArtifact:
    return SourceArtifact(
        TENANT, DOCUMENT, VERSION, hashlib.sha256(content).hexdigest(), "v1", content, True
    )


def _convert(receipt, original, subset, pages=PAGES):
    return convert_upstage_parse(
        receipt,
        source=_source(original),
        subset_pdf=subset,
        physical_pages=pages,
        parse_manifest_id=MANIFEST,
    )


def _rehash(receipt):
    receipt["response_sha256"] = canonical_hash(receipt["raw_response"])
    return receipt


@pytest.fixture
def pdfs():
    original = _pdf(5)
    return original, _subset(original)


def test_maps_subset_pages_coordinates_and_categories_without_approval(pdfs):
    original, subset = pdfs
    result = _convert(_receipt(subset), original, subset)
    blocks = {b.source.source_native_id: b for b in result.batch.blocks}
    assert {k: b.source.physical_page for k, b in blocks.items()} == {
        "0": 2,
        "1": 2,
        "2": 4,
        "3": 4,
        "4": 4,
    }
    assert {k: b.kind for k, b in blocks.items()} == {
        "0": "paragraph",
        "1": "heading",
        "2": "table",
        "3": "unknown",
        "4": "unknown",
    }
    assert blocks["3"].context == ("upstage_category=header",)
    assert blocks["0"].context == ()
    # normalized top-left (0.1,0.1)-(0.5,0.2) on 800x600 -> PDF bottom-left points
    assert blocks["0"].source.native_bbox == pytest.approx((80, 480, 400, 540))
    assert blocks["0"].bbox == pytest.approx((80, 60, 400, 120))
    assert blocks["0"].parser_coordinate_system == "upstage_normalized_top_left_page"
    assert blocks["4"].source.native_bbox is None
    table = blocks["2"]
    assert table.table_native_id == "2" and table.row_number is None
    assert not any(b.kind in ("table_row", "table_cell") for b in result.batch.blocks)
    batch = result.batch
    assert (batch.parser_name, batch.parser_version, batch.parser_family) == (
        "upstage-document-parse",
        PARSE_MODEL_PINNED,
        "upstage",
    )
    report = result.report
    assert report["citation_approved"] is False
    assert report["source_verification"] == "not_run"
    assert report["unsupported_categories"] == ["header", "list"]
    assert report["counts"]["table_cell_candidates"] == 0
    assert report["counts"]["unlocated"] == 1
    assert report["page_map"] == {"1": 2, "2": 4}
    assert report["hashes"]["response_sha256"] == canonical_hash(_receipt(subset)["raw_response"])
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    assert {b.quality for b in graph.blocks} == {"unverified", "unlocated"}
    json.dumps(report)


def test_conversion_is_deterministic(pdfs):
    original, subset = pdfs
    first = _convert(_receipt(subset), original, subset)
    second = _convert(_receipt(subset), original, subset)
    assert first.batch == second.batch and first.report == second.report


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda r: r["raw_response"].update(model="document-parse"), "UPSTAGE_MODEL_PIN_MISMATCH"),
        (lambda r: r.update(provider_model="document-parse"), "UPSTAGE_MODEL_PIN_MISMATCH"),
        (lambda r: r["raw_response"]["elements"].pop(), "UPSTAGE_RESPONSE_HASH_MISMATCH"),
        (lambda r: r.update(request_sha256="0" * 64), "UPSTAGE_REQUEST_HASH_MISMATCH"),
    ],
)
def test_rejects_broken_pins(pdfs, mutate, code):
    original, subset = pdfs
    receipt = _receipt(subset)
    mutate(receipt)
    with pytest.raises(UpstageCandidateError, match=code):
        _convert(receipt, original, subset)


@pytest.mark.parametrize(
    ("element", "code"),
    [
        (dict(coordinates=_box(0.1, 0.1, 1.2, 0.2)), "UPSTAGE_COORDINATES_OUT_OF_BOUNDS"),
        (dict(coordinates=_box(0.1, 0.1, 0.1, 0.2)), "UPSTAGE_COORDINATES_DEGENERATE"),
        (dict(page=3), "UPSTAGE_ELEMENTS_INVALID"),
        (dict(id=1), "UPSTAGE_ELEMENTS_INVALID"),
        (dict(category=""), "UPSTAGE_ELEMENTS_INVALID"),
    ],
)
def test_rejects_invalid_elements(pdfs, element, code):
    original, subset = pdfs
    receipt = _receipt(subset)
    receipt["raw_response"]["elements"][0].update(element)
    with pytest.raises(UpstageCandidateError, match=code):
        _convert(_rehash(receipt), original, subset)


def test_rejects_billing_that_does_not_cover_subset(pdfs):
    original, subset = pdfs
    receipt = _receipt(subset)
    receipt["raw_response"]["usage"]["standard"] = [1]
    with pytest.raises(UpstageCandidateError, match="UPSTAGE_BILLING_PAGES_INVALID"):
        _convert(_rehash(receipt), original, subset)


def test_rejects_wrong_physical_mapping_and_source(pdfs):
    original, subset = pdfs
    receipt = _receipt(subset)
    with pytest.raises(UpstageCandidateError, match="UPSTAGE_SUBSET_PAGE_MISMATCH"):
        _convert(receipt, original, subset, pages=(2, 3))
    with pytest.raises(UpstageCandidateError, match="UPSTAGE_PAGE_MAPPING_INVALID"):
        _convert(receipt, original, subset, pages=(4, 2))
    with pytest.raises(UpstageCandidateError, match="UPSTAGE_PAGE_MAPPING_INVALID"):
        _convert(receipt, original, subset, pages=(2, 9))
    with pytest.raises(UpstageCandidateError, match="UPSTAGE_SOURCE_HASH_MISMATCH"):
        convert_upstage_parse(
            receipt,
            source=SourceArtifact(TENANT, DOCUMENT, VERSION, "a" * 64, "v1", original),
            subset_pdf=subset,
            physical_pages=PAGES,
            parse_manifest_id=MANIFEST,
        )


def test_rotated_pages_are_unsupported():
    original = _pdf(5, rotate=90)
    subset = _subset(original)
    with pytest.raises(UpstageCandidateError, match="UPSTAGE_PAGE_GEOMETRY_UNSUPPORTED"):
        _convert(_receipt(subset), original, subset)


SAMPLE = Path(__file__).resolve().parents[2] / ".local" / "submission-20260929"


@pytest.mark.skipif(
    not (SAMPLE / "parse-api-sample" / "receipt.json").is_file(),
    reason="local paid-sample artifacts are not in the repository",
)
def test_saved_kia_three_page_receipt_offline():
    plan = json.loads((SAMPLE / "parse-api-sample" / "plan.json").read_bytes())
    original = (SAMPLE / "kia-real" / "sources" / "kia-sr-2025-kr.pdf").read_bytes()
    subset = (SAMPLE / "parse-api-sample" / "sample.pdf").read_bytes()
    receipt = json.loads((SAMPLE / "parse-api-sample" / "receipt.json").read_bytes())
    result = convert_upstage_parse(
        receipt,
        source=SourceArtifact(TENANT, DOCUMENT, VERSION, plan["source_sha256"], "local", original),
        subset_pdf=subset,
        physical_pages=tuple(plan["physical_pages"]),
        parse_manifest_id=MANIFEST,
    )
    counts = result.report["counts"]
    assert result.report["hashes"]["subset_sha256"] == plan["subset_sha256"]
    assert counts["elements"] == counts["located"] == 55
    assert counts["by_physical_page"] == {"2": 17, "111": 16, "132": 22}
    assert counts["tables"] == 5 and counts["table_cell_candidates"] == 0
    assert result.report["unsupported_categories"] == ["header", "list"]


def test_batch_round_trips_through_candidate_snapshot(pdfs):
    original, subset = pdfs
    batch = _convert(_receipt(subset), original, subset).batch
    snapshot = json.loads(json.dumps([asdict(batch)]))
    assert candidates_from_snapshot(snapshot) == (batch,)
