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

The same new-run prompt also distinguishes a claimed goal deadline (G1) from
a designation/registration/publication/reporting year, even when the upstream
track is goal. Missing goal-deadline support remains unknown. This clarifies
the existing G1 definition; it neither adds a domain rule nor repairs historical
classification records. A two-sentence, three-replicate same-Kakao wire comparison
kept the explicit 2040 deadline and rejected the 2025 designation year in all
three responses each; this is a bounded prompt result, not corpus accuracy.

### R34 opt-in preliminary goal-role prompt

New local pilot runs may set `--preliminary-goal-role` together with
`--preliminary-table-role` and its dependencies. The new
`upstage-preliminary-source-quotes-goal-role-v1` profile reuses the existing
`TABLE_SCHEMA` wire shape and the unchanged table validator; it differs from
the table-role profile only in its pinned prompt (the table-role prompt plus
one additive goal-role suffix, rendered before the existing Output JSON
schema). It adds no wire field, no new schema, and no new grade rule. The
default remains false, so existing prompt hashes, receipts, and replays are
unchanged; enabled runs pin a distinct prompt hash and a distinct transport
version for replay separation. Resume restores the stored flag and rejects
adding it to a legacy run. Profile/prompt pairs are pinned so neither the
table-role prompt nor any older prompt can be sent under the goal-role profile
and vice versa. Rollback disables the option for new runs and retains pinned
settings and immutable artifacts for existing runs. A bounded same-source
probe extracted the named company-target indicator in 3/3 goal cases and kept
3/3 ongoing-management cases, but still misread external risk in 2/3 cases, so
this change makes no accuracy or fix claim.

### R34 opt-in preliminary actor-role prompt

New local pilot runs may set `--preliminary-actor-role` together with
`--preliminary-goal-role` and its dependencies. The new
`upstage-preliminary-source-quotes-actor-role-v1` profile reuses the existing
`TABLE_SCHEMA` wire shape and the unchanged legacy validator; it differs from
the goal-role profile only in its pinned prompt (the goal-role prompt plus
one additive actor-role suffix, rendered before the existing Output JSON
schema). It adds no wire field, no new schema, and no new grade rule. The
default remains false, so existing prompt hashes, receipts, and replays are
unchanged; enabled runs pin a distinct prompt hash and a distinct transport
version for replay separation. Resume restores the stored flag and rejects
adding it to a legacy run. Profile/prompt pairs are pinned so neither the
goal-role prompt nor any older prompt can be sent under the actor-role profile
and vice versa. Rollback disables the option for new runs and retains pinned
settings and immutable artifacts for existing runs.

## 7. Optional tag_elements snapshot (R34-day1 export usability)

Status: additive optional `tag_elements` projection in `report_model_v1`,
captured in new local export snapshots only. No request-field, route, DTO,
database table, or migration change. Schema stays `report_model_v1`.

### Problem this closes

Claim UI shows accepted tag values and per-element source buttons, but report
export only records unresolved IDs (`missing_elements` / `unresolved_elements`).
Accepted values (`normalized_value`), states, and exact evidence quotes are not
in the report model, so JSON/CSV/HTML and `ReportPreview` cannot show what the
reviewer already accepted.

### Contract

- New `LocalExportStore.capture` copies the pinned immutable current tag
  revision's `elements` into optional `decisions[claim_id].tag_elements`:
  each entry keeps `element_id`, `state`, `normalized_value`, and full
  `evidence_refs` (exact quotes). No grade/label inference, no recalculation,
  no AI/human provenance change.
- `build_report_model` projects `tag_elements` per claim with existing
  validators only (`domain _element_from_dict` shape plus reporting
  `_source_refs` manifest pin). `report_model_v1` is unchanged; the field is
  optional and documented here.
- Semantics:
  - missing key (old snapshots, wholly-unavailable records) -> `null` =
    unavailable, never an empty evaluated list; HTML says the snapshot did not
    carry tag elements, CSV renders `null`.
  - `tag_revision == 0` new snapshots store `[]` = no tagged elements
    (untagged); tagged claims store the validated non-empty list.
  - duplicate `element_id`, malformed element, foreign
    `document_version_id`/`parse_manifest_id`, source-less `present`, or any
    `present` evidence ref whose `verification_state != verified` fails closed
    (`ValueError` in projection, `EXPORT_INTEGRITY_FAILED` in capture) and is
    never displayed as valid present.
  - `state` is preserved even when `normalized_value` is null.
  - all existing unknown/conflict/absent semantics and source verification are
    untouched.
