"""NEW-run Upstage Document Parse corroboration of native paragraphs off macOS.

Separately versioned from the v1 raster fallback (``raster_visibility`` /
``raster_checkpoint``), whose bytes and policy hash stay untouched. It reuses the
v1 renderer and offline receipt check unchanged: ``raster_ocr.prepare_raster_ocr``
(216 dpi crop -> image-only PDF + correspondence) and ``raster_ocr.replay_raster_ocr``
(re-render, request/response/receipt hashes, billing).

Eligibility comes only from the unchanged base receipt (``native_paragraph_attestation_v2``):
status ``unresolved``, reason ``rendered_text_unresolved`` and a rendered result that is
exactly the off-macOS ``UnsupportedPlatform`` verdict. So glyph mapping, visibility,
interactivity, box containment, clipping and native-words == candidate text all already
passed. A macOS Vision disagreement is never in scope.

Comparison ``normalized_quote_fold_v1``: ``citations._normalized`` followed by the finite
curly-quote fold of ``native_paragraph_typography`` (both pinned by hash in the policy).
No CER, no fuzzy matching, no whitespace removal. The provider text only corroborates
the native words already in the base receipt. It is never stored as the evidence text
and never promotes anything the base verifier rejected. No network here: replay uses
stored receipts only.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

from proofops.adapters.local import raster_ocr
from proofops.adapters.local.run_artifacts import native_paragraph_policy
from proofops.application.evidence import citations
from proofops.domain.provenance import canonical_hash

POLICY_SCHEMA = "native_upstage_ocr_policy_v1"
REQUEST_SCHEMA = "native_upstage_ocr_request_v1"
COMPARISON = "normalized_quote_fold_v1"
# Finite curly-quote fold, byte-pinned here through ``helper_sha256``. It is
# identical to ``native_paragraph_typography._QUOTE_FOLD`` (a test pins the equality)
# and is never widened: no primes, degree signs, digits, dashes or whitespace.
QUOTE_FOLD = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)
_PLATFORM_UNSUPPORTED = dict(
    status="unresolved", reason="rendered_reader_unavailable", error="UnsupportedPlatform"
)


def _file_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def native_upstage_ocr_policy(*, mode="standard", max_pages=10, max_calls=1) -> dict:
    """Pins every byte that decides eligibility, rendering, receipt checks and comparison."""
    if mode not in {"standard", "enhanced"}:
        raise ValueError("UPSTAGE_OCR_MODE_INVALID")
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        raise ValueError("UPSTAGE_OCR_MAX_PAGES_INVALID")
    if type(max_calls) is not int or not 1 <= max_calls <= 20:
        raise ValueError("UPSTAGE_OCR_MAX_CALLS_INVALID")
    here = Path(__file__)
    return {
        "schema": POLICY_SCHEMA,
        "mode": mode,
        "max_pages": max_pages,
        "max_calls": max_calls,
        "native_policy_sha256": canonical_hash(native_paragraph_policy()),
        "eligibility": "base_v2_rendered_text_unresolved_unsupported_platform_only",
        "comparison": COMPARISON,
        "helper_sha256": _file_sha(here),
        "store_sha256": _file_sha(here.with_name("native_upstage_ocr_store.py")),
        "raster_helper_sha256": _file_sha(Path(raster_ocr.__file__)),
        "normalization_sha256": _file_sha(Path(citations.__file__)),
        "quote_fold_sha256": canonical_hash(sorted(QUOTE_FOLD.items())),
        "reader_versions": {
            "pypdfium2": version("pypdfium2"),
            "pdfplumber": version("pdfplumber"),
            "pypdf": version("pypdf"),
            "Pillow": version("Pillow"),
        },
    }


def live_policy_for(stored) -> dict:
    """The live policy with the stored limits; equality means nothing pinned changed."""
    if not isinstance(stored, dict):
        raise ValueError("UPSTAGE_OCR_POLICY_INVALID")
    return native_upstage_ocr_policy(
        mode=stored.get("mode"),
        max_pages=stored.get("max_pages"),
        max_calls=stored.get("max_calls"),
    )


def eligible_upstage_sources(native) -> set[str]:
    """Base records that passed every native gate and lacked only a rendered reader."""
    if not isinstance(native, dict) or native.get("schema") != "native_paragraph_attestation_v2":
        raise ValueError("NATIVE_GLYPH_ATTESTATION_REQUIRED")
    return {
        row["source_id"]
        for row in native.get("records", ())
        if isinstance(row, dict)
        and row.get("status") == "unresolved"
        and row.get("reason") == "rendered_text_unresolved"
        and row.get("rendered") == _PLATFORM_UNSUPPORTED
        and "rendered_attempts" not in row
        and isinstance(row.get("words"), list)
        and row["words"]
    }


def native_words(native) -> dict[str, str]:
    return {
        row["source_id"]: " ".join(word["text"] for word in row["words"])
        for row in native["records"]
        if isinstance(row.get("words"), list)
    }


def _folded(text: str) -> str:
    return citations._normalized(text).translate(QUOTE_FOLD)


def matches(native_text: str, provider_text: str) -> bool:
    folded = _folded(native_text)
    return bool(folded) and folded == _folded(provider_text)


def corroborate(native, graph, source, entries, *, tenant_id, eligible):
    """Offline: recompute the base receipt, then replay each stored request/receipt.

    ``entries`` are ``(request, receipt, receipt_sha256)`` already bound to immutable
    storage by the caller. Returns ``(graph, corroborated_ids, readings)``.
    """
    from proofops.adapters.local.native_replay_cache import replay_cached

    baseline = replay_cached(native, graph, source, tenant_id=tenant_id)
    verified_before = {b.source_id for b in baseline.blocks if b.quality == "verified"}
    words = native_words(native)
    blocks = {block.source_id: block for block in graph.blocks}
    corroborated, readings = set(), []
    for request, receipt, receipt_sha256 in entries:
        correspondence = request["correspondence"]
        rows = raster_ocr.replay_raster_ocr(
            correspondence,
            receipt,
            graph,
            source,
            request_sha256=canonical_hash(correspondence),
            receipt_sha256=receipt_sha256,
            tenant_id=tenant_id,
        )
        for row in rows:
            source_id = row["source_id"]
            block = blocks.get(source_id)
            if (
                source_id not in eligible
                or source_id in verified_before
                or block is None
                or block.kind != "paragraph"
                or citations._normalized(words[source_id]) != citations._normalized(block.raw_text)
            ):
                raise ValueError("NATIVE_UPSTAGE_OCR_INELIGIBLE")
            agreed = matches(words[source_id], row["external_ocr"])
            if agreed:
                corroborated.add(source_id)
            readings.append(
                dict(
                    source_id=source_id,
                    request_id=request["request_id"],
                    provider_text_sha256=sha256(row["external_ocr"].encode()).hexdigest(),
                    corroborated=agreed,
                )
            )
    result = replace(
        baseline,
        blocks=tuple(
            replace(block, quality="verified") if block.source_id in corroborated else block
            for block in baseline.blocks
        ),
    )
    return result, corroborated, readings


def compose_checkpoint(snapshot, message, native, graph, source, entries, refs):
    """Producer and reader share this: offline composition from immutable records only.

    Returns ``(graph, coverage, refs, policy_sha256)``. ``graph`` must be the pre-native
    graph whose receipt ``native`` pins. Every request must be for this exact job, snapshot
    policy and native receipt. Receipts are replayed with no provider call.
    """
    from proofops.adapters.local.native_upstage_ocr_store import validate_request

    policy = snapshot.get("native_upstage_ocr_policy")
    if (
        not isinstance(policy, dict)
        or policy != live_policy_for(policy)
        or canonical_hash(policy) != snapshot.get("native_upstage_ocr_policy_hash")
    ):
        raise ValueError("UPSTAGE_OCR_POLICY_CHANGED")
    if (
        not isinstance(native, dict)
        or native.get("tenant_id") != message.tenant_id
        or native.get("source_sha256") != snapshot["document"]["sha256"]
        or native.get("parse_manifest_id") != graph.parse_manifest_id
        or graph.source_sha256 != snapshot["document"]["sha256"]
    ):
        raise ValueError("UPSTAGE_OCR_CHECKPOINT_INPUT_INVALID")
    pages = {block.source_id: block.page_num for block in graph.blocks}
    eligible = {
        sid
        for sid in eligible_upstage_sources(native)
        if pages.get(sid) in snapshot["selected_pages"]
    }
    if len(entries) > policy["max_calls"] or len(entries) != len(refs):
        raise ValueError("UPSTAGE_OCR_MAX_CALLS_EXCEEDED")
    requested: set[str] = set()
    for request, _receipt, _receipt_sha in entries:
        validate_request(request, message=message, snapshot=snapshot)
        if (
            request["native_attestation_sha256"] != canonical_hash(native)
            or request["correspondence"].get("graph_sha256") != native.get("input_graph_sha256")
            or request["eligible_source_ids"] != sorted(eligible)
            or requested & set(request["requested_source_ids"])
        ):
            raise ValueError("UPSTAGE_OCR_CHECKPOINT_REQUEST_MISMATCH")
        requested.update(request["requested_source_ids"])
    result, corroborated, _readings = corroborate(
        native, graph, source, entries, tenant_id=message.tenant_id, eligible=eligible
    )
    # max_calls/max_pages can leave eligible paragraphs unsent: they are listed as
    # skipped (and stay unresolved), never counted as checked.
    coverage = {
        "eligible_source_ids": sorted(eligible),
        "requested_source_ids": sorted(requested),
        "skipped_source_ids": sorted(eligible - requested),
        "corroborated_source_ids": sorted(corroborated),
        "unresolved_source_ids": sorted(eligible - corroborated),
        "complete": requested == eligible,
    }
    return result, coverage, list(refs), snapshot["native_upstage_ocr_policy_hash"]
