# Manual Preliminary Classification → Reprocess — Contract (R22)

Status: implemented locally; one real Naver claim reprocessed and read back through the API on 2026-09-22.
Scope: the smallest end-to-end route that lets an authorized reviewer classify a
**source-verified** claim that the tag stage stopped at `PRELIMINARY_TAGS_UNRESOLVED`,
and eventually reach the **existing element review queue + rule-engine grade** —
without fabricating editable `ReviewInputs`, fake consensus, or a human grade.
Coordinator technical decision recorded 2026-09-22 (Orca ask, dispatch
`ctx_c5a901103cff`). This document records the implemented additive contract. Real acceptance details are
in `docs/32_PIPELINE_COMPLETION_PLAN.md` R22.

Authority: `AGENTS.md` (LLM tags/extracts only; pure Python rules grade; humans edit
tagging not grade; unknown/conflict/unreadable preserved; tenant/version/page/replica/
hash preserved; immutable prior revisions). Domain GAP-002 (preliminary track) stays
open. This route adds a human/AI-delegated **classification** input; it never adds a
new grade/label path.

## 1. Problem, traced to code

`apps/worker/src/proofops_worker/tag_runner.py` `LocalTagRunner._execute` per-claim loop:
when `preliminary_supplier(claim, graph)` returns `track is None`, it sets
`reason = "PRELIMINARY_TAGS_UNRESOLVED"`, appends the blocked item and `continue`s
**before** `tag_replicates` / `form_consensus` / `publish_transaction`. The claim is
source-verified (`claim.source_quality == "verified"` and `_source_traceable`), so the
block is a classification gap, not a source gap.

Consequences (confirmed against R21 Naver run `d379689e…`, `outputs/agent-results/
R21-review-gap.json`: 10 claims, 9 preliminary-blocked, 1 source-blocked, 0
`review_inputs`, 0 `review_head`):

- `LocalTagStore.load_inputs` raises `KeyError("tagged claim not published")` for any
  claim without stored `review_inputs`, and otherwise **recomputes** `form_consensus`
  from real `tag_runs` and re-validates `replicate_hashes` (`tag_store.py:224-296`).
- `ReviewService.resolve_review` / `resolve_ai_delegated_review` require an existing
  `review_head` row **and** loadable, self-consistent `ReviewInputs`
  (`packages/proofops/application/reviews.py`).
- Therefore a preliminary-blocked claim has no review to open, and a manual
  classification **cannot** be expressed as a review resolution without inventing tag
  receipts/consensus — which is forbidden.

So manual classification must be a **separate immutable input layer** that seeds a
**bounded reprocess** of the real tagging stage. It is not a review-resolve, not a
tag-recovery re-send, and not a synthetic decision.

## 2. Two layers (both additive)

### Layer 1 — `preliminary_classification` record (immutable)

A durable authorization for exactly one claim's manual preliminary classification.
Persisted as a new `job_records` kind with immutable INSERT/UPDATE/DELETE triggers
mirroring `review_store` schema v1 (`LocalSQLiteReviewStore.__init__`); rollback stops
the writer/route and keeps records (no down-migration).

Holds only (never a grade/label):

- `track` ∈ {`goal`,`performance`,`management`} — the reviewer's classification.
- `safe_harbor_category` ∈ {`null`,`forward_looking`,`emissions_estimate`,
  `third_party_information`} — explicit; `null` stays `null` (unknown), never
  silently reset.
- `dimensions`: explicit reviewed source spans for `entity`/`metric`/
  `reporting_period` (+ any applicable optional axis). Each non-null span is
  validated through the **same** `_literal_dimension_ref` + `verify_source_ref`
  guards used by `validate_preliminary` (index into the claim's own numbered
  sources; verbatim unique quote; restored offsets; `verification_state ==
  "verified"` required). A source-unverified span is **rejected**. Any axis the
  reviewer does not resolve stays `null` (unknown), never invented; a `conflict`
  the reviewer does not resolve is not silently overwritten — an explicit reviewed
  value is required to change it.
