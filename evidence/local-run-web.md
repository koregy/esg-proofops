# Local run web evidence

Date: 2026-09-09 KST

## Scope

This check covers the approved `/documents/new` upload-to-run flow only. It uses the existing application created by `proofops_api.main`, the same temporary SQLite database for the browser and runners, explicit synthetic approved rights/consent/runtime/rule-pack fixtures, and the accepted local parser and synthetic extractor runners. It does not claim TASK-026 dashboard, claim review, production readiness, or live-model accuracy.

## Browser result

Server command:

```sh
task_tmp=$(mktemp -d); uv run python tests/e2e/local_browser_server.py --port 4193 --database "$task_tmp/state.sqlite3"
```

The final database was `/var/folders/tz/30ymnkyn2mz35sf7y3nbxl_00000gn/T/tmp.rG4jxtSZzQ/state.sqlite3`. The check used a new Orca page `caa2d827-6091-4aeb-b707-a564c1f6835a`; ports 4190/4191, the root-owned page, and unrelated port 8000 were not touched.

Observed sequence:

1. The UI loaded one same-tenant company plus explicitly approved rights, consent, and runtime options.
2. `evidence/task-001-web-fixture.pdf` uploaded through the real document/version/upload-complete flow. Ready version `171d7413-2c00-42f3-80e9-5b327bf5c966` reported one page; both the local file and stored object reported SHA-256 `8cfddae2ffd8d38dbfcede1423d130f3e549bf5fb415183b46f2e0023fe2f2c4`.
3. The run form selected active disclosure pack `proofops-domain-v2.0-impl1`. Advertising mode was disabled because no approved active advertising pack was seeded.
4. Declared subset page `2` produced `실제 문서 범위인 1~1페이지만 선택할 수 있습니다.` and `select count(*) from run_snapshots;` remained `0`.
5. Full scope generated `POST /v1/preflight` (200) before `POST /v1/runs` (202). Both requests carried CSRF and separate idempotency keys; the run payload used the actual ready version, approved consent/runtime, and active pack.
6. Run `d5ebc387-70cc-4d9e-aeb7-8433535ef4fd` displayed queued/parse without claiming later work was complete. The authenticated, CSRF-protected test-only advance route invoked `LocalParserRunner`, after which the UI displayed parse complete and extract pending/running.
7. The accepted `LocalExtractRunner` with the synthetic extractor advanced the same persisted run to tag. The UI displayed parse/extract complete, tag pending/running, one unreadable page, and `전수 검토 미완료`; it did not invent tag completion or a report.
8. Cost status with `amount: null` displayed `단가 미설정 · 금액 미확정` while real zero counters remained `0`.
9. UI cancellation sent CSRF, an idempotency key, and `If-Match: "5"`; the API returned 202 and the polled run ended `cancelled`, revision `6`.

Before implementation, a separate 4192 fixture page showed the upload-ready state without any analysis-run form, establishing the browser RED check.

The final polling review also aborts an in-flight poll before cancel/retry, ignores lower-revision responses, and starts a fresh poll cycle after every action so a successful failed-stage retry cannot remain stuck on its first response.

## Verification

```text
npm --prefix apps/web run typecheck
PASS (tsc --noEmit)

npm --prefix apps/web run build
PASS (Vite 6.4.3, 34 modules transformed)

node --check apps/web/test/browser-fixture.mjs
PASS

uv run python -m py_compile tests/e2e/local_browser_server.py
PASS

uv run ruff check tests/e2e/local_browser_server.py
PASS (All checks passed!)

uv run ruff format --check tests/e2e/local_browser_server.py
PASS (1 file already formatted)

uv run pytest tests/integration/test_run_lifecycle.py tests/integration/test_local_parser_runner.py tests/integration/test_local_extract_runner.py -q
PASS (80 passed, 2 dependency deprecation warnings)

uv run pytest tests/acceptance/test_upload.py tests/acceptance/test_registry.py tests/acceptance/test_advertising.py tests/acceptance/test_preflight.py tests/acceptance/test_cost.py tests/acceptance/test_coverage.py -q
PASS (174 passed, 2 dependency deprecation warnings)

uv run pytest tests/acceptance/test_upload_security.py tests/acceptance/test_auth.py tests/acceptance/test_session_security.py -q
PASS (66 passed, 2 dependency deprecation warnings)

python scripts/validate_package.py
NOT_RUN (`python` command is unavailable in this shell; rerun through the locked environment below)

uv run python scripts/validate_package.py
PASS (701 documentation/contract checks; this is not an application test)

git diff --check -- <owned implementation and harness paths>
PASS
```

## Explicitly not run

- Live LLM/model calls and live-model preflight probe: `not_run`
- AWS/cloud services and customer PDFs: `not_run`
- Human approval/review flows: `not_run`
- Root-owned claims list/detail API and report completion: `not_run`
- Git commit/push or cloud changes: `not_run`

The fixture approvals and parser/extractor advance endpoint are local test-only behavior; they do not alter product defaults or bypass the existing authenticated session, role, origin, or CSRF gates.

## Coordinator acceptance

`pnpm --dir apps/web build` passed with TypeScript and Vite after review.
The runnable browser regression `tests/e2e/run_progress_check.mjs` uses the actual
RunProgress component with controlled HTTP responses. On an isolated Vite server
at port 4195, it first failed `Tenant/run replacement must abort pending action`.
After adding action cancellation and checking aborted responses, it returned
`{"retry_poll_resumed":true,"stale_action_ignored":true}`. This covers retry polling
and a late cancellation response after tenant/run replacement; it is a synthetic
UI check, not a model or production test.

Rerun with Vite on port 4195 and the Orca browser console:

```js
await import('/@fs<local-project-root>/tests/e2e/run_progress_check.mjs').then(m => m.check())
```
