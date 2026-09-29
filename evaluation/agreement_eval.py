"""Paired grade agreement: python -m evaluation.agreement_eval --input X --output Y."""

import argparse
import json
from pathlib import Path

from evaluation.metrics.agreement import score_agreement


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.input.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("AGREEMENT_PACKET_TOO_LARGE")
        packet = json.loads(args.input.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
        result = score_agreement(packet)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
