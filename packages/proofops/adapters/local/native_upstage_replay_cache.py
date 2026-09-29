"""Bounded process-local reuse of successful Upstage OCR checkpoint compositions.

``compose_checkpoint`` re-attests the widget gate and re-rasterises every stored
request from the original bytes on each read (about 30 s for a 7.5 MB report).
This module caches only its successful result. The key covers every input, the
live pinned policies, the platform and the reader versions, so any changed input
or verifier misses and runs the unchanged full replay. Failures are never cached.
The pinned widget and Upstage modules stay byte-identical.
"""

import os
import sys
from collections import OrderedDict
from dataclasses import asdict, replace
from hashlib import sha256
from importlib.metadata import version
from json import dumps, loads
from threading import Lock

from proofops.adapters.local.run_artifacts import native_paragraph_policy
from proofops.domain.provenance import canonical_hash

_compositions: OrderedDict[str, tuple[frozenset[str], str, str, str, int]] = OrderedDict()
_bytes = 0
_lock = Lock()
_MAX_ENTRIES = 64
_MAX_BYTES = 16 * 1024 * 1024


def _live_policies(snapshot):
    """The live policies the replay itself compares against; they pin verifier bytes."""
    from proofops.adapters.local import native_upstage_ocr as v1
    from proofops.adapters.local.native_upstage_ocr_widget import HASH_KEY, KEY
    from proofops.adapters.local.native_widget_visibility import (
        native_widget_visibility_policy,
    )

    widget = native_widget_visibility_policy() if KEY in snapshot or HASH_KEY in snapshot else None
    return dict(
        upstage=v1.live_policy_for(snapshot.get("native_upstage_ocr_policy")),
        widget=widget,
        native=native_paragraph_policy(),
    )


def _key(snapshot, message, native, graph, source, entries, refs):
    return canonical_hash(
        dict(
            snapshot=snapshot,
            message=asdict(message),
            native_sha256=canonical_hash(native),
            graph_sha256=canonical_hash(asdict(graph)),
            source_sha256=sha256(source).hexdigest(),
            entries_sha256=canonical_hash([list(entry) for entry in entries]),
            refs_sha256=canonical_hash(refs),
            policies=_live_policies(snapshot),
            platform=sys.platform,
            toolchain=os.environ.get("DEVELOPER_DIR"),
            readers=[version(name) for name in ("pdfplumber", "pdfminer.six", "pypdfium2")],
        )
    )


def _promote(graph, verified):
    return replace(
        graph,
        blocks=tuple(
            replace(block, quality="verified") if block.source_id in verified else block
            for block in graph.blocks
        ),
    )


def compose_checkpoint_cached(snapshot, message, native, graph, source, entries, refs):
    """``native_upstage_ocr_widget.compose_checkpoint`` with successful results reused."""
    global _bytes
    from proofops.adapters.local.native_upstage_ocr_widget import compose_checkpoint

    try:
        key = _key(snapshot, message, native, graph, source, entries, refs)
    except (ValueError, TypeError, KeyError, AttributeError):
        # Unkeyable input: the full replay decides (and fails closed) as before.
        return compose_checkpoint(snapshot, message, native, graph, source, entries, refs)
    with _lock:
        cached = _compositions.get(key)
        if cached is not None:
            _compositions.move_to_end(key)
    if cached is None:
        # ponytail: simultaneous cold reads may repeat replay; coalesce only if measured.
        composed, coverage, out_refs, policy_sha256 = compose_checkpoint(
            snapshot, message, native, graph, source, entries, refs
        )
        verified = frozenset(b.source_id for b in composed.blocks if b.quality == "verified")
        if _promote(graph, verified) != composed:
            # Only a quality promotion is representable; anything else is not reused.
            return composed, coverage, out_refs, policy_sha256
        coverage_json = dumps(coverage, sort_keys=True, separators=(",", ":"))
        refs_json = dumps(out_refs, sort_keys=True, separators=(",", ":"))
        size = len(coverage_json) + len(refs_json) + len(policy_sha256) + sum(map(len, verified))
        cached = (verified, coverage_json, refs_json, policy_sha256, size)
        if size <= _MAX_BYTES:
            with _lock:
                previous = _compositions.pop(key, None)
                if previous is not None:
                    _bytes -= previous[4]
                _compositions[key] = cached
                _bytes += size
                while len(_compositions) > _MAX_ENTRIES or _bytes > _MAX_BYTES:
                    _bytes -= _compositions.popitem(last=False)[1][4]
    verified, coverage_json, refs_json, policy_sha256, _size = cached
    return _promote(graph, verified), loads(coverage_json), loads(refs_json), policy_sha256
