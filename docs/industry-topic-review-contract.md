# Company-activity industry topic review (R00 §12 GAP-010 A), project-only

GAP-010 A: the versioned industry crosswalk only presents candidate industries. Each topic is set applicable / not_applicable / undetermined after reviewing the company's own activity evidence. Only an explicit, approved not_applicable with a reason leaves the denominator; unknown stays in it.

No official GICS→SASB mapping, SASB topic set or legal Scope 3 deferral is invented. `config/sasb/industry_map.yaml` still has `mappings: []` and `unverified`, so every candidate is `undetermined`. The report is **not** standards compliance.

## Existing code reused, not duplicated
- `domain/applicability.py`: `IndustryIdentity`, `IndustryMapping`, `resolve_industry_applicability` (candidate only).
- The run-frozen rulepack file `sasb/industry_map.yaml`.
- The version metadata `industry_system` / `industry_code`.
- `span_citations.verify_source_ref`.
- `summaries.RequirementStatus`.

`reviews.py` `local_claim_applicability_v1` is claim-level trigger review and is untouched.

## Components
- `application/industry_topic_review.py` (pure): universe/actor validation, crosswalk candidate, review and approval revisions, `topic_state`, `build_report`, and `summary_applicability` (optional later integration).
- `adapters/local/industry_topic_store.py`: stores revisions in the run DB's existing `job_records` (kinds below) inside the job store's `BEGIN IMMEDIATE` transaction. `run_loader` re-derives pins from the frozen snapshot, original, native-replayed graph and rulepack.
  - `industry_topic_universe` (immutable) and `industry_topic_universe_head`
  - `industry_topic_revision` (immutable) and `industry_topic_head`
- `scripts/review_industry_topics.py`: `report`, `declare-universe`, `review` and `approve`. Every write command is a **dry run unless `--apply`**. The actor kind defaults to `ai_delegated`; `human` must be explicit. `--actor-id` and `--authority` record the real operator or delegation.

## Rules
- **Universe:** an operator-declared project topic list (`scope=operator_declared_project_topics`, `official_mapping=false`). Without one, the report says `universe_undeclared` and `denominator_count=null`.
- **Review:**
  - decision `applicable`, `not_applicable` or `undetermined`
  - a 20–2000 character reason
  - an actor `{kind, id, authority}` and an aware timestamp
  - pins: tenant, run, document_version, object_version, source sha, input_hash, parse_manifest, graph sha, rulepack sha, crosswalk sha, industry identity, universe sha
  - applicable and not_applicable need at least one company-activity evidence ref, resolved from `{source_id, char_start, char_end, quote}` against the pinned graph and verified by `verify_source_ref` (block `verified`, exact quote, same tenant)
- **Universe revisions:** a later revision may only **add** topics. Every existing topic id must stay listed with its identical label.
  - Removal is refused (`INDUSTRY_UNIVERSE_SHRINK_REFUSED`), even for an approved not_applicable topic; that topic stays listed and explicitly excluded.
  - Relabelling is refused (`INDUSTRY_TOPIC_REDEFINITION_REFUSED`).
  - An expansion changes the universe hash, so earlier reviews read `stale_inputs` and stay in the denominator until re-reviewed (fail-safe).
- **Approval:** approves exactly the current head review, which must be `not_applicable` under the current pins and universe (CAS on `expected_head`). Any newer review makes the approval stale; the topic returns to the denominator.
  - On every read, `topic_state` checks that the record immediately before an approval is a review revision with decision `not_applicable`, and that the approval's `approves_revision_sha256` and `prior_sha256` both equal that review's hash, under the same pins and universe. Otherwise it raises `INDUSTRY_APPROVAL_CHAIN_INVALID`.
  - R00 requires an **explicit** approval, not an independent or human approver. A same-actor approval (e.g. the same `ai_delegated` delegate) is allowed as a separate explicit revision. It is recorded as provenance (`approval_provenance.same_actor_as_reviewer`, `independent_or_human_approval_claimed=false`) and never prohibited or presented as independent.
- **Evidence:** needs at least one exact, verified, non-empty quote. No minimum quote length is imposed, because a length score is not semantic proof; the judgement is carried by the reason and the declared activity decision.
- **Missing crosswalk:** a rulepack without `sasb/industry_map.yaml` pins `crosswalk=None`, so every candidate stays undetermined and nothing is excluded.
- **Writes:** every write is a CAS on `expected_head`, so concurrent writers get one winner and the others `INDUSTRY_HEAD_CONFLICT`.
- **Reads:**
  - The whole chain is replayed: content hash, contiguous seq, prior link, head anchor.
  - Pins are re-derived from the run, and the head review's evidence refs are re-verified.
  - Changed pins (mutated original, stale input, new graph or universe) mark topics `stale_inputs`, which stay in the denominator.
  - A loader whose pins disagree with its graph, or a foreign tenant, is refused.
  - A rehashed or edited revision, or a forged approval row without the head anchor, is refused.
- **Report:** per topic, one of `applicable`, `not_applicable_approved` (excluded), `not_applicable_pending_approval`, `undetermined`, `unreviewed` or `stale_inputs`. `satisfaction` is always `null`; it is never inferred from applicability or claim grades. `denominator_count` equals the topics minus approved not_applicable.
- **Candidate:** the crosswalk result per topic is `role=candidate_only_never_excludes`.

## Scope of the consumer
The approved scope is a **project-only report** (CLI `report`). There is intentionally **no main summary consumer**: `LocalSummaryStore`/`summarize_snapshot` still receive `applicability=None`, and no API, composition or report denominator uses these revisions. `satisfaction` is null by design.

## Possible later integration (not in scope)
`summary_applicability(report)` returns `RequirementStatus` items for `summarize_snapshot(applicability=...)`. It returns `None` (the current behaviour) while any applicable topic lacks an independently verified satisfaction, which this producer never supplies. `LocalSummaryStore` still passes `applicability=None`; wiring it (and anything in `reviews.py`, `assurance_head`, composition) waits for coordination with A (`ctx_44b1335397a4`) and D (`ctx_b77fb7b3240a`).

## Trust limit
The run DB is the trust anchor, as for every other `job_records` revision. Someone with raw write access to the DB could rewrite both a revision and its head consistently. Content hashes and anchors defend against partial or rehashed file-level edits, not against a fully privileged DB writer.
