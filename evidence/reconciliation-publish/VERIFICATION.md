# Developer B publication verification — 2026-09-21

This change delivers C1–C4 rules, original-source verification, blocked outcomes
for unresolved policy, DART collection/preparation, CLI tools and a local
authenticated review API/SQLite/React workflow. C5 remains disabled and the
existing G/P/M grading rules are unchanged.

## Local validation

Windows, Python 3.12, Java 21; Korean fixtures require `PYTHONUTF8=1`.

- `scripts/verify_reconciliation.py`: all 15 release gates passed, including
  677 tests and eight synthetic CLI scenarios. This run preceded the final
  Unicode download regression test and a platform-guard refactor.
- Actual Java parsing and local tagging: 32 passed; the three previous Windows
  failures are resolved by pre-execution Job Object containment and a host-correct
  child environment.
- Final Windows parser containment tests: 27 passed. Final HTTP tests: 38 passed,
  including a Korean document filename download and original-byte digest check.
- Existing unit/contract/staging checks: 185 passed, seven optional legacy skips.
- Final Linux-target mypy: 205 source files passed.
- Exact staged-source export: Ruff lint and formatting passed; supply-chain
  license/secret gate passed; documentation/contract validator passed 871 checks.
  The local DART key was checked against staged blobs without printing it.
- Earlier delivery checks passed web typecheck/build, composed API/SQLite browser
  flow and 16 isolated UI boundary checks. Browser evidence is local; this does
  not claim a deployed production test.

The initial whole-workspace formatting failure concerned an unpublished local
ZIP-building script. The published tree passes formatting independently. The
initial Linux-target type errors in Windows-only initialization were fixed and
rechecked. A focused HTTP run without UTF-8 mode failed during fixture loading;
the documented UTF-8 invocation passed. Failed/intermediate logs remain local.

## Review boundaries

### CI failures diagnosed and corrected

Additional CI diagnostics identified two concrete causes after the initial local
checks: Windows output sampling raced with the JVM deleting a temporary PDF;
Ubuntu invoked the macOS-only Apple Vision Swift script, so a cold compilation
timeout and a subsequent unavailable-framework error produced different receipts.

Output accounting now takes one stat per entry and ignores only files already
removed. Permission errors still fail. Windows cleanup retries temporary handle
release failures for at most 15 seconds, and validates the exact resolved parent
and generated `.parse-` name before removing scratch data. Tests cover a real
descendant holding a file, cleanup deadlines and refusal to delete an unowned
directory. Process reaping runs even if job termination reports an error.

Non-macOS native OCR now returns a deterministic unresolved/UnsupportedPlatform
receipt without invoking Swift. Original-byte/receipt comparison is unchanged;
no unresolved source is promoted. The macOS reader path is unchanged. Intel
macOS investigation was stopped at the user's request and remains out of scope.
Native paragraph receipts pin the verifier source hash: receipts from an older
verifier are not rewritten or silently accepted by the changed verifier. Retain
the matching software version for historical replay or create a new verification
run while preserving the old receipt.

The final focused Windows regression passed 66 tests, including real Java
parsing/tagging, resource containment and the new race/platform cases. The
native source/paragraph suite also passed locally (the Apple Vision positive
case is platform-specific). Linux-target mypy passed for 205 source files.

Claude Opus handled the Windows parser lane under Orca supervision. Master
review found and corrected suspended-child cleanup, failure injection, child-only
resource limits, post-exit output enforcement and Linux-target typing issues.
Windows uses a strict configured memory cap; a kernel-denied allocation can
surface as `PARSER_FAILED`, not necessarily `PARSER_MEMORY_LIMIT`. This is process
resource containment, not a complete filesystem/network sandbox.

An authenticated Samsung FY2024 DART collection and original hashes were checked
locally. The real-company draft remains blocked pending human source review and
policy approval. C3 thresholds/account mapping, open domain decisions, held-out
accuracy evaluation and production deployment are not completed by this PR.

CI runs the offline release checks and actual Java parser regression on Ubuntu,
Windows and macOS 15 (Apple Silicon). Intel macOS verification was excluded
from this handoff at the user's request. Java 21 is installed for the
runner's architecture. The PR checks and linked Actions runs are the authoritative
remote result for each commit; this document records local evidence only.

No API key, local environment file, raw company report, generated delivery ZIP,
or historical audit directory belongs to this publication.

# 2026-09-22 follow-up — real-source validation on the integrated main

This section records the B-F01/B-F02/B-F03/B-F04 follow-up. It does not replace
or rewrite anything above it. **Collection success, fact extraction, policy
approval, a real-company verdict, independent accuracy and production readiness
are separate results and are reported separately below.**

## Baseline, branch and environment

Work happened in a new worktree on branch `koregy/developer-b-followup-20260922`,
created from `origin/main` at `ca75df592345fb412cd1593c29a0f5c506f81fe3`.
`git merge-base --is-ancestor ca75df5 origin/main` returned 0 and `origin/main`
is that exact commit. At the time the worker wrote this section the changes were
uncommitted. The coordinator then committed them on 2026-09-22 (KST) in two steps
on the same branch: the adapter fix and its regressions in
`04b8a39b3c6d1e584d2338234c7f21adcf53a225` (the last commit whose contents the
gate table below was executed against), followed by this report, the B-F02
proposals and the regenerated package-validation evidence in the commit that
carries this sentence. Integration into `main` remains the coordinator's step.
Changed files:

```
 packages/proofops/adapters/dart/candidates.py   | 246 +++++++++++++++-
 tests/reconciliation/test_candidates.py         | 285 +++++++++++++++++-
 evidence/reconciliation-publish/VERIFICATION.md | 334 +++++++++++++++++++
 docs/DEVELOPER_B_POLICY_PROPOSALS.md            | new (B-F02 proposals, unapproved)
```

The adapter diff is confined to the text-projection parser, the new row-span
helper and the catalogue bounds; no other region of the file moved.

Environment: Windows 11 Pro 10.0.26200.9457; Python 3.12.13 in a checkout-local
`.venv` built by `uv sync --locked` (no `.venv` shared with another checkout);
Java 21.0.2 as the default `java`, so `PROOFOPS_TEST_JAVA` was not needed;
Node v22.23.2 through fnm; pnpm 10.0.0; `PYTHONUTF8=1` for every Python command.
Playwright is not a repository dependency: `playwright-core` 1.56.1 was installed
outside the repository and selected with `PROOFOPS_PLAYWRIGHT_MODULE`, driving the
installed Edge through `PROOFOPS_BROWSER_CHANNEL=msedge`.

