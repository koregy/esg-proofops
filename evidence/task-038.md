# TASK-038 — 업로드 격리 / SEC-002 / AT-038

2026-09-09 KST. Implemented the assigned verification function, explicit synthetic
local immutable promotion adapter, and executable CloudFormation resource builder.
No commit/push, AWS mutation, model call, customer processing or shared contract edits.

## Owned changes

- `packages/proofops/application/uploads_security.py`
- `infra/cdk/lib/compute-stack.ts`
- `tests/acceptance/test_upload_security.py`
- `evidence/task-038.md`

The coordinator owns dependency/lock/CI changes. The already reviewed
`proofops[parsing]` extra provides pinned pypdf 6.18.0 through proofops-worker;
see `evidence/dependencies-task038.md`. No new dependency was introduced by this worker.

## Behavior and integration boundary

`verify_quarantined_pdf(QuarantinedPdf, PdfLimits, *, tenant_id) -> VerifiedPdf`
raises code-only `UploadRejected` on rejection. Supply the authenticated session's
tenant and server-owned, immutable quarantine snapshot. The storage caller must
bound its object read by `max_bytes` before constructing that snapshot; the function
checks the actual bytes against the expected SHA-256 and size, then PDF magic,
structure, encryption, pages, object budget and decompressed-stream budget.
Tenant/document-version UUID validation reuses the existing domain helper. The S3
object version is an opaque bounded string, never a path. Verified output retains
all original bytes, hash, tenant, document version and object version.

Each inspection runs in a separate Python process, with shell=False, an argument
list, generated private scratch filename, isolated interpreter, scrubbed inherited
environment and suppressed PDF/parser stderr. No viewer, JavaScript evaluator,
remote fetcher, OCR or product model is called. Ordinary URI annotations are kept
as inert metadata; the real localhost listener test confirms zero requests. Embedded
JS, Launch, automatic actions, embedded files, XFA and external stream files are
rejected. Indexed objects are traversed, including indirect and compressed objects;
streams are bounded and unsupported filters fail closed. pypdf's external JBIG2
binary invocation is explicitly disabled.

Wall timeout kills and reaps the child. CPU limits apply on macOS/Linux. Linux adds
hard address-space and process limits before any untrusted PDF parse; trusted parser
imports occur first so a too-small memory budget yields resource rejection rather
than a misleading dynamic-library/PDF error. macOS uses a sampled RSS watchdog
because its RLIMIT_AS setter rejects the requested cap; this is explicitly weaker
than kernel isolation and is not presented as a production sandbox.

`LocalUploadVault` is explicitly **synthetic local filesystem storage**, not S3.
It stages/fsyncs bytes and atomically links a read-only original file create-only;
identical retries succeed and conflicting bytes never overwrite an existing document
version. Rejected input never reaches the original vault. Its root must be private
to the service. TASK-001 owns durable Upload state, authentication/expiry/CAS, version
manifest persistence, the production S3 adapter, and POST complete route wiring.

`buildQuarantineCompute` returns executable CloudFormation resources without CDK/AWS
SDK dependencies or deployment. It requires caller-supplied image digest, execution
role, VPC and reviewed endpoint identifiers: no invented account or image defaults.
The Fargate definition has non-root UID, read-only root, bounded noexec/nosuid/nodev
scratch, CPU/memory limits, dropped capabilities and init process; no application
TaskRoleArn is granted. Security-group egress is a nonempty HTTPS-only list of supplied
AWS endpoint SGs and S3 prefix list, with no arbitrary IPv4/IPv6 CIDR egress. Deployment
must attach this SG to private task ENIs and verify endpoint policies, DNS controls,
image entrypoint/platform support and resource limits. Resource synthesis alone is
not an AWS enforcement test; local Python alone does not enforce host network egress.

No domain grades/labels/tags or decision/revision contracts were changed. Rejection
codes never convert unreadable/unknown into absent evidence. Public API and DB schemas
are unchanged, so no schema migration is required. Rollback is removal of these new,
not-yet-wired functions/resources; deployed task revision/SG rollback remains TASK-044.

## Failures observed before fixes

1. `uv run pytest tests/acceptance/test_upload_security.py -q`: exit 2,
   `ModuleNotFoundError: proofops.application.uploads_security` (acceptance tests first).
