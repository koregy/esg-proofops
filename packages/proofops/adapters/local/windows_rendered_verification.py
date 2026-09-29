"""Opt-in Windows.Media.Ocr corroboration for native paragraph attestations (new runs).

Off macOS, ``source_verification._rendered_text`` returns ``UnsupportedPlatform``
before reading a pixel, so every paragraph that passed all native gates stays
``rendered_text_unresolved``. This module adds a separately versioned, separately
hashed proof that supplies the missing rendered reading with the Windows OCR engine.
It mirrors ``native_paragraph_typography``: the base receipt, base policy, base
replay and every existing module stay byte-identical, and nothing is monkeypatched.
The reader is an explicit argument (default: the sha-pinned helper in
``windows_ocr.py``), so concurrent old runs never see a different reader.

Eligibility is taken only from the unchanged base receipt (v2, glyph mode): a record
is considered only when the base verifier reached the rendered step, i.e. status
``unresolved``, reason ``rendered_text_unresolved`` and a rendered result that is
exactly the platform verdict ``UnsupportedPlatform``. Glyph mapping, visibility,
interactivity, box containment, clipping and the exact native-words == candidate text
check therefore all already passed; no gate is re-implemented or widened here. A
macOS Vision disagreement is never eligible.

The crop is rendered exactly like the base reader (216 dpi, scale 3, floor/ceil pixel
box, white padding 0 then one text-blind retry at 6 px on a disagreeing read). A block
is promoted only when ``_normalized(OCR text) == _normalized(native words)``. The OCR
reading corroborates that the native text is what is visibly rendered; it never proves
numeric or semantic truth on its own and never supplies text. An unavailable engine is
recorded as ``unresolved`` and promotes nothing.

Replay recomputes the base replay and every rendering and OCR reading, and requires the
whole proof to be equal. A changed engine identity (OS build/UBR, language), helper
bytes, render bytes or text, or an engine that is unavailable where it once read,
refuses the proof. Old runs have no proof and are never promoted.
"""

from __future__ import annotations

import io
import math
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, replace
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from threading import Lock
from typing import Any

import pdfplumber
from PIL import ImageOps

from proofops.adapters.local import windows_ocr
from proofops.adapters.local.run_artifacts import native_paragraph_policy
from proofops.application.evidence.citations import _normalized
from proofops.domain.provenance import canonical_hash

POLICY_SCHEMA = "native_paragraph_windows_ocr_policy_v1"
PROOF_SCHEMA = "native_paragraph_windows_ocr_proof_v1"
RESOLUTION_DPI = 216
SCALE = 3
PADDINGS = (0, 6)
RENDER_PIXEL_LIMIT = 16_000_000
MAX_ELIGIBLE = 2_000
_PLATFORM_UNSUPPORTED = dict(
    status="unresolved", reason="rendered_reader_unavailable", error="UnsupportedPlatform"
)
Reader = Callable[[bytes], dict]

_replays: OrderedDict[str, object] = OrderedDict()
_lock = Lock()
_MAX_REPLAYS = 16


def native_paragraph_windows_ocr_policy() -> dict:
    """Pins the base verifier policy, this module, the helper bytes, every limit and the
    installed engine identity (OS version/build/UBR, language). Raises
    ``WindowsOcrUnavailable`` when no Korean engine can be probed: a new run cannot
    bind this policy without one, and a replay on a changed engine cannot match it."""
    base = native_paragraph_policy()
    helper = windows_ocr.helper_sha256()
    return dict(
        schema=POLICY_SCHEMA,
        mode="paragraph_native_windows_rendered_ocr_v1",
        base=base,
        base_sha256=canonical_hash(base),
        wrapper_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        helper_sha256=helper,
        helper_runner_sha256=sha256(Path(windows_ocr.__file__).read_bytes()).hexdigest(),
        reader="Windows.Media.Ocr",
        language=windows_ocr.LANGUAGE,
        engine=windows_ocr.engine_identity(expected_helper_sha256=helper),
        render=dict(
            resolution_dpi=RESOLUTION_DPI,
            scale=SCALE,
            paddings_px=list(PADDINGS),
            pixel_limit=RENDER_PIXEL_LIMIT,
            renderer_version=version("pypdfium2"),
            pillow_version=version("pillow"),
            pdfplumber_version=version("pdfplumber"),
        ),
        limits=windows_ocr.limits(),
        comparison="citations._normalized_exact_equality_with_native_words",
        scope="rendered_text_corroboration_only_no_numeric_or_semantic_proof",
    )


