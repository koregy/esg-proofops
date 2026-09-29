"""Append ONE audited total-cap increase to an existing Upstage session ledger.

Operator step after the user explicitly raises the SAME session's total (for
example "today total USD20"). It never reads an API key and makes no network
call. The original grant row, every recorded cost and reservation, and the
original expiry are preserved; the amendment is hash-chained to them and only
raises the gross (VAT-inclusive) total up to the USD30 hard ceiling.

``--expected-current-total-usd`` is a compare-and-swap guard: the write is
refused unless the ledger's current effective total equals it. A user-declared
entitlement (e.g. student plan) is stored as unverified metadata only; it does
not change standard-price cost estimates or lift the cap. Use ``--check`` to
validate without writing.
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
    parser = argparse.ArgumentParser(prog="amend_upstage_session", description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument(
        "--expected-current-total-usd", required=True, help="Current effective total (CAS)"
    )
    parser.add_argument("--new-total-usd", required=True, help="User-approved new session total")
    parser.add_argument(
        "--confirm-new-total-usd", required=True, help="Repeat the same total exactly"
    )
    parser.add_argument("--reason", required=True, help="Approval text/context (<=512 chars)")
    parser.add_argument("--authorized-by", required=True, help="Who approved (role, <=128)")
    parser.add_argument(
        "--authorized-at", required=True, help="Timezone-aware ISO time of the user's approval"
    )
    parser.add_argument(
        "--entitlement-statement",
        help="Optional user-declared entitlement text; recorded as unverified metadata",
    )
    parser.add_argument(
        "--entitlement-service",
        action="append",
        default=[],
        choices=["solar-pro2", "solar-pro3", "document-parse"],
        help="Service named by the declared entitlement (repeatable)",
    )
    parser.add_argument(
        "--acknowledge-same-session-expiry",
        action="store_true",
        help="Required: the amendment keeps the original grant's expiry",
    )
    parser.add_argument("--check", action="store_true", help="Validate only; write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.acknowledge_same_session_expiry:
        parser.error("--acknowledge-same-session-expiry is required")
    if args.new_total_usd != args.confirm_new_total_usd:
        parser.error("--confirm-new-total-usd must repeat --new-total-usd exactly")
    if bool(args.entitlement_statement) != bool(args.entitlement_service):
        parser.error("--entitlement-statement and --entitlement-service go together")
    from proofops.adapters.local import upstage

    ledger = args.ledger.expanduser().resolve()
    try:
        amendment = upstage.amend_session_total(
            ledger,
            expected_current_total_usd=args.expected_current_total_usd,
            new_total_usd=args.new_total_usd,
            reason=args.reason,
            authorized_by=args.authorized_by,
            authorized_at=args.authorized_at,
            entitlement_statement=args.entitlement_statement,
            entitlement_services=args.entitlement_service,
            check=args.check,
        )
    except ValueError as exc:
        parser.error(f"{exc} (nothing written)")
    written = not args.check
    print(json.dumps({"ledger": str(ledger), "written": written, "amendment": amendment}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
