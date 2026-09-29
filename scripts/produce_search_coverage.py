"""Produce, replay and inspect full-document search-coverage receipts for a local run.

Read-only over the run database: it loads the frozen run snapshot, the original
PDF, the native-replayed graph and the replayed claim, derives page facts from
the original bytes, and writes only immutable content-addressed receipt/review
files under ``--store-root`` (default: ``<database dir>/search-coverage``).
No key, model, network or paid call. A receipt never sets an element state:
``absent`` additionally needs a validated whole-corpus delegated review.

  produce       compute and store a receipt (``--check`` computes without writing)
  replay        recompute a stored receipt from current state; refuse any difference
  request       print the delegated review request for a complete receipt
  record-review validate and store a review JSON file (``--review``)
  prerequisite  consumer view: element_state_candidate is ``absent`` or ``unknown``
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parents[1]
if str(TEAM_ROOT) not in sys.path:
    sys.path.insert(0, str(TEAM_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="produce_search_coverage", description=__doc__)
    parser.add_argument(
        "command", choices=["produce", "replay", "request", "record-review", "prerequisite"]
    )
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--claim-id")
    parser.add_argument("--element")
    parser.add_argument("--query", action="append", default=[], help="Repeatable; required")
    parser.add_argument("--receipt-sha256")
    parser.add_argument("--review-sha256")
    parser.add_argument("--review", type=Path, help="Review JSON file for record-review")
    parser.add_argument("--store-root", type=Path)
    parser.add_argument("--check", action="store_true", help="produce: compute, write nothing")
    parser.add_argument("--summary", action="store_true", help="Print a compact summary")
    return parser


def summary(receipt: dict) -> dict:
    return dict(
        receipt_sha256=receipt["artifact_sha256"],
        claim_id=receipt["claim_id"],
        element=receipt["element"],
        registered_page_count=receipt["registered_page_count"],
        selected_pages=len(receipt["selected_pages"]),
        incomplete_pages=len(receipt["incomplete_pages"]),
        incomplete_reasons=sorted({r for p in receipt["pages"] for r in p["reasons"]}),
        search_prerequisites_complete=receipt["search_prerequisites_complete"],
        hits=sum(
            len(e["literal_hit_source_ids"]) + len(e["term_hit_source_ids"])
            for e in receipt["search_log"]
        ),
        element_state_effect=receipt["element_state_effect"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from proofops.adapters.local.run_store import LocalSQLiteRunStore
    from proofops.adapters.local.search_coverage_store import LocalSearchCoverageStore, run_loader
    from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
    from proofops.application.registry import Registry
    from proofops.application.uploads import UploadService

    database = args.database_path.resolve()
    if not database.is_file():
        parser.error(f"--database-path not found: {database}")
    uploads = UploadService(database, database.parent / "objects", Registry.sqlite(database))
    loader = run_loader(
        LocalSQLiteRunStore(database),
        uploads,
        OpenDataLoaderParser(database.parent / "parser-prepared"),
    )
    store = LocalSearchCoverageStore(args.store_root or database.parent / "search-coverage", loader)
    tenant, run = args.tenant_id, args.run_id
    try:
        if args.command == "produce":
            if not args.claim_id or not args.element:
                parser.error("produce needs --claim-id, --element and at least one --query")
            result = store.produce(
                tenant, run, args.claim_id, args.element, args.query, write=not args.check
            )
            result = summary(result) | {"written": not args.check} if args.summary else result
        elif args.command == "replay":
            result, _ = store.replay(tenant, run, args.receipt_sha256)
            result = summary(result) if args.summary else result
        elif args.command == "request":
            result = store.review_request(tenant, run, args.receipt_sha256)
        elif args.command == "record-review":
            if args.review is None:
                parser.error("record-review needs --review")
            result = store.record_review(
                tenant, run, json.loads(args.review.read_text(encoding="utf-8"))
            )
        else:
            if not args.claim_id or not args.element:
                parser.error("prerequisite needs --claim-id and --element")
            result = store.absence_prerequisite(
                tenant, run, args.claim_id, args.element, args.receipt_sha256, args.review_sha256
            )
    except ValueError as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
