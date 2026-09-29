"""Company-activity industry topic review for a local run (GAP-010 A), project-only.

Every command is a dry run unless ``--apply`` is given. The actor kind defaults to
``ai_delegated``; ``human`` must be stated explicitly and is never manufactured.
``--actor-id`` and ``--authority`` record who actually acted and under which
delegation. No key, model, network or paid call; no claim/tag/decision/summary
record is touched. The report is not standards compliance, and the crosswalk only
shows candidate industries.

  report            current per-topic state and denominator (read-only)
  declare-universe  record the operator-declared project topic list (--universe JSON)
  review            record applicable|not_applicable|undetermined for one topic with
                    verified company-activity evidence (--evidence JSON list)
  approve           approve EXACTLY the current not_applicable review (--expected-head)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parents[1]
if str(TEAM_ROOT) not in sys.path:
    sys.path.insert(0, str(TEAM_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review_industry_topics", description=__doc__)
    parser.add_argument("command", choices=["report", "declare-universe", "review", "approve"])
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--universe", type=Path, help="Topic list JSON: [{topic_id, label}]")
    parser.add_argument("--topic-id")
    parser.add_argument("--decision", choices=["applicable", "not_applicable", "undetermined"])
    parser.add_argument(
        "--evidence", type=Path, help="JSON list of {source_id,char_start,char_end,quote}"
    )
    parser.add_argument("--reason")
    parser.add_argument("--expected-head", help="Current head sha256 (omit for the first)")
    parser.add_argument("--actor-kind", choices=["ai_delegated", "human"], default="ai_delegated")
    parser.add_argument("--actor-id")
    parser.add_argument("--authority", help="Delegation/authority text recorded with the act")
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry run")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from proofops.adapters.local import industry_topic_store as store_module
    from proofops.adapters.local.run_store import LocalSQLiteRunStore
    from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
    from proofops.application.industry_topic_review import UNIVERSE_SCHEMA
    from proofops.application.registry import Registry
    from proofops.application.uploads import UploadService

    database = args.database_path.resolve()
    if not database.is_file():
        parser.error(f"--database-path not found: {database}")
    runs = LocalSQLiteRunStore(database)
    uploads = UploadService(database, database.parent / "objects", Registry.sqlite(database))
    loader = store_module.run_loader(
        runs, uploads, OpenDataLoaderParser(database.parent / "parser-prepared")
    )
    store = store_module.LocalIndustryTopicStore(runs.jobs, loader)
    now = datetime.now(UTC).isoformat()
    tenant, run = args.tenant_id, args.run_id

    def actor():
        if not args.actor_id or not args.authority:
            parser.error("--actor-id and --authority are required for writes")
        return dict(kind=args.actor_kind, id=args.actor_id, authority=args.authority)

    dry = not args.apply
    try:
        if args.command == "report":
            result = store.report(tenant, run)
        elif args.command == "declare-universe":
            if args.universe is None or not args.reason:
                parser.error("declare-universe needs --universe and --reason")
            result = store.declare_universe(
                tenant,
                run,
                dict(
                    schema=UNIVERSE_SCHEMA,
                    scope="operator_declared_project_topics",
                    official_mapping=False,
                    topics=json.loads(args.universe.read_text(encoding="utf-8")),
                    declared_by=actor(),
                    declared_at=now,
                    reason=args.reason,
                ),
                expected_head=args.expected_head,
                dry_run=dry,
            )
        elif args.command == "review":
            if not args.topic_id or not args.decision or not args.reason:
                parser.error("review needs --topic-id, --decision and --reason")
            evidence = (
                json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else []
            )
            result = store.review(
                tenant,
                run,
                topic_id=args.topic_id,
                decision=args.decision,
                reason=args.reason,
                evidence=evidence,
                reviewer=actor(),
                reviewed_at=now,
                expected_head=args.expected_head,
                dry_run=dry,
            )
        else:
            if not args.topic_id or not args.reason or not args.expected_head:
                parser.error("approve needs --topic-id, --reason and --expected-head")
            result = store.approve(
                tenant,
                run,
                topic_id=args.topic_id,
                approver=actor(),
                approved_at=now,
                reason=args.reason,
                expected_head=args.expected_head,
                dry_run=dry,
            )
    except ValueError as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
