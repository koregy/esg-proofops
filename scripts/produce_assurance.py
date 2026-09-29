"""Inspect or publish ONE explicitly selected opinion from a committed local run.

Dry plan by default. --invoke requires an existing shared budget ledger and a
key file; it never creates a budget pool or treats publication as claim coverage.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--list-page", action="append", type=int, default=[])
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument(
        "--request-id", help="Stable UUID for this opinion attempt; required to invoke"
    )
    parser.add_argument("--model", choices=("solar-pro3", "solar-pro4"), default="solar-pro3")
    parser.add_argument("--invoke", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.database_path.is_file():
        parser.error("existing run database required; nothing was created")
    if args.list_page and (args.invoke or args.source_id):
        parser.error("--list-page is read-only; choose source IDs in a separate invocation")
    if any(page < 1 for page in args.list_page):
        parser.error("--list-page must be positive")
    if not args.list_page and not args.source_id:
        parser.error("select --source-id or inspect --list-page")
    if args.invoke and (
        args.ledger is None
        or not args.ledger.is_file()
        or args.key_file is None
        or not args.key_file.is_file()
        or args.receipts is None
        or args.request_id is None
    ):
        parser.error("--invoke requires existing --ledger, --key-file, --receipts and --request-id")
    if args.invoke:
        from proofops.adapters.local.upstage import UpstageProbe

        # Merely touching an empty file must never mint the transport's legacy
        # allowance. Validate the existing policy and accounting without writes.
        try:
            with sqlite3.connect(args.ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
                UpstageProbe._authorized_limit(db)
                UpstageProbe._call_total(db)
        except (sqlite3.Error, ValueError):
            parser.error("existing authorized budget ledger is invalid; no call made")

    from proofops.adapters.local.assurance_producer import run_assurance_producer
    from proofops.adapters.local.run_artifacts import load_run_graph
    from proofops.adapters.local.run_store import LocalSQLiteRunStore
    from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
    from proofops.application.assurance_producer import select_opinion_boundary
    from proofops.application.registry import Registry
    from proofops.application.uploads import UploadService
    from proofops.domain.provenance import canonical_hash

    database = args.database_path.resolve()
    registry = Registry.sqlite(database)
    uploads = UploadService(database, database.parent / "objects", registry)
    store = LocalSQLiteRunStore(database)
    document_parser = OpenDataLoaderParser(database.parent / "parser-prepared")
    graph = load_run_graph(
        store, uploads, document_parser, tenant_id=args.tenant_id, run_id=args.run_id
    )
    if args.list_page:
        result = {
            "status": "inspection_only",
            "document_version_id": graph.document_version_id,
            "sources": [
                {
                    "source_id": block.source_id,
                    "page": block.page_num,
                    "quality": block.quality,
                    "text_preview": block.raw_text[:240],
                    "has_winner": block.winner is not None,
                }
                for block in graph.blocks
                if block.page_num in args.list_page
            ],
        }
    else:
        boundary, _ = select_opinion_boundary(graph, args.source_id)
        result = {
            "status": "planned",
            "tenant_id": args.tenant_id,
            "run_id": args.run_id,
            "document_version_id": boundary.document_version_id,
            "parse_manifest_id": boundary.parse_manifest_id,
            "source_ids": list(boundary.source_ids),
            "boundary_sha256": canonical_hash(list(boundary.source_ids)),
            "model": args.model,
            "model_calls": 0,
            "coverage": "not_assessed",
        }
        if args.invoke:
            key_lines = args.key_file.read_text(encoding="utf-8-sig").splitlines()
            keys = [
                line.split("=", 1)[1].strip().strip('"').strip("'")
                for line in key_lines
                if line.startswith("UPSTAGE_API_KEY=")
            ]
            if len(keys) != 1 or not keys[0]:
                parser.error("key file must contain one nonempty UPSTAGE_API_KEY entry")
            probe = UpstageProbe(keys[0], args.ledger.resolve(), model=args.model)
            del keys, key_lines
            result = run_assurance_producer(
                store=store,
                uploads=uploads,
                parser=document_parser,
                tenant_id=args.tenant_id,
                run_id=args.run_id,
                source_ids=boundary.source_ids,
                probe=probe,
                receipts=args.receipts,
                request_id=args.request_id,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