def default_reader(policy: dict) -> Reader:
    helper = policy["helper_sha256"]

    def read(png: bytes) -> dict:
        return windows_ocr.read_windows_ocr(png, expected_helper_sha256=helper)

    return read


def eligible_windows_sources(native: dict) -> list[str]:
    """Base records that passed every native gate and lacked only a rendered reader."""
    return [
        row["source_id"]
        for row in native.get("records", ())
        if isinstance(row, dict)
        and row.get("status") == "unresolved"
        and row.get("reason") == "rendered_text_unresolved"
        and row.get("rendered") == _PLATFORM_UNSUPPORTED
        and "rendered_attempts" not in row
        and isinstance(row.get("words"), list)
        and row["words"]
    ]


def _crop_png(image, box, padding_px: int) -> tuple[bytes, list[int]]:
    pixels = [
        math.floor(box[0] * SCALE),
        math.floor(box[1] * SCALE),
        math.ceil(box[2] * SCALE),
        math.ceil(box[3] * SCALE),
    ]
    with image.crop(pixels) as crop:
        buffer = io.BytesIO()
        with ImageOps.expand(crop, border=padding_px, fill="white") as padded:
            padded.save(buffer, format="PNG")
    return buffer.getvalue(), pixels


def _reading(reader: Reader, png: bytes, padding_px: int, engine: dict) -> dict:
    result = reader(png)
    entry = dict(padding_px=padding_px, image_sha256=sha256(png).hexdigest())
    if (
        isinstance(result, dict)
        and result.get("status") == "read"
        and isinstance(result.get("text"), str)
    ):
        if result.get("engine") != engine:
            return dict(entry, status="unresolved", reason="windows_ocr_engine_changed")
        return dict(entry, status="read", text=result["text"])
    reason = result.get("reason") if isinstance(result, dict) else None
    return dict(
        entry,
        status="unresolved",
        reason=reason if isinstance(reason, str) else "windows_ocr_unavailable",
    )


def apply_windows_ocr(native, graph, source, *, tenant_id, reader: Reader | None = None):
    """Return ``(graph, proof)``: the base replay plus Windows-corroborated promotions.

    ``graph`` must be the attested-input (pre-native) graph; the base receipt is always
    recomputed against the real bytes first (never trusted as supplied).
    """
    if not isinstance(native, dict) or native.get("schema") != "native_paragraph_attestation_v2":
        raise ValueError("NATIVE_PARAGRAPH_ATTESTATION_REQUIRED")
    policy = native_paragraph_windows_ocr_policy()
    reader = reader or default_reader(policy)
    from proofops.adapters.local.native_replay_cache import replay_cached

    baseline = replay_cached(native, graph, source, tenant_id=tenant_id)
    already = {b.source_id for b in baseline.blocks if b.quality == "verified"}
    eligible = eligible_windows_sources(native)
    if len(eligible) > MAX_ELIGIBLE or len(set(eligible)) != len(eligible):
        raise ValueError("WINDOWS_OCR_ELIGIBLE_INVALID")
    rows = {row["source_id"]: row for row in native["records"]}
    blocks = {block.source_id: block for block in graph.blocks}
    records, promoted = [], []
    pages: dict[int, Any] = {}
    with pdfplumber.open(io.BytesIO(source)) as document:
        try:
            for source_id in eligible:
                block = blocks.get(source_id)
                if block is None or block.kind != "paragraph" or block.winner is None:
                    raise ValueError("WINDOWS_OCR_GRAPH_MISMATCH")
                candidate = block.candidates[block.winner]
                box = candidate.bbox
                raw = " ".join(w["text"] for w in rows[source_id]["words"])
                if (
                    box is None
                    or source_id in already
                    or _normalized(raw) != _normalized(candidate.source.raw_text)
                    or not 1 <= block.page_num <= len(document.pages)
                ):
                    raise ValueError("WINDOWS_OCR_GRAPH_MISMATCH")
                record = dict(source_id=source_id, page_num=block.page_num, attempts=[])
                records.append(record)
                page = document.pages[block.page_num - 1]
                if page.width * page.height * SCALE * SCALE > RENDER_PIXEL_LIMIT:
                    record.update(status="unresolved", reason="render_limit")
                    continue
                if block.page_num not in pages:
                    pages[block.page_num] = page.to_image(resolution=RESOLUTION_DPI).original
                image = pages[block.page_num]
                for padding in PADDINGS:
                    png, pixels = _crop_png(image, box, padding)
                    record["pixel_bbox"] = pixels
                    reading = _reading(reader, png, padding, policy["engine"])
                    record["attempts"].append(reading)
                    # One fixed, text-blind retry, only after a completed disagreeing read.
                    if reading["status"] != "read" or (
                        reading["text"] and _normalized(reading["text"]) == _normalized(raw)
                    ):
                        break
                final = record["attempts"][-1]
                if (
                    final["status"] == "read"
                    and final["text"]
                    and _normalized(final["text"]) == _normalized(raw)
                ):
                    record.update(status="verified", reason=None)
                    promoted.append(source_id)
                else:
                    record.update(
                        status="unresolved",
                        reason="windows_rendered_text_mismatch"
                        if final["status"] == "read"
                        else final["reason"],
                    )
        finally:
            for image in pages.values():
                image.close()
    promoted_set = set(promoted)
    result = replace(
        baseline,
        blocks=tuple(
            replace(block, quality="verified") if block.source_id in promoted_set else block
            for block in baseline.blocks
        ),
    )
    proof = dict(
        schema=PROOF_SCHEMA,
        tenant_id=tenant_id,
        document_version_id=graph.document_version_id,
        parse_manifest_id=graph.parse_manifest_id,
        source_sha256=graph.source_sha256,
        input_graph_sha256=canonical_hash(asdict(baseline)),
        output_graph_sha256=canonical_hash(asdict(result)),
        native_attestation_sha256=canonical_hash(native),
        policy=policy,
        policy_sha256=canonical_hash(policy),
        base_verified_source_ids=sorted(already),
        eligible_source_ids=sorted(eligible),
        promoted_source_ids=sorted(promoted_set),
        records=records,
        coordinate_system="pdf_top_left_points",
        citation_effect="paragraph_source_quality_only",
    )
    proof["artifact_sha256"] = canonical_hash(proof)
    return result, proof


