"""Full-document search-coverage receipt: the prerequisite for any verified absence.

R00 §12 common guard: ``unknown -> absent`` only after the WHOLE registered
document's search scope, readability and search log are complete. A bounded
claim-local packet never proves absence. GAP-004 still restricts attribution:
a number, year, baseline or progress fact found on another page is never linked
to the claim by this module; it is only shown to the delegated reviewer.

This module is pure (no file, network or environment access). It never sets an
element state. Its strongest output is ``search_prerequisites_complete=True``,
which only makes a claim/element eligible for an explicit delegated review of the
whole corpus; lexical hits or their absence decide nothing.

Page facts (every text-layer word with its index, box and exact NFC text, and
the image count per physical page) must come from the original PDF bytes through
the local adapter, never from a client. Readable coverage is per native word:
each word must be attributed to exactly one verified block by geometry, and
each block's text must equal the exact ordered sequence of its attributed words,
so a character bag, duplicated block or shifted box can never mask an omission.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from proofops.domain.provenance import canonical_hash
from proofops.domain.rules import goal, management, performance

RECEIPT_SCHEMA = "search_coverage_receipt_v1"
REVIEW_REQUEST_SCHEMA = "search_absence_review_request_v1"
REVIEW_SCHEMA = "search_absence_review_v1"
POLICY_SCHEMA = "full_document_search_coverage_policy_v1"
# Element -> the primitives an ``absent_confirmed`` review asserts, all of them.
ELEMENT_PRIMITIVES = {
    element: tuple(sorted(primitives))
    for mapping in (goal.ELEMENTS, performance.ELEMENTS, management.ELEMENTS)
    for element, primitives in mapping.items()
}
ELEMENT_IDS = frozenset(ELEMENT_PRIMITIVES)
# Deterministic producers own these (P4 assurance coverage); never absent via search.
EXCLUDED_ELEMENTS = frozenset({"P4", "P6"})
# Numeric/temporal elements whose facts stay claim-local under GAP-004 A.
CLAIM_LOCAL_ELEMENTS = frozenset({"G1", "G3", "G5", "P1", "P2"})
REVIEW_DECISIONS = frozenset({"absent_confirmed", "not_absent", "undetermined"})
REVIEWER_KINDS = frozenset({"human", "ai_delegated"})
MAX_QUERIES = 32
MAX_QUERY_CHARS = 200
_IDENTITY_KEYS = (
    "tenant_id",
    "run_id",
    "document_version_id",
    "object_version_id",
    "input_hash",
    "source_sha256",
    "parse_manifest_id",
    "rulepack_sha256",
)
_PAGE_FACT_KEYS = frozenset({"page", "words", "images", "text_sha256"})
_WORD_KEYS = frozenset({"index", "text", "bbox"})


def search_coverage_policy() -> dict:
    """Pins this module's bytes so a receipt never replays under other rules."""
    return dict(
        schema=POLICY_SCHEMA,
        module_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        readable_block_quality="verified",
        issue_states_blocking="any",
        images="incomplete_unless_absent",
        text_correspondence="per_native_word_index_bbox_exact_nfc_ordered_sequence_v1",
        word_attribution="word_box_center_in_exactly_one_verified_block_box",
        duplicate_blocks="identical_nfc_text_and_box_deduplicated_else_refused",
        search="exhaustive_all_blocks_literal_and_terms",
        decides_element_state=False,
    )


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _words_digest(words: list[dict]) -> str:
    return canonical_hash([[w["index"], w["text"], w["bbox"]] for w in words])


def page_facts_from_words(page: int, words: list[dict], images: int) -> dict:
    """Canonical per-page facts from original-PDF words (pdfplumber dicts with
    ``text``, ``x0``, ``top``, ``x1``, ``bottom``), kept in text-layer order."""
    items = [
        dict(
            index=index,
            text=_nfc(word["text"]),
            bbox=[float(word[key]) for key in ("x0", "top", "x1", "bottom")],
        )
        for index, word in enumerate(words)
    ]
    return dict(page=page, words=items, images=images, text_sha256=_words_digest(items))


