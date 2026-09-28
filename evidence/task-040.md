# TASK-040 — 삭제·보존·복구

Date: 2026-09-09 KST. Requirement SEC-004; acceptance AT-040.
Outcome: implemented and verified within the coordinator-approved local scope.
Production physical deletion and customer retention approval remain blocked/not_run.

## Implemented behavior

- `delete_document_tree` requires a tenant/document-matched, explicitly approved retention policy and a registered immutable deletion manifest. Legal hold and unexpired retention block deletion. TTL is never confirmation.
- Every attempt inventories all document versions and tracked resource kinds before and after deletion. Original, derived, search, cache, review, memory, export and backup residues prevent completion. Successful deletion acknowledgment with remaining data stays `running`; actual delete failure stays `failed` and can be retried.
- `reapply_tombstones` re-enumerates restored data rather than trusting a previous completed manifest. Missing restore policy blocks deletion. Stored tombstones continue hiding data and fencing writes while blocked.
- The worker adapter is explicitly `local-synthetic-only`, with real file-backed SQLite resources and a separate durable tombstone/attempt ledger. Its tests delete actual synthetic BLOB rows, exercise database-trigger failures and ignored deletes, restore a real SQLite backup, and delete newly discovered restored versions. It is not connected to runtime customer storage or AWS.
- The real local HTTP `POST /v1/documents/{document_id}/deletion-requests` requires admin, CSRF/origin, bounded body, rate limit and idempotency key. It records one durable tombstone plus metadata-only audit atomically and always returns the fixed `DeletionRequest` schema with `blocked_retention`, because no customer policy was approved.
- Runtime manifest categories remain `not_run`; existing upload identities/hashes/version IDs are recorded. Runtime source, review, decision and export bytes are not physically deleted or rewritten. Cached tenant catalog snapshots are invalidated in the same transaction so old cursors cannot replay deleted metadata.

No new dependency, contract edit, commit, push, cloud mutation, real model invocation, customer document processing or legal/data approval was performed by this worker. No grade/label or evidence interpretation changed.

## Ownership and migration

Worker-owned paths:

- `packages/proofops/application/retention.py`
- `apps/worker/src/proofops_worker/deletion.py`
- `tests/acceptance/test_retention.py`
- `packages/proofops/adapters/local/retention_store.py`
- `apps/api/src/proofops_api/routers/deletion.py`
- `tests/integration/test_retention_api.py`
- `evidence/task-040.md`

The coordinator explicitly authorized the additional new local adapter/router/integration files through Orca. The coordinator owns the existing upload/job/run/export stores, API main/composition, shared contracts and `tests/integration/test_deleted_document_access.py`. Shared guards and composition were verified against those current files without editing them.

Runtime schema v1 adds `retention_schema` and append-only `retention_tombstones`; immutable inserts/replacements/updates/deletes cannot erase a tombstone. Request creation reuses the upload transaction and audit chain. No existing payload schema or revision migration is required. The route uses the existing OpenAPI ActionReason/DeletionRequest contract. Legacy databases without a tombstone table are compatible before the additive migration.

Rollback disables new request/worker execution while retaining tombstones and access guards; removing guards or restoring a database without preserving its deletion ledger would resurrect access and is not a supported rollback. Runtime backup/tombstone migration remains an operational gate. The isolated worker restores only its resource DB and retains/replays its separate ledger before access. Synthetic physical deletion is irreversible; SQL secure_delete is enabled, but no forensic device-erasure or cloud deletion claim is made.

## Tests first and important fixes

1. `uv run pytest tests/acceptance/test_retention.py -q` initially failed **9 tests** because the retention implementation was missing; after implementation: **9 passed**.
2. `uv run pytest tests/integration/test_retention_api.py -q --tb=short` initially failed **6 tests** because the runtime store/router were missing. After implementation, one visibility test remained red until coordinator-owned guards landed.
3. `uv run pytest tests/integration/test_retention_api.py -q -k 'oversized or later_idempotency or audit_failure' --tb=short` exposed **2 failures, 1 pass**: whitespace-padded oversized reasons and changed bodies under a later retry key were incorrectly accepted. Raw length validation and immutable-request hash conflict checks fixed both.
4. `uv run pytest tests/integration/test_deleted_document_access.py -q --tb=short` reproduced **1 failure**: a pre-deletion catalog cursor still worked. Atomic tenant snapshot invalidation fixed it; the combined focused run below passed.

Important review covered tenant scoping, irreversible-policy gates, post-delete inventory, ledger preservation across backup restore, immutable history, malformed policy flags/timestamps, audit rollback, concurrent request deduplication, stale catalog cursors, issued export tickets and worker commit fences. No test was removed or weakened.

## Final commands and actual results

All commands ran from `<local-project-root>` unless stated otherwise.

