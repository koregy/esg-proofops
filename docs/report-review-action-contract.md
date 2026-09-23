# Report review-action contract

Status: additive `review_action` projection in `report_model_v1` implemented locally
in `packages/proofops/application/reporting.py`, surfaced in JSON/CSV/HTML renderers.
No database migration or report route change.
ReportPreview also renders the optional field; older reports without it remain readable.
Verified with synthetic tests and fresh API exports of three stored real-model runs.
This formatting change requires no new model calls; AWS validation remains **not_run**.

## 1. Problem this closes

The frozen walkthrough exports (2026-09-22) show all 45 exported claim rows across the
naver / lotte / kb partial exports with `suggestion = null`. `suggestion` intentionally
fires only for **verified-missing** elements (`missing_elements`), so blocked, unresolved,
untagged, source-pending, assurance-not-run and domain-gap claims give the reviewer no
"what to check next" guidance even though the underlying report already records those
states. This is a review-usefulness gap, not a grading gap.

## 2. Contract

`build_report_model` now attaches `review_action` to every claim in the model:

- `review_action = null` when a claim has no outstanding review reason
  (a decided claim with sources, no missing/unresolved elements, no gap ids, no
  unverified basis, and assurance/safe_harbor that are not `not_run`).
- Otherwise a structured object:
  ```
  {
    "claim_id":            <uuid, same as claim>,
    "reasons":             [ordered reason codes],
    "checks":              [human-readable next steps, aligned to reasons],
    "unresolved_elements": [element ids, from the decision or pinned unfinished tag],
    "gap_ids":             [gap ids, copied from the decision],
    "source_pages":        [page numbers of the claim's existing source_refs]
  }
  ```

Reason codes (each distinct from verified-missing `suggestion`):

| code | trigger | meaning |
|---|---|---|
| `not_processed` | `decision_status == not_run` | decision not run; inspect source/classification/tagging and continue the unfinished stage |
| `unresolved_evidence` | `unresolved_elements` non-empty | confirm source attribution; do **not** call absent |
| `source_location_missing` | `source_status == not_run` and status ≠ not_run | secure evidence page/coords first |
| `basis_validation_pending` | any basis ref lacks a clause or is not `verified` | validate clause/standard mapping |
| `assurance_not_run` | `assurance.status == not_run` | run assurance cross-check |
| `safe_harbor_not_run` | `safe_harbor.status == not_run` | run safe-harbor checklist |
| `domain_gap` | `gap_ids` non-empty | review unresolved rule items and their effect; gap presence alone does not prove this decision is blocked |

### Invariants preserved

- `suggestion` is unchanged: still names only `missing_elements`, still `null` otherwise.
  `review_action` is additive and never overwrites or reinterprets `suggestion`.
- `review_action` never invents a grade, number, target year, or legal effect. Its
  `checks` strings are fixed review instructions plus copied element/gap identifiers.
- `unknown` / `unresolved` / `unreadable` are never converted to absent. The
  `unresolved_evidence` check explicitly warns against calling an element absent.
- Source and claim links are retained: `claim_id` and `source_pages` are copied from
  the already-validated claim; full `source_refs` remain on the claim untouched.
- Grades stay `null` wherever the decision was `null`. Read-only replay over the three
  immutable exports keeps `confirmed_grades = 0` before and after.

## 3. Old-export immutability and rollback

- `review_action` is computed at projection time from fields already stored in each
  immutable revision/manifest. It is **not** persisted into any snapshot, artifact,
  tag/decision snapshot or export ticket. It is included in newly rendered immutable
  export artifacts. `LocalExportStore` still writes the same immutable
  `export_snapshot` / `export_artifact` records under the same triggers; no schema
  version bump and no migration are required.
- Already-frozen ZIPs (`export_artifact`) are byte-for-byte immutable and are **not**
  rewritten. New verification must use a fresh idempotency key to mint a new export;
  the past ZIPs stay as-is.
- Rollback: reverting `reporting.py` removes `review_action` from newly rendered
  reports and the new CSV column with zero data-migration and zero effect on any
  previously stored artifact, tag revision, decision revision, or audit record.

## 4. Verification

- `tests/acceptance/test_report.py`: new `test_review_action_guides_unresolved_work_
  without_replacing_suggestion` and `test_review_action_is_null_only_when_no_
  outstanding_review_remains`; renderer test extended for the CSV column and HTML block.
