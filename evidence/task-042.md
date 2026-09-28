# TASK-042 / SEC-006 / AT-042 — supply-chain gate

Date: 2026-09-09 KST. Local implementation only; no commit/push, AWS calls,
product model calls, customer documents, or legal/data-rights approvals performed.

Coordinator acceptance: 42 focused tests passed again after worker completion.
CI type checking was extended to all Python application packages;
`uv run --no-sync mypy packages/proofops apps/api/src apps/worker/src apps/agent/src`
exited 0 (38 source files). The worker was released and its terminal closed.

## Scope and implementation

The existing unaccepted draft was repaired after 12 new acceptance failures
(`uv run pytest tests/acceptance/test_supply_chain.py -q`: 12 failed, 22 passed).
A missing SBOM generation command was separately reproduced (1 failed), then
three scanner directory/symlink bypasses were reproduced (3 failed, 1 passed).
No existing acceptance assertion was weakened: fixture SBOMs now carry actual
synthetic versions/ecosystems, and the former source-string wildcard prohibition
is tested against executable IAM policy results including the AWS ECR exception.
The inherited always-true CI assertion was replaced with an actual prohibition.

- `verify_supply_chain(*, root_dir, env=None, check_secrets=True)` preserves the
  immutable `SupplyChainResult(passed, errors, warnings)` interface. It parses
  uv TOML with stdlib `tomllib`, pnpm YAML with the already installed build tool,
  and checks exact ecosystem/name/version inventory against CycloneDX components.
  Missing tooling, malformed inventories, missing/unexpected versions, detected
  PyMuPDF without explicit flag/approval, unreadable sources and symlinks block.
- `scripts/check_licenses.py --generate-sbom` generates a real lock inventory
  including lock SHA-256 values. It invents no component versions or licenses.
  PyMuPDF approval is consumed only from trusted `config/license_decisions.yaml`:
  exactly one `name: pymupdf`, `decision: approved`, nonempty `approved_by`, ISO
  `approved_at` date, HTTPS `source`, and explicit `license` (`commercial`,
  `AGPL-3.0-only`, or `AGPL-3.0-or-later`). These are evidence-shape checks, not a
  legal opinion or authentication of an approver; repository approval/review is
  the trust boundary. No approval record is created in this repository.
- Secret scanning includes tests, dotfiles, and arbitrary file extensions,
  redacts all values, and checks common AWS/GitHub/private-key signatures. Source
  imports use AST inspection, including multi-imports and literal dynamic
  imports. Root `legacy_reference` remains an excluded research snapshot; nested
  production directories with that name cannot evade scanning.
- IAM is executable typed JSON, not deployed CDK infrastructure. Account/region,
  model and deployment ARNs are explicit caller inputs; broad inputs are rejected.
  GitHub OIDC trust requires protected staging/production environment subjects.
  API gets no Bedrock permission; worker gets only explicit approved model/profile
  resources and no Secrets Manager access. `ecr:GetAuthorizationToken` alone may
  use `Resource: "*"`; object actions alone may use a bounded bucket object suffix.
- CI now propagates typecheck/audit failures, uses the installed web TypeScript,
  runs local acceptance tests, produces SBOM from both locks, scans secrets, and
  runs real Python/npm vulnerability audits. It never assumes an AWS role.
  Shared manifests/locks and audit-tool dependency review belong to coordinator;
  see `evidence/dependencies-task042.md` (pip-audit 2.10.1 dev-only dependency).

## Required release boundaries

`passed` is this build-time artifact check, not a public deployment authorization.
TASK-029 owns POST `/v1/preflight` integration and the separate consent/data-rights
checks; this worker did not modify shared API/DTO/contracts. PyYAML remains
build-time/dev tooling: calling this checker in a runtime lacking it fails closed.
TASK-044 owns actual image inspection, deployed IAM/service-role integration,
GitHub protected-environment enforcement, cloud staging and rollback evidence.
Human license/data-rights/regulatory approvals, real AWS/model tests and browser
E2E are **not_run**, not implicitly approved by the local tests.

The signature scan is not a complete secret taxonomy or binary-image scanner;
arbitrary obfuscated dynamic imports are not a proof of dependency absence.
Locked installation, package inventory, repository review and release-image
inspection remain necessary. Synthetic approval records exist only in temporary
test directories and do not authorize PyMuPDF in this repository.

