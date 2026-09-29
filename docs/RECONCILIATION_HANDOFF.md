# Developer A integration handoff — C1–C4 reconciliation

This document describes the Developer B core boundary delivered before the local
product integration. The subsequent authenticated HTTP, SQLite and React work is
specified in [RECONCILIATION_PRODUCT_CONTRACT.md](RECONCILIATION_PRODUCT_CONTRACT.md)
and operated using [RECONCILIATION_PRODUCT.md](RECONCILIATION_PRODUCT.md).
Sections below about A-owned future integration describe that earlier handoff;
the product guide takes precedence for the current local application. No real
accounting policy is approved by either delivery.

Companion operator guide: [`docs/RECONCILIATION.md`](RECONCILIATION.md).
Registry field reference: [`evidence/reconciliation/sol/INTERFACE.md`](../evidence/reconciliation/sol/INTERFACE.md).

---

## 1. What this feature decides, and what it must never decide

It checks whether two disclosures differ and whether an explanation for that
difference exists in the disclosed text. It does **not** judge accounting
treatment, and it does **not** touch the existing G/P/M evidence ladder. A
reconciliation result carries no `evidence_grade`, `label`, `sublabel` or
`confidence`, and the strict output schema forbids those keys outright.

Treat the reconciliation verdict as a **second, separate axis** shown next to the
existing grade, never folded into it.

---

## 2. The three callable layers

| Layer | Import | Role |
|---|---|---|
| Pure engine | `proofops.domain.reconciliation.engine.evaluate(packet, policy) -> dict` | Applies an approved policy to an **already verified** packet. Stdlib only, no I/O. |
| Verified boundary | `proofops.application.reconciliation.reconcile(packet, policy, *, source_reader, explanation_search, policy_registry=None, coverage_registry=None, document_registry=None) -> dict` | Verifies bytes, locators, quotes, document identity, policy approval and search coverage, then calls the engine. |
| Local reader | `proofops.adapters.reconciliation.FileSourceReader(root, artifacts)` | Root-constrained immutable file reader. Rechecks hash and containment on every read. |

**A should call `reconcile`, not `evaluate`.** `evaluate` trusts its input
completely; it is the pure core that the boundary protects. Calling it directly
with a client packet would skip every provenance check.

`proofops.adapters.dart` (`DartClient`, `ArtifactStore`, `build_collection_manifest`,
normalizers) is the collection side and is described in §9.

---

## 3. Versions: three different version fields, do not conflate them

| Field | Where | Value today | Meaning |
|---|---|---|---|
| `schema_version` | input, policy, result | `"1.1"` | The exchange contract. A v1.0 packet renamed to 1.1 is not a v1.1 packet. |
| `engine_version` | result | **varies — see below** | Which code produced the decision. |
| `projection_schema_version` | presentation companion | `"reconciliation-presentation-1"` | Separate display envelope, versioned independently. |

### `engine_version` is not a constant — this is the integration trap

Two different values appear in a normal 1.1 result, and both are confirmed by
executed probes in this repository:

- `"reconciliation-engine-1.2.0"` — the request reached the pure engine.
- `"reconciliation-application-provenance-1"` — the **application layer** blocked
  before the engine ran (unapproved policy, unverified source, document identity
  mismatch, unverified coverage, …).

Both versions can produce valid `execution_state: "blocked"` results with `status: null`. A must
not assume a single engine version string, and must not treat the
application-provenance value as a fault or a different schema.

---

## 4. Result contract: strict 1.1 versus presentation

### Strict result (schema `contracts/reconciliation/output.schema.json`)

Exactly fifteen keys, `additionalProperties: false`:

```
schema_version claim_id item execution_state status review_required
reason_codes source_ids explanation_source_id
sustainability_value financial_value
packet_sha256 policy_sha256 synthetic engine_version
```

- `execution_state` ∈ `completed | blocked | not_run`.
- `status` ∈ `matched | needs_explanation | not_applicable` **only when
  `completed`**; otherwise `null`. There is no fourth linkage label.
