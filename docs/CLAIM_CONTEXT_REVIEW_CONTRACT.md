# Reviewed facility context and P6 recomputation (R28)

The trusted local AI review operation accepts an optional `context_review` with
policy `facility_section_context_v1`, the frozen input snapshot hash, literal
`dimensions` (`facility`, `reporting_period`, `metric`, `value`, `unit`), and a
literal `section_end` reference. Only the facility and end marker may lie outside
the claim. Their explicit semantic assignment is an AI review, never human gold.
The facility and end markers must bound the claim vertically in the same column
on the same page. The local adapter replays native/rendered source verification
for supplementary paragraph refs; no original graph or model packet is changed.

The operation appends `claim_context_review` to the existing immutable tag
revision through the existing If-Match, base-tag, audit and idempotency transaction.
It records source receipts, role refs and a deterministic P6 recheck. The recheck
retains all original candidate citations and distinguishes own-source, outside
the reviewed section, and unresolved comparisons. No same-scope bound table
observation means P6 `unknown` / `needs_review`, never `present` or `absent`.
Other unresolved evidence and any trusted existing deterministic result are not
silently discarded. Ordinary re-reviews carry and revalidate this receipt and
its original AI provenance. Model-origin P6 conflict cannot be a numeric finding.

Compatibility: the human HTTP correction body is unchanged. The local CLI gains
`--context-review-json`. Claim detail adds optional `reviewed_context` containing
`origin`, `dimensions` and `numeric_check`; old responses/readers remain valid.
The web detail displays the exact facility/year and why P6 remains on hold.

Migration: no SQL/table change. Existing immutable tag rows accept an additional
field; old rows without it read as no reviewed context. Existing revisions,
replicas, packet/rule hashes and exports remain unchanged. Rollback disables the
new writer/read projection; retained revisions are never deleted or overwritten.
No company registration, synthetic flag, policy approval or final grade is edited.
