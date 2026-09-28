# TASK-019 — 인간 태깅 검토

Status: implemented and verified locally; synthetic source/model fixtures only. No commit, push, real model call, AWS mutation, private customer processing, or human approval performed.

## Scope and contracts

Implemented `application/reviews.py`, `adapters/local/review_store.py` (coordinator-authorized addition), `routers/reviews.py`, `ReviewWorkspace.tsx`, and `tests/acceptance/test_reviews.py`. Shared composition, claim projections, App routing and contracts remain coordinator-owned.

- `ReviewService(store, load_inputs=...)`; `publish(inputs, review_id=None)` creates a genuine initial review, including `confirmed_tags=None`/no decision when consensus is unresolved.
- `publish_transaction(connection, inputs, review_id=None)` joins the caller's fenced tag/checkpoint/outbox transaction, writes initial audit/heads/revisions, and neither commits nor increments epoch. Standalone `publish` owns one transaction and epoch increment.
- `resolve_review(actor, review_id, body, if_match, idempotency_key)` reads immutable inputs before acquiring the writer lock, then revalidates the snapshot hash, review revision/state and claim tag head inside the atomic transaction. It appends new human tags, a pure-engine decision, review revision, claim heads, real run epoch, hash-chained audit and idempotency response together.
- Actor reviewer/admin capability, session CSRF/origin and 10 writes/minute are enforced. Stale quoted If-Match or base tags return 412; grading/tenant input or unverified tags return 422; changed-body idempotency reuse returns 409; foreign tenant lookup returns 404.
- Full original/selected evidence packets and actual three TagRun raw/guarded/usage/hash receipts remain in immutable input provenance. Canonical graph identity and digest pin the separately loaded graph, avoiding a full graph copy per claim. Source page/bbox, document/manifest, model/prompt/rule/replica identity is retained.
- Human source flags are not trusted: citation and claim binding are rechecked, literal normalized values must match cited spans, and numeric/year global evidence is constrained by existing binding/rule contracts. Unknown/conflict are not converted to absence. Absence/N/A and P4/P6 cannot acquire coverage/applicability/assurance/computation attestations from browser text.
- Internal engine decision and semantic hash remain unchanged; `human_confirmed` is exposed as review provenance in the API projection. A successful review may still return null grade/label with `blocked_rule_gap`.
- GET review queue reuses the shared bounded `catalog_pages` helper and `catalog_list_snapshots` for 15-minute fixed summary snapshots, including stable rows/epoch after concurrent resolutions.
- React component edits a separate draft, shows source choices and confirmation, retains draft on 412 and explicitly requires latest-state comparison before another submission. Tenant/user/review identity changes discard the old editor; late callbacks are ignored. Source text is escaped by React, and viewer/editor cannot submit.

## Storage compatibility and rollback

Declared before implementation through Orca and adapter documentation: additive private review schema v1; existing `job_records` kinds `review_head`, `review_revision`, `review_inputs`, `tag_revision`, `decision_revision`, `claim_head`, `review_idempotency`. Update/delete/replacement triggers protect immutable kinds. Existing job/audit transactions are reused; existing records are not rewritten. Existing catalog snapshot schema is reused, not changed. Unknown review schema versions fail closed. Rollback disables review routes/writers and retains all revisions/artifacts; no destructive down migration. SQLite remains explicitly `local-synthetic-only`, not a distributed DynamoDB implementation.

## Executed checks

All paths/commands below were run in `<local-project-root>`.