- `review_required` is `true` exactly when `execution_state != "completed"`.
  Confirmed against all eight published examples: `needs_explanation` is
  **not** review-required.
- `reason_codes` is a non-empty list of machine codes for aggregation. They are
  **not** public verdict labels — A maps them to safe Korean UI text.
- `sustainability_value` / `financial_value` echo the **raw** disclosed text, not
  the normalized form, and are `null` when the raw value is unknown.

Never add a field to this object. Anything extra belongs in a separate envelope.

### Presentation companion (separate object)

`evaluation.reconciliation_cli.project_result` emits
`projection_schema_version: "reconciliation-presentation-1"` with display text,
source refs and a fixed notice. It deliberately sets `basis: []` and
`confidence: null` because **no approved accounting basis exists**. A must not
populate `basis` from an unapproved draft.

### C5 is a different envelope entirely

`item: "C5"` raises `NotImplementedError` in both the engine and `reconcile`.
The CLI catches it and writes a **separate dispatch envelope**:

```json
{"dispatch_schema_version": "1.0", "item": "C5", "execution_state": "not_run",
 "status": null, "reason_codes": ["stage_disabled"], "synthetic": true}
```

That object is **not** a 1.1 result — it has different keys and would fail the
1.1 schema. A's caller must branch on it rather than parse it as a result, and
must not add `C5` to any 1.1 enum.

---

## 5. Trust boundary: what A must supply, and what it must never trust

The packet is **untrusted input**. Four operator-controlled inputs carry the
trust, and `reconcile` reads its decision facts from them, not from the packet:

| Input | Keyed by | Supplies |
|---|---|---|
| `document_registry` | `document_id` | identity, as-of/publication dates, period, artifact hash, item relevance, `decision_binding`, `source_bindings`, `fact_bindings` |
| `policy_registry` | canonical policy SHA-256 | the real approval record |
| `coverage_registry` | `receipt_id` | the real search state, reviewed sources, failed documents |
| artifact manifest | `document_id` | local path, format, expected SHA-256 for `FileSourceReader` |

Consequences A must design around:

- **`policy.approved: true` in the submitted policy means nothing.** Approval is
  taken only from `policy_registry`, matched by canonical policy hash, and the
  entry's `version`, `source_policy_sha256` and `synthetic_only` must all agree
  with the policy. No registry entry ⇒ `blocked / policy_unapproved`.
- **`search.state: "complete"` in the packet means nothing.** It is replaced by
  the coverage receipt. Absent receipt with a packet claiming `complete` ⇒
  `blocked / coverage_unverified`; absent receipt otherwise is rewritten to
  `not_run`. This is what stops "we did not find it" becoming "it is absent".
- **`sustainability` / `financial` values are replaced** by the registry's
  `fact_bindings` for that source. A mismatch is `fact_binding_mismatch`.
- **`decision_binding` pins the whole decision** — item, comparability, the full
  claim object, C3/C4 context and claim ID — to that document review. One
  document with several claims needs a **per-claim** review snapshot. Reusing a
  review because the document hash matches is exactly what this blocks.
- **Roles are explicit.** A source must carry the right role in
  `source_bindings` (`claim`, `sustainability_fact`, `financial_fact`,
  `c3_commitment`, `c3_funding`, `c4_definition`, `c4_calculation`,
  `explanation`). Correct text in the wrong role is `source_role_mismatch`.
- **Explanation candidates are verified too.** They must be inside the reviewed
  coverage set, pass byte/locator/quote verification and hold the `explanation`
  role. A model returns candidates; it never returns a status.

A local file interface does not authenticate an external document. In production
these four inputs must be backed by an authenticated store, a recorded
extraction review, a real policy approval and a real search execution record.

---

## 6. Source, decision and fact binding — the exact checks

`FileSourceReader` enforces: relative paths only, resolved path and symlinks
inside the root, manifest hash **and** ref hash checked on every read, 50 MiB
limit, and re-containment after read.

Supported locators (`packages/proofops/application/reconciliation/sources.py`):

