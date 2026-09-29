"""NEW-run static pushbutton visibility gate for native paragraphs (Upstage OCR only).

The base verifier (``source_verification``, hash-pinned by every stored native policy)
rejects every paragraph of a document that carries any AcroForm. Its bytes stay
untouched. This separately versioned gate only revisits base records whose reason is
exactly ``interactive_visibility_requires_review`` and only makes them *eligible* for
the external rendered-text corroboration of ``native_upstage_ocr``; it never verifies
anything by itself.

A record is eligible only when all of the following hold, otherwise it stays
unresolved with a reason:

* document: no ``OCProperties``, no catalog ``AA``/``OpenAction``, a ``Names`` tree
  with ``Dests`` only (no JavaScript, embedded files, ...), an AcroForm with keys
  within ``Fields``/``DA``/``DR`` (no XFA, NeedAppearances or calculation order), and
  every field in the tree is a pushbutton without ``AA`` whose action is absent, a
  ``GoTo`` or a named navigation action;
* page: no ``AA``; no annotation with NoZoom/NoRotate/ToggleNoView flags; media box
  equals the page box with origin (0, 0); every annotation
  is either a ``Link`` without an appearance, or an ``AP``-carrying ``Link``/pushbutton
  ``Widget`` with a finite, well-formed ``Rect`` that stays more than 2pt away from the
  paragraph box. An appearance stream is drawn inside its ``Rect`` (ISO 32000 12.5.5),
  so a non-overlapping static control cannot obscure the box. Any other subtype,
  malformed entry or overlap rejects the record;
* then the unchanged base native gates, copied from the pinned glyph mode: geometry,
  glyph mapping, no clipped/rotated word, native words exactly equal to the candidate
  text, and a rendered reading that is exactly the off-macOS ``UnsupportedPlatform``
  verdict (the same scope as base Upstage eligibility).

No fuzzy matching and no network. The result is recomputed from the original bytes
and the reproduced base receipt on every read.
"""

from __future__ import annotations

import io
import math
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pdfplumber
from pdfminer.pdftypes import resolve1

from proofops.adapters.local import source_verification
from proofops.adapters.local.native_glyph_geometry import native_word_ink_geometry
from proofops.adapters.local.run_artifacts import native_paragraph_policy
from proofops.application.evidence.citations import _normalized
from proofops.domain.provenance import canonical_hash

POLICY_SCHEMA = "native_static_pushbutton_visibility_v1"
PROOF_SCHEMA = "native_static_pushbutton_visibility_proof_v1"
MARGIN_PT = 2.0
_NAVIGATION = frozenset({"NextPage", "PrevPage", "FirstPage", "LastPage", "GoBack", "GoForward"})
_PUSHBUTTON = 65536
# Annotation flags whose rendering depends on viewer zoom/rotation or toggles
# visibility at runtime: NoZoom (8), NoRotate (16), ToggleNoView (256).
_DYNAMIC_FLAGS = 8 | 16 | 256
_PLATFORM_UNSUPPORTED = dict(
    status="unresolved", reason="rendered_reader_unavailable", error="UnsupportedPlatform"
)


def native_widget_visibility_policy() -> dict:
    return dict(
        schema=POLICY_SCHEMA,
        verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        # The promotion/composition code is pinned too, not only the gate.
        composition_sha256=sha256(
            Path(__file__).with_name("native_upstage_ocr_widget.py").read_bytes()
        ).hexdigest(),
        base_native_policy_sha256=canonical_hash(native_paragraph_policy()),
        margin_pt=MARGIN_PT,
    )


def _name(value):
    return getattr(resolve1(value), "name", None)


def _static_action(action) -> bool:
    if action is None:
        return True
    action = resolve1(action)
    if not isinstance(action, dict) or "Next" in action:
        return False
    kind = _name(action.get("S"))
    return kind == "GoTo" or (kind == "Named" and _name(action.get("N")) in _NAVIGATION)