- Provenance, distinct by construction:
  - **human**: HTTP route, `reviewer` role, session cookie + CSRF + exact `If-Match`
    + `Idempotency-Key`. `origin = "human_classification"`,
    `classified_by = actor.user_sub`.
  - **ai_delegated**: trusted local method only (reuse the
    `scripts/review_ai_delegated.py` wiring pattern: constructor-supplied
    `delegated_reviewer` / `delegation_authority`, never parsed from request JSON).
    `origin = "ai_delegated_classification"`,
    `classified_by = "ai-delegated-classification:<operator>"`,
    `review_origin = "ai_project_interpretation"`, verbatim `delegation_authority`.
  - Never labeled as a model 3-vote agreement and never labeled human when
    machine-driven.

Lineage pins (all recomputed, none trusted from the request): `tenant_id`,
`run_id`, `document_version_id`, `claim_id`, `parse_manifest_id`, `source_sha256`,
`graph_sha256`, `claim_sha256`, and the run's committed `tag_snapshot_sha256`
(lineage checkpoint). `revision = 1`; the record id is content-addressed on the
canonical classification snapshot (uuid5, like `ReviewService._review`).

The prior blocked artifacts are preserved alongside, untouched: the tag checkpoint's
`preliminary_records` / `preliminary_agreement` for this claim remain readable and are
never rewritten. The new record is a distinct `reviewed_classification`.

Stored non-null dimensions retain the exact source index, offsets and quote against
the pinned claim/graph. Replay revalidates these selections; GET returns full
verified SourceRefs, including the quote.

### Layer 2 — bounded reprocess tag job

A separate, explicitly authorized tag job (structurally reuses `tag_recovery.py`:
authorize → enqueue a new `tag` job with a **new** `job_id`, a bounded
`max_new_requests`, carry every non-targeted claim forward verbatim, all old
checkpoints/receipts/reports immutable). It differs from recovery in one way and must
not broaden recovery: `tag_recovery.eligible_claims` re-issues only **provably
never-sent** requests; this job instead supplies the reviewed classification as the
**preliminary supplier override** for exactly the named claim(s), then runs the
**real element-tagging stage** — actual 3 replicas → real `form_consensus` → real
`ReviewInputs` → `publish_transaction` → existing element review queue + rule-engine
grade. Element tagging is never replaced by the single classification.

Coordinator decisions encoded here:

- (a) **One** explicitly authorized manual classification is sufficient to seed the
  frozen preliminary packet for the reprocess; the **element** stage still requires
  the real 3 replicas. The single classification is never presented as a 3-vote
  agreement.
- (b) POST only **durably enqueues** the bounded job; **no synchronous model calls**.
  Existing worker budget/lease/stop guards apply unchanged (`can_call`, fenced usage,
  `_TagFenceLost`). The worker must not call paid providers on its own; a coordinator
  may later run one actual case under the shared USD 20 ledger.
- (c) The reprocess produces a **new immutable job checkpoint** and, for a claim that
  was never tagged, publishes **`tag_revision = 1` only** (via existing
  `form_consensus(..., tag_revision=1)` + `publish_transaction`). It must **not**
  fabricate a `revision 2` for a never-tagged claim, and it must **reject** any claim
  that already holds a `claim_head`/published review. The blocked checkpoint is not
  mutated.
- Exactly **one selected claim per job** initially (`claim_limit == 1`).
- Reject any outstanding tag job / recovery for the run, and any stale lineage
  (`tag_snapshot_sha256` / `tag_job.job_id` drift), inside the writer transaction, and
  re-check at fenced publication (same discipline as
  `tag_recovery.authorize_recovery` `_open_authorizations` + `TAG_RECOVERY_RUN_CHANGED`
  + the `publish` outbox fence).

## 3. HTTP contract — `/v1/runs/{run_id}/claims/{claim_id}/classification`

Guards (both verbs): tenant isolation (foreign tenant → `404`), viewer/reviewer roles
as noted, rate limit `120/min/user` (GET) and `10/min/user` (POST), `Cache-Control:
no-store`. All error bodies use the existing `{code, message}` envelope
(`_error_response`).

### 3.1 `GET` — eligibility + numbered sources + current state

