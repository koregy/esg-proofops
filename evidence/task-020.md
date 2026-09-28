# TASK-020 — 버전별 재채점

Implemented in assigned application/acceptance files, plus coordinator-authorized dedicated local store and router. No shared contracts, composition, existing adapters, dependencies or Git history were changed by this worker. Read the repository rules, master/original source, priority 26/27/28/31/19, TASK-020/FR-020/AT-020, rule snapshots/engine contracts, fixed RescoreCreate/JobAccepted and API operation metadata.

## Actual behavior

- `create_rescore(new_pack, tags, previous_pack=..., context=..., previous_decision=...)` uses the actual pure Python rule engine. It returns a new decision at the next revision or explicit `RetagRequired`; it never parses, calls a model, rewrites tags or assigns grades itself.
- Ontology, element set/meaning/condition and expanded source-scope changes require retagging even if a version was not bumped. Narrowed scopes must still admit each present source. Missing rule operands/applicability facts require retagging; stored unknown/conflict/not_applicable facts remain unresolved. Changed safe-harbor input checklists also require retagging. Basis-only metadata changes can be reevaluated with a new rule hash.
- `RescoreService` loads the immutable original graph/packets/replicas outside the SQLite writer transaction, validates snapshot, source citations, model/prompt/replica pins, document/tenant/claim identities and tag/decision heads, then prepares the real rules batch.
- `LocalSQLiteRescoreStore` captures the tenant-scoped active target pack and run/claim heads. A single transaction rechecks captured state, appends decision revisions, updates current decision heads, bumps run revision/epoch, appends chained audit, persists the child rescore artifact and its idempotent receipt. Stale state returns 412. Audit failure rolls everything back. Previous tags, decisions and the original run snapshot are unchanged.
- POST `/v1/runs/{run_id}/rescores` enforces reviewer capability, CSRF/Origin, bounded strict input, idempotency and 10/min/user. Direct label/grade fields are rejected. No If-Match header is required by the fixed contract; an optional supplied header is honored, and captured-head CAS always applies.
- HTTP 202 with `status: ready` is returned only after the actual rules batch commits. `status_url` points to GET `/v1/runs/{run_id}/rescores/{rescore_id}`, a read-only viewer endpoint with 120/min/user and no-store, returning the persisted JobAccepted receipt. Foreign/missing resources return 404. No fake queued task, fake worker completion, reparse, model invocation or usage record is created.

## Compatibility / rollback

Storage migration was defined before implementation and coordinated through Orca. Additive private schema version 1 adds `rescore_artifact` and `rescore_idempotency` job-record kinds and append-only update/delete/replacement guards (including decision revisions). Existing readers ignore new kinds; initialization is repeatable and unknown versions fail closed. Rollback disables the routes/writers and retains all artifacts/revisions; no destructive downgrade.

The coordinator owns the additive GET public contract and shared API composition. Stable wiring: `RescoreService(LocalSQLiteRescoreStore(runs.store), load_inputs=tags.load_inputs)` and `build_rescores_router(service, auth_store, allowed_origin=...)`. At this worker's final inspection, the standalone real router was tested but shared `main.py`/`composition.py` did not yet mount it; that integration was explicitly escalated to the coordinator and is not claimed here.

## Executed verification

All commands ran from `<local-project-root>`.