- Read-only replay `outputs/agent-results/R24/replay_review_action.py` over the frozen
  naver/lotte/kb exports: 45/45 rows `suggestion`-null before, 45/45 `review_action`-
  populated after, `confirmed_grades = 0` unchanged, N1/B1 grades stay `null`.
- Fresh API export/download of the same three actual run histories, with new
  idempotency keys: HTTP 200, JSON/CSV/HTML ZIPs contain 45/45 follow-up actions,
  45 null suggestions and zero grades. Prior revisions and frozen ZIPs remain
  unchanged. Evidence: ROOT `outputs/agent-results/R24/exports/validation.json`.
- `ruff` and `mypy` clean on the changed modules. No new model calls for this
  report-format verification; AWS checks are **not_run**.

## 5. Unfinished tagged claims (R34)

When `decision_revision == 0`, a new local export snapshot also copies element IDs
whose pinned tag state is `unknown` or `conflict` into `unresolved_elements`.
The report retains these IDs and existing `unresolved_evidence` guidance. This
is review work, not a decision: grade/label stay null and `missing_elements` stays
empty. Untagged claims and older snapshots without this optional list retain the
empty-list default. No API route, DTO, database table, or migration changes.
Rollback reverts the capture/projection additions; prior frozen ZIP bytes remain
unchanged. Verified by the unfinished-export regression and immutable-export tests.

## 6. Explicit claim quote (R34)

New local export snapshots copy the frozen discovered claim's exact `quote` into
optional `claim_quote`. The report projects it unchanged into JSON/CSV and labels
it separately from evidence excerpts in HTML. It is not a verified assertion,
source approval, rewritten quote, or new grade. Old records default to null; HTML
states the quote was not stored rather than guessing it from a source paragraph.
Non-null values must be non-empty strings. HTML escaping and CSV formula guards
apply. This is additive report/snapshot metadata: no route/DTO/table migration.
Readers that ignore extra keys remain compatible. Reverting capture/projection
removes the field from future reports; existing snapshots/ZIPs are never rewritten.

### R34 원문 읽기 경고 (read projection)

`GET /v1/runs/{run_id}/quality` retains the existing `QualityIssuePage` shape and
adds `image_text_not_extracted` (open) for a figure whose parser candidates all
contain empty/whitespace text. This is a deterministic read-time warning, keyed
by source UUID and warning version; it is not a mutation of the immutable parser
quality artifact, coverage counters, decisions, or exports. It does not assert
that a photograph contains text, that OCR ran, or that evidence is absent.
Old clients may display the existing free-text `kind`/`reason`; no DB migration is
needed. Rollback removes the projection and UI only; pinned runs remain valid.
The execution screen paginates these warnings and uses the existing authorized
source/ticket/preview endpoints. Empty/error responses never establish complete
extraction. Source conflicts or invalid geometry can still prevent a preview.

Published extraction snapshots additionally project `extraction_span_unprocessed`
for `unknown` exclusions whose reason is `unprocessed_span`, one warning per
source with a maximum 120-character excerpt. The existing `LocalClaimStore`
verifies and replays the immutable snapshot before projection; corrupted
published data returns 409 rather than an empty success. Parse-only runs retain
their parser warnings. These spans are neither confirmed claims nor confirmed
non-claims. This additive read projection does not change saved extraction,
source verification, grades, or previous exports; the same rollback applies.

### R34 선행분류 출처 보존

New immutable export snapshots may carry optional `classification_review` with
`classification_id`, `record_sha256`, `revision`, `origin`, and `track` copied
from the tenant/run/claim's content-addressed immutable preliminary classification
record. The mutable head must match that record and its source identity pins.
`human_classification` and `ai_delegated_classification` remain distinct in
JSON/CSV/HTML and the preview. This records the classification, not approval of
a grade or proof that the subsequent tagging job finished. It does not overwrite
later tag/decision fields. Older snapshots project null. CSV appends the field;
existing column positions and stored ZIP bytes remain unchanged. No DB migration;
rollback stops writing the optional projection and retains historical exports.

### R34 새 실행의 값 인용 안내

The local pilot's element prompt now explicitly demonstrates that a non-null
normalized value needs an identical literal evidence quote, not only its enclosing
sentence. The existing v3 quote transport and all source/binding/grade guards stay
unchanged. New runs pin the new prompt hash; resume and classification reprocessing
use the stored run settings. Historical responses and tag revisions are not reinterpreted.