def _static_pushbuttons(form) -> bool:
    if not isinstance(form, dict) or not set(form) <= {"Fields", "DA", "DR"}:
        return False
    fields = resolve1(form.get("Fields"))
    if not isinstance(fields, list):
        return False
    stack = [(field, None, None) for field in fields]
    seen: set[int] = set()
    while stack:
        value, inherited_type, inherited_flags = stack.pop()
        value = resolve1(value)
        if not isinstance(value, dict) or id(value) in seen or len(seen) > 10000:
            return False
        seen.add(id(value))
        if "AA" in value or not _static_action(value.get("A")):
            return False
        kind = value.get("FT", inherited_type)
        flags = value.get("Ff", inherited_flags)
        children = resolve1(value.get("Kids"))
        if children is not None:
            if not isinstance(children, list) or not children:
                return False
            stack.extend((child, kind, flags) for child in children)
        elif (
            _name(kind) != "Btn"
            or type(flags) is not int
            or flags != _PUSHBUTTON
            or _name(value.get("Subtype")) != "Widget"
        ):
            return False
    return True


def _document_reason(document):
    catalog = document.doc.catalog
    if not isinstance(catalog, dict) or {"OCProperties", "AA", "OpenAction"} & set(catalog):
        return "document_dynamic_content"
    names = resolve1(catalog.get("Names"))
    if names is not None and (not isinstance(names, dict) or not set(names) <= {"Dests"}):
        return "document_dynamic_content"
    if "AcroForm" in catalog and not _static_pushbuttons(resolve1(catalog["AcroForm"])):
        return "form_not_static_pushbuttons"
    return None


def _rect(value, height):
    rect = resolve1(value)
    if not isinstance(rect, list) or len(rect) != 4:
        return None
    rect = [resolve1(v) for v in rect]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in rect):
        return None
    x0, x1 = sorted(rect[0::2])
    y0, y1 = sorted(rect[1::2])
    if x0 >= x1 or y0 >= y1:
        return None
    return x0, height - y1, x1, height - y0


def _field_value(annotation, key):
    """Inherited field attribute through a bounded /Parent chain."""
    node = annotation
    for _ in range(32):
        if key in node:
            return node[key]
        node = resolve1(node.get("Parent"))
        if node is None:
            return None
        if not isinstance(node, dict):
            raise ValueError("malformed parent")
    raise ValueError("parent chain too deep")


def _annotation_reason(page, box):
    """None when every annotation on the page is static and clear of the box."""
    if "AA" in page.page_obj.attrs:
        return "page_dynamic_content"
    if tuple(page.mediabox) != tuple(page.bbox):
        return "annotation_geometry_unsupported"
    raw = resolve1(page.page_obj.attrs.get("Annots"))
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) > 10000:
        return "annotation_malformed"
    for item in raw:
        annotation = resolve1(item)
        if not isinstance(annotation, dict):
            return "annotation_malformed"
        subtype = _name(annotation.get("Subtype"))
        if subtype not in {"Link", "Widget"}:
            return "annotation_unsupported"
        if "AA" in annotation or not _static_action(annotation.get("A")):
            return "annotation_dynamic"
        flags = resolve1(annotation.get("F", 0))
        if type(flags) is not int or flags < 0:
            return "annotation_malformed"
        if flags & _DYNAMIC_FLAGS:
            return "annotation_dynamic"
        if subtype == "Widget":
            try:
                kind, flags = _field_value(annotation, "FT"), _field_value(annotation, "Ff")
            except ValueError:
                return "annotation_malformed"
            if _name(kind) != "Btn" or type(flags) is not int or flags != _PUSHBUTTON:
                return "annotation_not_pushbutton"
        elif "AP" not in annotation:
            # Same as the base verifier: a Link without appearance paints nothing.
            continue
        bounds = _rect(annotation.get("Rect"), page.height)
        if bounds is None:
            return "annotation_malformed"
        x0, y0, x1, y1 = bounds
        if (
            x1 > box[0] - MARGIN_PT
            and x0 < box[2] + MARGIN_PT
            and y1 > box[1] - MARGIN_PT
            and y0 < box[3] + MARGIN_PT
        ):
            return "annotation_overlaps_paragraph"
    return None


