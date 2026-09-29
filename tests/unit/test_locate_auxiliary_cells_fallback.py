"""The native ink fallback in locate_auxiliary_cells runs only for unattempted words.

The parser child already attaches ink geometry per word (``ink_bbox`` plus
``resolved``). Re-running the native reader per table for words it explicitly
left unresolved cost minutes on a full report without changing any result. A
resolved flag must never let a bare font box stand in for real ink geometry.
"""

from __future__ import annotations

import proofops.adapters.local.native_glyph_geometry as native
import pytest
from proofops.adapters.parsing.odl_table_repair import locate_auxiliary_cells

SOURCE = b"%PDF-stand-in"


def _table():
    cells = [
        {
            "type": "table cell",
            "id": f"t-r0-c{column}",
            "page number": 1,
            "bounding box": [x0, 0.0, x0 + 50.0, 20.0],
            "content": text,
        }
        for column, (x0, text) in enumerate(((0.0, "12"), (50.0, "34")))
    ]
    row = {"type": "table row", "id": "t-r0", "page number": 1, "cells": cells}
    return {"type": "table", "id": "t", "page number": 1, "rows": [row]}


def _words(**extra):
    words = [
        {"text": "12", "bbox": [10.0, 5.0, 20.0, 15.0]},
        {"text": "34", "bbox": [60.0, 5.0, 70.0, 15.0]},
    ]
    for word, patch in zip(words, extra.get("patches", ()), strict=False):
        word.update(patch)
    return {1: words}


INK = [[11.0, 6.0, 19.0, 14.0], [61.0, 6.0, 69.0, 14.0]]


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(source, page, indices):
        seen.append((page, list(indices)))
        if calls.fail:
            raise ValueError("native reader unavailable")
        return dict(
            matched_words=[
                dict(native_word_index=i, ink_bbox=INK[i]) for i in indices if i < len(INK)
            ],
            unresolved_word_indices=[],
        )

    calls = type("Calls", (), {"fail": False, "seen": seen})
    monkeypatch.setattr(native, "native_word_ink_geometry", fake)
    return calls


def _run(words):
    nodes, receipts = locate_auxiliary_cells([_table()], words, source=SOURCE)
    cells = [c for r in nodes[0]["rows"] for c in r["cells"]]
    return receipts[0], [c["bounding box"] for c in cells]


def test_child_attempted_words_skip_the_fallback_and_use_their_ink(calls):
    words = _words(
        patches=[
            {"ink_bbox": INK[0], "resolved": True},
            {"ink_bbox": INK[1], "resolved": True},
        ]
    )
    receipt, boxes = _run(words)
    assert calls.seen == []
    assert receipt["status"] == "repaired" and boxes == INK


def test_explicit_unresolved_is_not_retried_and_stays_refused(calls):
    # The child stores ink_bbox=None for an unmatched word; normalization drops it.
    words = _words(
        patches=[{"ink_bbox": INK[0], "resolved": True}, {"ink_bbox": None, "resolved": False}]
    )
    receipt, _ = _run(words)
    assert calls.seen == []
    assert (receipt["status"], receipt["reason"]) == ("unrepaired", "unresolved_glyph_geometry")


def test_legacy_words_without_ink_attempt_still_take_the_fallback(calls):
    receipt, boxes = _run(_words())
    assert calls.seen == [(1, [0, 1])]
    assert receipt["status"] == "repaired" and boxes == INK


def test_resolved_flag_without_ink_triggers_fallback(calls):
    words = _words(patches=[{"resolved": True}, {"ink_bbox": INK[1], "resolved": True}])
    receipt, boxes = _run(words)
    assert calls.seen == [(1, [0, 1])]
    assert receipt["status"] == "repaired" and boxes == INK


@pytest.mark.parametrize("flag", [True, None, "yes"])
def test_malformed_resolved_flag_cannot_promote_a_font_box(calls, flag):
    calls.fail = True
    words = _words(patches=[{"resolved": flag}, {"ink_bbox": INK[1], "resolved": True}])
    receipt, _ = _run(words)
    assert (receipt["status"], receipt["reason"]) == ("unrepaired", "unresolved_glyph_geometry")
