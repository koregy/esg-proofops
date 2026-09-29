# Bounded real extraction contract

Local `upstage_probe` freezes a non-synthetic model/prompt/rule profile and
`extraction_limits={max_calls:1..20,max_output_tokens:1..1024}` before run creation.
The worker checks exact profile/token limits, lease/cancellation, approval expiry,
and exact source SHA before each paragraph call. Other block kinds and over-limit
text remain unknown with replayable original spans. Rejected model output consumes
a call slot. Frozen snapshots are never edited to change the model or authorization.

The model returns exact quotes only. The extraction prompt requires assertions of
company actions, results, targets or practices; labels, reference codes and general
industry descriptions should return an empty list. Clauses retain available subject,
time and scope. This prompt is a heuristic, not a measured accuracy guarantee.
Unselected text stays unknown. Exact quote validation does not establish evidence
truth, company attribution or atomicity. No grades are calculated in this pilot.

Actual request IDs are read from the shared USD10 probe ledger, filtered by operation
start and parse manifest. No global cost deltas or fake accounting fallbacks are used.
Settled tokens/costs and unsettled reservations are separate. Missing accounting
fails closed. Budget/price/429/invalid settlement errors stop the stage without
automatic retries. Failed calls with no receipt retain their reservation and unknown
cost. Other rejected model content remains unknown through existing discovery rules.

The integration tests create authorized snapshots through RunService before parsing;
no immutability triggers are disabled. They stub HTTP responses over the real probe
reservation/settlement ledger and test bounded dispatch, unknown/replay preservation,
invalid output, hard stops, expiry and operation/manifest accounting isolation.

Bounded paragraph choice is stable across manifest/source UUIDs. Before the
existing discovery loop, the worker pre-selects at most `max_calls` eligible
paragraphs by canonical `(page_num, bbox, normalized_text)` order and only
those source ids may consume a model call; the legacy `(page_num, source_id)`
discovery visitation order is unchanged so old runs still replay. Blocks with
an identical canonical triple are content-geometry equivalent; a residual exact
tie retains the input graph order; the selected content and geometry are
equivalent, although the chosen source identity can differ. Non-paragraph kinds, out-of-scope pages, conflict/unreadable and
empty sources never enter the eligible set, over-limit paragraphs stay unknown
with reason `beyond_extraction_limit`, and the call-count fence, authorization
check and ledger accounting still apply per real call.

The selection policy is pinned in the extractor rule descriptor/hash. A new worker
rejects a queued old-profile run before calls; already-published old artifacts replay
through recorded responses unchanged. Rollback leaves both generations immutable.

## Explicit Pro4 local trial (2026-09-13)

Allow `solar-pro4` only as a separately hashed extraction model profile and exact
runtime `model_id` binding; Pro3 remains the omitted/default choice. The existing
profile hash selects the worker transport, so no new HTTP/DB/settings field or
migration is needed. Run creation and each call must compare the bound model ID to
the frozen model hash. A Pro4 transport can never execute a Pro3 snapshot, or vice
versa. Unknown model/hash pairs fail before paid calls. Existing immutable runs
and receipts retain their original hashes; new rule-validator hashes require new
runs. Rollback disables Pro4 trial creation and leaves all recorded results intact.
All calls use the same USD10 ledger, explicit source consent and output/call caps.
No automatic fallback/retry, reasoning mode, tagging, grade calculation, region
attestation or production enablement is introduced by this local option.

## Exact content bounds (R34, opt-in)

`--extraction-content-bounds` requires source-ID selection and defaults off. For each existing sentence candidate, the end offset can omit exactly one final ASCII `.` only if its preceding character is alphabetic. Numeric endings, ellipses and closing quotation marks retain their original bounds. No word, number, qualifier, source text, source hash or verifier policy changes. Selected quotes remain exact original substrings; the uncovered stop remains an `unknown/unprocessed_span` exclusion. This is not proof of atomicity or correctness.

Enabled sentence IDs pin `source_id:index:start:end`, and the extraction rule descriptor adds `extraction-content-bounds-v1`; old profiles and IDs remain byte-identical when disabled. Live request and receipt replay resolve the same index. Pilot manifests persist the option, resume restores it, and adding it to an existing run is rejected. Composition compares the complete frozen profile before model calls. Rollback disables the option for new runs; old runs/claims/revisions are never rewritten. No API/DB schema migration or grade rule change is introduced.
