# C3/C4 trigger review — the linkage contract bridge

Code: `packages/proofops/application/linkage_trigger_review.py`. Wiring:
`build_packet(trigger_review=...)` and `scripts/linkage_exchange_cli.py`.

## Why it exists

No tagging producer emits the contract triggers `currency_amount` (C3) or `revenue_share` (C4).
SCHEMA_GUIDE ("기존 태그 → 신규 trigger 변환") forbids creating them by renaming a tag. It requires that the source fact **and** a trigger decision receipt are kept.

A trigger review is that receipt. It is the C3/C4 counterpart of `C1EntitySetReview`. It creates no `ConfirmedFact`, no replica and no semantic approval.

## Review record (`item` = `C3` | `C4`, strict keys, no defaults)

### Pins and compare-and-set identity

Any mismatch blocks the review. Nothing is repaired. Each pin is compared against the current value:

| Pin | Blocks with |
|---|---|
| `synthetic` | `review_synthetic_mismatch` |
| `tenant_id` | `review_tenant_mismatch` |
| `company_id` (the trusted uploads company) | `review_company_mismatch` |
| `run_id` | `review_run_mismatch` |
| `claim_id` | `review_claim_mismatch` |
| `document_version_id` | `review_version_mismatch` |
| `source_sha256` (the claim's source bytes) | `review_source_hash_stale` |
| `tag_revision` (the accepted `claim_head`) | `review_revision_stale` |
| `fact_sha256` (canonical SHA-256 of the exact `ConfirmedFact`, including its evidence refs) plus `fact_value` | `review_fact_stale` |

### Readable facts

The review may only read a `present` fact that has `citation_verified`, `binding_accepted`, the same `source_tenant_id` and at least one evidence ref. The fact name must be one of these:

* **C3:** `target_metric` or `transition_plan`, and only on a **goal** claim.
* **C4:** `target_metric`, `current_progress` or `quantitative_or_qualified_ordinal`.

### Source bindings

`source_bindings` must match the fact's own evidence (source_id and quote, exactly). `source_id` names the one bound quote that holds the literals.

### Semantics (re-derived here, never trusted from the reviewer)

**C3.**
* `amount_literal` must occur exactly once. It must also be the quote's **only** currency amount; a stray scaled number such as the `1조` in `1조 2,000억원` counts as a second amount.
* It is parsed deterministically, for example `10조원` becomes `10000000000000` KRW. The parsed value must equal the review's `normalized_amount` and `currency`.
* `investment_label` must be a literal in the same quote that states investment or expenditure (`투자` — but not `투자자` — or `CAPEX`, `자본적 지출`, `지출`).
* If the quote carries revenue or sales wording (`매출`, `수익`, `판매`, `revenue`, `sales`), the review is blocked. Such an amount is not provably an investment.

**C4.**
* Any sale-count wording blocks the review: `판매`, `대수`, `units sold`, `unit sales`, `sales volume`, `volume`. A sales-volume share is never a revenue share.
* `revenue_label` must be a literal containing `매출`, `수익` or `revenue`.
* `classification_label` must be a literal in the quote.
* The quote must carry exactly one percentage, and it must equal `share_literal`.
* The label must equal the caller's `c4_context.classification_name`.

`review_origin` is either `human` or `ai_delegated`. The receipt records the second as `ai_delegated_trigger_review_not_independent_gold` (R00 §6).

## Packet effect

The packet schema is strict 1.1 and is unchanged. The review adds only the item trigger to `claim.trigger_elements`, along with that trigger's evidence as `sources[]`. It sets `sustainability`:

* **C3:** `kind=currency_amount`, `normalized=<Decimal>`, `unit=<currency>`.
* **C4:** `kind=classification`, `normalized=<classification label>`.

In both cases `raw` is the bound quote.

A reviewed trigger that would duplicate a direct trigger for the same item is blocked (`trigger_review_redundant`).

The receipt (`linkage-trigger-review-1`) is written separately and is never merged into the packet. It contains `packet_sha256`, `review_sha256`, the review, the derived value, and the list of things the review does not decide.

## Deliberately NOT decided (still gated elsewhere)

* **REC-003:** the CAPEX account mapping and allowlist. `c3_account_mapping_approved`/`allowed_capex_account_ids` stay in policy; while unapproved, the engine returns `c3_policy_unapproved`.
* **REC-004:** `c3_threshold` stays `null`. A threshold-based C3 stays blocked. Only a separately verified `commitment_source_id` can complete, as `commitment_disclosed`.
* **REC-007:** classification correctness and the definition/calculation basis. These stay with `c4_context` and the C4 engine.
* **Comparability and explanation search:** the builder still emits `comparability=unknown` and `search.state=not_run`.

## CLI

```
linkage_exchange_cli.py draft-trigger-review --item C3|C4 --fact-name <name> \
  --tenant-id T --run-id R --claim-id C --database-path DB --synthetic|--no-synthetic --output draft.json
linkage_exchange_cli.py validate-trigger-review --review filled.json \
  --tenant-id T --run-id R --claim-id C --database-path DB --synthetic|--no-synthetic
linkage_exchange_cli.py build-packet ... --item C3|C4 --financial-context fc.json \
  --trigger-review filled.json [--trigger-review-receipt-out receipt.json]
```

* **Draft:** the pins are copied from the accepted head. The semantic literals, review id, reviewer and origin stay `null`, so an unfilled draft never loads.
* **Validate:** re-checks the review against the current head and writes nothing.
* **Build:** additionally checks the C4 classification context. For non-synthetic packets it runs the existing byte verification, then the schema check. It writes the receipt with `open("x")`.

Supported amount literals: `<number>[조|십억|억|천만|백만|만|천]<원|KRW|달러|USD>`, and `$|USD <number> [thousand|million|billion]`. Anything else is blocked, for example compound `1조 2,000억원` or a missing currency.
