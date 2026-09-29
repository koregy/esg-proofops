# Full-document search-coverage receipt (R00 §12 common guard)

R00 §12 allows `unknown → absent` only after the whole document's search scope, readability and search log are complete. This contract is the producer for that prerequisite. It is not an absence verdict. A bounded claim-local retrieval packet (`retrieval.py` `search_coverage`) never proves absence. GAP-004 A is unchanged: numbers, years, baselines and progress found elsewhere are never attributed to the claim; they are only shown to the reviewer.

## Components
- `application/evidence/search_coverage.py`: pure receipt builder, review request and review validation, and `absence_candidate`. It does no IO and never sets an element state (`element_state_effect="none"`).
- `adapters/local/search_coverage_store.py`: loads a real run read-only and derives page facts internally from the original PDF bytes (pdfplumber words and images; no OCR or model). It writes immutable canonical JSON files with O_EXCL under `<root>/<sha256(tenant:run)[:32]>/{receipts,reviews}/<artifact_sha256>.json`.
  - Run loading: frozen snapshot through `load_run_inputs`, the native-replayed graph through `load_run_graph`, and the replayed claim through `LocalClaimStore.load_evidence`.
- `scripts/produce_search_coverage.py`: the `produce [--check]`, `replay`, `request`, `record-review` and `prerequisite` commands.

## Receipt `search_coverage_receipt_v1` (deterministic, no timestamps)
**Identity:**
- tenant, run, document_version, object_version, input_hash, original source sha256, parse_manifest, rulepack sha
- graph sha256
- claim_id, claim revision, claim quote sha, claim source ids
- element (G1–G8, P1–P6, M1–M6) and `element_primitives`, the element's full primitive mapping. P4 and P6 are refused with `SEARCH_ELEMENT_EXCLUDED`: deterministic producers own them.
- policy, which pins this module's bytes
- page-facts sha

**A page is `complete` only if all of these hold:**
- it is in the run's selected pages
- it has a native text layer
- it has no image XObject (any image may carry unread text)
- it has at least one parsed block
- every block's quality is `verified` (the existing native/visual receipt replay) with a selected source
- there is no quality issue on the page
- **word-level correspondence** (policy `per_native_word_index_bbox_exact_nfc_ordered_sequence_v1`):
  - each original text-layer word, recorded with its index, box and exact NFC text, has its box centre inside exactly one verified block box
  - each such block's NFC whitespace-split text equals the exact ordered sequence of the words attributed to it
  - two blocks with identical NFC text and identical box count as one source and are recorded in `deduplicated_source_ids`; any other overlap owning a word is refused
  - refusal reasons: `native_word_not_attributed_to_verified_block`, `native_word_attributed_to_overlapping_blocks`, `block_geometry_without_native_words`, `block_text_not_native_word_sequence`

This replaces the earlier character-multiset check. That check was unsound because duplicated or reordered text with the same character bag could mask an omitted phrase.

The same checks run over every physical page 1..N of the original PDF, not just the selected ones.

**Search:** the query list is explicit (1–32 unique non-empty queries, each with routing terms). Every block of the document is examined for every query; there is no top-k. The log records literal and term hits plus the examined-set hash.

**Result:** `search_prerequisites_complete` is true only when every page is complete and no block lies outside the registered pages.

## Absence path (explicit delegated review)
1. `request` is issued only for a complete, replay-valid receipt. It lists `asserted_primitives`: an `absent_confirmed` decision asserts every one of them absent; any disclosed or uncertain primitive means `not_absent` or `undetermined`. This matches the consumer's rule that items must list the element's full mapping. It carries the whole corpus manifest (every block id, page, kind, quality and text hash) and all hits. Zero hits is explicitly not evidence.
2. A `search_absence_review_v1` review must:
   - cite the receipt sha, the corpus sha and the block count
   - use one of the decisions `absent_confirmed`, `not_absent` or `undetermined`
   - give a 20–4000 character rationale
   - give a disposition for every hit
   - name a reviewer of kind `human` or `ai_delegated`
   - carry an aware timestamp

   It is stored immutably.
3. `absence_prerequisite(...)` returns `element_state_candidate="absent"` only when the receipt replays byte-identically from current state and the stored review re-validates as `absent_confirmed`. Every other case returns `unknown`, or raises `SEARCH_*`, which consumers must treat as unknown.

## Refusals (tested in `tests/unit/test_search_coverage.py`)
Each of the following keeps the result `unknown` or is refused:
- missing or unparsed page, partial run scope, image page, image-only page
- unverified or unselected block, open quality issue
- text in the native layer but not in blocks, and the reverse
- changed original PDF, stale input_hash, new claim revision
- cross-tenant copy, wrong claim/element
- tampered and re-hashed receipt, name/content hash mismatch, tampered review
- empty, duplicate, padded, term-less or oversized query lists; unknown element
- a review that does not cover the corpus, lacks a rationale, or leaves a hit undisposed

## Consumer handover (not done here)
`application/reviews.py`, `tag_store.py`, `claim_store.py` and `analysis_store.py` are owned by the P4 worker. After P4 settles, root integrates:
- Call `absence_prerequisite` before any `unknown → absent`.
- Record `receipt_sha256` and `review_sha256` in the new immutable revision.
- Never accept a client boolean.

No API/DB schema change or migration: the files live beside the run DB. Rollback is to stop calling the producer; existing revisions are untouched.

## Limits
- Real Windows runs are expected to stay incomplete: rendered readers are unavailable, so blocks are not verified, and images appear on most pages.
- Character-multiset correspondence is strict, so parser/text-layer spacing or ligature differences keep a page incomplete (fail-closed).
- Lexical routing may miss synonyms. That is why only a whole-corpus review can conclude absence.