Role: `viewer` minimum. Read-only projection; never enqueues.

Response `200` `ClassificationView` (field names fixed for the UI's existing
`requestJson` helper; the concurrency token is returned **both** as an `etag` body
field and the `ETag` header so the UI reuses it without a refactor):

```json
{
  "schema_version": 1,
  "run_id": "uuid",
  "claim_id": "uuid",
  "eligible": true,
  "ineligible_reason": null,
  "blocked_reason": "PRELIMINARY_TAGS_UNRESOLVED",
  "etag": "\"<64-character lineage_state_token>\"",
  "sources": [
    { "source_index": 0, "quote": "…", "source_ref": { "...": "full SourceRef including quote" } }
  ],
  "dimension_axes": ["entity", "metric", "reporting_period", "…applicable optional axes"],
  "preliminary_agreement": { "fields": {}, "dimensions": {} },
  "current_classification": null,
  "pending_job": null,
  "allowed_tracks": ["goal", "performance", "management"],
  "allowed_safe_harbor_categories": [null, "forward_looking", "emissions_estimate", "third_party_information"]
}
```

- `eligible` is `true` only when the claim is source-verified, currently
  `PRELIMINARY_TAGS_UNRESOLVED`, holds **no** `claim_head`/published review, and the
  run has **no** outstanding tag/recovery job. Otherwise `eligible=false` with a
  specific `ineligible_reason` (`ALREADY_TAGGED`, `NOT_PRELIMINARY_BLOCKED`,
  `SOURCE_UNVERIFIED`, `RUN_JOB_OUTSTANDING`, `LINEAGE_UNAVAILABLE`).
- `etag` (body) equals the `ETag` header value; the UI echoes it verbatim as the POST
  `If-Match`. The token is derived from the run's `tag_snapshot_sha256` **and** the
  current classification revision state, so POST fails closed if either the underlying
  blocked stage or a prior classification changed after the GET. When `eligible=false`
  for `ALREADY_TAGGED`, `etag` is `null` (nothing to condition a new classification on).
- `sources` are the claim's own numbered atomic sources exactly as
  `preliminary_request(...)` numbers them (index 0..n-1), so the UI can offer the same
  index space the validator will accept. Context blocks are **not** quotable and are
  not included as selectable sources.
- `dimension_axes` is the ordered axis list the reviewer may fill (always at least
  `entity`/`metric`/`reporting_period`, plus any applicable optional axis for the
  claim). The UI starts every axis at explicit unknown (`null`) and starts
  track/category with **no default**; a dimension becomes non-null only when the
  reviewer enters a quoted ref.
- `current_classification` is the latest immutable `preliminary_classification` record for this
  claim (or `null`). `pending_job` is `null` or `{job_id, status}` for an enqueued
  reprocess job (`"pending"` / `"leased"`). Existing conflicts from the prior blocked
  checkpoint remain visible in `preliminary_agreement` for deliberate review and are
  never auto-overwritten.

### 3.2 `POST` — record classification + enqueue bounded reprocess

Role: `reviewer`. Requires session cookie + CSRF, exact `If-Match: "<lineage_state_
token>"` (from GET), and `Idempotency-Key` (16–128 chars). HTTP origin is fixed
`human_classification`; the request body can never self-assert provenance.

Request body (exact key set; any extra key rejected):

```json
{
  "track": "performance",
  "safe_harbor_category": null,
  "dimensions": {
    "entity": { "source_index": 0, "quote": "…" },
    "metric": { "source_index": 0, "quote": "…" },
    "reporting_period": null
  },
  "reason": "≥5 and ≤1000 chars explaining the classification"
}
```

- `track` required, ∈ allowed tracks. `safe_harbor_category` required key, value
  `null` or an allowed category. `dimensions` must include `entity`, `metric`,
  `reporting_period` keys (value `null` or a `{source_index, quote}` /
  `{source_index, start, end, quote}` span, validated exactly as
  `_literal_dimension_ref` with `allow_offsets=True`). Optional axes use the existing
  `ClaimContext` vocabulary; unknown axis keys rejected. `reason` 5–1000 chars. The
  body carries **no** `confidence`, `origin`, or `grade`; a client can never assert
  those.
