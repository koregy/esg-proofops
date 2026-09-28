# TASK-029 — 모델·외부전송 사전 점검

Scope: FR-029 / AT-029, P0. Local implementation and acceptance verification succeeded; no production readiness or real inference claim.
Verification recorded 2026-09-09 KST (tool clock 2026-09-08 UTC).

## Implemented

- `check_runtime_binding` evaluates server-resolved approved runtime/consent artifacts: UUID/version/reviewer/time, account permission evidence, model identity, full supported ARN syntax/account/region, every processing destination, structured-output/image capability evidence, and positive bounded token limits.
- Empty/unapproved snapshots, unknown routing, wildcard regions, foreign tenants and nested fallback configurations fail closed. A fresh requested live probe stays `not_run` and blocks readiness.
- `BedrockInvoker` implements `ModelInvocationPort`, rechecks snapshots at dispatch, verifies the SDK client's region/endpoint plus supplied verified account identity, enforces document rights, and sends only the exact approved model/profile to SDK `invoke_model`. Exceptions are sanitized; no alternative target is selected. Trace is disabled.
- `POST /v1/preflight` router factory enforces server-session admin capability, configured-origin CSRF, strict UUID/bool/extra-field validation, idempotency conflicts/replay, 10/min/user and 2/min/tenant requested-live-probe limits. Error payloads are sanitized and foreign/missing profiles return the same 404.
- TASK-042 build results compose into the existing Preflight checks. Missing build evidence is `not_run`, ready=false. API runtime imports no PyYAML tooling; CLI runs the real build verifier. No dependency or shared contract changes.
- TASK-045 now verifies full artifacts in `Registry.resolve_profile`; the router directly consumes that method. Actual SQLite-registry integration checks content/hash/version/tenant/ID, detached caller inputs, and immutable resolved records. No redundant artifact resolver remains.

## Coordinator integration

Only these files were written by this worker:
`scripts/preflight.py`, `packages/proofops/application/preflight.py`,
`packages/proofops/adapters/aws/bedrock.py`,
`apps/api/src/proofops_api/preflight.py`,
`tests/acceptance/test_preflight.py`, and this evidence file.
New router ownership was explicitly granted through Orca; main/auth/composition stay with their assigned owners.

```python
from proofops_api.preflight import build_preflight_router

app.include_router(build_preflight_router(
    composition.auth_store,
    resolve_profile=registry.resolve_profile,
    allowed_regions=server_allowed_regions,
    allowed_origin=app_origin,  # trusted APP_ORIGIN, never Host
    build_result=verified_build_result,  # TASK-042 SupplyChainResult, or None to block
    app_env="local",
))
```

The resolver must be the server's trusted registry, not request-supplied approval data.
Binding `approved_by/approved_at` and capability evidence must be present in the verified artifact; nothing grants approval automatically.
The dispatch adapter accepts an SDK client from trusted composition; this task neither installs boto3 nor discovers credentials.
P0 ARN support is deliberately limited to foundation-model and explicit inference-profile/application-inference-profile resources on the standard commercial AWS endpoint; other resource/endpoint types fail closed pending routing review.
The HTTP replay/rate implementation is explicitly local and refuses nonlocal construction until durable request control is wired.
The baseline production composition and existing unbound BedrockTagger remain fail-closed; coordinator owns integration.

## Tests and red → green evidence

All model/account/approval fixtures are explicitly synthetic, including fabricated ARN-shaped strings and the in-process SyntheticBedrockClient.
The test transport records actual dispatch attempts; the preflight, authorization, serialization, CLI, build verifier, registry and HTTP behavior are real code, never mocked.
No fixture grants actual rights, legal approval, account permission, or model availability.

1. `uv run pytest tests/acceptance/test_preflight.py -q` before implementation: exit 2, collection failed with ModuleNotFoundError for `proofops.adapters.aws`. After core implementation: 37 passed.
2. Same command after adding HTTP/build/malformed-metadata acceptance cases: exit 1, 15 failed / 37 passed. After implementation: 52 passed.
3. `uv run pytest tests/acceptance/test_preflight.py -q --tb=short` after adding CLI build-composition case: exit 1, 1 failed / 51 passed (unsupported --build-root). After implementation: 52 passed.
4. Same short-traceback command after full ARN/UUID cases: exit 1, 8 failed / 53 passed. After implementation: 61 passed.
5. `uv run pytest tests/acceptance/test_preflight.py -q -k host_header --tb=short`: exit 1, Host+Origin spoof returned 200. Explicit configured-origin integration fixed it.
6. `uv run pytest tests/acceptance/test_preflight.py -q -k registry_artifact --tb=short`: exit 1 before artifact integration. Initial raw-byte resolver was later removed when TASK-045 supplied the same responsibility directly; equivalent rejection/immutability assertions now exercise its actual SQLite registry.
7. Intermediate combined regression during TASK-045's live interface rewrite: exit 1, 8 failed / 189 passed (registry constructor/API changed mid-run; HTTP build fixture scanned shared WIP source). Adapted to final verified-artifact API and isolated HTTP build verification using real synthetic lockfiles/SBOM. No assertions weakened.
8. Early lint identified import ordering and long lines; formatter/import fixes resolved them. Final commands below are authoritative.

## Final exact commands and observed outputs

### `uv run pytest tests/acceptance/test_preflight.py -q`

Exit code: 0 .