- Renderers: JSON keeps the validated list/null; CSV appends exactly one
  `tag_elements` column at END (old order retained, canonical JSON cell,
  formula-guarded); HTML adds an escaped per-element block (value/state/exact
  quote, bbox/page when present). `ReportPreview` renders the same three
  states (unavailable / no tagged elements / element list) with React escaping.
- No mandatory request fields are introduced.

### Old-export immutability and rollback

- `tag_elements` lives only inside newly captured frozen `export_snapshot`
  JSON (`decisions` map). No DB schema migration is expected because the
  export snapshot is a frozen JSON blob validated by `build_report_model`,
  not a relational table: old blobs lacking the key still validate to `null`,
  new code reads old blobs, old code ignores the extra key. No backfill is
  run because rewriting history would break immutability; verification mints
  a fresh export with a new idempotency key. Old exported ZIPs/revisions are
  never rewritten.
- Rollback: reverting `reporting.py` + `export_store.py` + `ReportPreview.tsx`
  removes the capture/projection/column with zero migration and zero effect on
  stored artifacts, tag/decision revisions, or audit records.

### Verification

- Focused fail-first regression in `tests/acceptance/test_report.py` and
  `tests/acceptance/test_exports.py`: immutable new element snapshot
  (values/states/exact quotes round-trip), old snapshot null, tamper/foreign
  evidence fail-closed, escaped HTML/CSV.
- Existing export/review lineage and report tests as applicable plus
  `ruff`/`mypy`/web build for touched files. Full 3600-test suite is not run;
  full PDF/UI/ZIP export is coordinator responsibility after code review.

### R34 opt-in paragraph selection

New local pilot runs may set `--extraction-complete-selection` together with
`--extraction-source-ids` and `--extraction-assertion-prompt`. The extraction
prompt asks for each independently reviewable sentence in a paragraph, preserving
source-ID quote restoration and all existing evidence admission guards. The
default remains false; existing prompt/rule hashes and receipts remain unchanged.
New enabled runs pin distinct prompt/rule hashes. Resume restores the stored flag
and rejects adding it to a legacy run. Invalid non-boolean or dependent settings
are rejected. No API response or DB schema changes; no migration is needed.
Rollback disables the option for new runs and retains pinned settings and
immutable artifacts for existing runs. Complete selection does not establish
complete recall, claim correctness, or approval of any final grade.

### Actor-role-v2: target deadline is not reporting period

New runs using the existing `--preliminary-actor-role` option select
`upstage-preliminary-source-quotes-actor-role-v2` /
`preliminary-source-quotes-actor-role-v2`. The prompt appends the evaluated
528-byte period-role clarification (SHA256
`b6bde180178474df6e8939816563730f759c91d7f00087467c7f5fc9621a141a`).
Future target deadlines remain available for G1; they must not be classified as
observed reporting periods. No period syntax, binding, source, consensus or
grade rule changes. Unknown periods stay null. TABLE_SCHEMA and source indices
are unchanged. Worker packet hashes, transport construction and preflight all
pin the same v2 prompt.

Saved settings retain their exact v1 or earlier profile/prompt on resume. There
is no data/API migration or rewrite of old revisions. Rollback for new runs is
to select the v1 profile/prompt again while retaining v2 readers for stored
receipts. Trial evidence covers selected Samsung/Kakao cases, not general
accuracy; the historical methodology footnote remains classification-uncertain.


### Source quote transport v4: bounded G1 literal year

New local pilot settings use `upstage-compact-source-quotes-v4`; existing manifests
retain their pinned v1/v2/v3 settings. The v3 model wire instructions remain unchanged;
the receipt transport version is `compact-source-quotes-v4`. Only a present G1 value
of four ASCII digits followed by 년 may gain one exact subspan reference inside a
model-selected citation. Original qualifiers, references, source identity, verification,
provider response and other elements remain intact. Repeated years, different source
candidates, longer digit strings and nonliteral values do not gain references. This
provides a location, not semantic approval: ordinary source, binding and consensus
guards still apply. Rollback selects v3 for new runs; persisted runs are never rewritten.
