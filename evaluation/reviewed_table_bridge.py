"""Write an unverified canonical-table artifact from a reviewed PDF grid.

``--numeric-input`` instead writes what the existing numeric-consistency service can
read from that grid. It builds the reviewed candidate graph, hands it to the
unmodified native selected-cell verifier and that module's own ``replay_tables``
promotion, normalizes the reviewed roles over the promoted graph with
``normalize_table_bindings``, checks every citation with ``verify_source_ref``, and
then really calls ``application.numeric_analysis.analyze_numeric_consistency``.

That call supplies ``claims=()`` and ``bindings=()``: no claim of these documents is
bound to a reviewed grid yet, so the service correctly returns no outcome. The
result is an executed integration with explicit per-role holds, not a finding, not a
grade and not a claim bridge.
"""

import argparse
import json
from pathlib import Path

from proofops.adapters.local.reviewed_table import (
    _TENANT,
    load_artifact,
    numeric_input_report,
    reviewed_numeric_inputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True, help="R02g *-candidates.json")
    parser.add_argument("--pdf", type=Path, required=True, help="matching immutable source PDF")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--native-attest", action="store_true", help="run existing selected-cell verifier"
    )
    parser.add_argument(
        "--numeric-input",
        action="store_true",
        help="write the numeric-service input view and invoke the existing service",
    )
    parser.add_argument(
        "--verify-context-cells",
        action="store_true",
        help="also verify reviewed metric/unit/year literals; requires --numeric-input",
    )
    parser.add_argument(
        "--requested-values",
        action="store_true",
        help="verify exactly the reviewed value cells within the existing 24-cell bound",
    )
    args = parser.parse_args()
    if args.requested_values and not args.numeric_input:
        parser.error("--requested-values requires --numeric-input")
    if args.verify_context_cells and not args.numeric_input:
        parser.error("--verify-context-cells requires --numeric-input")
    if args.numeric_input:
        review = json.loads(args.review.read_text())
        inputs = reviewed_numeric_inputs(
            review,
            args.pdf.read_bytes(),
            tenant_id=_TENANT,
            verify_context=args.verify_context_cells,
            requested_values=args.requested_values,
        )
        artifact = numeric_input_report(inputs, review)
    else:
        artifact = load_artifact(args.review, args.pdf, native_attest=args.native_attest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