| Exact command | Actual result |
|---|---|
| `uv run pytest tests/acceptance/test_rescore.py -q --tb=short` — initial pure RED | exit 1; 20 failed because implementation was missing |
| `uv run pytest tests/acceptance/test_rescore.py -q --tb=short` — durable HTTP RED | exit 1; 11 failed / 20 passed because durable store/router were missing |
| `uv run pytest tests/acceptance/test_rescore.py -q --tb=short` — original model/prompt pin regressions RED | exit 1; 2 failed / 39 passed; inconsistent pins incorrectly returned 202 before the fix |
| `uv run pytest tests/acceptance/test_rescore.py -q --tb=short` — mounted operation contract RED | exit 1; 1 failed / 41 passed; operationId was rescore_create instead of canonical rescore |
| `uv run pytest tests/acceptance/test_rescore.py -q` — final required acceptance | exit 0; **42 passed**, 2 dependency deprecation warnings, 3.10s |
| `uv run pytest tests/acceptance/test_rescore.py tests/acceptance/test_reviews.py tests/acceptance/test_rules.py tests/acceptance/test_rulepack_api.py tests/acceptance/test_audit.py tests/contracts/test_package_contracts.py tests/integration/test_local_tag_runner.py -q` | exit 0; **194 passed**, 2 warnings, 12.76s; real pure-domain behavior, HTTP/SQLite, contract and pipeline integration |
| `uv run pytest tests/acceptance/test_session_security.py tests/security/test_prompt_injection.py tests/integration/test_request_limits.py -q` | exit 0; **39 passed**, 2 warnings, 2.86s |
| `uv run pytest tests/unit/test_package_validation.py -q` | exit 0; **1 passed**, 0.86s; documentation/contract validator unit check, not a substitute for application tests |
| `uv run ruff check packages/proofops/application/rescores.py packages/proofops/adapters/local/rescore_store.py apps/api/src/proofops_api/routers/rescores.py tests/acceptance/test_rescore.py` | exit 0; All checks passed |
| `uv run ruff format --check packages/proofops/application/rescores.py packages/proofops/adapters/local/rescore_store.py apps/api/src/proofops_api/routers/rescores.py tests/acceptance/test_rescore.py` | exit 0; 4 files already formatted |
| `uv run mypy --follow-imports=silent packages/proofops/application/rescores.py packages/proofops/adapters/local/rescore_store.py apps/api/src/proofops_api/routers/rescores.py` | exit 0; no issues in 3 source files; scoped, not an all-WIP type claim |
| `uv build --package proofops --out-dir /tmp/proofops-task020-build` | exit 0; sdist and wheel built |
| `uv build --package proofops-api --out-dir /tmp/proofops-api-task020-build` | exit 0; sdist and wheel built |

Warnings are existing Starlette/httpx and AnyIO deprecations. Initial formatting/import checks reported issues corrected with scoped `ruff check --fix` and `ruff format`; final checks above passed. Two fixture integration errors (SessionRecord uses active_tenant_id; the tag runner already exposes a ReviewService) and the session seed token mismatch were corrected to use actual existing APIs; no behavioral assertion was weakened or removed.

## Important review findings / limits

- The real local parser → extraction → three synthetic tag receipts → human review → rescore chain is exercised. Current tag DTOs do not collect all applicability triggers: the actual chain correctly returns **RETAG_REQUIRED**, retaining old tags/decisions and making no additional model calls. This is a real upstream input limitation, not a successful live rescore claim.
- The successful durable fixture explicitly supplies a new immutable **local-synthetic-fixture** tag revision containing an **unknown** applicability trigger, with real original source/packet pins and actual engine decision. It does not modify product tags, manufacture absent/present, mock rule outputs or impersonate domain approval. The pure tests separately demonstrate reproducible E3 reevaluation of fully supplied synthetic verified facts.
- Concurrent rescores prepare against the same heads; exactly one commits and the other gets 412. Repeat idempotency keys replay without mutation, changed bodies conflict, and a new key appends another immutable decision revision. Activation changes prevent a ready receipt. SQLite triggers prevent overwriting/deleting/replacing stored rescore artifacts. Foreign-tenant POST and receipt GET are blocked.
- An actual SQLite audit write failure originally surfaced as 422 because AuditConflict inherits DomainValidationError. The service now maps it to 409 while preserving transaction rollback.
- **not_run:** real model calls, AWS/DynamoDB distributed transactions, customer/private documents, real source rights/data/legal/applicability approval and production performance/quality measurement.
- **not_run:** browser/UI E2E and frontend build for this backend-only assigned task; no frontend files were changed. Shared application mounting remains coordinator-owned as noted above. No production-complete or accuracy claim is made.
