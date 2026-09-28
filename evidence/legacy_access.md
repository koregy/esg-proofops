# Legacy Access Inventory — evidence/legacy_access.md

> Task: legacy source access and reuse inventory only, independent of TASK-000
> Repo: tskwak111/esg-evidence-audit @ 70da628a401b89b5ea2c08ee4243523ce99acb50
> Date: 2026-09-09
> Scope: bounded prep — verified SHA/license inventory; deeper tests deferred to TASK-043

## 1. Source Access Verdict

| Item | Result | Evidence |
|---|---|---|
| Exact commit readable | **yes — readable** | `gh api repos/tskwak111/esg-evidence-audit/commits/70da628...` returned 200; `git clone --no-checkout` + `git checkout 70da628a401b89b5ea2c08ee4243523ce99acb50` succeeded; `git rev-parse --verify` == requested SHA; `commit_meta.json` stored |
| Repo visibility | public, not private | `gh api repos/... --jq .private` == false; `.license == null` |
| DNS/clone restriction (docs/26 §1) | **not reproduced in this run** | clone succeeded from Orca runner; prior audit noted DNS-limited environment — now cleared, but recorded as environment-dependent |
| Product model / secrets / cloud access | **not used** | No Bedrock/AWS calls, no secrets read; verified via read-only GitHub API + local git |
| Commit metadata |  | `sha=70da628a401b89b5ea2c08ee4243523ce99acb50`, `tree=bde94913807aacdb9d2ce92100346fa373668c76`, `author=takawk111 <[owner-email-redacted]>`, `date=2026-08-12T15:57:17Z`, `message="Ignore generated package metadata"`, `parents=[529ee359456f4d8f5d3550a4a3fe00b89a718ad5]`, `verified=false (unsigned)` |

**Access method:** GitHub REST API (`gh api`) + `git clone https://github.com/tskwak111/esg-evidence-audit.git` with detached checkout. No write/push/branch/PR performed on legacy repo (per §5 of 26).

## 2. License Record

| Check | Result | Evidence |
|---|---|---|
| `LICENSE` / `LICENCE` file at tree `bde9491...` | **not found (404)** | `gh api .../contents/LICENSE?ref=70da628...` → 404; `ls /tmp/.../LICENSE*` → no match; root tree listing has no LICENSE entry |
| `pyproject.toml` license field | absent | `[project]` has no `license` key |
| `.license` via repo API | `null` | `gh api repos/... --jq .license` == null |
| `README.md` license mention | none (only `license_status` as data-field concept) | `grep -i license README.md` → only provenance/retention policy refs, no outbound license grant |
| Reuse gate | **No explicit repository license or reuse approval was found in the inspected metadata.** Preserve this as an internal reference and defer implementation copying until the applicable rights are recorded. | Cross-checked with 26 §5: "PDF 원본, provider raw payload... provenance·license·retention 정책과 함께 관리" — legacy tracks `license_status` per report, not repo license. |

Recorded snapshot: `legacy_reference/commit_meta.json`, `legacy_reference/COMMIT`, `legacy_reference/snapshot/pyproject.legacy.toml`.

## 3. Read-only Snapshot Placed

Location: `legacy_reference/` (gitignored via `.gitignore: legacy_reference/`). All files set `chmod a-w` (read-only).

```
legacy_reference/
  COMMIT                # HEAD SHA + oneline
  commit_meta.json      # gh API commit metadata (sha, tree, author, date, verified)
  snapshot/
    run_signature.py        # 7823 B — candidate hashing utilities
    graph_ensemble.py       # 5627 B — graph merge utilities
    document_graph.py       # 4701 B — GraphBlock/TableStructure/DocumentGraph
    parser_adapters.py      # 15036 B — PyMuPDF/pdfplumber adapters (bbox fallback path)
    README.legacy.md        # upstream README at commit
    pyproject.legacy.toml   # upstream pyproject at commit
```

SHA256 (snapshot, local):
- `document_graph.py` 3afc26ac8a845e19553c1cb471738dcd6d4e7e2ebd37bef2dc4178ce3b7d1bc7
- `graph_ensemble.py` 96b71e8b0b4a551d450c0d71dcc107c4875a8c2fa050377b0e809209a91985ac
- `parser_adapters.py` ccddd25fae512f6446de19656886a0d03d1d205f7275b0f6361eb92b42883f19
- `run_signature.py` 30c6edbac0531d6a30aae433219bda34cffef476bf164438b15a0a03bdd76985

