#!/usr/bin/env python3
"""Trusted local CLI: link a deterministic numeric check to P6 (numeric-link-v1).

``observations``  read-only: lists the replayed graph's table observations with
                  their exact dimensions and whether every source ref re-verifies
                  (only ``verified`` ones can be compared).
``link``          derives the P6 fact from ``--binding-json`` (one explicitly accepted
                  comparison: observation id, reported value span inside the claim,
                  metric/scope/subject/scope2 basis/boundary/unit/denominator/period/
                  quantity kind). Dry run (default) prints the receipt and the body
                  that would be sent. ``--apply`` resolves through
                  ``ReviewService.resolve_ai_delegated_review(numeric_review=...)`` with
                  If-Match and an idempotency key (``--re-review`` for a resolved
                  review); the service re-derives the fact and appends an immutable
                  ``ai_delegated`` revision. Consumers replay the receipt from source.

``consistent`` -> P6 present; ``inconsistent`` -> P6 conflict. Undecided checks
(not_computable/not_comparable) are reported and never written; P6 is never
``absent``. P6 is an additional element: it never changes E/label/range. No model,
network or paid call; no run, table or prior revision is mutated.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("observations", "link"))
    parser.add_argument("--state-db", default=str(ROOT / ".local" / "state.sqlite3"))
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--binding-json")
    parser.add_argument("--delegated-reviewer", default=None)
    parser.add_argument("--delegation-authority", default="user delegation 2026-09-29")
    parser.add_argument("--reason", default="본문 수치와 표 결정적 대조(numeric-link-v1)")
    parser.add_argument("--actor-sub", default=None)
    parser.add_argument("--if-match", default=None)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--re-review", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def compose(database_path: Path) -> dict:
    """Real local composition; the numeric head verifier replays receipts on reload."""
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
    assurance = LocalAssuranceStore(runs, uploads, parser)
    coverage = LocalSearchCoverageStore(
        database_path.parent / "search-coverage", run_loader(runs, uploads, parser)
    )
    runs.jobs.assurance_verifier = AssuranceProofVerifier(
        runs.jobs,
        load_inputs=tags.load_inputs,
        load_statement=assurance.load,
        source_digest=lambda tenant, version: sha256(
            uploads.read_original(tenant, version)
        ).hexdigest(),
    )
    runs.jobs.absence_verifier = AbsenceProofVerifier(
        runs.jobs, load_inputs=tags.load_inputs, evidence=coverage
    )
    runs.jobs.numeric_verifier = NumericProofVerifier(runs.jobs, load_inputs=tags.load_inputs)
    # Re-reviewing a mixed head replays every carried receipt in build(), so the
    # service needs the same trusted loaders the consumer verifiers use.
    service = ReviewService(
        LocalSQLiteReviewStore(runs.jobs),
        load_inputs=tags.load_inputs,
        verify_context_sources=tags.verify_context_sources,
        load_assurance_statement=assurance.load,
        search_coverage=coverage,
    )
    return dict(service=service, load_inputs=tags.load_inputs)


def current_head_tag(store, tenant_id, run_id, claim_id) -> dict:
    jobs = store.jobs
    with jobs._transaction() as db:
        head = jobs._get(db, tenant_id, run_id, "claim_head", claim_id)
        return jobs._get(
            db, tenant_id, run_id, "tag_revision", f"{claim_id}:{head['tag_revision']:010}"
        )


def _emit(payload, code):
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return code


def main(argv=None, *, parts=None) -> int:
    args = _parse_args(argv)
    try:
        UUID(args.tenant_id)
        UUID(args.review_id)
    except ValueError:
        return _emit({"ok": False, "error": "tenant-id and review-id must be UUIDs"}, 1)
    if args.command == "link" and not args.binding_json:
        return _emit({"ok": False, "error": "link requires --binding-json"}, 1)
    if args.command == "observations" and (args.apply or args.binding_json):
        return _emit({"ok": False, "error": "observations is read-only"}, 1)
    if args.apply and (not args.idempotency_key or not (args.delegated_reviewer or "").strip()):
        return _emit(
            {"ok": False, "error": "--apply requires --idempotency-key and --delegated-reviewer"},
            1,
        )

    from proofops.application.authorization import AuthContext
    from proofops.application.reviews import ReviewRejected
    from proofops.application.tagging.numeric_link import (
        POLICY,
        POLICY_HASH,
        NumericLinkRejected,
        build_request,
        derive_numeric_fact,
        p6_element,
        verified_observations,
    )

    parts = parts or compose(Path(args.state_db))
    service = parts["service"]
    try:
        review = service.store.get(args.tenant_id, args.review_id)
    except KeyError:
        return _emit({"ok": False, "error": "REVIEW_NOT_FOUND"}, 1)
    run_id, claim_id = review["run_id"], review["claim_id"]
    try:
        inputs = parts["load_inputs"](args.tenant_id, run_id, claim_id)
        if args.command == "observations":
            items = [
                dict(
                    observation_id=o.observation_id,
                    quality=o.quality,
                    value_raw=o.value_raw,
                    metric_raw=o.metric_raw,
                    scope=o.scope,
                    subject=o.subject,
                    scope2_basis=o.scope2_basis,
                    organizational_boundary=o.organizational_boundary,
                    unit_canonical=o.unit_canonical,
                    denominator=o.denominator,
                    reporting_period=o.reporting_period,
                )
                for o in verified_observations(inputs)
            ]
            claim = inputs.context.claim
            return _emit(
                dict(
                    ok=True,
                    claim_quote=claim.quote,
                    claim_source_refs=[asdict(ref) for ref in claim.source_refs],
                    observations=items,
                    note="only quality=verified observations can be compared",
                ),
                0,
            )
        binding = json.loads(Path(args.binding_json).read_text(encoding="utf-8"))
        request = build_request(inputs, binding)
        fact, receipt = derive_numeric_fact(inputs, request)
    except NumericLinkRejected as exc:
        return _emit({"ok": False, "status": "blocked", "error": str(exc)}, 1)
    except (KeyError, OSError, ValueError) as exc:
        return _emit({"ok": False, "error": f"INPUT_INVALID:{type(exc).__name__}"}, 1)
    report = dict(
        policy=POLICY,
        policy_hash=POLICY_HASH,
        review_id=review["review_id"],
        run_id=run_id,
        claim_id=claim_id,
        numeric_review=request,
        receipt=receipt,
        would_record_origin="ai_delegated",
        note="deterministic check only; P6 never changes E/label/range",
    )
    if fact is None:
        # Undecided: P6 stays unknown; nothing is written even with --apply.
        return _emit({"ok": False, "status": "undecided"} | report, 1)
    tag = current_head_tag(service.store, args.tenant_id, run_id, claim_id)
    body = dict(
        base_tag_revision=tag["tag_revision"],
        track=(tag.get("confirmed_tags") or {}).get("track") or inputs.packet.to_dict()["track"],
        elements=[p6_element(fact) if e["element_id"] == "P6" else e for e in tag["elements"]],
        reason=args.reason,
    )
    body = json.loads(json.dumps(body))
    report["head_tag_revision"] = tag["tag_revision"]
    if not args.apply:
        return _emit({"ok": True, "dry_run": True, "body": body} | report, 0)
    actor = AuthContext(
        args.actor_sub or f"ai-delegated-cli:{args.delegated_reviewer.strip()}",
        args.tenant_id,
        "reviewer",
        frozenset({"viewer", "reviewer"}),
        "link-numeric-review",
    )
    try:
        result = service.resolve_ai_delegated_review(
            actor,
            review["review_id"],
            body,
            args.if_match or f'"{review["revision"]}"',
            args.idempotency_key,
            delegated_reviewer=args.delegated_reviewer.strip(),
            delegation_authority=args.delegation_authority,
            numeric_review=request,
            reopen=args.re_review,
        )
    except ReviewRejected as exc:
        return _emit({"ok": False, "error": exc.code, "status": exc.status} | report, 1)
    return _emit(
        {
            "ok": True,
            "applied": True,
            "new_tag_revision": result["new_tag_revision"],
            "review_status": result["decision"]["review_status"],
            "decision_evidence_grade": result["decision"].get("evidence_grade"),
            "decision_label": result["decision"].get("label"),
        }
        | report,
        0,
    )


if __name__ == "__main__":
    sys.exit(main())
