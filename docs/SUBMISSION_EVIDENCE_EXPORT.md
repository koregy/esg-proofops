# Submission evidence export

`scripts/export_submission_evidence.py` summarizes ONE local pilot state directory (`state.sqlite3`, `objects/`, `parser-prepared/`, `pilot.json`, `parser.json`, `settings.json`) for PPT or code review. It reads what the run store holds. It does not re-run, re-grade or re-export anything.

```sh
PYTHONUTF8=1 uv run python scripts/export_submission_evidence.py \
  --state .local/k10 \
  --out-dir .local/submission-20260929/evidence-export-k10 \
  --source-receipt .local/submission-20260929/kia-real/sources/sr-download-receipt.json \
  --label intermediate
```

## Output

The script writes `evidence-export.json`, `evidence-export.md` and `SHA256SUMS` into a new directory.

## Safety contract

- **Read-only DB access.** The state DB is opened with `mode=ro` and copied into memory with SQLite's backup API, retrying while a live writer commits. The product stores (`LocalSQLiteRunStore`, `Registry`, `UploadService` and the export store) are **not** used, because their constructors run DDL and their reads take `BEGIN IMMEDIATE`.
- **No DB writes.** The pilot's own inspection path also writes an auth session, so it is not used either.
- **No network, model, AWS or ledger access.** The budget ledger is never opened.
- **Output directory.** `--out-dir` must not exist and must be outside `--state`. An existing directory is refused, never overwritten.
- **Allowlisted reads only.** The script reads `job_records` for kinds `run`, `job`, `artifact`, `claim_head`, `tag_revision`, `decision_revision`, `review_head`, `preliminary_classification` and `usage`, plus `run_snapshots` and `rulepack_revisions`.
- **Never read:** cursor HMAC keys, upload or export tickets, sessions, cookies or CSRF tokens, API keys, `browser.json`, or tag-revision `inputs`.
- **Filtered config.** From `pilot.json`, only allowlisted keys are echoed. Ledger paths are dropped.

## What it distinguishes

| Topic | Rule |
|---|---|
| Coverage levels | Document pages, selected pages, parsed pages, claims extracted, claims source-verified (`source_quality == "verified"`), claims tagged, claims with a stored decision, and claims graded (`decided` with E0–E3) are separate numbers. A selected page is not a parsed page. |
| Unknown vs E0 | `grade_null_or_undecided` counts every claim without a decided E0–E3 grade. It is never folded into `E0`. Tag element states are reported as stored (`unknown`, `conflict`, `absent`, `present`). |
| Missing vs zero | If a stage has no verified checkpoint, claim-level counts are `null`, not `0`. `missing_inputs` names what is absent. |
| Stage failure / partial | Each stage is reported with its per-job status, attempt and `error_code`. A failed job is listed in `stage_failures`. The derived label is `complete_per_store` only when all of the following hold: run `completed`, `coverage.complete`, no failures, no missing inputs, and every extracted claim graded. Otherwise it is `partial_or_failed`. `--label intermediate` can override the label, but `derived_status` is always kept. |
| Artifact trust | Checkpoints are re-hashed against `artifact_ref.sha256` and `byte_size` is recorded. A mismatching checkpoint is not used for counts. |
| Live vs stored / synthetic | These fields are echoed as stored: `extraction_mode`, `extraction_profile.synthetic`, checkpoint `synthetic`, `tagging_mode`, binding `synthetic` and model ids, usage totals (`model_calls` vs `synthetic_calls`), and decision `local_synthetic`. The document `local_synthetic` flag and `local-synthetic:` object version describe local object **storage**, not model output. |
| AI-delegated vs human | The rule pack `approved_by` prefix `ai-delegated-review:` is classed as `ai_delegated`. Any other subject is classed as `recorded_non_ai_subject`; the store does not prove a person. The export also reports tag `origin` (`consensus` / `human` / `ai_delegated`), decision `review_status` (`human_confirmed` / `ai_delegated_confirmed`), and the preliminary classification origin. |
| Source | The export reports the stored sha256 and page count, and re-hashes the original object in `objects/original`. The URL comes only from a `--source-receipt` whose `sha256` matches the document; otherwise the URL is withheld. |
| Hashes | Run `revision` / `mutation_epoch`, rule pack, parser profile, model binding and snapshot hashes, every `*_hash` / `*_sha256` field in the frozen run snapshot, a canonical snapshot sha256, config file sha256s, and prepared parser file sha256s. |

`inspection-*.json` files written by the pilot are listed with the demo's `_coverage_from_inspection`. They cover the first claims page only and are **not** report totals.

## Tests

```sh
PYTHONUTF8=1 uv run pytest tests/unit/test_export_submission_evidence.py -q
```

The stores in these tests are built offline in `tmp_path`. The tests cover:

- a partial run: separate levels, null grades vs E0, and review origins;
- the complete label;
- a failed parse: null counts, a stage failure, and 134 selected pages not treated as complete;
- a tampered checkpoint;
- a receipt mismatch that withholds the URL, and a non-AI approver not being called human;
- refusal of an existing output directory or a missing state.

The tests also check that the state DB bytes are unchanged and that no secret or ledger path leaks into the output.
