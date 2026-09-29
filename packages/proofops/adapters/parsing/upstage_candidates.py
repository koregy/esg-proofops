"""Offline conversion of a pinned Upstage Document Parse receipt into parser candidates.

Pure over in-memory bytes: no network, no store, no provider call. The receipt must come
from UpstageParseProbe for a page subset of the original PDF; every hash in the chain
(original -> subset -> request -> response) is recomputed, and each subset page must be
byte-identical in content stream and geometry to its original physical page.

Output is a candidate batch only. Blocks carry table-level geometry at most; no table
cells, rows, or numeric values are inferred. Citation approval and source verification
are explicitly not granted here -- graph fusion keeps these blocks "unverified".
"""

from __future__ import annotations

import io
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from typing import NoReturn
from uuid import UUID, uuid5

from proofops.adapters.local.upstage_parse import MAX_RESPONSE_BYTES, PARSE_MODEL_PINNED
from proofops.application.ingest.geometry import affine_apply, invert_affine
from proofops.application.ingest.graph_fusion import (
    CandidateBatch,
    CandidateBlock,
    SourceArtifact,
)
from proofops.domain.documents import NativeSource, PageGeometry
from proofops.domain.provenance import canonical_hash
from proofops.domain.values import _require_uuid
from pypdf import PdfReader

CONVERTER_VERSION = "upstage_candidates_v1"
PARSER_NAME = "upstage-document-parse"
PARSER_FAMILY = "upstage"
COORDINATE_SYSTEM = "upstage_normalized_top_left_page"
MAX_PAGES = 10
MAX_ELEMENTS = 10_000
MAX_TEXT_CHARS = 200_000
# Only categories whose meaning matches a graph kind; all others stay kind "unknown" with
# the provider category kept in context. "list" is a merged multi-item block, not a paragraph.
KIND_MAP = {
    "paragraph": "paragraph",
    "heading1": "heading",
    "caption": "caption",
    "footnote": "footnote",
    "table": "table",
    "figure": "figure",
}
LIMITS = (
    "table_level_coordinates_only_no_cell_geometry",
    "no_table_cells_or_numeric_values_inferred",
    "coordinates_normalized_to_unrotated_page_equal_crop_and_media_box",
    "polygon_reduced_to_axis_aligned_envelope",
    "html_output_hashed_not_parsed",
    "text_not_source_verified",
    "citation_not_approved",
)


class UpstageCandidateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UpstageCandidateConversion:
    batch: CandidateBatch
    report: dict = field(repr=False)


def _fail(code: str) -> NoReturn:
    raise UpstageCandidateError(code)