- **No fabricated model confidence.** A manual classification is validated with
  `validate_track_candidates` (track ∈ TRACKS, no topic inference) plus the literal
  span validation above — or an explicitly separate manual validation helper — and is
  recorded with `track_confidence = null` (it is a reviewer judgment, never a model
  `confidence = 1` or a 3-vote agreement). Relation tags, when needed downstream, come
  from context exactly as today, not from the classification.

Behavior (single writer transaction):

1. Load the target claim + committed graph + lineage; recompute all pins. Reject on
   stale lineage (`If-Match` mismatch or `tag_snapshot_sha256` drift) → `412
   STALE_CLASSIFICATION`.
2. Re-check eligibility (source-verified, still `PRELIMINARY_TAGS_UNRESOLVED`, no
   `claim_head`/review, no outstanding tag/recovery job) → `409` with the matching
   `ineligible_reason` code otherwise.
3. Validate `track` / `safe_harbor_category` / each `dimensions` span through the
   shared preliminary guards; a source-unverified or ambiguous/absent quote →
   `422 CLASSIFICATION_SOURCE_REJECTED`; `null` axes stay unknown.
4. Write the immutable `preliminary_classification` record (`revision = 1`,
   `origin = "human_classification"`, content-addressed id).
5. Enqueue the bounded reprocess tag job (`claim_limit = 1`, new `job_id`, bounded
   `max_new_requests`, lineage-pinned) in the same transaction; reject a duplicate
   in-flight authorization for the same lineage/claim (`TAG_REPROCESS_ALREADY_PENDING`).
   Idempotency-Key replays return the same record + job without a second enqueue.

Response `202` `ClassificationAccepted`:

```json
{
  "schema_version": 1,
  "classification": {
    "classification_id": "uuid",
    "claim_id": "uuid",
    "run_id": "uuid",
    "track": "performance",
    "safe_harbor_category": null,
    "revision": 1,
    "origin": "human_classification",
    "classified_by": "user-sub"
  },
  "reprocess_job": { "job_id": "uuid", "status": "pending", "claim_ids": ["uuid"] }
}
```

Headers: `ETag: "1"` (classification revision), `Cache-Control: no-store`.

Error codes: `412 STALE_CLASSIFICATION`; `409 ALREADY_TAGGED` /
`RUN_JOB_OUTSTANDING` / `TAG_REPROCESS_ALREADY_PENDING`; `422 VALIDATION_ERROR` /
`CLASSIFICATION_SOURCE_REJECTED`; `403 FORBIDDEN` / `CSRF_INVALID`; `400
IF_MATCH_REQUIRED` / `IDEMPOTENCY_KEY_INVALID`; `404 RESOURCE_NOT_FOUND`.

### 3.3 Trusted local AI-delegated method (no HTTP)

`scripts/classify_ai_delegated.py` validates a dry run by default; `--apply` requires
an explicit `--if-match`, `--delegated-reviewer`, and `--delegation-authority`. No
default delegation is invented. It records the same
immutable record with `origin = "ai_delegated_classification"`,
`classified_by = "ai-delegated-classification:<operator>"`,
`review_origin = "ai_project_interpretation"`, verbatim `delegation_authority`. It
performs the identical source/dimension validation and the identical bounded-job
enqueue, and is **never** reachable over HTTP. It is not accepted as a human gold
classification.

## 4. Persistence, migration, rollback

- `packages/proofops/adapters/local/classification_reprocess.py` owns SQLite/file
  authorization; the application validator has no worker dependency.
- New `job_records` kinds only: `preliminary_classification` (immutable),
  `preliminary_classification_head` (latest pointer), plus the reprocess job records
  reusing the existing `tag`/authorization kinds. Immutable triggers as in
  `review_store` schema v1; re-init idempotent.
- Additive schema marker; existing readers ignore the new kinds. No column changes to
  existing tables. The extraction/claim/tag checkpoints and any `review_inputs` /
  `claim_head` are untouched.
- Rollback: stop the route + writer + the reprocess authorizer; retain all records;
  no destructive down-migration. Newer clients treat absence of the classification
  endpoint as "manual classification unavailable".
