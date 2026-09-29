# Additional-session Upstage budget

Use `scripts/authorize_upstage_session.py --help` to create a **new** ledger from an explicit user grant. Its amount is the complete additional session ceiling, including 10% VAT, not an extension of an assumed historical balance. Prior cumulative usage remains unknown. Existing ledgers and response directories are never overwritten; no automatic top-up is available. A bounded expiry stops new reservations while permitting settlement of prior calls.

Pass the same absolute ledger path via `--budget-ledger` to `scripts/analyze_report.py` and the pilot, and via `--ledger` to `scripts/produce_assurance.py`. The pilot freezes this path into its manifest and workers receive `LOCAL_UPSTAGE_LEDGER_PATH`. An invalid explicit path fails closed rather than falling back to a legacy budget. Keep the key file outside Git and pass only its path.

Each request reserves USD1 in a SQLite immediate transaction. Successful calls settle using provider usage and pinned gross prices. Unknown/failed calls retain the reservation; they are not automatically retried or treated as free. Hence usable headroom can be less than the nominal remaining balance. A new ledger is not a way to escape this session limit: all calls authorized by one grant must share the same ledger.

## Full-report execution

Run the analyzer without `--invoke` first. `--all-pages` selects every physical page; it does not claim completed processing or verified evidence. New runs can explicitly set `--parser-memory-bytes`, `--parser-timeout-seconds` and `--parser-max-output-bytes`; these are bounded and frozen. Windows without long-path support needs a short state root such as `.local/k10`.

The existing `--capacity-policy-refresh` opt-in selects the pinned September 25 capacity policy (expires October 2, 2026). It changes neither model pricing nor the session allowance. Older saved runs retain their policy and resource limits. Provider context evidence: https://www.upstage.ai/blog/en/solar-pro-4 (rechecked September 29, 2026).

On September 29 the user authorized at most USD10 additional usage until midnight KST. Local receipts and the shared ledger, rather than this document, are the accounting source of truth. A successful connection probe is an extraction candidate, not source geometry verification, accepted tagging, or model accuracy evidence.

## Same-session total-cap amendment

When the user raises the total of the **same** session (e.g. "today total USD20"), use `scripts/amend_upstage_session.py` on the existing session ledger instead of creating a new one. It appends one row to `probe_session_amendments` (UPDATE/DELETE blocked by triggers) that is hash-chained to the unchanged original grant row and to the previous amendment. It requires `--expected-current-total-usd` (compare-and-swap under `BEGIN IMMEDIATE`), `--new-total-usd` repeated in `--confirm-new-total-usd`, and `--acknowledge-same-session-expiry`. The new total is gross, must strictly increase and stays within the USD30 hard ceiling. The original `expires_at` is copied and cannot change; an expired grant cannot be amended. Recorded costs, reservations and unknown-cost calls are neither rewritten nor released; the amendment stores their snapshot. Legacy ledgers without a session grant are refused, and any chain/canonical/expiry/grant-hash mismatch fails closed as `BUDGET_POLICY_MISMATCH`. Use `--check` first; it rolls back and writes nothing.

`--entitlement-statement` with `--entitlement-service` (solar-pro2, solar-pro3, document-parse) records a user-declared plan such as a student entitlement as `user-declared-not-provider-verified` metadata. It does not zero or lower standard-price estimates, does not lift the cap and provides no paid fallback once the cap is reached. Consumers that read `UpstageProbe._authorized_limit` (reservation, `analyze_report.py` ledger status, `parse_report_api.py` gate, settlement and assurance scripts) see the effective total; `read_session_grant` still returns the original grant, so the pilot's audit record names the original grant, not the amendment.