Git blob SHA verification (matches `evidence/legacy_inspection.json` where present):
- `run_signature.py` blob `8bf94a7025daa4d0e807809e50bc7d986a08d55a` ✓
- `graph_ensemble.py` blob `3a4da7b46c18639a9381d912a00d8b13ecb9c15d` ✓
- `document_graph.py` blob `23882dea4a5787c5d7f9ba67c00fe380b6aa3774` ✓
- `parser_adapters.py` blob `67073c83de884e1ee243b69d3756302a9d3d6cdd` ✓

Snapshot is **read-only reference only**; no import/migration into `packages/` performed (TASK-000 boundary).

## 4. Candidate Hashing Utilities — Inspection (read-only)

File: `legacy_reference/snapshot/run_signature.py` (blob `8bf94a7...`, 7823 B)

| Aspect | Finding | Risk |
|---|---|---|
| `canonical_json_bytes` / `sha256_canonical` | Deterministic JSON: `sort_keys=True`, `separators=(",",":")`, `allow_nan=False`, UTF-8, StrictModel passthrough. Ordered vs unordered string sets explicitly separated (`sha256_ordered_strings` preserves order, `sha256_unordered_strings` sorts deduped). | **Reusable pattern** — meets docs/26 §2 recommendation to port pure hashing utilities first; preserves `tenant/replica/model/prompt` separation intent. |
| `_SECRET_KEYS` + `assert_secret_free` | Rejects `api_key`, `authorization`, `access_token`, `refresh_token`, `client_secret`, `password`, `*_api_key` anywhere in `provider_options`; `LLMRequestSignature.provider_options` validator calls it. | **Low risk, positive** — prevents secret persistence; keep. |
| `SourceSignature` | Fields: report_id, pdf_sha256, parser_name/version, parser_config_sha256, source_block_ids (dedup-checked), source_block_text_sha256, claim_clause_id, atomic_claim_text_sha256, claim_decomposer_version, topic_matcher_version/config_sha, context_builder_version/config_sha, evidence_bundle_sha256, source_input_sha256, document_reference_year. Missing: tenant, permission/consent, replicate_id, inference profile, extraction epoch (noted as gaps in 26 §2 row for this file). | **Important gap** — domain rule requires tenant/document_version/page/coord preservation and replicate_id in request id; port must add those per 26 §2 "필수 보완". Do not assume current hash equals ProofOps provenance. |
| `LLMRequestSignature` | Includes provider, model, endpoint_mode, api_contract_version, system/user prompt sha, source_input_sha256, temperature/top_p/max_tokens/seed, response_format, reasoning_effort, tool_schema_sha256, provider_options (secret-free). | **Important** — needs `replicate_id` addition to avoid cache-counting-as-3x self-consistency (26 R-05). |
| `PostprocessSignature` / `RunSignature` | Covers payload/element/taxonomy/threshold/crosswalk/provenance guard hashes + scorer_code_sha256 + dataset/row selection + dirty_worktree. | **Reusable** with addition of tenant/consent fields. |

**Inspection scope:** static read only; no execution, no model calls. Full replay/caching semantics and integration tests deferred to TASK-043 per coordinator.

## 5. Graph Utilities — Inspection (read-only)

### 5a. `document_graph.py` (blob `23882de...`)

- Models: `GraphBlock` (block_id, page_num≥1, block_type ∈ {sentence,table,caption,table_row,table_segment,figure,chart,unknown}, text, bbox 4-tuple, source_parser, source_block_id, metadata, table_structure), `TableStructure` (raw_rows/header/row/source_table_id/row_index/segment_index/column_headers), `BlockRelation` (relation_type ∈ {derived_from,parent_table,caption_of,same_page,nearby,section_parent}, source/target block_id), `DocumentGraph` (report_id, source_pdf, parser_runs[], blocks[], relations[]), `ParserRunMetadata`.
- No tenant, document_version, coordinate-system, raw/normalized text split, original hash, multi-source edge integrity at this commit — extensions required per 26 §2.
- `block_by_id` raises KeyError if missing — useful for edge validation.

### 5b. `graph_ensemble.py` (blob `3a4da7b...`, `merge_document_graphs`)

Critical/important risks reproduced from 26 §3 (static confirmation, no test harness):