- Reprocess output is published via the existing `publish_transaction` path, so the
  resulting element review + decision are ordinary immutable revisions
  (`tag_revision = 1`) — no new grade path.

## 5. Reusable code (real, in this checkout)

- `packages/proofops/application/tagging/preliminary.py`: `preliminary_request`
  (numbered sources), `_literal_dimension_ref`, `_sources`, `validate_preliminary`,
  `PreliminaryClassification`, `SYSTEM_PROMPT`/`SCHEMA` pins.
- `packages/proofops/application/tagging/tracks.py`: `TrackCandidate`,
  `validate_track_candidates` (track ∈ TRACKS; no topic inference).
- `packages/proofops/application/evidence/span_citations.py`: `verify_source_ref`.
- `apps/worker/src/proofops_worker/tag_runner.py`: `LocalTagRunner._execute`
  preliminary-supplier seam (the override injection point), fenced usage / lease /
  stop guards, `publish_transaction` wiring.
- `apps/worker/src/proofops_worker/tag_recovery.py`: `TagRecoveryPlan`,
  `authorize_recovery` / `_open_authorizations` / `TAG_RECOVERY_RUN_CHANGED`,
  `carry_forward`, `verify_published`, `eligible_claims` — the bounded-job authorization
  and carry-forward pattern to structurally reuse (not to broaden).
- `packages/proofops/adapters/local/review_store.py`: `LocalSQLiteReviewStore`
  immutable-trigger schema pattern; `publish_transaction`.
- `packages/proofops/adapters/local/tag_store.py`: `LocalTagStore.load_snapshot` /
  `load_inputs`, `tag_pins`, `validate_tag_commit`.
- `apps/api/src/proofops_api/routers/reviews.py`: role/CSRF/If-Match/idempotency/
  rate-limit route shape; `apps/api/src/proofops_api/routers/claims.py`:
  `review_projection` read-only projection + `_BLOCKED_ACTION_TEXT`
  (`PRELIMINARY_TAGS_UNRESOLVED`) to extend for the GET view.
- `scripts/review_ai_delegated.py`: trusted local AI-delegated provenance wiring
  pattern for the CLI method.
- `docs/untagged-review-contract.md`: prior additive read-only projection precedent.

## 6. Unresolved hard gates (not decided here)

- GAP-002 (preliminary track domain criteria) stays open; this route lets an
  authorized reviewer classify, it does not define the correct track for a claim.
- `max_new_requests` is fixed at **6** (up to 3 relation + 3 element replicas),
  with a durable authorization-time request baseline. Restart never renews it.
  The frozen run selects synthetic-local or `upstage_local`; production calls still
  require the existing shared budget and explicit execution authorization.
- Whether a later phase allows >1 claim per reprocess job (initially fixed at 1).
- One Naver R19 claim reached the existing element review queue through three real
  element calls (AI-delegated classification, not human gold). Consensus remains
  unresolved and grade is null. Independent gold and full-report completion remain
  `not_run`.

## 7. Traceability

- Blocked state producer: `tag_runner.py` `_execute` (`reason=
  "PRELIMINARY_TAGS_UNRESOLVED"`).
- Review load gate: `tag_store.py` `load_inputs`; `reviews.py`
  `ReviewInputs.validate` / `ReviewService._resolve_with_provenance`.
- Bounded-job precedent: `tag_recovery.py`.
- Diagnosis of the gap: `32_PIPELINE_COMPLETION_PLAN.md` R21;
  `outputs/agent-results/R21-review-gap.json`.
- Invariants: `AGENTS.md`; `00_MASTER_SPEC.md` §3/§5; `docs/untagged-review-contract.md`.

This implementation is traceable to the above code and preserves the original invariants
(rule engine is the only grader; humans set classification/tagging, not grade;
human vs AI-delegated provenance distinct; unknown/conflict preserved; source-
unverified rejected; prior runs/checkpoints/receipts/reports immutable; tenant/
session/CSRF/If-Match/idempotency enforced). It establishes a review path, not a completed grade or production release.