def _native_reading(page, block, candidate, source, glyphs):
    """The unchanged base glyph-mode gates, from geometry up to the rendered reader."""
    box, geometry = candidate.bbox, candidate.geometry
    if (
        box is None
        or candidate.has_invalid_geometry
        or page.rotation
        or geometry.rotation
        or tuple(page.bbox[:2]) != (0, 0)
        or tuple(geometry.crop_box[:2]) != (0, 0)
        or abs(page.width - geometry.width_pt) > 0.001
        or abs(page.height - geometry.height_pt) > 0.001
    ):
        return "geometry_unsupported", []
    words = page.extract_words()
    if block.page_num not in glyphs:
        try:
            glyphs[block.page_num] = native_word_ink_geometry(
                source, block.page_num, list(range(len(words)))
            )
        except ValueError:
            glyphs[block.page_num] = dict(status="unresolved")
    proof = glyphs[block.page_num]
    if not isinstance(proof.get("matched_words"), list):
        return "glyph_geometry_unresolved", []
    boxes = {w["native_word_index"]: w["ink_bbox"] for w in proof["matched_words"]}
    unresolved = set(proof.get("unresolved_word_indices", ()))
    if set(boxes) | unresolved != set(range(len(words))) or any(
        words[i]["x1"] > box[0]
        and words[i]["x0"] < box[2]
        and words[i]["bottom"] > box[1]
        and words[i]["top"] < box[3]
        for i in unresolved
    ):
        return "glyph_geometry_unresolved", []
    selected, clipped = [], False
    for index, word in enumerate(words):
        if index not in boxes:
            continue
        wb = boxes[index]
        if wb[2] <= box[0] or wb[0] >= box[2] or wb[3] <= box[1] or wb[1] >= box[3]:
            continue
        if not word["upright"] or not (
            box[0] - 0.001 <= wb[0] < wb[2] <= box[2] + 0.001
            and box[1] - 0.001 <= wb[1] < wb[3] <= box[3] + 0.001
        ):
            clipped = True
        selected.append(dict(index=index, text=word["text"], bbox=wb))
    if clipped:
        return "clipped_or_rotated_words", []
    raw = " ".join(w["text"] for w in selected)
    if not raw or _normalized(raw) != _normalized(candidate.source.raw_text):
        return "text_mismatch", []
    if source_verification._rendered_text(page, box) != _PLATFORM_UNSUPPORTED:
        return "rendered_reader_in_scope", []
    return None, selected


def attest_widget_visibility(native, graph, source, *, tenant_id):
    """Recompute the base receipt, then gate only its interactive-visibility records."""
    from proofops.adapters.local.native_replay_cache import replay_cached

    if not isinstance(native, dict) or native.get("schema") != "native_paragraph_attestation_v2":
        raise ValueError("NATIVE_GLYPH_ATTESTATION_REQUIRED")
    replay_cached(native, graph, source, tenant_id=tenant_id)
    blocks = {block.source_id: block for block in graph.blocks}
    targets = [
        row["source_id"]
        for row in native["records"]
        if row.get("status") == "unresolved"
        and row.get("reason") == "interactive_visibility_requires_review"
    ]
    records, glyphs = [], {}
    with pdfplumber.open(io.BytesIO(source)) as document:
        document_reason = _document_reason(document) if targets else None
        for source_id in targets:
            block = blocks[source_id]
            candidate = block.candidates[block.winner]
            record = dict(source_id=source_id, status="unresolved", words=[])
            records.append(record)
            if document_reason is not None:
                record["reason"] = document_reason
                continue
            page = document.pages[block.page_num - 1]
            reason = (
                "geometry_unsupported"
                if candidate.bbox is None
                else _annotation_reason(page, candidate.bbox)
            )
            if reason is None:
                reason, words = _native_reading(page, block, candidate, source, glyphs)
                record["words"] = words
            record["reason"] = reason
            if reason is None:
                record["status"] = "eligible"
    proof = dict(
        schema=PROOF_SCHEMA,
        policy=native_widget_visibility_policy(),
        tenant_id=tenant_id,
        base_attestation_sha256=canonical_hash(native),
        input_graph_sha256=canonical_hash(asdict(graph)),
        records=records,
    )
    proof["artifact_sha256"] = canonical_hash(proof)
    return proof


def widget_eligible_words(proof) -> dict[str, str]:
    """Native words of every eligible record; used only for Upstage corroboration."""
    return {
        row["source_id"]: " ".join(word["text"] for word in row["words"])
        for row in proof["records"]
        if row["status"] == "eligible" and row["words"]
    }