**Application external API usage this session: 0 paid model calls, 0 AWS calls,
0 new DART requests, 0 cost.** That accounting covers the product's own outbound
calls only; it is not a claim that the assistant subscription used to do the work
is free. The existing
Samsung FY2024, Ottogi FY2024 and Dongsuh FY2024 collections were re-used read-only
from the original checkout; no original bytes, database or key are published here.

## Real originals actually re-verified (B-F01 step 1)

Every artifact below was re-hashed in this run and matched its manifest.

| Source | Identity | SHA-256 (prefix) | Bytes | Result |
|---|---|---|---|---|
| Samsung statements JSON | `00126380` FY2024 `20250311001085` CFS | `02ba3366` | 106,589 | match |
| Samsung document ZIP | same | `606ffc72` | 676,026 | match |
| Samsung XBRL ZIP | same | `695395de` | 759,807 | match |
| Samsung SR PDF | `samsung-sr-2025-kor`, 87 pages, published 2025-06-27 | `342a99a1` | 5,649,767 | match |

The SR is the 2025-published report whose page 86 states the measured period as
"2024년 1월1일부터 2024년 12월 31일까지"; the financial side is FY2024 filed
2025-03-11. The publication year was not used as the fiscal year. The issuer's own
disclosure listing for that report is
<https://www.samsung.com/sec/ir/reports-disclosures/public-disclosure/3587/>; the
`2025-06-27` publication date recorded in the SR source manifest comes from that
page, not from the local download timestamp.

Candidate preparation was then re-run from those originals with
`evaluation.reconciliation_prepare` into fresh output directories. Every candidate
source in each catalogue was independently re-read through the shipped
`FileSourceReader` and re-checked for exact quote, locator and artifact hash:

| Company | Candidates | Re-verified | Failures |
|---|---|---|---|
| Samsung `00126380` / `20250311001085` | 2,001 (2,000 DART + 1 operator SR) | 2,001 | 0 |
| Ottogi `00141529` / `20250318000979` | 2,000 | 2,000 | 0 |
| Dongsuh `00144395` / `20251210000195` | 2,000 | 2,000 | 0 |

Total 6,001/6,001. These counts are the **pre-row-span catalogue**; the row-span
work described under B-DEF-01 below adds new candidates on top of them and does
not change any of the 6,001.

Four real-byte probes were run: **three negative probes, all rejected, plus one
positive control that still verified** — a single flipped byte in a copied derived
artifact → `artifact_hash_mismatch`; an Ottogi source read through Samsung's
artifact manifest → `document_not_manifested`; a locator shifted by one character
→ `quote_mismatch`; and the untouched source as the control → verified. The
control is not a fourth rejection.

**Search coverage is not complete and is not claimed to be.** An independent count
with the shipped projection parser found 59,053 text elements across the 3 XML
members of the Samsung document ZIP, 19,070 across the 6 XBRL members, plus 213
statement rows — about 78,336. The catalogue keeps 2,000 of them (document 894 /
XBRL 893 / statements 213), roughly 2.6%, and reports `truncated: true` with
`trust.search_complete: false`.

## Real case table (B-F01/B-F03)

Expected values below were written by this AI developer from the contract text and
are **AI review material (silver), not independent gold**. No case was registered
in a product database, because no verified developer A claim exists for these
companies (see "Missing developer A input"). Verdicts were produced by
`evaluation.reconciliation_cli`, which calls the application `reconcile`; the pure
`evaluate` entry point was not called directly for any of these results.

| case_id | 기업 | FY | 접수번호 | SR 버전 | C항목 | 개발/평가 구분 | 기대 execution_state/status | 실제 execution_state/status | reason_codes | 원문 참조 | 기대값 검토 출처 | 결함/외부입력/정상보류 | 처리시간 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RSV-20260922-SEC-C2 (legacy offline placeholder probe) | 삼성전자 | 2024 | 20250311001085 | `samsung-sr-2025-kor` `342a99a1` | C2 | 개발 중 사용 | blocked / null | blocked / null | `policy_unapproved` | SR page:86 quote; DART cover `사업연도` row, derived `chars:304:597` | B 기술 검토 (AI, silver) | 정상보류 + 외부입력 (정책 미승인 **및** 등록 claim·source 전제 미충족) | prepare 59s + reconcile 1s |
| RSV-20260922-SEC-C2-REGISTERED | 삼성전자 | 2024 | 20250311001085 | `samsung-sr-2025-kor` `342a99a1` | C2 | 평가 | not_run | **not_run** | — | — | — | 외부입력 (등록 claim 없음) | — |
| RSV-20260922-OTT-PREP | 오뚜기 | 2024 | 20250318000979 | 없음 | — | 이전 턴 열람 (reuse) | prepare 성공, 판정 not_run | prepare exit 0, 판정 not_run | — | 2,000 후보 전부 byte/locator 재검증 | B 기술 검토 (AI, silver) | 외부입력 (SR·claim 없음) | 105s |
| RSV-20260922-DSH-PREP | 동서 | 2024 | 20251210000195 | 없음 | — | 이전 턴 열람 (reuse) | prepare 성공, 판정 not_run | prepare exit 0, 판정 not_run | — | 2,000 후보 전부 byte/locator 재검증 | B 기술 검토 (AI, silver) | 외부입력 (SR·claim 없음) | 56s |
| RSV-20260922-DSH-REJECT | 동서 | 2024 | 20250313000532 | 없음 | — | 이전 턴 열람 (reuse) | 거부 (수집 실패) | exit 2, `collection_identity_or_status_mismatch`, 출력 폴더 미생성 | — | manifest의 `statements` 아티팩트 `collection_or_identity_failed` | B 기술 검토 (AI, silver) | 정상보류 (수집 실패) | <1s |

Ottogi and Dongsuh were already inspected by master/Sol in the previous turn at
`060c69b`. They are **re-used material, not a fresh independent hold-out**, and
their exact-source matches are not semantic C1–C4 gold. No genuinely unused
company was evaluated in this follow-up.

The Samsung row is a **legacy offline placeholder probe**, not a registered case.
Its `tenant_id`/`company_id`/`claim_id` are placeholder UUIDs carried over from the
earlier offline probe (`aaaaaaaa-…`, `bbbbbbbb-…`, `cccccccc-…`). The probe proves
the engine path, nothing about a real company. **Evaluation of a registered claim
is `not_run`**, and the policy gate is not the only thing missing: there is no
registered `run_id`/`claim_id`, no claim-side verified source binding and no
reviewed snapshot for this company, so even an approved policy would not let this
case be evaluated today.