2. First implementation: 5 failed / 16 passed. Direct diagnostic identified an
   incorrect pypdf import name; inspected installed source and used `apply_configuration`.
3. Infrastructure-only acceptance case: failed for missing compute-stack.ts before
   adding the resource builder. One initial test authoring NameError was corrected
   before confirming the missing implementation failure.
4. Opaque S3 version regression: failed because legitimate `+/=` characters were
   rejected; replaced filesystem-style validation with bounded opaque-string validation.
5. Coordinator important review corrected ordinary URI behavior: actual Link annotation
   test first failed with PDF_INVALID, then passed after preserving inert URI metadata.
   Embedded JS/Launch rejection assertions remain intact.
6. First Linux container run: valid/JS/encrypted/pages/bomb passed; memory failed with
   PDF_INVALID. Direct child stderr on synthetic input proved an RLIMIT_AS-induced
   shared-library mapping ImportError. Moving trusted imports before the address-space
   cap and checking existing virtual-memory size fixed resource classification.

## Final exact commands and results

| Command | Actual result |
|---|---|
| `uv run pytest tests/acceptance/test_upload_security.py -q` | exit 0, **25 passed** (1.41s final run) |
| `uv run --no-sync ruff check packages/proofops/application/uploads_security.py tests/acceptance/test_upload_security.py` | exit 0, All checks passed |
| `uv run --no-sync ruff format --check packages/proofops/application/uploads_security.py tests/acceptance/test_upload_security.py` | exit 0, 2 files already formatted |
| `uv run --no-sync mypy packages/proofops/application/uploads_security.py` | exit 0, no issues in 1 source file |
| `pnpm --dir apps/web exec tsc --noEmit --project ../../infra/cdk/tsconfig.json` | exit 0, no diagnostics |
| `uv run --no-sync pytest tests/unit tests/contracts -q` | exit 0, **45 passed**, 2 existing Starlette/httpx deprecation warnings |
| `uv run --no-sync python scripts/verify_architecture.py` | exit 0, purity/DTO/ports/composition/contracts passed |
| `uv build --package proofops --out-dir /tmp/proofops-task038-dist` | exit 0, sdist and wheel built |
| `git diff --check` | exit 0, no whitespace errors |

Acceptance coverage includes actual pypdf-written PDFs, actual bounded child processes,
actual local sockets, actual filesystem promotion/retry/conflict behavior, and actual
Node execution of TypeScript resource builder with unsafe-input rejection. No mocks
replace PDF parsing, limits, storage or policy generation. The pure-function route
allowed by AT-038 is tested; these tests do not claim the future HTTP route exists.
No full in-flight acceptance suite was rerun, per coordinator instruction.

### Linux isolation integration / security verification

An existing local image was used with its entrypoint overridden; no image was pulled.
Its older pypdf was overridden read-only with the workspace's exact pinned pypdf 6.18.0.
The script asserts that version. Linux Python reported 3.12.14; the host was macOS
Python 3.12.13. These are synthetic input runs, not product image certification.

Final command (exit 0):

```sh
docker run --rm --pull never --network none --read-only --user 10001:10001 --memory 512m --cpus 1 --pids-limit 32 --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,noexec,nosuid,nodev,size=268435456 --mount type=bind,src=<local-project-root>/packages/proofops,dst=/verification/proofops,readonly --mount type=bind,src=/tmp/proofops-task038-linux-check.py,dst=/verification/check.py,readonly --mount type=bind,src=<local-project-root>/.venv/lib/python3.12/site-packages/pypdf,dst=/app/.venv/lib/python3.12/site-packages/pypdf,readonly --entrypoint python esg-proofops-runtime:sealed -I /verification/check.py
```

Actual output:

```text
PASS valid
PASS js
PASS encrypted
PASS pages
PASS bomb
PASS memory
PASS timeout
```

## not_run / deployment gates

Coordinator reran the 25 upload-security acceptance checks, focused Ruff/mypy and
the infrastructure TypeScript check; all exited 0. Local verification is accepted.
The subsequent default-limit regression first failed for an actual 301-page PDF:
the verifier default was 1000 despite both config/limits.yaml and config/parsing.yaml
setting 300. The default now matches 300, and the focused acceptance suite passes
**26 tests**. A caller can still supply an explicitly configured PdfLimits object.