```sh
uv run pytest tests/acceptance/test_retention.py -q
# exit 0: 9 passed in 0.23s

uv run pytest tests/integration/test_deleted_document_access.py tests/integration/test_retention_api.py tests/acceptance/test_retention.py -q --tb=short
# exit 0: 19 passed, 2 warnings in 1.69s

uv run pytest tests/integration/test_deleted_document_access.py tests/integration/test_retention_api.py tests/acceptance/test_retention.py tests/acceptance/test_exports.py tests/acceptance/test_audit.py tests/acceptance/test_auth.py tests/integration/test_local_api_composition.py tests/integration/test_catalog_lists.py tests/integration/test_worker_recovery.py -q --tb=short
# exit 0: 76 passed, 2 warnings in 7.40s

uv run pytest tests/contracts/test_package_contracts.py tests/unit tests/security -q --tb=short
# exit 0: 55 passed, 2 warnings in 1.74s

uv run ruff check packages/proofops/application/retention.py packages/proofops/adapters/local/retention_store.py apps/worker/src/proofops_worker/deletion.py apps/api/src/proofops_api/routers/deletion.py tests/acceptance/test_retention.py tests/integration/test_retention_api.py
# exit 0: All checks passed!

uv run ruff format --check packages/proofops/application/retention.py packages/proofops/adapters/local/retention_store.py apps/worker/src/proofops_worker/deletion.py apps/api/src/proofops_api/routers/deletion.py tests/acceptance/test_retention.py tests/integration/test_retention_api.py
# exit 0: 6 files already formatted

uv run mypy packages/proofops/application/retention.py packages/proofops/adapters/local/retention_store.py apps/worker/src/proofops_worker/deletion.py apps/api/src/proofops_api/routers/deletion.py
# exit 0: Success: no issues found in 4 source files

uv build --package proofops --out-dir /tmp/proofops-task040-build
uv build --package proofops-worker --out-dir /tmp/proofops-task040-build
uv build --package proofops-api --out-dir /tmp/proofops-task040-build
# each exit 0: source distributions and wheels built successfully

git diff --check
# exit 0: no output
```

The two warnings are existing Starlette/httpx TestClient and anyio BlockingPortal deprecations. No new dependency was added to suppress them. The package contract/unit checks are not substituted for the actual HTTP/SQLite deletion tests above.

Coordinator-owned cross-store regression was also run by this worker and passed: real synthetic export/private ticket/worker lease → actual deletion POST → document/version/original/review/run/export/ticket denied, fresh lists hide deleted data, old cursor rejected, late model-call/commit fence denied, retained immutable ZIP still present. Coordinator additionally reported the active-concurrency-slot regression green; that later assertion is covered by the coordinator's combined final gate.

## Remaining gates

Coordinator acceptance additionally ran the exact shared application path and related guards:

```text
uv run --no-sync pytest tests/acceptance/test_retention.py tests/integration/test_retention_api.py tests/integration/test_deleted_document_access.py tests/integration/test_local_api_composition.py tests/acceptance/test_jobs.py tests/acceptance/test_exports.py tests/acceptance/test_upload.py -q --tb=short
exit 0: 69 passed, 2 upstream warnings in 8.22s

uv run --no-sync mypy --follow-imports=silent packages/proofops/application/uploads.py packages/proofops/adapters/local/job_store.py packages/proofops/adapters/local/run_store.py packages/proofops/adapters/local/export_store.py apps/api/src/proofops_api/composition.py apps/api/src/proofops_api/main.py
exit 0: no issues in 6 source files
```

Scoped Ruff lint/format and `git diff --check` also passed. The root regression first
failed because deleted documents remained readable, then because an old catalog cursor
remained usable, then because a tombstoned running job still consumed an active slot.
Common upload/run guards, atomic cache invalidation, and the existing active-count path
now cover those failures. The composed API test makes a real deletion request after
reopening its SQLite database and verifies the document disappears from GET and lists.
Rollback must retain these visibility/fencing guards while any tombstone exists; merely
rolling back to a pre-retention reader would restore access and is unsafe.

- Customer/legal retention-policy approval: **blocked**, not inferred from draft `config/retention.yaml` or request body.
- Physical deletion of runtime/customer original, derivations, cache, search, memory, review/export history and cloud backups: **not_run**; runtime manifest says so.
- Real AWS deletion, backup recovery, model calls and operational distributed fencing: **not_run**, outside authorization.
- Browser/UI E2E for deletion: **not_run** by this worker; no deletion UI was assigned, coordinator owns UI/browser work. Real HTTP and composed local application integration are tested above.
- No production readiness, legal erasure guarantee, accuracy figure or cloud deletion success is claimed.

## Coordinator browser verification

On 2026-09-09 KST, Orca's real browser ran `tests/e2e/export_http_check.mjs` followed
by `tests/e2e/deletion_http_check.mjs` against the built application and a fresh shared
SQLite database at `http://[::1]:4217`. HTTP responses were not mocked. The form
created and downloaded a 92,985-byte ZIP with verified SHA, then the test-only
synthetic administrator requested deletion of that generated fixture. Results:

```text
request_accepted=true policy_blocked=true document_hidden=true
run_hidden=true export_hidden=true existing_ticket_denied=true
```

The original document/version/run/export GETs and a ticket issued before deletion
all returned 404. The document list omitted the tombstone. This supersedes the
worker's browser `not_run` boundary for this local synthetic request flow only;
runtime physical erasure and actual AWS backup recovery remain not_run.