Reproducibility: `prepare` + `reconcile` were run three times from the same
originals into three fresh directories. All three produced a byte-identical result
object, `packet_sha256 6b13086c…`, `policy_sha256 bcd010a0…`.

## Denominators

- Cases with an evaluable expected **semantic** result (matched /
  needs_explanation / not_applicable): **0**. Agreement rate: **N/A (denominator 0)**.
- Wrong `needs_explanation` among cases expected not to need an explanation:
  **N/A (denominator 0)**.
- Wrong `matched` among cases expected to be `needs_explanation`:
  **N/A (denominator 0)**.
- Of 4 real inputs: 1 blocked, 3 not_run. Decomposition — missing external input 2
  (no SR and no verified claim for Ottogi/Dongsuh), unapproved policy 1, collection
  failure 1, **code defect 0**.
- Execution-state expectations that matched the observed behaviour: 4/4. This is an
  execution-path check, not accuracy.
- Collection: **0 new DART requests this session.** The fixed request unit is one
  receipt, (corp_code, FY, rcept_no). The denominator is **4 re-used receipt-level
  inputs**: Samsung `20250311001085`, Ottogi `20250318000979` and Dongsuh
  `20251210000195` complete, plus the original Dongsuh `20250313000532` whose
  collection failed. At artifact level that is **11 of 12** `retrieved`, the twelfth
  being the failed Dongsuh `statements`. This is not a 98% collection-rate claim.
- False-positive rate on normal differences: not measurable this round
  (denominator 0). The "<5%" figure remains a target, not an achieved number.

## Missing developer A input (exact identifiers)

`reconciliation_import` needs a real run of the same tenant/company/document
version plus an existing verified claim. A GitHub/main inventory was checked
read-only. What exists and what does not:

- `evidence/section-map-results.json` (tracked on main) contains the **same Samsung
  SR bytes**, `source_sha256 342a99a14d7c32b4e66cbbf977cc89dc79044ccc7c5e71367fb476c14538c8c5`,
  87 pages, `status: "candidate_only"`, `map_sha256 08dbee38…`. It is a section map,
  **not a verified claim**, and carries no `tenant_id`, `company_id`, `run_id`,
  `claim_id` or `document_version_id`.
- Real A identifiers do exist, but for other documents:
  `evidence/live-tagging-kb-baseline-20260919.json` (`run_id 8db23077-5360-4e86-ba6b-f79d4e6a39b5`,
  every listed claim `blocked` / `SOURCE_VALIDATION_REQUIRED`),
  `evidence/live-tagging-kb-local-attribution-20260919.json`
  (`run_id b5857267-1b2c-4df7-a818-2ddf67d4094e`, `tenant_id 0a0e0b09-62c3-461f-808b-ba73cdc1fe07`),
  `evidence/raster-prose-downstream-live-20260919.json`
  (`run_id a06437ae-209b-4a6e-83b6-0c4221fab899`, `verified_claims: 7`, `non_null_decisions: 0`),
  `evidence/preliminary-examples-ab-20260919.json`
  (`tenant_id 2e2d9c40-9dfb-4a8c-bcbb-9d7bd4302177`, Doosan Bobcat claims).
- Those documents are Doosan / KB / Naver. The DART+SR material is Samsung /
  Ottogi / Dongsuh. **The two sets do not overlap**, so binding them would mix
  companies, which the handover forbids.
- The product databases reachable from this machine are empty of real runs:
  the original checkout's `.local/state.sqlite3` has 0 rows in `run_snapshots`
  and `reconciliation_case`; the only populated ones
  (`.local/reconciliation-http-5`, `.local/reconciliation-http-final`) hold 4
  synthetic cases with `synthetic=1` under tenant `11111111-…`.

Still required from A / the coordinator, per company to be evaluated:
`tenant_id`, `company_id`, `run_id`, `claim_id`, the claim/tag input revision that
produced it, the verified source proof for that claim, and the matching
`document_version_id` for the SR that B already holds. Credentials and whole
production databases are explicitly not wanted.

Also still missing: a non-synthetic policy record with its approver, role, scope
and approval state. Flipping `synthetic_only` on the synthetic policy is not an
acceptable substitute, and no approval was fabricated.

## Defects

**B-DEF-01 (fixed in the adapter; option (a) implemented).** On real DART documents
a reporting period is split across table cells, so no per-cell candidate carried a
complete period and a C2 financial period could only be reached either by writing a
`normalized` value whose end date was not inside the cited quote, or by staying at
`blocked / value_unresolved` forever.

*Correction to the first version of this section:* it described the Samsung cover as
one `<TR>` holding all five cells. The original bytes say otherwise. The cover
states the period across **two `<TR>` elements bound by an explicit
`ROWSPAN="2"`** on the label cell — `<TD ROWSPAN="2" ...>사업연도</TD>` with
`<TU AUNIT="PERIODFROM" AUNITVALUE="20240101">2024년 01월 01일</TU><TD>부터</TD>`
in the first row and
`<TU AUNIT="PERIODTO" AUNITVALUE="20241231">2024년 12월 31일</TU><TD>까지</TD>`
in the second.

The adapter now emits an additional `document_row_span` candidate: an exact,
contiguous slice of the **already-written** derived projection covering one
disclosed row. A row is one `<tr>`, or the consecutive `<tr>` siblings the markup
itself binds with `ROWSPAN`. The guards are deliberately strict — only a
well-formed `ROWSPAN` between 2 and `MAX_DECLARED_ROWSPAN` (8) declared on a cell
tag (`td`/`te`/`th`/`tu`) directly inside the open row may bind rows; a row is
spanned only when every one of its disclosed cells appears in a single contiguous
run, so a row interrupted by a nested table, shortened by the quote-length filter
or cut by the candidate bound is skipped entirely rather than published as a
partial "complete" row; a declaration left unsatisfied when `</TBODY>` or the end
of the member arrives — `ROWSPAN="3"` with only two rows present — drops its whole
group, so a malformed row is never published as a complete one; an over-long span
is skipped rather than trimmed. A `ROWSPAN` declared on a later row of a group is
measured from *that* row, so `ROWSPAN="3"` on the second row of a group reaches the
fourth row and three rows still count as incomplete. Rows are never joined merely
because they are adjacent. In every one of those refusals the individual cell
candidates remain available, so nothing that existed before is lost.