def search_terms(text: str) -> list[str]:
    """Same routing terms as ``adapters.local.evidence_search.search_terms``.

    Duplicated (not imported) because application code must not import adapters.
    Terms only route hits to the reviewer; they never decide presence or absence.
    """
    terms: list[str] = []
    for word in re.findall(r"[가-힣]+|[a-z]+|\d+(?:[.,]\d+)*", text.casefold()):
        if re.fullmatch(r"[가-힣]+", word):
            terms.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            terms.append(word)
    return terms


def _validate_queries(queries) -> tuple[str, ...]:
    if isinstance(queries, str | bytes) or not isinstance(queries, list | tuple):
        raise ValueError("SEARCH_QUERIES_INVALID")
    items = tuple(queries)
    if not items or len(items) > MAX_QUERIES:
        raise ValueError("SEARCH_QUERIES_INVALID")
    for query in items:
        if (
            not isinstance(query, str)
            or query != query.strip()
            or not query
            or len(query) > MAX_QUERY_CHARS
            or not search_terms(query)
        ):
            raise ValueError("SEARCH_QUERIES_INVALID")
    if len(set(items)) != len(items):
        raise ValueError("SEARCH_QUERIES_INVALID")
    return items


def _validate_page_facts(page_facts, registered_page_count: int) -> dict[int, dict]:
    if type(registered_page_count) is not int or registered_page_count < 1:
        raise ValueError("REGISTERED_PAGE_COUNT_INVALID")
    facts: dict[int, dict] = {}
    for item in page_facts:
        if not isinstance(item, dict) or set(item) != _PAGE_FACT_KEYS:
            raise ValueError("PAGE_FACTS_INVALID")
        page = item["page"]
        if type(page) is not int or not 1 <= page <= registered_page_count or page in facts:
            raise ValueError("PAGE_FACTS_INVALID")
        if type(item["images"]) is not int or item["images"] < 0:
            raise ValueError("PAGE_FACTS_INVALID")
        words = item["words"]
        if not isinstance(words, list) or any(
            not isinstance(word, dict)
            or set(word) != _WORD_KEYS
            or word["index"] != index
            or not isinstance(word["text"], str)
            or not word["text"]
            or word["text"] != _nfc(word["text"])
            or not isinstance(word["bbox"], list)
            or len(word["bbox"]) != 4
            or any(
                isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
                for v in word["bbox"]
            )
            for index, word in enumerate(words)
        ):
            raise ValueError("PAGE_FACTS_INVALID")
        if item["text_sha256"] != _words_digest(words):
            raise ValueError("PAGE_FACTS_INVALID")
        facts[page] = item
    if set(facts) != set(range(1, registered_page_count + 1)):
        raise ValueError("PAGE_FACTS_INVALID")
    return facts


def evaluate_pages(graph, page_facts, *, registered_page_count: int, selected_pages) -> list[dict]:
    """Per physical page 1..N: complete only when every readable-coverage check holds."""
    facts = _validate_page_facts(page_facts, registered_page_count)
    selected = set(selected_pages)
    by_page: dict[int, list] = {}
    for block in graph.blocks:
        by_page.setdefault(block.page_num, []).append(block)
    issues: dict[int, list] = {}
    for issue in graph.issues:
        issues.setdefault(issue.page_num, []).append(issue)
    records = []
    for page in range(1, registered_page_count + 1):
        fact, blocks = facts[page], by_page.get(page, [])
        reasons = []
        if page not in selected:
            reasons.append("page_not_in_run_scope")
        if not fact["words"]:
            reasons.append("no_native_text_layer")
        if fact["images"]:
            # An image may carry text no verified reader read; never assume it empty.
            reasons.append("image_region_unread")
        if not blocks:
            reasons.append("no_parsed_blocks")
        unverified = sorted(b.source_id for b in blocks if b.quality != "verified")
        if unverified:
            reasons.append("block_not_source_verified")
        if any(b.winner is None for b in blocks):
            reasons.append("block_without_selected_source")
        if issues.get(page):
            reasons.append("quality_issue_on_page")
        words = _word_correspondence(fact["words"], blocks)
        reasons.extend(words.pop("reasons"))
        records.append(
            dict(
                page=page,
                status="incomplete" if reasons else "complete",
                reasons=reasons,
                block_count=len(blocks),
                unverified_source_ids=unverified,
                native_text_sha256=fact["text_sha256"],
                images=fact["images"],
                **words,
            )
        )
    return records