def _is_hash(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")


def _content(page) -> bytes:
    contents = page.get_contents()
    return b"" if contents is None else contents.get_data()


def _page_geometry(page, reason: str) -> PageGeometry:
    crop, media = tuple(map(float, page.cropbox)), tuple(map(float, page.mediabox))
    rotation = int(page.rotation) % 360
    # Upstage normalizes against the rendered page image; only the unambiguous case is admitted.
    if rotation != 0 or crop != media or not all(math.isfinite(v) for v in crop):
        _fail(reason)
    return PageGeometry(crop[2] - crop[0], crop[3] - crop[1], 0, crop)


def _validate_receipt(receipt: dict, subset_pdf: bytes, pages: int) -> tuple[dict, str]:
    raw = receipt.get("raw_response") if isinstance(receipt, dict) else None
    if not isinstance(raw, dict):
        _fail("UPSTAGE_RECEIPT_SHAPE_INVALID")
    if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
        _fail("UPSTAGE_RESPONSE_LIMIT")
    mode = receipt.get("mode")
    if (
        receipt.get("model") != PARSE_MODEL_PINNED
        or receipt.get("provider_model") != PARSE_MODEL_PINNED
        or raw.get("model") != PARSE_MODEL_PINNED
        or receipt.get("provider_model_hash") != sha256(PARSE_MODEL_PINNED.encode()).hexdigest()
    ):
        _fail("UPSTAGE_MODEL_PIN_MISMATCH")
    if receipt.get("response_sha256") != canonical_hash(raw):
        _fail("UPSTAGE_RESPONSE_HASH_MISMATCH")
    request = dict(
        model=PARSE_MODEL_PINNED,
        mode=mode,
        pdf_sha256=sha256(subset_pdf).hexdigest(),
        pages=pages,
        bytes_len=len(subset_pdf),
    )
    if mode not in ("standard", "enhanced") or receipt.get("request_sha256") != canonical_hash(
        request
    ):
        _fail("UPSTAGE_REQUEST_HASH_MISMATCH")
    usage = raw.get("usage")
    other = "enhanced" if mode == "standard" else "standard"
    if (
        not isinstance(usage, dict)
        or type(usage.get("pages")) is not int
        or usage["pages"] != pages
        or type(receipt.get("pages")) is not int
        or receipt["pages"] != pages
        or not isinstance(usage.get(mode), list)
        or any(type(p) is not int for p in usage[mode])
        or sorted(usage[mode]) != list(range(1, pages + 1))
        or usage.get(other, []) != []
    ):
        _fail("UPSTAGE_BILLING_PAGES_INVALID")
    return raw, mode


def _envelope(coordinates) -> tuple[float, float, float, float] | None:
    if coordinates is None or coordinates == []:
        return None
    if not isinstance(coordinates, list) or len(coordinates) != 4:
        _fail("UPSTAGE_COORDINATES_INVALID")
    xs: list[float] = []
    ys: list[float] = []
    for point in coordinates:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            _fail("UPSTAGE_COORDINATES_INVALID")
        for axis, bucket in (("x", xs), ("y", ys)):
            value = point[axis]
            if isinstance(value, bool) or not isinstance(value, int | float):
                _fail("UPSTAGE_COORDINATES_INVALID")
            if not math.isfinite(value) or not 0 <= value <= 1:
                _fail("UPSTAGE_COORDINATES_OUT_OF_BOUNDS")
            bucket.append(float(value))
    box = (min(xs), min(ys), max(xs), max(ys))
    if not (box[0] < box[2] and box[1] < box[3]):
        _fail("UPSTAGE_COORDINATES_DEGENERATE")
    return box


def convert_upstage_parse(
    receipt: dict,
    *,
    source: SourceArtifact,
    subset_pdf: bytes,
    physical_pages: tuple[int, ...],
    parse_manifest_id: str,
    receipt_sha256: str | None = None,
) -> UpstageCandidateConversion:
    """Map a pinned subset receipt onto original physical pages as unverified candidates."""
    _require_uuid("parse_manifest_id", parse_manifest_id)
    pages = tuple(physical_pages) if isinstance(physical_pages, tuple | list) else ()
    if (
        not 1 <= len(pages) <= MAX_PAGES
        or any(type(p) is not int or p < 1 for p in pages)
        or tuple(sorted(set(pages))) != pages
    ):
        _fail("UPSTAGE_PAGE_MAPPING_INVALID")
    if sha256(source.content).hexdigest() != source.sha256:
        _fail("UPSTAGE_SOURCE_HASH_MISMATCH")
    if (
        not isinstance(subset_pdf, bytes)
        or receipt_sha256 is not None
        and not _is_hash(receipt_sha256)
    ):
        _fail("UPSTAGE_RECEIPT_SHAPE_INVALID")
    raw, mode = _validate_receipt(receipt, subset_pdf, len(pages))

    try:
        original, subset = PdfReader(io.BytesIO(source.content)), PdfReader(io.BytesIO(subset_pdf))
        if original.is_encrypted or subset.is_encrypted:
            raise ValueError
        total, labels = len(original.pages), original.page_labels
        subset_pages = list(subset.pages)
    except UpstageCandidateError:
        raise
    except Exception:
        _fail("UPSTAGE_PDF_UNREADABLE")
    if len(subset_pages) != len(pages) or pages[-1] > total:
        _fail("UPSTAGE_PAGE_MAPPING_INVALID")
    geometries, printed = {}, {}
    for index, physical in enumerate(pages, 1):
        page, copy = original.pages[physical - 1], subset_pages[index - 1]
        geometry = _page_geometry(page, "UPSTAGE_PAGE_GEOMETRY_UNSUPPORTED")
        if _page_geometry(copy, "UPSTAGE_SUBSET_PAGE_MISMATCH") != geometry or _content(
            copy
        ) != _content(page):
            _fail("UPSTAGE_SUBSET_PAGE_MISMATCH")
        geometries[index], printed[index] = geometry, labels[physical - 1]

    elements = raw.get("elements")
    if not isinstance(elements, list) or len(elements) > MAX_ELEMENTS:
        _fail("UPSTAGE_ELEMENTS_INVALID")
    response_sha256 = receipt["response_sha256"]
    run_id = str(uuid5(UUID(parse_manifest_id), f"{PARSER_NAME}:{response_sha256}"))
    blocks, rows, seen, chars = [], [], set(), 0
    for element in elements:
        content = element.get("content") if isinstance(element, dict) else None
        if (
            not isinstance(content, dict)
            or type(element.get("id")) is not int
            or element["id"] < 0
            or element["id"] in seen
            or type(element.get("page")) is not int
            or element["page"] not in geometries
            or not isinstance(element.get("category"), str)
            or not element["category"]
            or not isinstance(content.get("text"), str)
            or not isinstance(content.get("html", ""), str)
        ):
            _fail("UPSTAGE_ELEMENTS_INVALID")
        seen.add(element["id"])
        text, category = content["text"], element["category"]
        chars += len(text)
        if chars > MAX_TEXT_CHARS:
            _fail("UPSTAGE_TEXT_LIMIT")
        geometry = geometries[element["page"]]
        width, height = geometry.width_pt, geometry.height_pt
        to_canonical = (width, 0.0, 0.0, height, 0.0, 0.0)
        box = _envelope(element.get("coordinates"))
        native_bbox = None
        if box is not None:
            to_native = invert_affine(geometry.to_canonical_affine())
            corners = [
                affine_apply(to_native, *affine_apply(to_canonical, x, y))
                for x in (box[0], box[2])
                for y in (box[1], box[3])
            ]
            native_bbox = (
                min(p[0] for p in corners),
                min(p[1] for p in corners),
                max(p[0] for p in corners),
                max(p[1] for p in corners),
            )
        kind = KIND_MAP.get(category, "unknown")
        native_id = str(element["id"])
        blocks.append(
            CandidateBlock(
                kind,
                NativeSource(
                    source.document_version_id,
                    parse_manifest_id,
                    run_id,
                    native_id,
                    pages[element["page"] - 1],
                    printed[element["page"]],
                    native_bbox,
                    "pdf_bottom_left_points",
                    text,
                    0,
                    len(text),
                ),
                geometry,
                () if kind != "unknown" else (f"upstage_category={category}",),
                box,
                COORDINATE_SYSTEM,
                to_canonical,
                native_id if kind == "table" else None,
            )
        )
        rows.append(
            dict(
                native_id=native_id,
                category=category,
                kind=kind,
                submitted_page=element["page"],
                physical_page=pages[element["page"] - 1],
                located=box is not None,
                text_sha256=sha256(text.encode()).hexdigest(),
                html_sha256=sha256(content.get("html", "").encode()).hexdigest(),
            )
        )

    config = dict(
        converter=CONVERTER_VERSION,
        model=PARSE_MODEL_PINNED,
        mode=mode,
        transport="UpstageParseProbe",
        coordinate_system=COORDINATE_SYSTEM,
        kind_map=KIND_MAP,
        physical_pages=list(pages),
        subset_sha256=sha256(subset_pdf).hexdigest(),
    )
    batch = CandidateBatch(
        source.tenant_id,
        source.document_version_id,
        parse_manifest_id,
        source.sha256,
        run_id,
        PARSER_NAME,
        PARSE_MODEL_PINNED,
        PARSER_FAMILY,
        canonical_hash(config),
        tuple(blocks),
        (),
        synthetic=source.synthetic,
    )
    categories = Counter(row["category"] for row in rows)
    report = dict(
        schema="upstage_candidate_conversion_v1",
        quality="candidate_only",
        citation_approved=False,
        source_verification="not_run",
        tenant_id=source.tenant_id,
        document_version_id=source.document_version_id,
        parse_manifest_id=parse_manifest_id,
        parser_run_id=run_id,
        parser=dict(name=PARSER_NAME, family=PARSER_FAMILY, model=PARSE_MODEL_PINNED, mode=mode),
        config=config,
        config_hash=batch.config_hash,
        hashes=dict(
            source_sha256=source.sha256,
            subset_sha256=config["subset_sha256"],
            request_sha256=receipt["request_sha256"],
            response_sha256=response_sha256,
            provider_model_hash=receipt["provider_model_hash"],
            receipt_sha256=receipt_sha256,
        ),
        page_map={str(i): p for i, p in enumerate(pages, 1)},
        counts=dict(
            elements=len(rows),
            located=sum(row["located"] for row in rows),
            unlocated=sum(not row["located"] for row in rows),
            by_category=dict(sorted(categories.items())),
            by_kind=dict(sorted(Counter(row["kind"] for row in rows).items())),
            by_physical_page={
                str(p): sum(row["physical_page"] == p for row in rows) for p in pages
            },
            tables=categories.get("table", 0),
            table_cell_candidates=0,
        ),
        unsupported_categories=sorted(c for c in categories if c not in KIND_MAP),
        limits=list(LIMITS),
        elements=rows,
    )
    return UpstageCandidateConversion(batch, report)