- AWS Fargate provisioning and enforcement, versioned S3 promotion, VPC endpoint/DNS
  verification, customer/data-rights/legal approvals: **not_run**; human/deployment
  gates remain blocked as applicable.
- Product model/AWS calls: **not_run**, neither required nor authorized.
- Browser E2E and HTTP upload-complete integration: **not_run**, later TASK-001/UI
  wiring is outside assigned scope; AT-038 explicitly permits function invocation.
- Full converter/OpenDataLoader behavior, antivirus certification and corpus accuracy:
  **not_run**; this is bounded PDF verification, not a malware-free guarantee.
- Cloud/task resource policy synthesis and Linux local container enforcement were
  tested separately; neither is labeled production completion.

## Sources inspected

- Repository docs 00, 26, 27, 28, 31, 19; original v2 domain source; TASK-038/AT-038;
  docs 11/09 and connected upload DTO, storage, dependency/task contracts.
- Installed pinned pypdf source (`_configuration.py`, `_reader.py`, `filters.py`) and
  [official configuration API](https://pypdf.readthedocs.io/en/latest/modules/configuration.html)
  for decompression limits and disabling external JBIG2 invocation.
- [AWS Fargate task parameters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html)
  for supported resource/container controls and
  [AWS tmpfs announcement](https://aws.amazon.com/about-aws/whats-new/2026/01/amazon-ecs-tmpfs-mounts-aws-fargate-managed-instances/)
  for current Fargate tmpfs support. No cloud deployment was inferred from these sources.

## Reproducible Linux check script

Saved during the run at `/tmp/proofops-task038-linux-check.py`; exact final content:

```python
import sys
sys.path.insert(0, '/verification')
from proofops.application.uploads_security import PdfLimits, QuarantinedPdf, UploadRejected, LocalUploadVault
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid5, NAMESPACE_DNS
from pypdf import PdfWriter, __version__
from pypdf.generic import DecodedStreamObject, NameObject
assert __version__ == '6.18.0'

def pdf(kind):
    writer = PdfWriter()
    for _ in range(2 if kind == 'pages' else 1):
        writer.add_blank_page(width=600, height=800)
    if kind == 'js':
        writer.add_js('app.alert("never executed")')
    if kind == 'encrypted':
        writer.encrypt('secret')
    if kind == 'bomb':
        stream = DecodedStreamObject()
        stream.set_data(b' ' * 2_000_000)
        writer.pages[0][NameObject('/Contents')] = writer._add_object(stream.flate_encode())
    result = BytesIO()
    writer.write(result)
    return result.getvalue()

cases = [
 ('valid', PdfLimits(), None),
 ('js', PdfLimits(), 'PDF_INVALID'),
 ('encrypted', PdfLimits(), 'PDF_PASSWORD_REQUIRED'),
 ('pages', PdfLimits(max_pages=1), 'UPLOAD_LIMIT_EXCEEDED'),
 ('bomb', PdfLimits(max_decoded_bytes=1024), 'UPLOAD_LIMIT_EXCEEDED'),
 ('memory', PdfLimits(memory_bytes=1), 'UPLOAD_LIMIT_EXCEEDED'),
 ('timeout', PdfLimits(timeout_seconds=.000001), 'UPLOAD_LIMIT_EXCEEDED'),
]
for kind, limits, code in cases:
    data = pdf(kind)
    source = QuarantinedPdf('00000000-0000-4000-8000-000000000001', str(uuid5(NAMESPACE_DNS, kind)), 'object-version', data, len(data), sha256(data).hexdigest())
    vault = LocalUploadVault(Path('/tmp/vault'))
    try:
        result = vault.verify_and_promote(source, limits, tenant_id=source.tenant_id)
    except UploadRejected as error:
        assert str(error) == code, (kind, str(error), code)
        assert not (Path('/tmp/vault/original') / source.tenant_id / f'{source.document_version_id}.pdf').exists()
    else:
        assert code is None, kind
        assert vault.original_path(result).read_bytes() == data
    print('PASS', kind)
```
