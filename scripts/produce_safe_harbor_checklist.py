#!/usr/bin/env python3
"""Trusted local CLI: produce ``project_checklist_completeness_v1`` checklist items.

GAP-001 · A (R00 §12, 2026-09-28 user adoption). Two steps, both read-only by default:

``template``  prints the operator decision template for one review: the claim's
              fixed checklist ids (all ``unknown``) and the replayed packet refs
              that may be selected by ``ref_sha256``.
``build``     validates a filled decision with
              ``application.tagging.checklist_producer`` and prints the exact
              ``safe_harbor_review`` request plus a preview. Only ``--apply`` writes,
              through ``ReviewService.resolve_ai_delegated_review`` with If-Match and
              an idempotency key; the service replays every reference again and
              appends an immutable AI-delegated tag revision. Prior revisions,
              runs and packs are never mutated.

A run is eligible only if its pinned rule pack already carries the checklist policy
(derived by ``scripts/review_rulepack.py --checklist-policy``; this tool never derives
or activates a pack). Otherwise the exact blocking reason is printed.

The recorded provenance is ``ai_delegated`` (not human, gold or legal approval).
``absent`` is refused until the search-coverage/absence contract is integrated.
All-present gives documentation completeness only; E/label stay null and
``legal_effect`` stays ``not_determined``. No model, network or paid call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

DEFAULT_AUTHORITY = (
    "R00 §12 2026-09-28 user adoption GAP-001·A (project_checklist_completeness_v1); "
    "AI-delegated checklist review, not legal or official verification"
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("template", "build"))
    parser.add_argument("--state-db", default=str(ROOT / ".local" / "state.sqlite3"))
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--decision-json")
    parser.add_argument("--source-authority", default=DEFAULT_AUTHORITY)
    parser.add_argument("--delegated-reviewer", default=None)
    parser.add_argument("--delegation-authority", default="user adoption R00 §12 2026-09-28")
    parser.add_argument(
        "--reason", default="안전항 체크리스트 항목 검토(safe-harbor-checklist-producer-v1)"
    )
    parser.add_argument("--actor-sub", default=None)
    parser.add_argument("--if-match", default=None)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--re-review", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def compose(database_path: Path) -> dict:
    """Real local composition (same wiring as ``review_ai_delegated.py``)."""
    from proofops.adapters.local.review_store import LocalSQLiteReviewStore
    from proofops.adapters.local.rulepack_store import RulePackSqliteStore
    from proofops.adapters.local.run_store import LocalSQLiteRunStore
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
    service = ReviewService(
        LocalSQLiteReviewStore(runs.jobs),
        load_inputs=tags.load_inputs,
        verify_context_sources=tags.verify_context_sources,
    )
    return dict(service=service, load_inputs=tags.load_inputs)


def current_head_tag(store, tenant_id, run_id, claim_id) -> dict:
    """Read the claim head pointer's exact tag revision (read-only)."""
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
    if args.command == "template" and (args.apply or args.decision_json):
        return _emit({"ok": False, "error": "template is read-only and takes no decision"}, 1)
    if args.command == "build" and not args.decision_json:
        return _emit({"ok": False, "error": "build requires --decision-json"}, 1)
    if args.apply and (not args.idempotency_key or not (args.delegated_reviewer or "").strip()):
        return _emit(
            {"ok": False, "error": "--apply requires --idempotency-key and --delegated-reviewer"},
            1,
        )

    from proofops.application.authorization import AuthContext
    from proofops.application.reviews import ReviewRejected
    from proofops.application.tagging.checklist_producer import (
        PRODUCER,
        PRODUCER_HASH,
        ChecklistProducerRejected,
        build_checklist_review,
        decision_template,
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
        if args.command == "template":
            return _emit(
                {"ok": True, "review_id": review["review_id"]} | decision_template(inputs), 0
            )
        decision = json.loads(Path(args.decision_json).read_text(encoding="utf-8"))
        request, receipt = build_checklist_review(
            inputs, decision, source_authority=args.source_authority
        )
    except ChecklistProducerRejected as exc:
        return _emit({"ok": False, "status": "blocked", "error": str(exc)}, 1)
    except (KeyError, OSError, ValueError) as exc:
        return _emit({"ok": False, "error": f"INPUT_INVALID:{type(exc).__name__}"}, 1)

    tag = current_head_tag(service.store, args.tenant_id, run_id, claim_id)
    body = dict(
        base_tag_revision=tag["tag_revision"],
        # Unresolved heads have no confirmed tags yet; the frozen packet names the track.
        track=(tag.get("confirmed_tags") or {}).get("track") or inputs.packet.to_dict()["track"],
        elements=tag["elements"],
        reason=args.reason,
    )
    body = json.loads(json.dumps(body))
    report = dict(
        producer=PRODUCER,
        producer_hash=PRODUCER_HASH,
        review_id=review["review_id"],
        run_id=run_id,
        claim_id=claim_id,
        head_tag_revision=tag["tag_revision"],
        safe_harbor_review=request,
        producer_receipt=receipt,
        would_record_origin="ai_delegated",
        note="AI 검토(위임·사람 아님); completeness only, not grade/legal effect",
    )
    if not args.apply:
        return _emit({"ok": True, "dry_run": True, "body": body} | report, 0)
    actor = AuthContext(
        args.actor_sub or f"ai-delegated-cli:{args.delegated_reviewer.strip()}",
        args.tenant_id,
        "reviewer",
        frozenset({"viewer", "reviewer"}),
        "safe-harbor-checklist-producer",
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
            safe_harbor_review=request,
            reopen=args.re_review,
        )
    except ReviewRejected as exc:
        return _emit({"ok": False, "error": exc.code, "status": exc.status} | report, 1)
    return _emit(
        {"ok": True, "applied": True, "new_tag_revision": result["new_tag_revision"]}
        | {"review_status": result["decision"]["review_status"]}
        | {
            "decision_evidence_grade": result["decision"].get("evidence_grade"),
            "decision_label": result["decision"].get("label"),
        }
        | report,
        0,
    )


if __name__ == "__main__":
    sys.exit(main())