| Command | Actual result |
|---|---|
| `uv run pytest tests/acceptance/test_reviews.py -q` (initial red) | exit 1, 21 failures: review implementation missing |
| `uv run pytest tests/acceptance/test_reviews.py -q --tb=short` (final) | exit 0, **29 passed**, 2 existing Starlette/httpx/AnyIO deprecation warnings, 1.70s |
| `uv run pytest tests/acceptance/test_reviews.py tests/acceptance/test_citations.py tests/acceptance/test_binding.py tests/acceptance/test_rules.py tests/acceptance/test_audit.py tests/acceptance/test_auth.py tests/contracts/test_package_contracts.py -q` | exit 0, **233 passed**, same 2 warnings, 5.42s; domain/unit behavior, HTTP/SQLite integration, auth and contract regression |
| `uv run pytest tests/acceptance/test_session_security.py tests/security/test_prompt_injection.py -q` | exit 0, **23 passed**, same 2 warnings, 0.85s |
| `uv run ruff check packages/proofops/application/reviews.py packages/proofops/adapters/local/review_store.py apps/api/src/proofops_api/routers/reviews.py tests/acceptance/test_reviews.py` | exit 0, All checks passed |
| `uv run ruff format --check packages/proofops/application/reviews.py packages/proofops/adapters/local/review_store.py apps/api/src/proofops_api/routers/reviews.py tests/acceptance/test_reviews.py` | exit 0, 4 files already formatted |
| `uv run mypy packages/proofops/application/reviews.py packages/proofops/adapters/local/review_store.py apps/api/src/proofops_api/routers/reviews.py --follow-imports=silent` | exit 0, no issues in 3 source files; scoped check, not an all-WIP type claim |
| `pnpm --dir apps/web run build` | exit 0, TypeScript `tsc --noEmit` and Vite build passed, 34 modules, 409ms |
| `uv build --package proofops --out-dir /tmp/proofops-task019-build` | exit 0, source distribution and wheel built |
| `uv build --package proofops-api --out-dir /tmp/proofops-task019-build` | exit 0, source distribution and wheel built |

The acceptance file also executes the actual React component through the installed esbuild + React server renderer and Node assertions; it checks rendering, accessible form text, escaped source markup and disabled viewer controls. Its HTTP tests use actual ASGI clients, SQLite files and the existing pure rule/citation/binding code; source/model fixtures are explicitly synthetic, not mocked result endpoints.

## Critical review and corrections

- Both direct service competitors and two HTTP clients produce one success and one 412, preserving old revisions.
- A real SQLite trigger failure on audit append rolls back tags, decisions, review/claim heads, epoch and idempotency. Caller transaction rollback also removes initial publication/audit.
- A new durable-loader test first failed with 409 after an 11.23-second lock timeout. Root cause was opening existing transactional read adapters from inside a writer transaction. Input loading now occurs before the writer transaction, while pins and heads are still checked before committing.
- A queue pagination test first failed with 409 after an epoch change. Fixed snapshots now return the original remaining review rows and epoch.
- Cross-run idempotency reuse initially reached an unrelated missing claim head; key lookup now spans the same tenant/user/operation across runs and returns 409 before writes.
- Initial fixture expected `blocked_evidence`; the unchanged domain engine returned `blocked_rule_gap` because the synthetic claim is a product variant. The assertion was corrected to the actual source-defined gap behavior, retaining null grade/label checks; no grading rule was weakened.
- Formatting/import issues and one test syntax error were corrected. The first maintenance command `python` failed because this environment exposes Python through `uv run python`; subsequent commands used that runtime. No dependencies were installed or changed.

## Remaining gates and ownership

- **not_run:** real model/AWS/DynamoDB distributed transactions, real customer/public-report quality measurement, human source/rights/legal/applicability approval.
- **not_run:** interactive browser E2E for the newly composed review page. Actual React render and HTTP/SQLite integration passed; coordinator owns full App/API composition and browser navigation verification.
- No newly approved absent/N/A/assurance/numeric attestation producer is invented here. Such states remain rejected or unresolved until dedicated existing source/coverage/applicability/check services supply trustworthy inputs; this is not a production-complete claim.
- Coordinator owns the current-claim projection helper: `claim_head` stores tag/decision revision numbers; revision keys are `claim_id:0000000001`; decision records retain internal `decision` and public `api`. `history` is intended for audit/testing and should not be used to load all raw receipts on each list row.

Final related pipeline integration: `uv run pytest tests/integration/test_local_tag_runner.py -q` exited 0 with **18 passed**, 2 existing deprecation warnings, 5.42s. This exercises the other worker's real local tag/checkpoint publication integration against this task's transaction hook, including partial gating and crash/reopen behavior; no files in that worker's scope were changed.
# Coordinator integration acceptance

The combined tag-runner/tagging/review/jobs suite passed 118 tests, with two
existing dependency deprecations. A further actual HTTP/SQLite test in
`tests/integration/test_claim_api.py` loads the three-call synthetic checkpoint,
reads fixed ClaimDetail, resolves unknown tags through the real service, and
reads immutable revision 2 with null grade and human_confirmed provenance.
That claim suite passed 2 tests; composition/source/contract checks passed 32.
Live models, AWS, customer data and real human source approval remain not_run.
