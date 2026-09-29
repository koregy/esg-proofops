"""Record ONE explicit additional-current-session Upstage grant in a NEW ledger.

Operator bootstrap for a checkout that has no historical ``budget.sqlite3``.
It never reads an API key, makes no network call, and refuses to touch an
existing ledger: historical usage and previously approved limits are neither
faked nor revised. The grant's amount is the whole cap of the new ledger
(pending USD1 reservations and unknown-cost calls count against it), is bounded
by the USD30 hard ceiling, is gross (VAT-inclusive: every reservation and
settlement already applies the 1.10 multiplier), expires at ``--expires-at``
(no new reservations afterwards) and records prior cumulative usage as ``unknown``.

Run this only after the user has explicitly approved the additional amount;
``--authorized-at`` is the time of that approval, not the time of this command.
Use ``--check`` to validate the receipt without writing anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parents[1]
if str(TEAM_ROOT) not in sys.path:
    sys.path.insert(0, str(TEAM_ROOT))
# Legacy default the worker composition reads when no explicit ledger is forwarded.
DEFAULT_LEDGER = TEAM_ROOT / ".local" / "upstage" / "budget.sqlite3"
# NOTE: a ledger elsewhere is fenced only when the pilot is given the same path
# via --budget-ledger (forwarded to the worker as LOCAL_UPSTAGE_LEDGER_PATH).


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="authorize_upstage_session", description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--additional-usd", required=True, help="User-approved amount, e.g. 5")
    parser.add_argument(
        "--confirm-additional-usd", required=True, help="Repeat the same amount exactly"
    )
    parser.add_argument("--reason", required=True, help="Approval text/context (<=512 chars)")
    parser.add_argument("--authorized-by", required=True, help="Who approved (role, <=128)")
    parser.add_argument(
        "--authorized-at", required=True, help="Timezone-aware ISO time of the user's approval"
    )
    parser.add_argument(
        "--expires-at",
        required=True,
        help="Timezone-aware ISO end of the approved window (<=24h after approval); "
        "no new reservation after it, settlement of reserved calls still allowed",
    )
    parser.add_argument(
        "--acknowledge-prior-usage-unknown",
        action="store_true",
        help="Required: this grant is additional; prior cumulative usage is unknown",
    )
    parser.add_argument("--check", action="store_true", help="Validate only; write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.acknowledge_prior_usage_unknown:
        parser.error("--acknowledge-prior-usage-unknown is required")
    if args.additional_usd != args.confirm_additional_usd:
        parser.error("--confirm-additional-usd must repeat --additional-usd exactly")
    from proofops.adapters.local import upstage

    ledger = args.ledger.expanduser().resolve()
    request = dict(
        amount_usd=args.additional_usd,
        reason=args.reason,
        authorized_by=args.authorized_by,
        authorized_at=args.authorized_at,
        expires_at=args.expires_at,
    )
    try:
        if args.check:
            grant = upstage.session_grant_request(ledger, **request)
        else:
            grant = upstage.create_session_ledger(ledger, **request)
    except ValueError as exc:
        parser.error(f"{exc} (nothing written)")
    written = not args.check
    print(json.dumps({"ledger": str(ledger), "written": written, "grant": grant}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