- **R-01 Dangling edge (critical):** `seen_text_keys = (page_num, block_type, normalized_text)` dedup without bbox. On duplicate, code does `id_map[(graph_index, block.block_id)] = block.block_id` (maps to self, not retained canonical id). Later `merged_relations` remaps via `id_map.get(...)` — dropped block's relations still reference the dropped id, which no longer exists in `merged_blocks`. `DocumentGraph` has no edge-endpoint validation in inspected range. Matches 26 R-01 scenario (B1 dropped but B2→B1 remains).
- **R-02 Bbox-free text key (important):** Same `(page, type, normalized_text)` merges distinct regions (e.g., two "합계" cells on same page or different plant tables). No bbox/area overlap check. New merge must include document_version/page/area/type/table context and keep non-overlapping same-text blocks.
- **R-03 bbox fallback elsewhere:** Not in this file but in `parser_adapters.py: _graph_block_from_table_record` line `bbox = table_record.get("bbox") or (0.0,0.0,0.0,0.0)` and `_extract_pdfplumber_tables` uses `getattr(table,"bbox",(0,0,0,0))`. Zero-box is placeholder, not a real location — new contract requires `bbox=null, source_quality=unlocated` (26 R-03).

Other notes: `dedupe_text` default True, `sort_by_position` optional interleave by (page, y0, x0, derived_rank, original_index) with `derived_from` anchoring — conservative ensemble without semantic conflict resolution (as docstring states).

### 5c. `parser_adapters.py` (blob `67073c...`, 15036 B)

- `PyMuPDFParserAdapter`: wraps `esg_pipeline.parser.parse_pdf`, emits `GraphBlock` per `ParsedBlock`, optional `reconstruct_table_blocks` adds derived rows/segments with `derived_from` relation. Notes `parser_version=_package_version("pymupdf")`. License note: PyMuPDF is AGPL/commercial — 26 §2 flags "라이선스 승인 후 비교용".
- `PdfPlumberParserAdapter`: optional `table_extractor` injection for tests; production path imports `pdfplumber` lazily; `_extract_pdfplumber_tables` iterates `page.find_tables()` with `page_index` 1-based. License: pdfplumber MIT — lower friction for table cross-check.
- **Risk confirmed:** `_graph_block_from_table_record` bbox fallback to `(0,0,0,0)` (line 244) — must be replaced with null/unlocated per new contract.

## 6. Bounded-Scope Compliance

- No app code implemented or migrated before TASK-000 — only read-only snapshot under `legacy_reference/` and this file under `evidence/`. No edits to `packages/`, `contracts/`, `docs/`, `scripts/`.
- No commit/push/cloud change/product model/secret access.
- No rule changes, legal-rights approval, or performance claims.
- Validation: `python scripts/validate_package.py` is **not** an app test (per AGENTS.md); deeper parser/graph/hashing tests and re-grading belong to TASK-043 — marked `not_run` here.

## 7. Open Items / Next Steps (not_run in this task)

| Item | Status | Owner |
|---|---|---|
| Full file-by-file audit of `table_reconstruction.py`, `claim_decomposer.py`, etc. (26 §2 "2차 검토 후보") | not_run — bounded prep only | TASK-043 |
| Running existing tests / `uv run pytest` on legacy snapshot | not_run — would require installing legacy deps; deferred | TASK-043 |
| License grant clarification with repo owner | pending — no LICENSE at commit; do not assume rights | legal/owner |
| Clone/DNS re-check in restricted network | recorded as environment-dependent | infra |

## 8. Validation Evidence Summary

- GitHub API commit fetch: 200, SHA matches.
- `git clone` + `git rev-parse HEAD:…` blob SHAs match `evidence/legacy_inspection.json` for inspected files.
- `legacy_reference/` gitignored and read-only; `evidence/legacy_access.md` is sole writable output besides `legacy_reference/`.
- This dispatch reported writes only to its assigned snapshot and inventory. The shared checkout also contains concurrent bootstrap/coordinator changes; whole-workspace Git status cannot attribute those changes to this dispatch. Ignored `legacy_reference/` is absent from ordinary `git status --short`.
- Coordinator independently checked the requested commit ID, all four recorded SHA256 values, and read-only file permissions after accepted completion. These checks passed; behavioral characterization remains TASK-043.

---
Generated by bounded legacy-access worker. Deeper behavioral verification is out of scope for this dispatch.
