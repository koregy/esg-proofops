#!/usr/bin/env python3
"""Trusted local CLI: publish P4 (assurance_covered) from ONE published assurance statement.

P4 is never an LLM vote and cannot be typed into a review. This command is the
operator entry for ``assurance-link-v1``:

1. Loads the review, the replayed ``ReviewInputs`` (``LocalTagStore.load_inputs``) and
   the run's single published statement through ``LocalAssuranceStore.load`` (which
   re-extracts it from its stored graph refs).
2. Pins the statement id + semantic hash and the exact loader snapshot, then derives
   the match: claim metric/period/entity/facility come only from the claim's own
   source-bound dimensions; one opinion, no combination; exclusions, unresolved
   statement fields, partial scope or unknown dimensions stay ``unknown``.
3. Dry run (default) prints the match receipt and the P4 element that would be sent.
4. ``--apply`` appends a new immutable revision through
   ``ReviewService.resolve_ai_delegated_review(assurance_review=...)`` with If-Match and
   an idempotency key (``--re-review`` for a resolved review), so the recorded
   provenance is ``ai_delegated`` (never human), then reloads the head and re-derives
   the proof from source (``verify_head_proof``).

No run snapshot, statement or prior revision is mutated; no API, model or network call.
``--verify`` only reloads and re-derives the current head's proof.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-db", default=str(ROOT / ".local" / "state.sqlite3"))
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--delegated-reviewer", required=True)
    parser.add_argument("--delegation-authority", default="user delegation 2026-09-29")
    parser.add_argument("--reason", default="검증의견서 범위와 주장 차원 일치(assurance-link-v1)")
    parser.add_argument("--actor-sub", default=None)
    parser.add_argument("--if-match", default=None)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--re-review", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true", help="Only reload and re-derive.")
    return parser.parse_args(argv)


def compose(database_path: Path) -> dict:
    """Real local composition; tests replace this with the same shape."""
    from hashlib import sha256

    from proofops.adapters.local.assurance_head import (
        AbsenceProofVerifier,
        AssuranceProofVerifier,
        NumericProofVerifier,
    )
    from proofops.adapters.local.assurance_store import LocalAssuranceStore
    from proofops.adapters.local.review_store import LocalSQLiteReviewStore
    from proofops.adapters.local.rulepack_store import RulePackSqliteStore
    from proofops.adapters.local.run_store import LocalSQLiteRunStore
    from proofops.adapters.local.search_coverage_store import LocalSearchCoverageStore, run_loader
    from proofops.adapters.local.tag_store import LocalTagStore
    from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
    from proofops.application.registry import Registry, RulePackChoice
    from proofops.application.reviews import ReviewService
    from proofops.application.uploads import UploadService

    rulepacks = RulePackSqliteStore(database_path)

    def active(tenant_id):
        return tuple(
            RulePackChoice(
                p.rule_pack_id,
                p.tenant_id,
                p.version,
                p.sha256,
                p.status,
                p.mode,
                p.effective_date,
                p.unresolved_gap_ids,
            )
            for p in rulepacks.list_active_packs(tenant_id)
        )

    registry = Registry.sqlite(database_path, active_rule_packs=active)
    runs = LocalSQLiteRunStore(database_path, rulepacks=rulepacks)
    uploads = UploadService(database_path, database_path.parent / "objects", registry)
    parser = OpenDataLoaderParser(database_path.parent / "parser-prepared")
    tags = LocalTagStore(runs, uploads, parser)
    statements = LocalAssuranceStore(runs, uploads, parser)
    coverage = LocalSearchCoverageStore(
        database_path.parent / "search-coverage", run_loader(runs, uploads, parser)
    )
    # Re-reviewing a mixed head replays every carried receipt in build(), so the
    # service needs the same trusted loaders the consumer verifiers use.
    service = ReviewService(
        LocalSQLiteReviewStore(runs.jobs),
        load_inputs=tags.load_inputs,
        verify_context_sources=tags.verify_context_sources,
        load_assurance_statement=statements.load,
        search_coverage=coverage,
    )

    def source_digest(tenant, version):
        return sha256(uploads.read_original(tenant, version)).hexdigest()

    runs.jobs.assurance_verifier = AssuranceProofVerifier(
        runs.jobs,
        load_inputs=tags.load_inputs,
        load_statement=statements.load,
        source_digest=source_digest,
    )
    runs.jobs.absence_verifier = AbsenceProofVerifier(
        runs.jobs, load_inputs=tags.load_inputs, evidence=coverage
    )
    runs.jobs.numeric_verifier = NumericProofVerifier(runs.jobs, load_inputs=tags.load_inputs)
    return dict(
        service=service,
        load_inputs=tags.load_inputs,
        load_statement=statements.load,
        source_digest=source_digest,
    )


def _emit(payload, code):
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return code


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        UUID(args.tenant_id)
        UUID(args.review_id)
    except ValueError:
        return _emit({"ok": False, "error": "tenant-id and review-id must be UUIDs"}, 1)
    if args.apply and not args.idempotency_key:
        return _emit({"ok": False, "error": "--apply requires --idempotency-key"}, 1)

    from proofops.adapters.local.assurance_head import (
        AssuranceHeadRejected,
        read_head,
        verify_head_proof,
    )
    from proofops.application.authorization import AuthContext
    from proofops.application.reviews import ReviewRejected
    from proofops.application.tagging.assurance_link import (
        AssuranceLinkRejected,
        build_request,
        derive_assurance_fact,
        p4_element,
    )

    parts = compose(Path(args.state_db))
    service = parts["service"]
    jobs = service.store.jobs
    tenant = args.tenant_id
    try:
        review = service.store.get(tenant, args.review_id)
    except KeyError:
        return _emit({"ok": False, "error": "REVIEW_NOT_FOUND"}, 1)
    run_id, claim_id = review["run_id"], review["claim_id"]

    def verify():
        return verify_head_proof(
            jobs,
            tenant,
            run_id,
            claim_id,
            load_inputs=parts["load_inputs"],
            load_statement=parts["load_statement"],
            source_digest=parts.get("source_digest"),
        )

    if args.verify:
        try:
            return _emit({"ok": True, "verification": verify()}, 0)
        except (AssuranceHeadRejected, AssuranceLinkRejected, KeyError, ValueError) as exc:
            return _emit({"ok": False, "error": str(exc) or type(exc).__name__}, 1)

    try:
        inputs = parts["load_inputs"](tenant, run_id, claim_id)
        statement = parts["load_statement"](tenant, run_id)
        head, tag = read_head(jobs, tenant, run_id, claim_id)
    except (AssuranceHeadRejected, KeyError, ValueError) as exc:
        return _emit({"ok": False, "error": str(exc) or type(exc).__name__}, 1)
    if statement is None:
        return _emit(
            {"ok": False, "status": "not_run", "error": "ASSURANCE_STATEMENT_UNAVAILABLE"}, 1
        )
    request = build_request(inputs, statement)
    try:
        fact, receipt = derive_assurance_fact(inputs, request, statement)
    except AssuranceLinkRejected as exc:
        return _emit({"ok": False, "error": str(exc)}, 1)
    report = dict(
        review_id=review["review_id"],
        run_id=run_id,
        claim_id=claim_id,
        review_status=review["status"],
        head_tag_revision=head["tag_revision"],
        request=request,
        receipt=receipt,
        would_record_origin="ai_delegated",
        note="P4 is published only for a covered single-statement match; unknown stays unknown",
    )
    if fact is None:
        return _emit(
            {"ok": False, "status": "unknown", "error": "ASSURANCE_NOT_COVERED"} | report, 1
        )
    track = (tag.get("confirmed_tags") or {}).get("track") or inputs.packet.to_dict()["track"]
    elements = [e for e in tag["elements"] if e["element_id"] != "P4"] + [p4_element(fact)]
    body = dict(
        base_tag_revision=head["tag_revision"],
        track=track,
        elements=json.loads(json.dumps(elements)),
        reason=args.reason,
    )
    report["p4_element"] = p4_element(fact)
    if not args.apply:
        return _emit({"ok": True, "dry_run": True} | report, 0)
    actor = AuthContext(
        args.actor_sub or f"ai-delegated-cli:{args.delegated_reviewer}",
        tenant,
        "reviewer",
        frozenset({"viewer", "reviewer"}),
        "link-assurance-p4",
    )
    try:
        result = service.resolve_ai_delegated_review(
            actor,
            review["review_id"],
            body,
            args.if_match or f'"{review["revision"]}"',
            args.idempotency_key,
            delegated_reviewer=args.delegated_reviewer,
            delegation_authority=args.delegation_authority,
            assurance_review=request,
            reopen=args.re_review,
        )
        verification = verify()
    except ReviewRejected as exc:
        return _emit({"ok": False, "error": exc.code, "status": exc.status} | report, 1)
    except (AssuranceHeadRejected, AssuranceLinkRejected) as exc:
        return _emit({"ok": False, "error": str(exc), "stage": "reload_verify"} | report, 1)
    return _emit(
        {"ok": True, "applied": True, "result": result, "verification": verification} | report, 0
    )


if __name__ == "__main__":
    sys.exit(main())
