"""Offline, source-bound section candidates; never approves scope or evidence.

Run: uv run python -m evaluation.report_sections --pdf PATH --output NEW_JSON
No network calls. Printed page numbers are never treated as physical destinations.

Compatibility wrapper: the inspection logic lives in
``proofops.adapters.parsing.report_sections`` (product code imports it from there,
never from ``evaluation``). This module keeps the historical path-based ``inspect``
and CLI, whose output (including ``source_path`` and ``map_sha256``) is unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from proofops.adapters.parsing.report_sections import (  # noqa: F401 - re-exported API
    MAX_PAGES,
    MAX_PDF_BYTES,
    PATTERNS,
    POLICY_HASH,
    SectionInspectionError,
    _normalized_title,
    build_map,
    inspect_pdf_bytes,
    role,
    toc_links,
    toc_text_fallback,
)


def inspect(pdf: Path):
    # Existing ingest gates still own production acceptance; this is a bounded local study.
    if pdf.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("local section study limit: 100 MiB")
    # One bounded read: the digest and every parser see the same immutable bytes.
    with pdf.open("rb") as stream:
        content = stream.read(MAX_PDF_BYTES + 1)
    return inspect_pdf_bytes(content, source_path=str(pdf.resolve()))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pdf", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    result = inspect(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "page_count",
                    "method",
                    "claim_candidate_pages",
                    "unknown_pages",
                    "conflict_pages",
                )
            }
        )
    )
