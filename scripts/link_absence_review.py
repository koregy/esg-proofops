#!/usr/bin/env python3
"""Trusted local CLI: publish reviewed whole-document absences (search-absence-link-v1).

``unknown -> absent`` needs (R00 §12) a complete full-document search-coverage receipt
and a stored whole-corpus review that concluded ``absent_confirmed``; both are made
and validated by ``scripts/produce_search_coverage.py``. This command only links
them into a new immutable review revision:

1. Loads the review, the replayed ``ReviewInputs`` and the producer store
   (``LocalSearchCoverageStore``, which replays each receipt from the original PDF,
   graph, claim and run snapshot and re-validates the stored review).
2. ``--item ELEMENT=RECEIPT_SHA256:REVIEW_SHA256`` (repeatable) names content
   addresses only; there is no client boolean. The derivation refuses incomplete
   search, a non-``absent_confirmed`` review, a stale claim/source/input, a base
   element that is present/conflict, compound partial absence, and P4/P6.
3. Dry run (default) prints the derived receipt and the body that would be sent.
   ``--apply`` resolves through ``ReviewService.resolve_ai_delegated_review`` with
   If-Match and an idempotency key (``--re-review`` for a resolved review); the
   recorded provenance is ``ai_delegated``. It then reloads the head through the
   proof-guarded claim reader.

``--correction-json`` supplies the complete 4-key body when the track changes or other
elements need values; the named elements are always replaced by the derived absent
element. No model, network, API or paid call; no run, receipt or prior revision is
mutated.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

ITEM = re.compile(r"^([GPM][1-8])=([0-9a-f]{64}):([0-9a-f]{64})$")


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-db", default=str(ROOT / ".local" / "state.sqlite3"))
    parser.add_argument("--store-root", help="default: <database dir>/search-coverage")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--track", required=True, choices=("goal", "performance", "management"))
    parser.add_argument("--item", action="append", default=[], required=True)
    parser.add_argument("--correction-json")
    parser.add_argument("--applicability-review-json")
    parser.add_argument("--delegated-reviewer", required=True)
    parser.add_argument("--delegation-authority", default="user delegation 2026-09-29")
    parser.add_argument(
        "--reason", default="전체 문서 검색·검토 후 부재 확인(search-absence-link-v1)"
    )
    parser.add_argument("--actor-sub", default=None)
    parser.add_argument("--if-match", default=None)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--re-review", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def compose(database_path: Path, store_root: Path | None) -> dict:
    """Real local composition; tests replace this with the same shape."""
    from proofops.adapters.local.assurance_head import AbsenceProofVerifier
    from proofops.adapters.local.claim_store import LocalClaimStore
    from proofops.adapters.local.review_store import LocalSQLiteReviewStore
    from proofops.adapters.local.rulepack_store import RulePackSqliteStore
    from proofops.adapters.local.run_store import LocalSQLiteRunStore
    from proofops.adapters.local.search_coverage_store import (
        LocalSearchCoverageStore,
        run_loader,
    )
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
    coverage = LocalSearchCoverageStore(
        store_root or database_path.parent / "search-coverage", run_loader(runs, uploads, parser)
    )
    runs.jobs.absence_verifier = AbsenceProofVerifier(
        runs.jobs, load_inputs=tags.load_inputs, evidence=coverage
    )
    service = ReviewService(
        LocalSQLiteReviewStore(runs.jobs),
        load_inputs=tags.load_inputs,
        verify_context_sources=tags.verify_context_sources,
        search_coverage=coverage,
    )
    return dict(
        service=service,
        load_inputs=tags.load_inputs,
        coverage=coverage,
        claims=LocalClaimStore(runs, uploads, parser),
    )


def _emit(payload, code):
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return code


def _json_file(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"{label} JSON unreadable") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} JSON must be an object")
    return value


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        UUID(args.tenant_id)
        UUID(args.review_id)
    except ValueError:
        return _emit({"ok": False, "error": "tenant-id and review-id must be UUIDs"}, 1)
    if args.apply and not args.idempotency_key:
        return _emit({"ok": False, "error": "--apply requires --idempotency-key"}, 1)
    parsed = []
    for value in args.item:
        match = ITEM.match(value)
        if match is None:
            return _emit({"ok": False, "error": "--item must be ELEMENT=RECEIPT_SHA:REVIEW_SHA"}, 1)
        parsed.append(match)
    try:
        correction = (
            _json_file(args.correction_json, "correction") if args.correction_json else None
        )
        applicability = (
            _json_file(args.applicability_review_json, "applicability")
            if args.applicability_review_json
            else None
        )
    except ValueError as exc:
        return _emit({"ok": False, "error": str(exc)}, 1)

    from proofops.adapters.local.assurance_head import AssuranceHeadRejected, read_head
    from proofops.application.authorization import AuthContext
    from proofops.application.reviews import ReviewRejected
    from proofops.application.tagging.absence_link import (
        AbsenceLinkRejected,
        absent_element,
        build_request,
        derive_absences,
    )
    from proofops.domain.rules.engine import MAPPINGS

    parts = compose(Path(args.state_db), Path(args.store_root) if args.store_root else None)
    service = parts["service"]
    tenant = args.tenant_id
    try:
        review = service.store.get(tenant, args.review_id)
    except KeyError:
        return _emit({"ok": False, "error": "REVIEW_NOT_FOUND"}, 1)
    run_id, claim_id = review["run_id"], review["claim_id"]
    items = [
        dict(
            element_id=m.group(1),
            absent_facts=sorted(MAPPINGS[args.track].get(m.group(1), ())),
            receipt_sha256=m.group(2),
            review_sha256=m.group(3),
        )
        for m in parsed
    ]
    try:
        inputs = parts["load_inputs"](tenant, run_id, claim_id)
        head, tag = read_head(service.store.jobs, tenant, run_id, claim_id)
        request = build_request(inputs, args.track, items)
        facts, receipt = derive_absences(inputs, request, parts["coverage"])
    except (AbsenceLinkRejected, AssuranceHeadRejected, KeyError, ValueError) as exc:
        return _emit({"ok": False, "status": "unknown", "error": str(exc)}, 1)
    if correction is not None:
        if set(correction) != {"base_tag_revision", "track", "elements", "reason"}:
            return _emit({"ok": False, "error": "correction JSON must carry exactly 4 keys"}, 1)
        body = dict(correction, base_tag_revision=head["tag_revision"])
    else:
        track = (tag.get("confirmed_tags") or {}).get("track")
        if track != args.track:
            return _emit({"ok": False, "error": "track change needs --correction-json"}, 1)
        body = dict(
            base_tag_revision=head["tag_revision"],
            track=args.track,
            elements=tag["elements"],
            reason=args.reason,
        )
    body["elements"] = [
        absent_element(e["element_id"]) if e["element_id"] in facts else e for e in body["elements"]
    ]
    body = json.loads(json.dumps(body))
    report = dict(
        review_id=review["review_id"],
        run_id=run_id,
        claim_id=claim_id,
        head_tag_revision=head["tag_revision"],
        request=request,
        receipt=receipt,
        would_record_origin="ai_delegated",
        note="absent only from replayed complete search + stored absent_confirmed review",
    )
    if not args.apply:
        return _emit({"ok": True, "dry_run": True, "body": body} | report, 0)
    actor = AuthContext(
        args.actor_sub or f"ai-delegated-cli:{args.delegated_reviewer}",
        tenant,
        "reviewer",
        frozenset({"viewer", "reviewer"}),
        "link-absence-review",
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
            applicability_review=applicability,
            absence_review=request,
            reopen=args.re_review,
        )
        reloaded = parts["claims"].current_tag(tenant, run_id, claim_id)
    except ReviewRejected as exc:
        return _emit({"ok": False, "error": exc.code, "status": exc.status} | report, 1)
    except (AssuranceHeadRejected, AbsenceLinkRejected) as exc:
        return _emit({"ok": False, "error": str(exc), "stage": "reload_verify"} | report, 1)
    return _emit(
        {
            "ok": True,
            "applied": True,
            "result": result,
            "reloaded_tag_revision": reloaded["tag"]["tag_revision"],
        }
        | report,
        0,
    )


if __name__ == "__main__":
    sys.exit(main())