## Upstream verification

Read-only `git ls-remote https://github.com/REPO.git refs/tags/VERSION refs/tags/VERSION^{}`
returned these published refs (all calls exit 0):

| Repository / tag | Commit used |
| --- | --- |
| actions/checkout v4.2.2 | 11bd71901bbe5b1630ceea73d27597364c9af683 |
| actions/setup-python v5.4.0 | 42375524e23c412d93fb67b49958b491fce71c38 |
| astral-sh/setup-uv v6.3.0 | 445689ea25e0de0a23313031f5fe577c74ae45a1 |
| pnpm/action-setup v4.1.0 (peeled annotated tag) | a7487c7e89a18df4991f7f222e4898a00d66ddda |
| actions/setup-node v4.3.0 | cdca7365b2dadb8aad0a33bc7601856ffabcc48e |
| actions/upload-artifact v4.6.2 | ea165f8d65b6e75b540449e92b4886f43607fa02 |

CLI audit usage verified against [official pip-audit README](https://github.com/pypa/pip-audit)
and the installed tool. ECR token resource behavior follows
[AWS ECR push policy](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-push-iam.html).
The formerly present gitleaks action tag was also resolved upstream, but the
workflow now invokes the repository signature scan directly, with its scope
explicit above; it does not claim gitleaks ran.

## Actual validation commands and results

Earlier targeted type/lint cleanup exposed line-length issues and missing YAML
stubs; these were corrected without a new dependency. An initial plain `python`
read-only ref check failed (command not found), then succeeded with `python3`.
The reports below are actual local runs; the documentation validator is never
counted as app behavior validation.

### `uv run pytest tests/acceptance/test_supply_chain.py -q`

Exit: 0

```text
.......................................                                  [100%]
39 passed in 0.94s
```

### `uv run --no-sync ruff check packages/proofops/application/supply_chain.py scripts/check_licenses.py tests/acceptance/test_supply_chain.py`

Exit: 1

```text
packages/proofops/application/supply_chain.py:23:101: E501 Line too long (102 > 100)
   |
21 | _SBOM_CANDIDATES = ("sbom.json", "bom.json", "sbom.cdx.json", "evidence/sbom.json")
22 | _IGNORE_DIRS = {".git", ".venv", "node_modules", ".local", "__pycache__"}
23 | # ponytail: signature scan covers common credentials; broader detector/image scan belongs to TASK-044.
   |                                                                                                     ^^ E501
24 | _SECRET_PATTERNS = (
25 |     re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
   |

Found 1 error.
```

### `uv run --no-sync ruff format --check packages/proofops/application/supply_chain.py scripts/check_licenses.py tests/acceptance/test_supply_chain.py`

Exit: 0

```text
3 files already formatted
```

### `uv run --no-sync mypy packages/proofops/application/supply_chain.py scripts/check_licenses.py`

Exit: 0

```text
Success: no issues found in 2 source files
```

### `pnpm --dir apps/web exec tsc --noEmit --project ../../infra/cdk/tsconfig.json`

Exit: 0

```text
(no output)
```

### `uv run --no-sync pytest tests/unit/test_package_validation.py tests/contracts -q`

Exit: 0

```text
..............................                                           [100%]
=============================== warnings summary ===============================
tests/contracts/test_package_contracts.py::test_run_create_real_http_post
  <local-project-root>/.venv/lib/python3.12/site-packages/starlette/testclient.py:51: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
30 passed, 1 warning in 1.26s
```

### `uv run --no-sync python scripts/verify_architecture.py`

Exit: 0

```text
PASS purity:packages/proofops/domain/__init__.py
PASS purity:packages/proofops/domain/applicability.py
PASS purity:packages/proofops/domain/documents.py
PASS purity:packages/proofops/domain/errors.py
PASS purity:packages/proofops/domain/regulatory.py
PASS purity:packages/proofops/domain/rulepacks.py
PASS purity:packages/proofops/domain/rules/__init__.py
PASS purity:packages/proofops/domain/rules/engine.py
PASS purity:packages/proofops/domain/rules/goal.py
PASS purity:packages/proofops/domain/rules/management.py
PASS purity:packages/proofops/domain/rules/performance.py
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

### `uv sync --locked --dry-run`

Exit: 0

```text
Would use project environment at: .venv
Resolved 61 packages in 2ms
Checked 59 packages in 1ms
Would make no changes
```

### `pnpm install --frozen-lockfile --ignore-scripts`

Exit: 0

```text
Scope: all 2 workspace projects
Already up to date
Done in 114ms
```

### `uv build --package proofops --wheel --out-dir /tmp/proofops-task042-wheel`

Exit: 0

```text
Building wheel...
Successfully built /tmp/proofops-task042-wheel/proofops-0.0.0-py3-none-any.whl
```

### `uv run --no-sync python scripts/check_licenses.py --generate-sbom --env ENABLE_LEGACY_PYMUPDF=false`

Exit: 0

```text
supply-chain gate: PASSED
WARN: Human license/data-rights approvals and deployed-image verification remain separate release gates.
```

### `pnpm audit --audit-level high`

Exit: 0

```text
No known vulnerabilities found
(node:81974) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
(Use `node --trace-deprecation ...` to show where the warning was created)
```

## Subsequent hardening

A further failing check demonstrated a stale SBOM was still accepted after a
lock-file content change that preserved package versions (`-k lock_resolution`:
1 failed). The checker now requires matching SHA-256 values for both lockfiles;
this also binds package sources/integrities and prevents stale inventory reuse.
IAM execution checks additionally verify the exact ECR wildcard exception,
worker model allowlist and API exclusion even with configured model ARNs.
After this change the targeted suite has 40 tests.

The initial real Python audit ran these exact commands:

```sh
uv export --locked --no-emit-workspace --format requirements-txt --output-file /tmp/proofops-audit.txt
uv run --no-sync pip-audit --strict --no-deps --disable-pip -r /tmp/proofops-audit.txt
```

Export exited 0. Audit exited 1 with the following tool-reported findings
(9 advisory rows, including duplicate upstream IDs, in 2 dependencies):

```text
pytest    8.4.2   PYSEC-2026-1845 9.0.3
starlette 0.48.0  PYSEC-2026-161  1.0.1
starlette 0.48.0  PYSEC-2026-161  1.0.1
starlette 0.48.0  PYSEC-2026-248  1.3.0
starlette 0.48.0  PYSEC-2026-249  1.3.1
starlette 0.48.0  PYSEC-2026-248  1.3.0
starlette 0.48.0  PYSEC-2026-1942 0.49.1
starlette 0.48.0  PYSEC-2026-2281 1.1.0
starlette 0.48.0  PYSEC-2026-2280 1.1.0
```

The failure was escalated to the coordinator for shared dependency remediation;
no ignore list, suppressed exit status, or automatic fix was applied here.

Two further strict-type negative checks (`-k boolean_version`: 2 failed) caught
JSON/TOML booleans being accepted as format version `1` through Python equality.
They now fail closed, with the same acceptance assertions retained.

Latest scoped implementation checks (before the coordinator dependency refresh):

| Exact command | Exit / result |
| --- | --- |
| `uv run --no-sync pytest tests/acceptance/test_supply_chain.py -q` | 0; 42 passed in 0.88s |
| `uv run --no-sync ruff check packages/proofops/application/supply_chain.py scripts/check_licenses.py tests/acceptance/test_supply_chain.py` | 0; All checks passed |
| `uv run --no-sync mypy packages/proofops/application/supply_chain.py scripts/check_licenses.py` | 0; no issues in 2 source files |
| `pnpm audit` | 0; No known vulnerabilities found; Node DEP0169 warning |

The earlier recorded lint failure was a too-long `ponytail:` comment, since
shortened; no lint error was suppressed.

## Final dependency refresh and verification

Coordinator refreshed the shared lock after the audit finding: FastAPI 0.141.1,
Starlette 1.6.0, pytest 9.1.1, annotated-doc 0.0.5. No vulnerability suppression
was introduced. The following fresh commands supersede earlier implementation
snapshots and verify the final lock and artifacts.

### `uv run --no-sync python scripts/check_licenses.py --generate-sbom --env ENABLE_LEGACY_PYMUPDF=false`

Exit: 0

```text
supply-chain gate: PASSED
WARN: Human license/data-rights approvals and deployed-image verification remain separate release gates.
```

### `uv run pytest tests/acceptance/test_supply_chain.py -q`

Exit: 0

```text
..........................................                               [100%]
42 passed in 1.06s
```

### `uv run --no-sync ruff check packages/proofops/application/supply_chain.py scripts/check_licenses.py tests/acceptance/test_supply_chain.py`

Exit: 0

```text
All checks passed!
```

### `uv run --no-sync ruff format --check packages/proofops/application/supply_chain.py scripts/check_licenses.py tests/acceptance/test_supply_chain.py`

Exit: 0

```text
3 files already formatted
```

### `uv run --no-sync mypy packages/proofops/application/supply_chain.py scripts/check_licenses.py`

Exit: 0

```text
Success: no issues found in 2 source files
```

### `pnpm --dir apps/web exec tsc --noEmit --project ../../infra/cdk/tsconfig.json`

Exit: 0

```text
(no output)
```

### `uv run --no-sync pytest tests/unit/test_package_validation.py tests/contracts -q`

Exit: 0

```text
..............................                                           [100%]
=============================== warnings summary ===============================
tests/contracts/test_package_contracts.py::test_run_create_real_http_post
  <local-project-root>/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

tests/contracts/test_package_contracts.py::test_run_create_real_http_post
  <local-project-root>/.venv/lib/python3.12/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
30 passed, 2 warnings in 1.36s
```

### `uv build --package proofops --wheel --out-dir /tmp/proofops-task042-wheel`

Exit: 0

```text
Building wheel...
Successfully built /tmp/proofops-task042-wheel/proofops-0.0.0-py3-none-any.whl
```

### `uv export --locked --no-emit-workspace --format requirements-txt --output-file /tmp/proofops-audit.txt`

Exit: 0

```text
Resolved 62 packages in 3ms
```

### `uv run --no-sync pip-audit --strict --no-deps --disable-pip -r /tmp/proofops-audit.txt`

Exit: 0

```text
WARNING:pip_audit._cli:--no-deps is supported, but users are encouraged to fully hash their pinned dependencies
WARNING:pip_audit._cli:Consider using a tool like `pip-compile`: https://pip-tools.readthedocs.io/en/latest/#using-hashes
No known vulnerabilities found
```

### `pnpm audit`

Exit: 0

```text
No known vulnerabilities found
(node:87810) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
(Use `node --trace-deprecation ...` to show where the warning was created)
```

### Final SBOM schema and built-artifact inspection

Executed (exit 0):

```sh
uv run --no-sync python - <<'PY'
from pathlib import Path
from hashlib import sha256
import json
import zipfile
from cyclonedx.validation.json import JsonStrictValidator
from cyclonedx.schema import SchemaVersion
bom = Path('sbom.json')
assert JsonStrictValidator(SchemaVersion.V1_6).validate_str(bom.read_text(), all_errors=True) is None
print('CycloneDX 1.6 schema valid')
with zipfile.ZipFile('/tmp/proofops-task042-wheel/proofops-0.0.0-py3-none-any.whl') as archive:
    names = archive.namelist()
    assert not any('legacy_reference' in n or 'legacy_pymupdf' in n for n in names)
print('Built wheel excludes legacy research and unapproved legacy_pymupdf adapter')
for name in ('uv.lock', 'pnpm-lock.yaml', 'sbom.json'):
    print(name, sha256(Path(name).read_bytes()).hexdigest())
print('SBOM components:', len(json.loads(bom.read_text())['components']))
PY
```

```text
CycloneDX 1.6 schema valid
Built wheel excludes legacy research and unapproved legacy_pymupdf adapter
uv.lock 7ae72bac2859d2c1d8c9bbb2a434b2b000dab96fe87348a5e4e7ab8b7b059fb7
pnpm-lock.yaml c77052b1ae88f31c7f802f98ed4778d06f2b84b6e50e9204d41f6ef06b27c2ce
sbom.json 5b7064b72dd5a237d222ef01d54bc20d7950ae1888b6d122f7197d5b068ce2fa
SBOM components: 184
```

Final local implementation status: **passed** (42 acceptance + 30 relevant
unit/contract tests, scoped lint/format/type, real wheel build, SBOM schema and
inventory, secret signatures, Python and npm known-vulnerability audits).
Tests use temporary synthetic metadata and real filesystem/subprocess/compiler
behavior; no cloud/model mock stands in for executed behavior. TestClient and
Node deprecation warnings remain visible. Actual GitHub-hosted CI, browser E2E,
AWS CDK deployment/IAM enforcement, image scans, human rights/legal approvals,
and TASK-029 preflight HTTP integration remain **not_run** as scoped above.