| Format | Locator | Notes |
|---|---|---|
| `text` | `chars:<start>:<end>` | Unicode code points, end-exclusive |
| `xml` / `html` | `id:<element-id>` | duplicate IDs rejected; XML DTD rejected regardless of encoding |
| `pdf` | `page:<1-based-page>` | encrypted PDFs and non-extractable text fail closed |

The quote must occur **exactly** at that locator. There is no similarity match
and no normalization fallback. **PDF bounding boxes are never invented.**
Unsupported locator or format fails closed.

---

## 7. Immutability, hashes and reproduction

- `packet_sha256` / `policy_sha256` hash the **submitted** packet and policy —
  the client revision — not the registry-derived internal packet the engine saw.
  This is deliberate: the internal packet is a verification artifact, not a
  revision A can cite back to a user.
- Canonical hashing is `json.dumps(value, ensure_ascii=True, sort_keys=True,
  separators=(",", ":"), allow_nan=False)` encoded UTF-8, then SHA-256. This
  ASCII-escaped form is what the published handoff fixtures use; a
  `ensure_ascii=False` variant produces different hashes for any non-ASCII
  packet. Reuse the shipped helpers rather than re-implementing:
  `proofops.application.reconciliation.canonical_sha256` or
  `proofops.domain.reconciliation.engine.canonical_sha256` / `canonical_json`.
- **To reproduce a decision you need more than the packet and policy.** Retain
  the packet, the policy, all three registries, the artifact manifest and the
  explanation-candidate snapshot. Same packet + different registry ⇒ legitimately
  different result.
- The CLI writes results with exclusive create (`open(..., "xb")`) and refuses an
  existing output path. Results are new revisions; nothing is overwritten.

---

## 8. Approved policy versus synthetic policy

The shipped example policy is `version: "synthetic-demo-only"`,
`synthetic_only: true`, `current_stage: 1`, with `c3_threshold: null`,
`c3_account_mapping_approved: false` and `allowed_capex_account_ids: []`.

- A `synthetic_only` policy against a non-synthetic packet is
  `blocked / policy_scope_mismatch`. Flipping `synthetic` to `false` to dodge a
  guard is exactly what that check exists to stop.
- **C3 has no default multiple and no default account mapping.** The 5.0 figure
  in the specification is an illustration. REC-004 B keeps the threshold null,
  so threshold comparisons remain `blocked / c3_policy_unapproved`. A registered,
  source-verified matching investment commitment can complete as
  `matched / commitment_disclosed` without a threshold. Both paths still require
  approved account mapping and an allowlist covering requested CAPEX accounts,
  plus the currency, period, amount and authenticated policy guards.
- Everything in `config/accounting/` is `status: draft`,
  `verification_status: unverified`, `approved: false`, with
  `account_mapping: null` and C5 `execution: disabled`. These drafts are **not**
  loaded by any executing code. Do not wire them in.

---

## 9. DART collection is original-document capture, not fact extraction

`proofops.adapters.dart` and `evaluation.reconciliation_collect` pin
corp code, fiscal year, receipt number and consolidation basis, then store the
**received bytes** with their SHA-256 plus a `collection-1` manifest recording
UTC fetch time. Responses are not re-serialised. `not_available` (no such data)
is distinguished from `failed` (auth, rate limit, identifier or transport error).

**A successful collection means a document was captured. It does not mean a fact
was extracted, a policy was approved, or a C1–C4 item was decided.** The path
from stored bytes to a `fact_bindings` entry is a reviewed extraction step that
an operator must perform and record.

Collection requires `DART_API_KEY` in the environment and makes real network
calls. Keys never appear in arguments, filenames, logs or exception text.

---

## 10. A / B responsibility boundary

| Owned by B (implemented) | Owned by A (not implemented here) |
|---|---|
| `evaluate`, `reconcile`, `FileSourceReader`, DART adapters, the two CLIs | HTTP routes, DTOs, persistence, revisions, UI |
| Deciding `execution_state` / `status` / `reason_codes` | Mapping `reason_codes` to Korean UI text |
| Verifying bytes, locators, quotes, identity, coverage | Producing the registries from authenticated systems |
| Refusing unapproved policy | Obtaining and recording the real approval |
| Blocking C5 | Rendering C5 as a separate not-run stage |
| — | Keeping G/P/M grades unchanged when a linkage result changes |

