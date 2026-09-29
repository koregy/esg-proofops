"""Evaluate supplied source-bound review cases: python -m evaluation.retrieval_eval.

Input: {reviewer_kind, reviewer_id, cases: [RetrievalCase fields], rankings: {...}}.
Output is a new immutable JSON file. This does not approve or manufacture labels.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.metrics.retrieval import RetrievalCase, score_retrieval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.input.stat().st_size > 16 * 1024 * 1024:
        parser.error("evaluation packet exceeds 16 MiB")
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {
            "reviewer_kind",
            "reviewer_id",
            "cases",
            "rankings",
        }:
            raise ValueError("explicit cases, rankings and review identity required")
        result = score_retrieval(
            tuple(RetrievalCase(**case) for case in data["cases"]),
            data["rankings"],
            reviewer_kind=data["reviewer_kind"],
            reviewer_id=data["reviewer_id"],
        )
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