Nothing existing moved. The derived artifact bytes, their hashes, every existing
candidate id, source id and locator, the frozen reader, the result schemas and all
trust guards are unchanged; the spans carry `verification_state: "candidate"` and
an empty `normalization_suggestions`, so nothing is auto-approved or normalized.
Row spans have their own bound (`max_row_span_candidates`, default 200) outside
`max_candidates`, so they cannot displace an element candidate.

Demonstrated on the real Samsung FY2024 document (`.local/b-followup-rowspan-final`,
re-run on the converged code):
span `samsung-fs-2024-20250311001085:document:b0682fd46de5:row:9-13`, artifact
`e1b4a2f4…`, locator `chars:304:597`, cells
`['사업연도', '2024년 01월 01일', '부터', '2024년 12월 31일', '까지']`. **Both
dates are inside one quote**, the locator slice equals the quote byte for byte, and
all 200 emitted spans re-verified through `FileSourceReader` (200/200). The
pre-existing 2,001 candidates, the artifact index and `packet.json` are byte-identical
to the pre-row-span run, and `reconcile` still returns the same result object
(`packet_sha256 6b13086c…`, `blocked / null / policy_unapproved`). The catalogue
total goes from 2,001 to 2,201; the 6,001-source re-verification figure above is
the pre-row-span number and is unaffected.

Seven regression tests cover it: the split-period positive, the never-join-two-rows and preservation check, the nested-table interruption, the malformed/oversized/non-cell ROWSPAN variants, the bound-cut row, the unsatisfied declaration at `</TBODY>` and at end of member, and the later-row declaration arithmetic.

This closes the extraction half of B-DEF-01. It does **not** decide the operator's
normalization: turning that quote into a period value is still a reviewer action
under DEC-PERIOD, and no normalization is suggested or approved by the adapter.

**B-DEF-02 (fixed).** The prepared catalogue reported `limits.max_candidates: 2000`
while holding 2,001 candidates, because operator sustainability sources are added
after the DART bound is applied and the operator bound was not recorded anywhere.
Only a real, bound-filling collection exposes it. `add_operator_sr_sources` now
reports `max_operator_sources` and an `operator_source_count` counted from the
catalogue itself, so repeated additions stay truthful. Two regression tests cover
the single-add invariant and repeated addition. Verified on real data: the Samsung
catalogue now reports
`{max_candidates: 2000, truncated: true, max_operator_sources: 100, operator_source_count: 1}`
for its 2,001 entries. No verdict, hash, schema field or trust boundary changed.

Neither finding changed error, hold or processing-time behaviour for any case
above; the before/after timings are the same within noise, and the Samsung C2
result object is unchanged.

Both defects are now closed in the adapter. What remains open around them is not a
defect but an external input: the operator normalization under DEC-PERIOD, and the
registered claim and approved policy listed below.

## Local gates actually executed (B-F04)

All of the following ran on the final working tree, on Windows, with
`PYTHONUTF8=1` and a fresh output directory per run. Skips are not counted as
passes.

| Command | Exit | Result | Evidence |
|---|---|---|---|
| `uv run --no-sync python scripts/verify_reconciliation.py --output .local/b-followup-release-final --timeout-seconds 600` | 0 | 15/15 gates; its pytest gate 700 passed, 0 skipped, 3 warnings, 113.4s | `.local/b-followup-release-final/checks.json` |
| `uv run --no-sync python -m pytest tests/reconciliation/test_windows_parser.py tests/reconciliation/test_windows_upload.py tests/acceptance/test_parsing.py tests/integration/test_local_tag_runner.py tests/unit/test_tagging_platform.py -q` | 0 | 75 passed, 0 skipped, 2 warnings, 80.1s | `.local/b-followup-windows-pytest-03.log` |
| `pnpm --filter proofops-web typecheck` | 0 | tsc clean | — |
| `pnpm --filter proofops-web build` | 0 | vite build, 54 modules | `apps/web/dist` |
| `uv run --no-sync python -m pytest tests/reconciliation -q` | 0 | 551 passed, 0 skipped (7 new row-span/limit regressions) | changed-scope run |
| `ruff check` / `ruff format --check` / `mypy` on the two changed files | 0 / 0 / 0 | clean | — |
| `uv run --no-sync python scripts/validate_package.py` | 0 | 961 checks passed, 0 failed (documentation/contract scope only) | `.local/b-followup-validate-01.log` |

That validator rewrites `evidence/package_validation.json` and
`evidence/package_validation.txt` as a side effect; the count rises from 951 to 961
because the new proposals document is checked. The worker reverted those two
generated files to `HEAD`; the coordinator regenerated them at commit time with
the same command (`961 | passed: 961 | failed: 0`) so the tracked evidence matches
the delivered tree.

Earlier full runs at intermediate states are kept for comparison and history:
`.local/b-followup-release-01` (15/15, 691 passed) before any change,
`-02` (692 passed) after the first limits fix, `-03` (693 passed) at the first
delivery, and `-04`/`-05` during the row-span review iterations.

The Windows five-file set, `pnpm typecheck`, `pnpm build` and the browser check
were **not re-run** for the row-span change. That change is confined to
`packages/proofops/adapters/dart/candidates.py`, which none of those suites
imports or exercises: the Windows set covers the parser containment, upload,
parsing-acceptance, local tag runner and tagging platform paths, and the web and
browser checks cover the React workspace against the local HTTP server. Their
recorded exit codes above therefore still describe the delivered tree; re-running
them would only repeat an unchanged result.

Browser verification used a separate terminal, loopback only and a new database:

```powershell
uv run --no-sync python -X utf8 -m tests.e2e.local_browser_server --host 127.0.0.1 --port 4194 \
  --origin http://localhost:4194 --database .local/b-followup-browser-02/state.sqlite3 --include-reconciliation
node scripts/check_reconciliation_browser.mjs http://localhost:4194 .local/b-followup-browser-evidence-03
```

Exit 0, `status: passed`, with C1 `matched/same_verified_entity_set`,
C2 `matched/same_period`, C4 `matched/classification_basis_present`,
C3 `blocked/policy_unapproved`, plus source-download hashes, an immutable old
revision, idempotent replay, the stale-review notice and no page errors.
**This browser run is a synthetic functional check** (`synthetic: true`) and is
reported separately from the real-data work above; it says nothing about
real-company accuracy.