B does not modify G/P/M grading code, and a reconciliation result must not alter
an existing grade, label or decision revision.

---

## 11. Injection example (executed, offline, no network)

Generate explicitly synthetic originals with real bytes and hashes, then run the
verified path. Output directories must be new.

```powershell
$env:PYTHONPATH = 'packages'
uv sync --locked
uv run --no-sync python -m evaluation.reconciliation_fixtures --output .local/reconciliation-example
$case = '.local/reconciliation-example/c1-same-entities'
uv run --no-sync python -m evaluation.reconciliation_cli `
  --packet "$case/packet.json" --policy "$case/policy.json" `
  --artifacts "$case/artifacts" --artifact-index "$case/artifacts.json" `
  --documents "$case/documents.json" --policy-registry "$case/policies.json" `
  --coverage-registry "$case/coverage.json" `
  --output "$case/result.json" --projection "$case/presentation.json"
```

Equivalent in-process call, which is the shape A should wire:

```python
import json
from pathlib import Path

from proofops.adapters.reconciliation import FileSourceReader
from proofops.application.reconciliation import reconcile

case = Path(".local/reconciliation-example/c1-same-entities")


def read(name):
    return json.loads((case / name).read_text(encoding="utf-8"))


reader = FileSourceReader(case / "artifacts", read("artifacts.json"))
result = reconcile(
    read("packet.json"),
    read("policy.json"),
    source_reader=reader,
    explanation_search=lambda packet: [],      # verified candidates, never a status
    policy_registry=read("policies.json"),
    coverage_registry=read("coverage.json"),
    document_registry=read("documents.json"),
)
```

Historical executed result for `c1-same-entities` (synthetic input, engine 1.1.0):

```json
{"schema_version": "1.1", "claim_id": "fixture-claim", "item": "C1",
 "execution_state": "completed", "status": "matched", "review_required": false,
 "reason_codes": ["same_verified_entity_set"],
 "source_ids": ["sr-scope", "fs-scope"], "explanation_source_id": null,
 "sustainability_value": "가상법인 A, B", "financial_value": "가상법인 A, B",
 "packet_sha256": "659b7013b18a06b1e5521ce1929b73284e1f2da0eb23981bdb678e23f1bdba57",
 "policy_sha256": "478295c4467dfdd6cbb8d2bc12f37273ddd7ad697398e02f9a9d0ba778b43859",
 "synthetic": true, "engine_version": "reconciliation-engine-1.1.0"}