def _probe(proof, graph, source, reader: Reader, engine: dict) -> bool:
    """Re-read one recorded crop: the engine must still exist and answer identically."""
    blocks = {block.source_id: block for block in graph.blocks}
    for record in proof["records"]:
        for attempt in record["attempts"]:
            if attempt["status"] != "read":
                continue
            block = blocks[record["source_id"]]
            with pdfplumber.open(io.BytesIO(source)) as document:
                page = document.pages[block.page_num - 1]
                with page.to_image(resolution=RESOLUTION_DPI).original as image:
                    png, _ = _crop_png(
                        image, block.candidates[block.winner].bbox, attempt["padding_px"]
                    )
            return _reading(reader, png, attempt["padding_px"], engine) == attempt
    return True


def replay_windows_ocr(proof, native, graph, source, *, tenant_id, reader: Reader | None = None):
    """Recompute everything and require the identical proof; otherwise refuse.

    The first replay in a process recomputes every rendering and OCR reading. A later
    replay of the same proof/graph/source re-verifies the base receipt and re-reads one
    recorded crop, so a changed or unavailable engine is still refused.
    """
    policy = native_paragraph_windows_ocr_policy()
    if (
        not isinstance(proof, dict)
        or proof.get("schema") != PROOF_SCHEMA
        or proof.get("policy") != policy
        or proof.get("artifact_sha256")
        != canonical_hash({k: v for k, v in proof.items() if k != "artifact_sha256"})
    ):
        raise ValueError("WINDOWS_OCR_PROOF_INVALID")
    reader = reader or default_reader(policy)
    key = canonical_hash(
        dict(
            proof=proof["artifact_sha256"],
            native=canonical_hash(native),
            graph=canonical_hash(asdict(graph)),
            source=sha256(source).hexdigest(),
            tenant_id=tenant_id,
        )
    )
    with _lock:
        cached = _replays.get(key)
    if cached is not None:
        from proofops.adapters.local.native_replay_cache import replay_cached

        replay_cached(native, graph, source, tenant_id=tenant_id)
        if not _probe(proof, graph, source, reader, policy["engine"]):
            raise ValueError("WINDOWS_OCR_PROOF_MISMATCH")
        return cached
    result, expected = apply_windows_ocr(native, graph, source, tenant_id=tenant_id, reader=reader)
    if canonical_hash(expected) != canonical_hash(proof):
        raise ValueError("WINDOWS_OCR_PROOF_MISMATCH")
    with _lock:
        _replays[key] = result
        _replays.move_to_end(key)
        while len(_replays) > _MAX_REPLAYS:
            _replays.popitem(last=False)
    return result