def _inside(point, box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _word_correspondence(words: list[dict], blocks) -> dict:
    """Attribute every native word to exactly one verified block by geometry and
    require each block's text to equal its words' exact ordered NFC sequence.

    Only verified blocks with a selected source and a box can own words; other
    blocks already make the page incomplete and never cover anything here. Two
    blocks with identical NFC text and identical box are one source (the later
    source_id is recorded as deduplicated); any other overlap owning the same
    word is refused rather than resolved.
    """
    owners, deduplicated, seen = [], [], set()
    for block in sorted(blocks, key=lambda b: b.source_id):
        if block.quality != "verified" or block.winner is None or block.bbox is None:
            continue
        key = (_nfc(block.raw_text), tuple(float(v) for v in block.bbox))
        if key in seen:
            deduplicated.append(block.source_id)
            continue
        seen.add(key)
        owners.append(block)
    attributed: dict[str, list[int]] = {b.source_id: [] for b in owners}
    unattributed, ambiguous = [], []
    for word in words:
        box = word["bbox"]
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        hits = [b.source_id for b in owners if _inside(center, b.bbox)]
        if not hits:
            unattributed.append(word["index"])
        elif len(hits) > 1:
            ambiguous.append(word["index"])
        else:
            attributed[hits[0]].append(word["index"])
    empty, mismatched = [], []
    for block in owners:
        indices = attributed[block.source_id]
        if not indices:
            empty.append(block.source_id)
        elif [words[i]["text"] for i in indices] != _nfc(block.raw_text).split():
            mismatched.append(block.source_id)
    reasons = []
    if unattributed:
        reasons.append("native_word_not_attributed_to_verified_block")
    if ambiguous:
        reasons.append("native_word_attributed_to_overlapping_blocks")
    if empty:
        reasons.append("block_geometry_without_native_words")
    if mismatched:
        reasons.append("block_text_not_native_word_sequence")
    return dict(
        reasons=reasons,
        native_word_count=len(words),
        attributed_word_count=sum(len(v) for v in attributed.values()),
        unattributed_word_indices=unattributed,
        ambiguous_word_indices=ambiguous,
        blocks_without_words=empty,
        blocks_text_mismatch=mismatched,
        deduplicated_source_ids=deduplicated,
    )


def corpus_manifest(graph, registered_page_count: int) -> dict:
    """Every block of the registered document, for the reviewer's whole-corpus pass."""
    blocks = sorted(
        (
            dict(
                source_id=b.source_id,
                page=b.page_num,
                kind=b.kind,
                quality=b.quality,
                raw_text_sha256=sha256(b.raw_text.encode()).hexdigest(),
            )
            for b in graph.blocks
            if 1 <= b.page_num <= registered_page_count
        ),
        key=lambda item: (item["page"], item["source_id"]),
    )
    return dict(block_count=len(blocks), blocks=blocks, corpus_sha256=canonical_hash(blocks))


def exhaustive_search(graph, queries) -> list[dict]:
    """Every block is examined for every query; no ranking cut-off or top-k."""
    blocks = sorted(graph.blocks, key=lambda b: (b.page_num, b.source_id))
    examined = canonical_hash([b.source_id for b in blocks])
    log = []
    for query in queries:
        literal = unicodedata.normalize("NFC", query).casefold()
        terms = set(search_terms(query))
        literal_hits, term_hits = [], []
        for block in blocks:
            text = block.normalized_text.casefold()
            if literal in text:
                literal_hits.append(block.source_id)
            elif terms & set(search_terms(block.normalized_text)):
                term_hits.append(block.source_id)
        log.append(
            dict(
                query=query,
                terms=sorted(terms),
                examined_block_count=len(blocks),
                examined_source_ids_sha256=examined,
                literal_hit_source_ids=literal_hits,
                term_hit_source_ids=term_hits,
            )
        )
    return log


def _validate_identity(identity: dict, graph, claim) -> dict:
    if not isinstance(identity, dict) or set(identity) != set(_IDENTITY_KEYS):
        raise ValueError("SEARCH_COVERAGE_IDENTITY_INVALID")
    if any(not isinstance(identity[key], str) or not identity[key] for key in _IDENTITY_KEYS):
        raise ValueError("SEARCH_COVERAGE_IDENTITY_INVALID")
    if (
        graph.tenant_id != identity["tenant_id"]
        or graph.document_version_id != identity["document_version_id"]
        or graph.parse_manifest_id != identity["parse_manifest_id"]
        or graph.source_sha256 != identity["source_sha256"]
        or claim.tenant_id != identity["tenant_id"]
        or claim.document_version_id != identity["document_version_id"]
        or claim.parse_manifest_id != identity["parse_manifest_id"]
        or claim.source_sha256 != identity["source_sha256"]
    ):
        raise ValueError("SEARCH_COVERAGE_IDENTITY_MISMATCH")
    return dict(identity)


def build_receipt(
    *,
    identity: dict,
    graph,
    claim,
    element: str,
    queries,
    page_facts,
    registered_page_count: int,
    selected_pages,
) -> dict:
    """Deterministic receipt; no timestamps so replay recomputes identical bytes."""
    if element not in ELEMENT_IDS:
        raise ValueError("SEARCH_ELEMENT_INVALID")
    if element in EXCLUDED_ELEMENTS:
        raise ValueError("SEARCH_ELEMENT_EXCLUDED")
    items = _validate_queries(queries)
    identity = _validate_identity(identity, graph, claim)
    pages = evaluate_pages(
        graph,
        page_facts,
        registered_page_count=registered_page_count,
        selected_pages=selected_pages,
    )
    corpus = corpus_manifest(graph, registered_page_count)
    outside = sorted(b.source_id for b in graph.blocks if b.page_num > registered_page_count)
    log = exhaustive_search(graph, items)
    incomplete = [p["page"] for p in pages if p["status"] != "complete"]
    receipt = dict(
        schema=RECEIPT_SCHEMA,
        policy=search_coverage_policy(),
        **identity,
        graph_sha256=canonical_hash(asdict(graph)),
        claim_id=claim.claim_id,
        claim_revision=claim.revision,
        claim_quote_sha256=sha256(claim.quote.encode()).hexdigest(),
        claim_source_ids=sorted({ref.source_id for ref in claim.source_refs}),
        element=element,
        element_primitives=list(ELEMENT_PRIMITIVES[element]),
        attribution_scope="claim_local" if element in CLAIM_LOCAL_ELEMENTS else "element_policy",
        registered_page_count=registered_page_count,
        selected_pages=sorted(set(selected_pages)),
        pages=pages,
        blocks_outside_registered_pages=outside,
        page_facts_sha256=canonical_hash(sorted(page_facts, key=lambda f: f["page"])),
        corpus=dict(block_count=corpus["block_count"], corpus_sha256=corpus["corpus_sha256"]),
        queries=list(items),
        search_log=log,
        incomplete_pages=incomplete,
        search_prerequisites_complete=not incomplete and not outside,
        element_state_effect="none",
    )
    receipt["artifact_sha256"] = canonical_hash(receipt)
    return receipt


def verify_receipt_hash(receipt: dict) -> None:
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("SEARCH_COVERAGE_RECEIPT_INVALID")
    body = {key: value for key, value in receipt.items() if key != "artifact_sha256"}
    if canonical_hash(body) != receipt.get("artifact_sha256"):
        raise ValueError("SEARCH_COVERAGE_RECEIPT_INVALID")


def review_request(receipt: dict, graph) -> dict:
    """Explicit delegated review over the validated whole corpus, never a verdict.

    Only issued when every search prerequisite is complete. The reviewer must
    inspect the whole corpus (not just the hits) and dispose of every hit.
    """
    verify_receipt_hash(receipt)
    if receipt["search_prerequisites_complete"] is not True:
        raise ValueError("SEARCH_PREREQUISITES_INCOMPLETE")
    corpus = corpus_manifest(graph, receipt["registered_page_count"])
    if corpus["corpus_sha256"] != receipt["corpus"]["corpus_sha256"]:
        raise ValueError("SEARCH_COVERAGE_RECEIPT_MISMATCH")
    hits = sorted(
        {
            source_id
            for entry in receipt["search_log"]
            for source_id in entry["literal_hit_source_ids"] + entry["term_hit_source_ids"]
        }
    )
    return dict(
        schema=REVIEW_REQUEST_SCHEMA,
        receipt_sha256=receipt["artifact_sha256"],
        tenant_id=receipt["tenant_id"],
        run_id=receipt["run_id"],
        claim_id=receipt["claim_id"],
        element=receipt["element"],
        asserted_primitives=receipt["element_primitives"],
        attribution_scope=receipt["attribution_scope"],
        corpus=corpus,
        search_hit_source_ids=hits,
        allowed_decisions=sorted(REVIEW_DECISIONS),
        instructions=(
            "Inspect every corpus block, not only the search hits. Zero lexical hits is "
            "not evidence of absence. Record a rationale and a disposition for every hit. "
            "Numbers, years, baselines and progress found outside the claim's own sources "
            "are never attributed to the claim (GAP-004); if such a fact exists, choose "
            "undetermined or not_absent, never absent_confirmed. absent_confirmed asserts "
            "EVERY primitive in asserted_primitives absent; if any one is disclosed or "
            "uncertain, choose not_absent or undetermined."
        ),
    )


def validate_review(receipt: dict, review: dict) -> dict:
    """Validate a delegated review against the receipt it cites; fail closed."""
    verify_receipt_hash(receipt)
    if receipt["search_prerequisites_complete"] is not True:
        raise ValueError("SEARCH_PREREQUISITES_INCOMPLETE")
    keys = {
        "schema",
        "receipt_sha256",
        "reviewed_corpus_sha256",
        "reviewed_block_count",
        "decision",
        "rationale",
        "hit_dispositions",
        "reviewer",
        "reviewed_at",
    }
    if not isinstance(review, dict) or set(review) != keys or review["schema"] != REVIEW_SCHEMA:
        raise ValueError("SEARCH_REVIEW_INVALID")
    if (
        review["receipt_sha256"] != receipt["artifact_sha256"]
        or review["reviewed_corpus_sha256"] != receipt["corpus"]["corpus_sha256"]
        or review["reviewed_block_count"] != receipt["corpus"]["block_count"]
    ):
        raise ValueError("SEARCH_REVIEW_SCOPE_MISMATCH")
    if review["decision"] not in REVIEW_DECISIONS:
        raise ValueError("SEARCH_REVIEW_INVALID")
    rationale = review["rationale"]
    if not isinstance(rationale, str) or not 20 <= len(rationale.strip()) <= 4000:
        raise ValueError("SEARCH_REVIEW_RATIONALE_REQUIRED")
    reviewer = review["reviewer"]
    if (
        not isinstance(reviewer, dict)
        or set(reviewer) != {"kind", "id"}
        or reviewer["kind"] not in REVIEWER_KINDS
        or not isinstance(reviewer["id"], str)
        or not reviewer["id"].strip()
        or len(reviewer["id"]) > 128
    ):
        raise ValueError("SEARCH_REVIEW_INVALID")
    try:
        reviewed_at = datetime.fromisoformat(str(review["reviewed_at"]).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("SEARCH_REVIEW_INVALID") from None
    if reviewed_at.tzinfo is None:
        raise ValueError("SEARCH_REVIEW_INVALID")
    hits = {
        source_id
        for entry in receipt["search_log"]
        for source_id in entry["literal_hit_source_ids"] + entry["term_hit_source_ids"]
    }
    dispositions = review["hit_dispositions"]
    if (
        not isinstance(dispositions, dict)
        or set(dispositions) != hits
        or any(not isinstance(v, str) or not v.strip() for v in dispositions.values())
    ):
        raise ValueError("SEARCH_REVIEW_HITS_UNADDRESSED")
    return dict(review)


def absence_candidate(receipt: dict, review: dict | None) -> str:
    """The only states this producer can hand a consumer: ``absent`` requires a
    complete receipt AND a validated ``absent_confirmed`` whole-corpus review."""
    verify_receipt_hash(receipt)
    if receipt["search_prerequisites_complete"] is not True or review is None:
        return "unknown"
    validate_review(receipt, review)
    return "absent" if review["decision"] == "absent_confirmed" else "unknown"