```

All eight generated cases were executed through the CLI with `FileSourceReader`
and exited 0, covering `matched`, `needs_explanation`, `not_applicable` and
`blocked`.

CLI exit codes: `0` for any valid meaning including `blocked`, `2` for malformed
input or an output conflict, `3` for collection or internal error. A **blocked
result is exit 0** — it is an answer, not a crash.

### Delivery shape: source overlay, not a wheel-only CLI

The intended delivery is a **bounded Developer B source overlay** (ZIP with a
manifest and a base commit) applied to an existing checkout of this repository,
verified against a clean baseline snapshot. It is **not** a wheel-only CLI drop.
Apply the overlay at the recorded base commit and run the release verifier
(§12) in that tree.

The installed wheel is the *library* surface for an A service that imports the
package at runtime. Both paths are supported and both were exercised; they cover
different things, so do not substitute one for the other:

| | Source overlay onto the repo | Installed wheel |
|---|---|---|
| Contains | engine, application, adapters, **plus** `evaluation/*` CLIs, tests, scripts | engine, application, adapters, bundled schemas |
| Gives you | the two CLIs, fixture generator, release verifier, test suite | `reconcile`, `FileSourceReader`, DART adapters, `validate_schema` |
| Missing | — | `evaluation.*`, the C5 dispatch envelope, the presentation projection |
| Use for | verification, reproduction, release checks | embedding the decision path in an A service |

### Installed-wheel integration

The wheel built from `packages/proofops` contains
`proofops/domain/reconciliation/**`, `proofops/application/reconciliation/**`
including the three bundled `schemas/*.schema.json`, `proofops/adapters/reconciliation/**`
and `proofops/adapters/dart/**`.

This was verified against master's freshly built wheel, outside the repository:
fixtures generated into a directory outside the checkout, the wheel extracted to
a scratch directory, and the run performed under `python -I` with the extracted
wheel as the **sole** `sys.path` entry. Every module — `service`, `schema`,
`files`, `engine`, `dart.client` — was asserted to resolve inside the wheel and
**not** inside the repository. All eight cases then ran through `reconcile`, with
the bundled `validate_schema` checking input, policy **and** output, producing
the same semantics as the in-repo run with Korean text and hashes intact.

`evaluation.*` is **not** in the wheel. The two CLIs and the fixture generator
are repository tools. An A service consuming the installed package must call
`reconcile` directly and implement its own invocation, output and C5 branching —
including the C5 dispatch envelope, which today exists only in the CLI.

---

## 12. Release verification

Sol's offline release checker exists and takes a new evidence directory:

```powershell
uv sync --locked
uv run --no-sync python scripts/verify_reconciliation.py --output .local/reconciliation-release
```

Exit `0` when the checks pass, `1` when they do not, `2` for invalid arguments.
An existing `--output` path is rejected. Run it in an overlay-applied checkout;
the verifier needs `evaluation/*`, `scripts/` and `tests/`, which the wheel does
not carry.

Type checking is platform-sensitive: the CI Linux target passes, while a native
Windows run reports POSIX-typing errors **outside** the Developer B lane. Judge
this lane on the CI Linux result, not on a local Windows mypy run.

---

## 13. Current product status and remaining live work

The local product integration now extends the original library handoff. Follow
[RECONCILIATION_PRODUCT.md](RECONCILIATION_PRODUCT.md) for current commands and
[RECONCILIATION_PRODUCT_CONTRACT.md](RECONCILIATION_PRODUCT_CONTRACT.md) for the
API, persistence, revisions and rollback contract.

1. **Authenticated local review.** SQLite persistence and authenticated review,
   policy approval, evaluation and source-download routes now exist. Imported
   registries do not grant approval; the server binds cases to verified claims
   and records reviewer actions. Production document/search-store integration
   and evidence of exhaustive real-company search remain separate work.
2. **Real policy approval.** Project-level REC-001 through REC-008 were adopted
   on 2026-09-28; this does not create authenticated runtime approval or mapping
   values. The shipped example policies remain `synthetic_only`. Real C3 needs
   approved account mapping and an allowlist; threshold comparisons stay blocked
   while the adopted threshold is null. A verified matching commitment may take
   the direct-existence path described in section 8.
3. **Live DART collection.** One authenticated Samsung FY2024 annual-report
   collection was verified on 2026-09-21, including JSON, document ZIP and XBRL
   ZIP. This is a collection smoke test. The 98% retrieval-success and sub-5%
   false-positive figures remain targets, not measurements.
4. **Extraction from collected originals.** Candidate preparation and explicit
   operator selections now produce review drafts with original/derived hashes.
   They do not automatically prove semantic correctness, complete search or
   approve accounting policies. The real-company C2 draft remains blocked.
5. **A-side surface.** The local API, React workspace, DTOs, SQLite persistence,
   revisions and integration contracts are implemented. Deployment and review
   of developer A's independent branches remain separate from this handoff.
6. **Held-out evaluation.** No accuracy, precision/recall or block-rate
   measurement on unseen companies has been run.
7. **Open domain decisions.** `DEC-C1`, `DEC-C3`, `DEC-STATUS`, `DEC-PERIOD`,
   `DEC-INDUSTRY` and `GAP-001…010` remain open; the conservative behaviour
   (exact set identity, blocked-on-unresolved) is the interim implementation,
   not an approved resolution.
8. **Operational concerns.** Throughput and retry/backoff under real rate limits
   have not been load-tested. Production credential rotation, retention of
   collected originals and deployment remain open.