```text
...............................................................          [100%]
=============================== warnings summary ===============================
tests/acceptance/test_preflight.py::test_http_admin_preflight_and_idempotency_contract
  <local-project-root>/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

tests/acceptance/test_preflight.py::test_http_admin_preflight_and_idempotency_contract
  <local-project-root>/.venv/lib/python3.12/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
63 passed, 2 warnings in 0.58s
```

### `uv run pytest tests/unit tests/contracts tests/acceptance/test_preflight.py tests/acceptance/test_auth.py tests/acceptance/test_supply_chain.py tests/acceptance/test_registry.py tests/acceptance/test_session_security.py -q`

Exit code: 0 .

```text
........................................................................ [ 36%]
........................................................................ [ 72%]
........................................................                 [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  <local-project-root>/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

.venv/lib/python3.12/site-packages/starlette/testclient.py:53
  <local-project-root>/.venv/lib/python3.12/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
200 passed, 2 warnings in 2.78s
```

### `uv run ruff check scripts/preflight.py packages/proofops/application/preflight.py packages/proofops/adapters/aws/bedrock.py apps/api/src/proofops_api/preflight.py tests/acceptance/test_preflight.py`

Exit code: 0 .

```text
All checks passed!
```

### `uv run ruff format --check scripts/preflight.py packages/proofops/application/preflight.py packages/proofops/adapters/aws/bedrock.py apps/api/src/proofops_api/preflight.py tests/acceptance/test_preflight.py`

Exit code: 0 .

```text
5 files already formatted
```

### `uv run mypy scripts/preflight.py packages/proofops/application/preflight.py packages/proofops/adapters/aws/bedrock.py apps/api/src/proofops_api/preflight.py`

Exit code: 0 .

```text
Success: no issues found in 4 source files
```

### `uv run python scripts/verify_architecture.py`

Exit code: 0 .

```text
PASS purity:packages/proofops/domain/__init__.py
PASS purity:packages/proofops/domain/applicability.py
PASS purity:packages/proofops/domain/audit.py
PASS purity:packages/proofops/domain/documents.py
PASS purity:packages/proofops/domain/errors.py
PASS purity:packages/proofops/domain/provenance.py
PASS purity:packages/proofops/domain/regulatory.py
PASS purity:packages/proofops/domain/rulepacks.py
PASS purity:packages/proofops/domain/rules/__init__.py
PASS purity:packages/proofops/domain/rules/engine.py
PASS purity:packages/proofops/domain/rules/exceptions.py
PASS purity:packages/proofops/domain/rules/goal.py
PASS purity:packages/proofops/domain/rules/management.py
PASS purity:packages/proofops/domain/rules/performance.py
PASS purity:packages/proofops/domain/rules/safe_harbor.py
PASS purity:packages/proofops/domain/values.py
PASS dto-boundary:apps/api/src/proofops_api/dto.py (pydantic-only)
PASS ports:SyntheticTagger implements TaggerPort without grading fields
PASS ports:BedrockTagger refuses without approved binding
PASS composition:local-synthetic builds
PASS composition:synthetic rejected in staging
PASS composition:bedrock rejected in staging
PASS composition:synthetic rejected in production
PASS composition:bedrock rejected in production
PASS contracts:llm_tags fixture validates against fixed schema (read-only)
verify_architecture: all checks passed
```

### `uv run python scripts/check_licenses.py --root . --json`

Exit code: 0 .

```text
{
  "passed": true,
  "errors": [],
  "warnings": [
    "Human license/data-rights approvals and deployed-image verification remain separate release gates."
  ]
}
```

### `uv run python scripts/preflight.py`

Exit code: 1 (expected: no approved live account/consent artifacts).

```text
{"ready": false, "checks": [{"name": "configuration", "status": "fail", "reason": "missing, invalid or inaccessible approval artifacts"}, {"name": "live_model_probe", "status": "not_run", "reason": "offline CLI never calls a model"}, {"name": "supply_chain", "status": "pass", "reason": "build/license checks passed"}], "binding_sha256": null, "checked_at": "2026-09-08T16:57:48.031402+00:00"}
```

### `uv build --package proofops --out-dir /tmp/proofops-task-029-build`

Exit code: 0 .

```text
Building source distribution...
Building wheel from source distribution...
Successfully built /tmp/proofops-task-029-build/proofops-0.0.0.tar.gz
Successfully built /tmp/proofops-task-029-build/proofops-0.0.0-py3-none-any.whl
```

### `uv build --package proofops-api --out-dir /tmp/proofops-task-029-build`

Exit code: 0 .

```text
Building source distribution...
Building wheel from source distribution...
Successfully built /tmp/proofops-task-029-build/proofops_api-0.0.0.tar.gz
Successfully built /tmp/proofops-task-029-build/proofops_api-0.0.0-py3-none-any.whl
```

## Not run / remaining gates

- Real Bedrock probes, live product inference, actual AWS account/IAM/routing discovery, AWS mutation, private customer data, data/rights/legal approval: `not_run` (not authorized).
- Production/deployed-image attestation, durable distributed HTTP replay/rate control, root API/composition integration, full browser E2E and AWS E2E: `not_run`; coordinator owns follow-on integration/deployment.
- `python scripts/validate_package.py` is not an app test; not invoked directly because it rewrites shared coordinator-owned evidence. Related unit/contract tests above executed successfully.
- No commit, push, AWS change, customer processing, approval grant, grade generation or revision overwrite was performed.
- Two existing TestClient/AnyIO deprecation warnings remain; no dependencies were changed.

SDK request semantics checked against the primary AWS reference:
[Amazon Bedrock InvokeModel](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModel.html).
This is API documentation verification, not an account availability or routing attestation.

