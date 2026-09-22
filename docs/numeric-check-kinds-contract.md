# Numeric check kinds — temporal growth and same-period product reduction — 2026-09-22 KST

`check_numeric_consistency` computes bounded numeric relations from verified
original source only, never a grade or label. This note adds two `CheckKind`
values to the existing `comparison`/`sum`/`reduction` set. Both reuse the exact
`Fraction` arithmetic and the existing per-value display rounding intervals, and
neither relaxes any existing source, dimension or reduction guard.

## growth (temporal rate)

Two observations, `(baseline, current)`, of the same subject and all other
dimensions, in two distinct periods. `baseline_period` and `reporting_period`
must both be supported (`is_supported_period`) and must differ, exactly like
`reduction`. Computed value is `(current / baseline - 1) * 100`, with the result
interval taken from the corners of the two input display intervals. A zero or
sign-crossing baseline interval is `not_computable` with reason
`zero_baseline_interval`; a non-terminating quotient preserves the exact ratio
and reason `non_terminating_decimal` rather than fabricating precision. Every
dimension, including `subject`, must match the binding; a mismatch is never a
finding. Sales-count growth `(644685/598846 - 1) * 100 ≈ 7.65` is consistent
with a reported `약 8%` under its display band.

## product_reduction (same-period, different product)

Two observations, `(baseline_product, current_product)`, in the *same* period,
where exactly the `subject` dimension is deliberately different and all other
dimensions match. `baseline_period` must equal `reporting_period`. Computed value
is `(1 - current / baseline) * 100`, identical to the reduction formula, with the
same zero-baseline and non-terminating-decimal handling. LCA comparison
`(1 - 35.40/48.73) * 100 ≈ 27.35` is consistent with a reported `약 27%`.

The different-product comparison is accepted explicitly, never inferred from the
numbers. It requires all of: `product_comparison_accepted=True`; a non-empty
`subject` (current product) and `baseline_subject` (baseline product) that are
different literals; and `subject_ref` and `baseline_subject_ref` that are verified
source spans (`_verified`) whose quotes equal those two product literals and lie
inside the accepted claim's own verified source refs. The baseline observation's
`subject` must equal `baseline_subject` and the current observation's `subject`
must equal `subject`; every other dimension must still match the binding. When the
explicit product acceptance is missing the result is `not_comparable` with reason
`product_comparison_unaccepted`; a dimension or observation-source mismatch remains
a non-finding (`not_comparable`/`not_computable`), never `consistent`.

## Compatibility and scope

New `ClaimBinding` fields — `baseline_subject`, `subject_ref`,
`baseline_subject_ref`, `product_comparison_accepted`, `conditions` — are optional with
defaults that reproduce prior behavior, so existing bindings, immutable prior
revisions, reports and the aggregation binding hash are unchanged. No API/DB
structural change is required: the JSON binding loader (`evaluation/numeric_run.py`)
accepts the optional keys, and `CheckResult` is unchanged, so existing
serialization stays valid. Historical sum hashes are accepted only when every new
field has its default value; new hashes also remain supported. The runtime seam (`application.numeric_analysis`) forwards
these bindings as operator tagging only; the pure check still validates every
accepted binding against verified original/claim provenance. `comparison`, `sum`
and `reduction` semantics — including that a same-period `reduction` stays
`not_comparable` — are unchanged. Rollback stops producing the new kinds. Retain a
reader for their saved records; older loaders must reject unsupported kinds/keys
instead of silently interpreting them as reduction. No old record is rewritten.

## Explicit shared-condition review

The default still holds nontrivial footnotes. For `growth` and `product_reduction`
only, an operator may tag a `NumericCondition` with the exact verified full note,
the ordered operand IDs, `condition_binding_hash(binding)`, reviewer identity,
`acceptance_state=accepted`, and `relation=same_basis_for_selected_observations`.
This declares a reviewed fact (e.g. both sales columns use wholesale counting),
not a domain policy or grade. The hash excludes the conditions themselves and
pins the remaining binding; changing periods, subjects or operands invalidates it.
The source note must actually attach through table lineage to BOTH operands.
Each operand must retain the verified note reference; open source issues,
unassigned notes, unsupported unreviewed notes and dimension mismatches still hold.
Neither a number match nor merely identical note text creates acceptance.

Source fields may use different verified subspans of one original cell (e.g.
`tCO2eq/대` as unit and `대` as denominator). Every subspan keeps the same raw block
hash and geometry and passes the existing original-source verifier independently.
