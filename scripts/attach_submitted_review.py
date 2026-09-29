#!/usr/bin/env python3
"""Attach four CSVs as review references to an existing local run; no model calls.

Use a backup database with its objects/ and parser-prepared/ directories beside it.
CSV claim_id is the external annotation ID; unique literal quote + physical page
matching links it to a runtime claim. Source SHA must equal the run's frozen PDF.
The output lists those links. This never creates tag revisions or final decisions.
"""

import argparse
import json
from pathlib import Path

from proofops.adapters.local.claim_store import LocalClaimStore
from proofops.adapters.local.run_store import LocalSQLiteRunStore
from proofops.adapters.local.submitted_review import ORIGINS, attach_submission
from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
from proofops.application.registry import Registry
from proofops.application.uploads import UploadService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-db", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--origin", required=True, choices=ORIGINS)
    parser.add_argument("--operator", required=True)
    args = parser.parse_args()
    if not args.state_db.is_file():
        parser.error("Existing state database required")
    registry = Registry.sqlite(args.state_db)
    try:
        claims = LocalClaimStore(
            LocalSQLiteRunStore(args.state_db),
            UploadService(args.state_db, args.state_db.parent / "objects", registry),
            OpenDataLoaderParser(args.state_db.parent / "parser-prepared"),
        )
        values = vars(args).copy()
        values.pop("state_db")
        references = attach_submission(claims, **values)
        print(
            json.dumps(
                [
                    {
                        key: reference[key]
                        for key in (
                            "run_id",
                            "claim_id",
                            "external_claim_id",
                            "reference_sha256",
                            "status",
                            "claim_source_quality",
                        )
                    }
                    for reference in references
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        registry.close()


if __name__ == "__main__":
    main()