Two earlier browser attempts failed and the logs are kept. Both were harness
preconditions, not product defects: re-running against the already-exercised
database on port 4193 failed at `check_reconciliation_browser.mjs:38` because the
C1 case had already been evaluated, and re-using an existing evidence directory
failed at line 14 with "Evidence output already exists" — the harness refuses to
overwrite evidence, which is the intended behaviour.

Both test servers were stopped afterwards: PIDs 21668 and 28356 for the port 4193
run and PIDs 13684 and 16240 for the port 4194 run (`uv run` plus its child
interpreter in each case). A final check found no `local_browser_server` process
and nothing listening on 4193 or 4194.

Remote CI is `not_run`. GitHub Actions free minutes were exhausted at handover and
nothing here was executed on a runner. Local passes are not CI passes.

*Update 2026-09-22 (KST), after the repository was made public:* the PR #10 run
was re-executed on GitHub-hosted runners. Every job passed except
`reconciliation (ubuntu-24.04)`, whose release gate failed 6 of the new row-span
tests with `TypeError: sequence item 0: expected str instance, int found` raised
inside `html/parser.py` on the runner's CPython 3.12.14. Root cause: 3.12.14 added
a private `HTMLParser._pending` buffer that `close()` joins as strings, and the
DART projection parser kept its ROWSPAN bookkeeping under the same attribute
name; 3.12.13, used locally, has no such attribute. Fixed in `8f85070` by renaming
the subclass state to `_rowspan_pending`. The failure was reproduced locally by
placing the unmodified 3.12.14 `html` package ahead of the standard library
(6 failed / 16 passed on the pre-fix code) and the fix verified the same way
(22/22, and `tests/reconciliation` 551 passed under both parsers). No candidate,
hash, locator or verdict changes. The remote result for the fixed commit is
recorded on the PR itself.

## Final status table

| 작업 | 상태 | 실제 확인 결과 | 미해제 조건·담당 | 증거 |
|---|---|---|---|---|
| B-F01 원문·후보·사실 검토 | 완료 | 실제 원본 4건 해시 일치, 후보 6,001건(row-span 이전 기준) 전부 exact quote/locator/hash 재검증, 음성 3건 거부 + 대조군 1건 정상 검증, 3회 재현 동일. row-span 추가 후 삼성 카탈로그는 2,201건이며 신규 span 200/200 재검증 | — | 위 표, `.local/b-followup-real-01`, `-02`, `-final`, `b-followup-timing-02`, `b-followup-rowspan-final` |
| B-F01 실제 정책 적용·최종 판정 | 외부입력대기 | 실제 C2가 `reconcile`에서 `blocked/policy_unapproved`로 정상 보류. 의미 판정 0건 | 실제 승인 정책(승인자·범위·해시) — 기준/데이터 담당, A | `samsung-result-01/02.json` |
| B-F01 실자료 앱·출력·재현 | 외부입력대기 | 등록된 A claim이 없어 실자료 케이스를 제품 DB에 등록하지 못함. 앱 경로는 합성 입력으로만 확인 | tenant/company/run/claim/revision — A | 미싱 입력 절, 합성 브라우저 결과 |
| B-F02 정책 결정안 | 완료(제안) | DEC-C1 / DEC-C3 / DEC-STATUS / DEC-PERIOD / DEC-COVERAGE 5건, 실제 사례·반례·미확정점 포함. 승인 0건 | 승인 주체 결정 — 사용자/기준 담당 | [B-F02 정책 결정안](../../docs/DEVELOPER_B_POLICY_PROPOSALS.md) |
| B-F03 실제 사례 평가·결함 수정 | 부분완료 | 실사례 5행(등록 claim 평가 1행은 not_run), 결함 2건 모두 수정 + 회귀 9개(B-DEF-01 7, B-DEF-02 2). 의미 정확도 분모 0 | 검증된 claim, SR, 미사용 기업 2곳 — A/조정자 | 사례표, B-DEF-01/02 |
| B-F04 Windows 최종 검증 | 완료 | 15/15 게이트(최종 700 통과), Windows 5종 75 통과, `tests/reconciliation` 551 통과, 0 제외, typecheck/build 통과, 합성 브라우저 통과. Windows/web/browser는 이번 어댑터 변경과 무관하여 재실행하지 않음(사유 기재) | — | 위 게이트 표 |
| 원격 CI | 미실행 | Actions 무료 한도 소진, 최종 커밋 미실행 | 한도 회복 후 재실행 — 조정자 | — |

## Developer A identity delivery checked (2026-09-22, KST afternoon)

Developer A delivered `developer-b-inputs-20260922/` (README, `identities.json`,
three `claim-*.json`, `files.json`). It is kept outside Git as a delivery package;
only this summary is recorded. What was verified read-only:

| Check | Result |
|---|---|
| `files.json` SHA-256 of the 4 JSON files | 4/4 match |
| Existing review chain IDs | tenant `05176751…`, company `86f61841…`, run `5b5445d9…`, 3 claims (`ce7e85bf…` p185, `9542052a…` p186, `9f4127b3…` p190), `tag_revision 2`, `decision_revision 1`, `source_quality verified`, document version `50156f94…`, source sha `75388f16…` (NAVER 2025 통합보고서) |
| Registered company of that run | `실제 보고서 검토 시험` (demo registration), `local_synthetic: true` |
| Real NAVER registration (`8f660eef…` / `4df47fae…`, DART:00266961) | 2 documents `ready` (통합보고서 `75388f16…`, 연결감사보고서 `1cd29568…`), **`run_id` and `claim_id` null**, `real_company_reconciliation_ready: false` |
| Rows for either tenant in B-local databases | 0 in `.local/state.sqlite3`, `.local/reconciliation-http-final`, `b-followup-browser-02` (as A's README predicts) |
| NAVER originals on this machine | **none**: no file hashes to `75388f16…` or `1cd29568…`; the only large PDF present is the Samsung SR `342a99a1…`. No NAVER DART collection (corp `00266961`) exists in B's stores |
| C1–C4 applicability of the 3 claims | **none**: all three are management-track biodiversity/partnership statements (topic `environment`, p185/186/190). None states a consolidation scope (C1), a reporting period (C2), a CAPEX/investment commitment (C3) or a product/activity classification (C4). Per the follow-up §6-3 they stay 미평가 rather than being forced into a case |

Against `register_case` in `reconciliation_store.py` the delivered chain would satisfy
`tenant_id`, `claim_id`, `sustainability_document_version` and `source_quality`, and
`company_id` would have to be the demo company `86f61841…`; the anchored document is
`local_synthetic: true`, so any packet registered on it would be `synthetic: true`
and could never be reported as a real-company verdict. The real NAVER registration
cannot be registered at all until A produces a run and verified claims through the
normal pipeline. **Consequently B-F01 "실제 정책 적용·최종 판정" and "실자료 앱·출력·재현"
remain 외부입력대기; this delivery changes the identifiers B knows, not the status.**

Still required from A for a NAVER case: a run and verified claims bound to
`4df47fae…` / `f65386eb…` (SR) with `local_synthetic: false`, at least one claim that
actually carries a C1/C2/C3/C4 fact, the C1 `entity_set` and C2 `implementation_scope`
inputs A's README lists as incomplete, and either the NAVER originals (SR PDF and the
DART filing with its 접수번호) or authorization for B to collect the DART side into a
new immutable path. Nothing was collected, no DART or paid call was made, and no
identifier was copied into a B database.

