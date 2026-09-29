"""Bounded, opt-in Upstage text probe; independent of production model composition.

One local SQLite ledger covers the user's cumulative USD 10 base authorization.
The general spending ceiling is USD30 via authorize_additional_budget(); both base and
any authorized extension are durably recorded in the ledger before any spending
counts against the new limit.  Before each request, reserve USD 1 (deliberately
much larger than these tiny requests at the pinned rates).  Unknown/failed calls
keep that reservation.  No retries, tools, redirects, document uploads or
credential logging are enabled.

Separately, create_session_ledger() (scripts/authorize_upstage_session.py) can
create a NEW ledger that carries one explicit additional-current-session grant
(<= USD30) as its entire cap; prior cumulative usage stays "unknown" and legacy
ledgers without that table keep the historical flow unchanged.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from proofops.application.budget import PricingSnapshot, TokenUsage, usage_cost
from proofops.domain.provenance import canonical_hash

MODEL = "solar-pro3"
MODEL_PRO4 = "solar-pro4"
# Text, document parse and information-extract rates rechecked at the official
# pricing page on 2026-09-25; keep one deadline across transports.
PRICE_RECHECK_AT = datetime(2026, 10, 2, tzinfo=UTC)
GENERAL_CEILING_USD = Decimal("30.00")
R32_HISTORICAL_GENERAL_USD = Decimal("20.00")
RECORDED_GENERAL_REASON = (
    "User 2026-09-18 approved additional USD10, cumulative USD20; "
    "existing spend and unsettled reservations preserved."
)
# Recognise the existing R32 authorization exactly; it is historical and cannot
# be selected by an environment variable or a caller of the general probe.
R32_REASON = (
    "2026-09-24 user: 이번 평가용 누적 $22 승인; R32 Samsung/Kakao/Hana "
    "fixed-report evaluation only; cumulative USD22 including prior reservations; "
    "no unrelated runs."
)
PRICE = PricingSnapshot(
    "upstage-solar-pro3-2026-09-09",
    MODEL,
    "provider-managed-unverified",
    "2026-09-09T00:00:00Z",
    Decimal("0.15"),
    Decimal("0.60"),
)
# Source: https://www.upstage.ai/pricing/api (2026-09-09); prices exclude 10% VAT.
# Conservative undiscounted Pro4 snapshot; promotions ignored.
PRICE_PRO4 = PricingSnapshot(
    "upstage-solar-pro4-2026-09-12",
    MODEL_PRO4,
    "provider-managed-unverified",
    "2026-09-12T00:00:00Z",
    Decimal("0.30"),
    Decimal("1.20"),
)
_PRICES = {MODEL: PRICE, MODEL_PRO4: PRICE_PRO4}
POLICY = {
    "limit_usd": "10.00",
    "reservation_usd": "1.00",
    "price": PRICE.to_dict(),
    "vat_allowance": "1.10",
    "max_request_bytes": 16384,
    "max_output_tokens": 4096,
}

# Shared fail-closed contract: every code here means the current operation must
# not make another billable call (reservation retained/unsettled, ledger
# exhausted/invalid, or request cannot proceed without re-authorization).
# Valid-usage malformed model content (MODEL_SPAN_OR_SCHEMA_INVALID etc.) is
# NOT in this set and stays unknown-and-continue. UPSTREAM_UNAVAILABLE is the
# extractor-sanitized unknown transport failure; it also halts.
UPSTAGE_TRANSPORT_STOP_CODES = frozenset(
    {
        "BUDGET_EXHAUSTED",
        "BUDGET_POLICY_MISMATCH",
        "DUPLICATE_PROBE_REQUEST",
        "BUDGET_SETTLEMENT_INVALID",
        "PRICE_RECHECK_REQUIRED",
        "PROBE_REQUEST_TOO_LARGE",
        "UPSTAGE_REQUEST_FAILED",
        "UPSTAGE_RECEIPT_INVALID_RESERVATION_RETAINED",
        "UPSTREAM_UNAVAILABLE",
        *(f"UPSTAGE_HTTP_{n}" for n in (400, 401, 403, 404, 429, 500, 502, 503)),
    }
)


def _zero_request_usage() -> dict:
    return {
        "model_calls": 0,
        "reserved_calls": 0,
        "settled_calls": 0,
        "unsettled_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "token_usage_complete": True,
        "cost_with_vat_reserve_usd": "0",
        "committed_or_reserved_usd": "0",
        "unknown_reservation_cost_usd": None,
    }


def request_usage(ledger, request_ids) -> dict:
    """Shared read-only accounting over supplied request IDs.

    Counts only distinct supplied IDs with a ledger reservation; IDs without
    a reservation (e.g. preflight rejections) are ignored. Unsettled
    reservations stay unknown while their reservation remains in
    ``committed_or_reserved_usd``. Never aggregates global ledger totals.
    The ledger is opened read-only so a missing ledger cannot create a file;
    empty ID lists return zero without opening the ledger.
    """
    identifiers = list(dict.fromkeys(request_ids))
    if not identifiers:
        return _zero_request_usage()
    if ledger is None:
        raise ValueError("ACCOUNTING_UNAVAILABLE")
    try:
        uri = Path(ledger).resolve().as_uri() + "?mode=ro"
        entries = []
        with sqlite3.connect(uri, uri=True) as db:
            for identifier in identifiers:
                row = db.execute(
                    "SELECT committed, receipt FROM probe_calls WHERE request_id=?",
                    (identifier,),
                ).fetchone()
                if row is not None:
                    amount = Decimal(row[0])
                    if not amount.is_finite() or amount < 0:
                        raise ValueError("invalid committed amount")
                    entries.append((amount, json.loads(row[1]) if row[1] else None))
    except (sqlite3.Error, ValueError, TypeError, OSError, InvalidOperation):
        raise ValueError("ACCOUNTING_UNAVAILABLE") from None
    settled = [receipt for _, receipt in entries if receipt is not None]
    unknown = len(entries) - len(settled)
    cost = sum((amount for amount, _ in entries), Decimal(0))
    input_tokens = output_tokens = document_parse_pages = 0
    has_parse_receipt = False
    has_aggregate_bound = False
    for receipt in settled:
        if not isinstance(receipt, dict) or any(
            key in receipt and not isinstance(receipt[key], str)
            for key in ("model", "provider_model")
        ):
            raise ValueError("ACCOUNTING_UNAVAILABLE")
        if receipt.get("settlement_origin") == "provider_aggregate_upper_bound":
            if not isinstance(receipt.get("aggregate_bound_id"), str):
                raise ValueError("ACCOUNTING_UNAVAILABLE")
            has_aggregate_bound = True
            continue
        parse_models = {"document-parse-260128", "document-parse"}
        if receipt.get("model") in parse_models or receipt.get("provider_model") in parse_models:
            if (
                receipt.get("model") not in parse_models
                or receipt.get("provider_model") not in parse_models
                or "input_tokens" in receipt
                or "output_tokens" in receipt
                or type(receipt.get("pages")) is not int
                or receipt["pages"] < 1
                or not isinstance(receipt.get("usage"), dict)
                or type(receipt["usage"].get("pages")) is not int
                or receipt["usage"]["pages"] != receipt["pages"]
            ):
                raise ValueError("ACCOUNTING_UNAVAILABLE")
            document_parse_pages += receipt["pages"]
            has_parse_receipt = True
            continue
        has_input = "input_tokens" in receipt
        has_output = "output_tokens" in receipt
        if has_input or has_output:
            if (
                not (has_input and has_output)
                or type(receipt["input_tokens"]) is not int
                or type(receipt["output_tokens"]) is not int
                or receipt["input_tokens"] < 0
                or receipt["output_tokens"] < 0
            ):
                raise ValueError("ACCOUNTING_UNAVAILABLE")
            input_tokens += receipt["input_tokens"]
            output_tokens += receipt["output_tokens"]
            continue
        raise ValueError("ACCOUNTING_UNAVAILABLE")
    result = {
        "model_calls": len(entries),
        "reserved_calls": len(entries),
        "settled_calls": len(settled),
        "unsettled_calls": unknown,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_usage_complete": not unknown and not has_parse_receipt and not has_aggregate_bound,
        "cost_with_vat_reserve_usd": "unknown" if unknown or has_aggregate_bound else str(cost),
        "committed_or_reserved_usd": str(cost),
        "unknown_reservation_cost_usd": "unknown" if unknown or has_aggregate_bound else None,
    }
    if has_parse_receipt:
        result["document_parse_pages"] = document_parse_pages
    return result


# Explicit NEW session ledger (opt-in; legacy ledgers never carry this table).
# A session grant is a fixed ADDITIONAL allowance authorized for the current
# session only.  It says nothing about prior cumulative usage (recorded as
# "unknown"), never revises historical limits, and is the whole cap of the ledger
# it lives in: it replaces, rather than adds to, the legacy POLICY base.
SESSION_GRANT_SCHEMA = "upstage-session-grant-v1"
SESSION_GRANT_SCOPE = "additional-current-session"
SESSION_GRANT_FIXED = {
    "schema": SESSION_GRANT_SCHEMA,
    "scope": SESSION_GRANT_SCOPE,
    "prior_cumulative_usage": "unknown",
    "historical_ledger": "not-present-in-this-checkout",
    "revises_prior_limits": False,
    "hard_ceiling_usd": str(GENERAL_CEILING_USD),
    # Every reservation (USD1) and settlement already carries the 1.10 VAT
    # multiplier, so the grant is a gross cap; never reinterpreted as net.
    "amount_basis": "gross-including-10pct-vat",
}
# A grant is valid for one bounded session; new reservations stop at expires_at.
SESSION_GRANT_MAX_HOURS = 24
_SESSION_GRANT_KEYS = frozenset(SESSION_GRANT_FIXED) | {
    "authorized_usd",
    "reason",
    "authorized_by",
    "authorized_at",
    "recorded_at",
    "expires_at",
}
_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS probe_policy (id INTEGER PRIMARY KEY, body TEXT)",
    "CREATE TABLE IF NOT EXISTS probe_calls (request_id TEXT PRIMARY KEY, "
    "signature TEXT NOT NULL, committed TEXT NOT NULL, receipt TEXT)",
    "CREATE TABLE IF NOT EXISTS probe_extensions "
    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "additional_usd TEXT NOT NULL, reason TEXT NOT NULL, authorized_at TEXT NOT NULL)",
)


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _session_amount(raw) -> Decimal:
    """Parse a grant amount: plain decimal, at most 2 places, 0 < amount <= ceiling."""
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,2})?", raw):
        raise ValueError("AUTHORIZATION_AMOUNT_INVALID")
    amount = Decimal(raw).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("AUTHORIZATION_AMOUNT_INVALID")
    if amount > GENERAL_CEILING_USD:
        raise ValueError("AUTHORIZATION_EXCEEDS_CEILING")
    return amount


def _aware_timestamp(raw) -> datetime:
    if not isinstance(raw, str) or len(raw) > 64:
        raise ValueError("AUTHORIZATION_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("AUTHORIZATION_TIME_INVALID") from None
    if parsed.tzinfo is None:
        raise ValueError("AUTHORIZATION_TIME_INVALID")
    return parsed


def _session_expiry(approved: datetime, raw) -> datetime:
    expires = _aware_timestamp(raw)
    seconds = (expires - approved).total_seconds()
    if not 0 < seconds <= SESSION_GRANT_MAX_HOURS * 3600:
        raise ValueError("AUTHORIZATION_EXPIRY_INVALID")
    return expires


def session_grant_expired(grant: dict) -> bool:
    return datetime.now(UTC) >= _aware_timestamp(grant["expires_at"])


def read_session_grant(db) -> dict | None:
    """Return the validated session grant, None for a legacy ledger; fail closed."""
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='probe_session_grant'"
    ).fetchone():
        return None
    try:
        rows = db.execute("SELECT id, body FROM probe_session_grant").fetchall()
        if len(rows) != 1 or rows[0][0] != 1 or not isinstance(rows[0][1], str):
            raise ValueError
        grant = json.loads(rows[0][1])
        if (
            not isinstance(grant, dict)
            or set(grant) != _SESSION_GRANT_KEYS
            or any(grant[key] != value for key, value in SESSION_GRANT_FIXED.items())
            or rows[0][1] != _canonical_json(grant)
        ):
            raise ValueError
        amount = _session_amount(grant["authorized_usd"])
        if grant["authorized_usd"] != str(amount):
            raise ValueError
        for key, limit in (("reason", 512), ("authorized_by", 128)):
            text = grant[key]
            if not isinstance(text, str) or not text.strip() or text != text.strip():
                raise ValueError
            if len(text) > limit:
                raise ValueError
        approved = _aware_timestamp(grant["authorized_at"])
        _aware_timestamp(grant["recorded_at"])
        _session_expiry(approved, grant["expires_at"])
        # The grant is the whole allowance; it can never be topped up in place.
        if db.execute("SELECT COUNT(*) FROM probe_extensions").fetchone()[0]:
            raise ValueError
    except (sqlite3.Error, InvalidOperation, TypeError, ValueError, KeyError):
        raise ValueError("BUDGET_POLICY_MISMATCH") from None
    return grant


def session_grant_limit(db) -> Decimal | None:
    grant = read_session_grant(db)
    return None if grant is None else Decimal(grant["authorized_usd"])


def session_grant_request(
    ledger: Path,
    *,
    amount_usd: str,
    reason: str,
    authorized_by: str,
    authorized_at: str,
    expires_at: str,
) -> dict:
    """Validate an operator receipt and return the grant body; writes nothing."""
    amount = _session_amount(amount_usd)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 512:
        raise ValueError("AUTHORIZATION_REASON_REQUIRED")
    if (
        not isinstance(authorized_by, str)
        or not authorized_by.strip()
        or len(authorized_by.strip()) > 128
    ):
        raise ValueError("AUTHORIZATION_OPERATOR_REQUIRED")
    approved = _aware_timestamp(authorized_at)
    now = datetime.now(UTC)
    if (approved - now).total_seconds() > 300:
        raise ValueError("AUTHORIZATION_TIME_INVALID")
    expires = _session_expiry(approved, expires_at)
    if expires <= now:
        raise ValueError("AUTHORIZATION_EXPIRY_INVALID")
    if ledger.exists() or ledger.with_name(ledger.name + ".responses").exists():
        raise ValueError("LEDGER_ALREADY_EXISTS")
    return {
        **SESSION_GRANT_FIXED,
        "authorized_usd": str(amount),
        "reason": reason.strip(),
        "authorized_by": authorized_by.strip(),
        "authorized_at": approved.astimezone(UTC).isoformat(),
        "recorded_at": now.isoformat(),
        "expires_at": expires.astimezone(UTC).isoformat(),
    }


def create_session_ledger(
    ledger: Path,
    *,
    amount_usd: str,
    reason: str,
    authorized_by: str,
    authorized_at: str,
    expires_at: str,
) -> dict:
    """Atomically create a NEW ledger holding one explicit session grant.

    Refuses any existing ledger (never rewrites history or old approved limits).
    The file is fully built under a temporary name and then hard-linked into
    place, so no observer ever sees a legacy USD10 ledger without the grant.
    No network access and no credential is involved.
    """
    ledger = Path(ledger)
    grant = session_grant_request(
        ledger,
        amount_usd=amount_usd,
        reason=reason,
        authorized_by=authorized_by,
        authorized_at=authorized_at,
        expires_at=expires_at,
    )
    amount = Decimal(grant["authorized_usd"])
    now = datetime.fromisoformat(grant["recorded_at"])
    ledger.parent.mkdir(parents=True, exist_ok=True)
    staging = ledger.with_name(f".{ledger.name}.{os.getpid()}.{now.timestamp():.6f}.staging")
    try:
        db = sqlite3.connect(staging)
        try:
            with db:
                for statement in _LEDGER_DDL:
                    db.execute(statement)
                db.execute(
                    "CREATE TABLE probe_session_grant "
                    "(id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT INTO probe_policy VALUES (1, ?)", (json.dumps(POLICY, sort_keys=True),)
                )
                db.execute(
                    "INSERT INTO probe_session_grant VALUES (1, ?)", (_canonical_json(grant),)
                )
            if UpstageProbe._authorized_limit(db) != amount:
                raise ValueError("BUDGET_POLICY_MISMATCH")
        finally:
            db.close()
        try:
            os.link(staging, ledger)
        except FileExistsError:
            raise ValueError("LEDGER_ALREADY_EXISTS") from None
    finally:
        staging.unlink(missing_ok=True)
    ledger.chmod(0o600)
    return grant


class UpstageProbe:
    """Local development transport; not a production source-quality attestation."""

    def __init__(self, api_key: str, ledger: Path, *, model: str = MODEL):
        if not isinstance(model, str) or model not in _PRICES:
            raise ValueError("UNSUPPORTED_MODEL")
        if not api_key or any(c.isspace() for c in api_key):
            raise ValueError("UPSTAGE_API_KEY_MISSING_OR_INVALID")
        self._model = model
        self._price = _PRICES[model]
        self._api_key = api_key
        self.ledger = ledger
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(ledger) as db:
            # probe_extensions rows record one explicit budget extension with audit reason
            # and timestamp; additive to POLICY["limit_usd"]; rows are append-only.
            for statement in _LEDGER_DDL:
                db.execute(statement)
            body = json.dumps(POLICY, sort_keys=True)
            db.execute("INSERT OR IGNORE INTO probe_policy VALUES (1, ?)", (body,))
            if db.execute("SELECT body FROM probe_policy WHERE id=1").fetchone()[0] != body:
                raise ValueError("BUDGET_POLICY_MISMATCH")
        ledger.chmod(0o600)
        self._responses = ledger.with_name(ledger.name + ".responses")
        self._responses.mkdir(mode=0o700, exist_ok=True)
        self._responses.chmod(0o700)

    @staticmethod
    def _authorized_limit(db) -> Decimal:
        """Validate all recorded grants, then return this probe's general-run cap."""
        stored = db.execute("SELECT body FROM probe_policy WHERE id=1").fetchone()
        if stored is None or stored[0] != json.dumps(POLICY, sort_keys=True):
            raise ValueError("BUDGET_POLICY_MISMATCH")
        session_limit = session_grant_limit(db)
        if session_limit is not None:
            return session_limit
        general = Decimal(POLICY["limit_usd"])
        scoped_count = 0
        for extension_id, raw, reason, authorized_at in db.execute(
            "SELECT id, additional_usd, reason, authorized_at FROM probe_extensions ORDER BY id"
        ):
            try:
                amount = Decimal(raw)
                timestamp = datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
                if (
                    not amount.is_finite()
                    or amount <= 0
                    or not isinstance(reason, str)
                    or not reason.strip()
                    or len(reason) > 512
                    or timestamp.tzinfo is None
                ):
                    raise ValueError
            except (InvalidOperation, AttributeError, TypeError, ValueError):
                raise ValueError("BUDGET_POLICY_MISMATCH") from None
            if reason == R32_REASON:
                scoped_count += 1
                if (
                    extension_id != 2
                    or amount != Decimal("2.00")
                    or scoped_count != 1
                    or general != R32_HISTORICAL_GENERAL_USD
                ):
                    raise ValueError("BUDGET_POLICY_MISMATCH")
            else:
                try:
                    decoded = json.loads(reason)
                    canonical_general = (
                        isinstance(decoded, dict)
                        and set(decoded) == {"scope", "reason"}
                        and decoded["scope"] == "general"
                        and isinstance(decoded["reason"], str)
                        and bool(decoded["reason"].strip())
                        and reason == json.dumps(decoded, sort_keys=True, separators=(",", ":"))
                    )
                except (TypeError, ValueError):
                    canonical_general = False
                recorded_general = (
                    extension_id == 1
                    and amount == Decimal("10.00")
                    and reason == RECORDED_GENERAL_REASON
                )
                if not (canonical_general or recorded_general) or (
                    scoped_count
                    and (extension_id != 3 or amount != Decimal("10.00") or not canonical_general)
                ):
                    raise ValueError("BUDGET_POLICY_MISMATCH")
                general += amount
                if general > GENERAL_CEILING_USD:
                    raise ValueError("BUDGET_POLICY_MISMATCH")
        return general

    @staticmethod
    def _call_total(db) -> Decimal:
        total = Decimal(0)
        for raw, receipt in db.execute("SELECT committed, receipt FROM probe_calls"):
            try:
                amount = Decimal(raw)
                if (
                    not amount.is_finite()
                    or amount < 0
                    or amount > Decimal(POLICY["reservation_usd"])
                ):
                    raise ValueError
                if receipt is None:
                    if amount != Decimal(POLICY["reservation_usd"]):
                        raise ValueError
                else:
                    parsed = json.loads(receipt)
                    if not isinstance(parsed, dict):
                        raise ValueError
                    if (
                        "cost_with_vat_reserve_usd" in parsed
                        and Decimal(parsed["cost_with_vat_reserve_usd"]) != amount
                    ):
                        raise ValueError
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError("BUDGET_LEDGER_INVALID") from None
            total += amount
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='probe_aggregate_bounds'"
        ).fetchone():
            for bound_id, raw, audit_raw in db.execute(
                "SELECT id, committed, audit FROM probe_aggregate_bounds"
            ):
                try:
                    amount = Decimal(raw)
                    audit = json.loads(audit_raw)
                    if (
                        not amount.is_finite()
                        or amount < 0
                        or not bound_id.startswith("aggregate:")
                        or not isinstance(audit, dict)
                        or Decimal(audit["total_usd"]) != amount
                    ):
                        raise ValueError
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    raise ValueError("BUDGET_LEDGER_INVALID") from None
                total += amount
        return total

    @property
    def model(self) -> str:
        return self._model

    def _reserve(self, request_id, body):
        with sqlite3.connect(self.ledger, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            authorized_limit = self._authorized_limit(db)
            grant = read_session_grant(db)
            if grant is not None and session_grant_expired(grant):
                # No new spend after the grant window; settlement stays allowed.
                raise ValueError("BUDGET_EXHAUSTED")
            if db.execute("SELECT 1 FROM probe_calls WHERE request_id=?", (request_id,)).fetchone():
                raise ValueError("DUPLICATE_PROBE_REQUEST")
            total = self._call_total(db)
            if total + Decimal(POLICY["reservation_usd"]) > authorized_limit:
                raise ValueError("BUDGET_EXHAUSTED")
            db.execute(
                "INSERT INTO probe_calls VALUES (?, ?, ?, NULL)",
                (request_id, canonical_hash(body), POLICY["reservation_usd"]),
            )

    def _settle(self, request_id, cost, receipt):
        with sqlite3.connect(self.ledger) as db:
            updated = db.execute(
                "UPDATE probe_calls SET committed=?, receipt=? "
                "WHERE request_id=? AND receipt IS NULL AND committed=?",
                (
                    str(cost),
                    json.dumps(receipt, sort_keys=True),
                    request_id,
                    POLICY["reservation_usd"],
                ),
            ).rowcount
            if updated != 1:
                raise ValueError("BUDGET_SETTLEMENT_INVALID")

    def is_recorded_output_truncation(self, request: dict, *, request_id: str) -> bool:
        """Read-only classification; never release reservations or reuse partial content."""
        try:
            if request["request_id"] != request_id:
                return False
            body = self.request_body(
                request["system_prompt"],
                request["user_json"],
                request_id=request_id,
                max_tokens=request["max_tokens"],
                json_mode=request["json_mode"],
            )
            with sqlite3.connect(self.ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
                self._authorized_limit(db)
                row = db.execute(
                    "SELECT signature, committed, receipt FROM probe_calls WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            if row != (canonical_hash(body), POLICY["reservation_usd"], None):
                return False
            saved = json.loads(
                (self._responses / (canonical_hash(request_id) + ".json")).read_text()
            )
            if saved["request_id"] != request_id:
                return False
            data = saved["provider_response"]
            usage = TokenUsage(
                data["usage"]["prompt_tokens"],
                data["usage"]["completion_tokens"],
                0,
                0,
                0,
                "succeeded",
                data["id"],
            )
            if usage.input_tokens is None or usage.output_tokens is None:
                return False
            cost = Decimal(usage_cost(usage, self._price.to_dict())) * Decimal("1.10")
            allowed = (
                (MODEL, "solar-pro3-260323")
                if self.model == MODEL
                else (MODEL_PRO4, "solar-pro4-260806")
            )
            choice = data["choices"][0]
            return (
                data["model"] in allowed
                and choice["finish_reason"] == "length"
                and isinstance(choice["message"]["content"], str)
                and 0 < usage.output_tokens <= request["max_tokens"]
                and cost <= Decimal(POLICY["reservation_usd"])
            )
        except (
            OSError,
            sqlite3.Error,
            KeyError,
            TypeError,
            ValueError,
            IndexError,
            InvalidOperation,
        ):
            return False

    def authorize_additional_budget(self, additional_usd: str, *, reason: str) -> dict:
        """Append explicit authorization, preserving history and the process ceiling."""
        if not isinstance(additional_usd, str):
            raise ValueError("AUTHORIZATION_AMOUNT_INVALID")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise ValueError("AUTHORIZATION_REASON_REQUIRED")
        stored_reason = json.dumps(
            {"scope": "general", "reason": reason.strip()},
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(stored_reason) > 512:
            raise ValueError("AUTHORIZATION_REASON_REQUIRED")
        try:
            amount = Decimal(additional_usd)
        except InvalidOperation:
            raise ValueError("AUTHORIZATION_AMOUNT_INVALID") from None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("AUTHORIZATION_AMOUNT_INVALID")
        stored_usd = format(amount, "f")
        with sqlite3.connect(self.ledger, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            current_limit = self._authorized_limit(db)
            if session_grant_limit(db) is not None:
                # A session grant is a fixed, separately authorized allowance.
                raise ValueError("SESSION_GRANT_NOT_EXTENSIBLE")
            if current_limit + amount > GENERAL_CEILING_USD:
                raise ValueError("AUTHORIZATION_EXCEEDS_CEILING")
            authorized_at = datetime.now(UTC).isoformat()
            db.execute(
                "INSERT INTO probe_extensions "
                "(additional_usd, reason, authorized_at) VALUES (?,?,?)",
                (stored_usd, stored_reason, authorized_at),
            )
            self._authorized_limit(db)
        return {
            "previous_limit_usd": str(current_limit),
            "additional_usd": stored_usd,
            "authorized_at": authorized_at,
            "reason": reason.strip(),
        }

    def summary(self):
        with sqlite3.connect(self.ledger) as db:
            rows = db.execute("SELECT committed, receipt FROM probe_calls").fetchall()
            authorized_limit = self._authorized_limit(db)
            committed = self._call_total(db)
            grant = read_session_grant(db)
        result = {
            "limit_usd": POLICY["limit_usd"],
            "authorized_limit_usd": str(authorized_limit),
            "calls": len(rows),
            "unsettled_calls": sum(receipt is None for _, receipt in rows),
            "committed_usd": str(committed),
        }
        if grant is not None:
            result["session_grant"] = grant
        return result

    def _post(self, body):
        connection = http.client.HTTPSConnection("api.upstage.ai", timeout=60)
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={
                    "Authorization": "Bearer " + self._api_key,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"UPSTAGE_HTTP_{response.status}")
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("UPSTAGE_RESPONSE_TOO_LARGE")
            return json.loads(raw)
        finally:
            connection.close()

    def request_body(
        self,
        system: str,
        user_json: str,
        *,
        request_id: str,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ):
        # Pricing reverified at https://www.upstage.ai/pricing/api on 2026-09-25:
        # Pro3 $0.15/$0.60, Pro4 $0.30/$1.20 per M tokens (conservative, promotions ignored).
        # Recheck in one week; historical price IDs/rates and ledger policy stay unchanged.
        if datetime.now(UTC) >= PRICE_RECHECK_AT:
            raise ValueError("PRICE_RECHECK_REQUIRED")

        if (
            not isinstance(system, str)
            or not isinstance(user_json, str)
            or not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 128
            or type(max_tokens) is not int
            or not 1 <= max_tokens <= 4096
            or type(json_mode) is not bool
        ):
            raise ValueError("INVALID_PROBE_REQUEST")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_json},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if (
            len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            > POLICY["max_request_bytes"]
        ):
            raise ValueError("PROBE_REQUEST_TOO_LARGE")
        return body

    def complete(
        self,
        system: str,
        user_json: str,
        *,
        request_id: str,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ):
        body = self.request_body(
            system, user_json, request_id=request_id, max_tokens=max_tokens, json_mode=json_mode
        )
        self._reserve(request_id, body)
        try:
            data = self._post(body)
            # Preserve decoded HTTP-200 JSON before validating usage/content.
            # Hash the caller ID so it cannot become a filesystem path.
            path = self._responses / (canonical_hash(request_id) + ".json")
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as stream:
                json.dump({"request_id": request_id, "provider_response": data}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception as error:
            # Do not expose exception bodies/headers which may echo a credential.
            code = str(error)
            if code not in {f"UPSTAGE_HTTP_{n}" for n in (400, 401, 403, 404, 429, 500, 502, 503)}:
                code = "UPSTAGE_REQUEST_FAILED"
            raise ValueError(code) from None
        try:
            native = data["usage"]
            usage = TokenUsage(
                native["prompt_tokens"],
                native["completion_tokens"],
                0,
                0,
                0,
                "succeeded",
                data["id"],
            )
            if usage.input_tokens is None or usage.output_tokens is None:
                raise ValueError("unknown token usage")
            cost = Decimal(usage_cost(usage, self._price.to_dict())) * Decimal("1.10")
            choice = data["choices"][0]
            content = choice["message"]["content"]
            allowed_models = (
                (MODEL, "solar-pro3-260323")
                if self.model == MODEL
                else (MODEL_PRO4, "solar-pro4-260806")
            )
            if (
                data["model"] not in allowed_models
                or choice["finish_reason"] != "stop"
                or not isinstance(content, str)
                or not content.strip()
                or usage.output_tokens > max_tokens
                or cost > Decimal("1.00")
            ):
                raise ValueError("invalid provider completion")
        except (KeyError, TypeError, ValueError, IndexError):
            raise ValueError("UPSTAGE_RECEIPT_INVALID_RESERVATION_RETAINED") from None
        receipt = {
            "model": self.model,
            "provider_request_id": data["id"],
            "provider_model": data["model"],
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "price_snapshot": self._price.to_dict(),
            "cost_with_vat_reserve_usd": str(cost),
            "response_sha256": canonical_hash(data),
        }
        if json_mode:
            receipt["response_format"] = {"type": "json_object"}
        self._settle(request_id, cost, receipt)
        return {**receipt, "content": content}
