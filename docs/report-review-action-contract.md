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