## Developer A real-company delivery checked (2026-09-22, KST evening)

A superseded the diagnostic package with `developer-b-real-company/` (kept outside
Git): a **new registration of 네이버 주식회사 (DART:00266961)** with a real run and
claims, two operator-draft cases (C1, C2), three claim records, `integration.patch`,
`policy-source.md`, `normalization-review.json`, `verification.json`, `files.json`.
A's covering note states plainly that `local_synthetic=true` remains, that the cases
are draft-level, and that this is **not** a request to mark B-F01 complete. B agrees.

| Check | Result |
|---|---|
| `files.json` SHA-256 (23 files) | 23/23 match |
| Identity chain | tenant `3f4101d1…`, company `81a4606a…`, run `9aaf0254…`, claim `405ea5ec…` (p185, `source_quality verified`, `tag_revision 2`, M1/M2 present, M3–M6 unknown, AI-delegated review), SR version `74bb8e2c…` (`75388f16…`), FS version `e2deea33…` (`1cd29568…`, 제27기 연결감사보고서) |
| Packet identity vs `identities.json` | C1 and C2: tenant/company/claim/sustainability_document_version all equal; schema 1.1; `synthetic: true` |
| Canonical hashes under B's contract | C1 packet `6e592d17…`, C2 packet `863ffe25…`, policy `8937247e…` — **byte-identical to the values A stored** in `observed-case.json`, so A's store and B's `canonical_sha256` agree |
| Policy | `approved: false`, `synthetic_only: true`, no approver/hash; draft version `r25-local-integration-draft-1`, `source_policy_sha256` = sha256 of the enclosed `policy-source.md` (`29ef6330…`) |
| A's stored result | both cases `revision 2`, `blocked / null / [policy_unapproved]`, review `pending`, coverage unconfirmed, search `not_run` |
| B re-execution on this machine | `application.reconcile` + `FileSourceReader` raised `artifact_not_found` for both cases (CLI exit 2, `input_or_authorization_rejected`): the two NAVER PDFs are not on this machine and were not in the package. **No B verdict was produced**; nothing was fabricated to get past the reader |
| `integration.patch` | Applies cleanly to this branch. The reconciliation hunks (`application/reconciliation/sources.py`, `tests/reconciliation/test_sources.py`) were integrated as `8fcff4d`: opt-in `page:N:whitespace-v1` PDF locator, exact whitespace collapse only, ambiguity refused, legacy literal path unchanged, verifier not receipt-hashed. `test_sources.py` 7 passed, `tests/reconciliation` 552 passed, ruff/mypy clean. The pilot script, docs/27 and live-pilot test hunks are A's and were left for A's branch. PR #9 does not touch these files |
| Rows for tenant `3f4101d1…` in B-local databases | 0 (expected; A's `.local/r25-naver-real-company/state.sqlite3` holds them) |

Substantive reading of the two drafts, for the case table rather than for a verdict:

- **C1** binds the SR scope sentences on p2 and the FS phrase "네이버 주식회사와 그
  종속회사"; both `normalized: null` because neither text enumerates a verified legal
  entity set (`normalization-review.json`). With an approved policy the engine would
  next stop at `value_unresolved`; the DEC-C1 proposal (no count-only matching, set
  difference through the explanation path) is exactly what this case needs, and A's
  `entity_set` input is still outstanding.
- **C2** compares an activity month ("2025년 1월", the agreement date on p185) with the
  FS reporting year. A's own review marks comparability `unknown` and warns not to
  equate the two or label the gap unexplained. Under DEC-PERIOD this is a period
  reference of a different kind, not a reporting-period statement, and `rcept_no`,
  `published_at` and `available_on` are all null. It stays a draft until A supplies a
  claim that states the SR measurement period, or the coordinator decides the
  activity-month reading. A has said it will restate C2 as engine-basis period input.
- The claim itself is the biodiversity partnership sentence; it carries an
  `organizational_boundary` trigger only. It remains the only reviewed claim.

**Status after this delivery:** B-F01 "실제 정책 적용·최종 판정" and "실자료 앱·출력·재현"
remain 외부입력대기. What changed is that a real-company registration with a run and a
verified claim now exists in A's environment and its draft packets pass B's identity,
schema and hash checks. What has not changed: the anchored documents are
`local_synthetic: true`, no policy is approved, no originals are available to B,
search is not run, and the two drafts do not yet carry evaluable C1/C2 facts.

## Domain-expert first submission and Kia FY2024 financial collection (2026-09-22, KST evening)

The domain expert's first submission (`작성해서_제출/`, kept outside Git) covers one
company only, Kia (DOC-034, SR sha `d0d814d9…`): 5 claims, 34 elements, 3 numeric checks,
4 assurance rows, 24 standards (22 source-checked), 8 crosswalk rows, 3 rule questions.
`reconciliation.csv` is still the empty template and the expert states it starts once
developer B supplies the financial originals. `rules.csv` lists REC-001…008 (the DEC-C1 /
DEC-C3 / DEC-PERIOD / DEC-COVERAGE questions) all `unresolved`; `acceptance.csv` keeps
the <0.05 / ≥0.98 targets as `proposed`. **No expected C1–C4 result and no policy approval
were delivered**, so the B-F03 accuracy denominator stays 0. The three teams are on three
companies (A: NAVER, B: Samsung, expert: Kia); B therefore collected Kia so the expert
can start.

Collection (DART free API, `DART_API_KEY` from the ignored env file, no paid call):

| Item | Value |
|---|---|
| Company | 기아(주) `00106641`, 000270, December year-end |
| Filing list receipt | `list.json` 2025-01-01..2025-12-31, type A: 사업보고서 `20250306000691` (2025-03-06, `정연`) and [기재정정] `20250313001390` (2025-03-13, `연`); response sha recorded in `filings-receipt.json` |
| Pinned primary | `20250313001390` (corrected filing), CFS, 11011 → `kia-fs-2024-20250313001390`: statements `171c4bb9…` 134,132 B, document ZIP `c7064e00…` 368,362 B, XBRL ZIP `27f28eca…` 919,995 B, all `retrieved` |
| Superseded original | `20250306000691` → document `5c052c95…` and XBRL `5bf3d1e5…` retrieved; **statements `collection_or_identity_failed`** because the statements API answers for the corrected receipt only — the collector's identity guard, not a defect |
| Store | `.local/dart-live-kia-2024/` (new immutable path, nothing overwritten) |
| Candidates | default bound: 623 (500 + 123 row spans), 623/623 re-verified; at the 2,000 maximum: 2,200 (2,000 + 200 row spans), `truncated: true`; search coverage is therefore not complete |

Facts located in the full derived projections (document member 143,547 chars, statements
114,302, XBRL 190,924) and packaged for the expert in
`developer-b-kia-financial-20260922/` (outside Git; README, `facts.json`, the three derived
texts, manifests, `files.json`), every one of the 11 sources re-validated through
`FileSourceReader`:

- **C2** cover row span `chars:7869:8162` — `사업연도 2024년 01월 01일 부터 2024년 12월 31일 까지` in one quote (the B-DEF-01 row-span path on a second real company).
- **C1** summary table rows (상장/비상장/합계 spans): 연결대상회사수 기초 24 / 기말 24, 주요종속회사수 23 — **counts only**; the legal-entity list '상세표-1' is an attachment not present in the collected XML member. XBRL note sentences add an overseas-sales-subsidiary enumeration and the "지배력이 있으나 소규모 종속기업으로 관계기업투자 분류" exception, a real DEC-C1 boundary case.
- **C3** 당기 시설·설비 투자 합계 33,985억원, 2025 예상투자 42,672억원 (R&D excluded), consolidated cash-flow 유형자산의 취득 3,485,121백만원 (제81기).
- **C4**: the filing contains no 친환경차/하이브리드 wording (전기차 ×2, 전동화 ×3); the classification definition for DOC034-C03 must come from the SR itself.

No verdict, policy, expected value or SR binding was produced; the Kia SR PDF is not on
this machine (`기업보고서/` is absent), so no Kia packet can be assembled until the expert
or coordinator provides it or the reconciliation.csv rows.

## Remaining work, split by owner

Developer A / coordinator: supply, per company to be evaluated, a verified
`tenant_id`/`company_id`/`run_id`/`claim_id` with its input revision and source
proof, bound to a document version B already holds; obtain a real, non-synthetic
policy record with approver, scope and approval state; nominate two genuinely
unused companies with SRs if a hold-out check is wanted; re-run the platform
checks on the final commit once CI minutes return; commit, integrate and publish.
B-DEF-01 no longer needs a coordinator decision — the extraction half is
implemented in the B adapter, and what is left of it is the reviewer's
normalization call under DEC-PERIOD.

Developer B, once those arrive: register the real case through
`reconciliation_import`, walk the review → policy-approval → evaluate → previous
revision → source download → JSON export path in the app on real data, and report
the real verdict with its denominators. Until then the correct statement remains
**product path verified / real-company verdict pending**, not a completed
real-company evaluation.

## 2026-09-29 resumed work and expert CSV receipt

This dated entry supersedes the older statements that no expert reconciliation
rows had arrived and that remote CI had never run. It does not reinterpret earlier
test logs as runs against the current code.

### Integration and source of authority

The follow-up branch was at `e3c6a9f`; latest main `3340225` was integrated in
`db81c04`. The only merge conflicts were generated
`evidence/package_validation.json` and `.txt`. Regenerating them on the merged
tree with `uv run --no-sync python scripts/validate_package.py` passed 974/974
documentation/contract checks (exit 0). No original checkout modifications were
discarded or stashed, and no force push was used.

The user's September 28 adoption of REC-001 through REC-008 is recorded in
`docs/R00_DOMAIN_DECISIONS.md` section 12. These decisions are no longer all
unresolved proposals. A tenant/company-bound executable policy and its approval
record are separate from this project-level adoption. Missing runtime identifiers,
source receipts, and approval timestamps must not be invented.

### Received Kia expectation rows

The separately delivered root `reconciliation.csv` has SHA-256
`df4b7817184b3a5fcfcce948b3514b70335fbdd48c01172ea4c6d89869723017`.
It contains 24 columns and eight unique, well-formed rows: four case families,
each with a CUR/ASM scenario, rather than eight independent company cases.
There are four C1 rows, two C2 rows, two C3 rows and no C4 rows.

The nine files listed by the existing Kia financial bundle's `files.json` all
matched their recorded hashes. All eight CSV financial references matched
`facts.json` and passed the shipped `FileSourceReader` quote/locator/hash check.
CSV embedded line breaks were parsed as CSV text; the supplied files were not
rewritten. The receipt is retained privately at
`.local/b-resume-20260929/csv-audit.json`; the CSV, report text and other original
delivery files are not published in Git.

| Case family | Expert expectation | Execution status in this receipt |
|---|---|---|
| DOC034-C01 / C2 reporting period | CUR blocked; ASM matched | not_run |
| DOC034-C01 / C1 full boundary | CUR blocked; ASM blocked | not_run |
| DOC034-C01 / C1 KCN difference | CUR blocked; ASM blocked until whole-set prerequisites | not_run |
| DOC034-C04 / C3 no currency trigger | CUR blocked; ASM not_applicable | not_run |

The SR PDF with expected SHA-256
`d0d814d98c4aeedbbdb2bf8631b8981ae5cde94dec32aa32c57510420274da1f`
was not found in the inspected workspace and B checkout. The financial bundle
explicitly excludes that SR. Financial-source verification is therefore not a
claim that the SR quotes, claim attribution, or the expert's semantic expectations
have been independently verified. Engine-evaluated rows: 0; semantic agreement:
N/A (denominator 0). Human-authored expectations have been received; final gold
status is not inferred from file receipt or from a blank adjudicator field.

Clarifications to preserve in the next input revision:

- CUR/ASM reflect an earlier policy assumption; retain them as historical inputs
  and relate revised expectations to the September 28 decisions.
- General policy rejection happens before the C3 domain branch. Once application
  prerequisites pass, a verified claim without the currency-amount trigger is
  `not_applicable/c3_trigger_absent`, not a threshold-mapping rejection.
- A C1 null/unresolved normalized value and an explicitly empty verified set can
  produce different reason codes; do not encode missing evidence as an empty set
  simply to force `entity_set_empty`.
- Narrative `search_coverage` is not an authenticated completed-search receipt.
- C4 expectations remain absent. An applicable DOC034-C03 classification case
  needs its own definition/calculation source review.

### Ownership while waiting for replies

A is asked for existing Kia registration and DOC034 claim-to-UUID/revision mapping,
source receipts, existing executable policy state and a reproducible product
environment. The expert is asked for the matching SR/access path and page basis,
period/boundary attribution and the missing C4 case if applicable. Neither party
is asked to resend financial material already verified by B. Keys, cookies and
whole production databases are not requested.

B owns main integration, implementation of the adopted rules, regression checks,
CSV-to-verified-input preparation after the prerequisites arrive, and the eventual
real review/evaluate/revision/source-download/export replay. C1 financial
attachment acquisition also remains B's work; locating or describing an attachment
is not a request for the expert to implement collection.

Validation for this resumed implementation is recorded below.


### Kia financial annex recovered (2026-09-29)

The official DART filing index and detailed subsidiaries table were retrieved
with HTTP 200 from public DART (no model call, no API key needed). Receipt
`20250313001390`, document `10404742`, element `128`, offset `4268203`, length
`23871`, `dart4.xsd`: 24 seven-column subsidiary rows were extracted. The HTML
response is 20,583 bytes with SHA-256
`af6bef326c5f531ef3d0ba564c28a58b773f8895569922e9ec0db21a6317e2e6`.
The financial-annex acquisition gap is now resolved. This does not verify an
SR facility-to-entity mapping, KCN scope, or exhaustive package search.

Local handoff: `output/developer-b-kia-annex-20260929/` under the user's project
root (outside Git). It contains original HTML, URL/hash receipts, derived text,
24 extracted rows, a FileSourceReader-verified exact source reference and
`files.json` hashing all 9 other package files. Derived-text SHA-256:
`1c99c114561f6c596745a53b2f6217198653ac59aaf1f7970209cf88021abd00`.
The derived locator preserves literal CRLF bytes decoded as UTF-8 on Windows;
normalizing newlines before computing offsets was rejected as quote_mismatch
and corrected before delivery. Extracted rows remain `extracted_not_human_verified`.
Original financial package and expert CSV are unchanged.


### Adopted-rule implementation and final local validation

Engine version is now `reconciliation-engine-1.2.0`; schemas remain 1.1 and old
stored revisions remain immutable. C1 blocks unresolved/mismatched SR and FS
periods and unknown consolidation. C3 requires CAPEX to match the pinned FS
period; a verified matching commitment can complete with a null threshold while
mapping, allowlist, currency, period, amount, source role and approval guards
remain enforced. Threshold-based comparisons still block with a null threshold.
A complete search receipt must include both registered SR and FS versions.
The adopted-vs-applied matrix and remaining contract limitations are in
`docs/DEVELOPER_B_POLICY_PROPOSALS.md`; handoff instructions were updated too.

Orca supervised Claude Opus 5.5 under run `run_7e9b0234947b`, task
`task_fe96f66f7a10`, successful dispatch `ctx_717ed6f900df`. Its source/test/document
work was reviewed by the coordinator. The initial trust-prompt launch exited
before editing and was released; the successful dispatch settled. Cleanup
returned `retained/user_takeover` (no process action), and no reclaimable worker
terminals remained. This is a real Claude run, not a renamed Codex subagent.

Windows local checks (Python 3.12, `PYTHONUTF8=1`):

| Check | Result |
|---|---|
| `scripts/verify_reconciliation.py --output .local/b-resume-20260929/release --timeout-seconds 600` | 15/15 gates passed, exit 0; pytest 758 passed, 3 warnings, 0 skips |
| Windows parser/upload + acceptance parsing + local tag runner + tagging platform tests | 80 passed, 2 warnings, exit 0 |
| Claude focused reconciliation suite | 576 passed; liaison/head-consumer tests 30 passed |
| `pnpm --filter proofops-web typecheck` and `build` | Both exit 0 |
| `scripts/check_reconciliation_browser.mjs` using Edge against local composed HTTP/SQLite | passed; C1/C2/C4 matched, C3 policy_unapproved; source hashes, immutable prior revision, replay and stale-review notice passed; no page errors |
| `scripts/validate_package.py` | 974/974 documentation/contract checks, exit 0 |
| Diff whitespace / local credential-value scan | Passed; no key value printed or committed |

Release, Windows and browser artifacts stay under `.local/b-resume-20260929/`.
The test-only browser server was stopped after verification. No real model/AWS
execution or production deployment occurred. Earlier remote CI was 9/9 passed at
`e3c6a9f`; that historical status is not proof for this new revision. The PR checks
on the newly pushed revision are authoritative for remote platform validation.

Remaining external inputs are the matching Kia SR, verified real registered
claim/revision binding, relevant runtime approval/account mapping, and expert
clarification of C1/C2/C4 expectations. Actual import and semantic evaluation of
the delivered CSV are still not_run. Facility-to-entity mapping and multi-year
CAPEX schedules have no executable field in schema 1.1 and stay conservatively
blocked; they are explicitly documented limitations, not fabricated mappings.

### Remote CI follow-up

PR #10 was updated to d0ee16e and became mergeable. CI run 36506112322 exposed
pre-existing formatting failures introduced by the integrated main: five E501
lines in scripts/build_demo_snapshot.py, plus six files rejected by the global
formatter (scripts/analyze_report.py and five acceptance/integration/unit tests).
Only formatting was changed in those seven files. Python AST equality against
the prior commit was checked for every affected file; no behavior was changed.
Repository-wide ruff check and ruff format --check now pass (577 files).
The original failure is retained as a CI failure, not relabeled as a pass; checks
on the subsequent commit are the final remote gate.
The global mypy command on Windows reports 9 Unix-only resource/fcntl attribute
errors in existing source_preview.py and upstage_tagging.py. The CI global mypy
job targets Linux; this broad Windows result is not counted as a pass or hidden
with ignores. B's cross-platform release mypy gates passed separately.
