"""Reconcile reserved Upstage calls from a user-supplied provider usage/billing export.

See outputs/agent-results/R48-ledger-policy/NOTES.md for the evidence contract.
No network calls; dry-run is the default and opens the ledger read-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from proofops.adapters.local.upstage import (
    MODEL,
    MODEL_PRO4,
    POLICY,
    PRICE,
    PRICE_PRO4,
    UpstageProbe,
)
from proofops.application.budget import TokenUsage, usage_cost
from proofops.domain.provenance import canonical_hash

PROVIDER_ID_SQL_KEY = (
    "trim(json_extract(receipt, '$.provider_request_id'), "
    "char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,"
    "8198,8199,8200,8201,8202,8232,8233,8239,8287,12288))"
)


def canonical_provider_id(value: object) -> str:
    """Trim ends; keep case; require 1-256 printable ASCII non-whitespace chars."""
    if not isinstance(value, str):
        raise ValueError("provider request ID malformed")
    value = value.strip()
    if (
        not value
        or len(value) > 256
        or any(not char.isascii() or not char.isprintable() or char.isspace() for char in value)
    ):
        raise ValueError("provider request ID malformed")
    return value


def stored_provider_ids(db: sqlite3.Connection) -> set[str]:
    ids = set()
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('probe_calls', 'probe_settlement_audit')"
        )
    }
    for table in tables:
        for (raw,) in db.execute(f"SELECT receipt FROM {table} WHERE receipt IS NOT NULL"):
            receipt = json.loads(raw)
            if not isinstance(receipt, dict):
                raise ValueError("stored receipt malformed")
            provider_id = receipt.get("provider_request_id")
            if provider_id is not None:
                ids.add(canonical_provider_id(provider_id))
    return ids


def provider_id_is_used(db: sqlite3.Connection, provider_id: str) -> bool:
    return any(
        db.execute(
            f"SELECT 1 FROM {table} WHERE receipt IS NOT NULL AND {PROVIDER_ID_SQL_KEY}=? LIMIT 1",
            (provider_id,),
        ).fetchone()
        for table in ("probe_calls", "probe_settlement_audit")
    )


def records(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream))
    data = json.loads(path.read_text(encoding="utf-8"))
    result = data["records"] if isinstance(data, dict) else data
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise ValueError("EVIDENCE_FORMAT_INVALID")
    return result


def utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
    return parsed.astimezone(UTC)


def decide(request_id: str, signature: str, entry: dict) -> tuple[Decimal, dict]:
    """Require an original body hash, model, provider identity and close UTC times."""
    if entry.get("ledger_request_id") != request_id:
        raise ValueError("request ID mismatch")
    body = entry.get("request_body")
    if isinstance(body, str):  # CSV cell containing JSON
        body = json.loads(body)
    if not isinstance(body, dict) or canonical_hash(body) != signature:
        raise ValueError("request signature mismatch")
    model = body.get("model")
    aliases = {
        MODEL: {MODEL, "solar-pro3-260323"},
        MODEL_PRO4: {MODEL_PRO4, "solar-pro4-260806"},
    }
    if model not in aliases or entry.get("provider_model") not in aliases[model]:
        raise ValueError("provider model mismatch")
    seconds = (
        utc(entry.get("provider_timestamp_utc")) - utc(entry.get("request_timestamp_utc"))
    ).total_seconds()
    if abs(seconds) > 300:
        raise ValueError("timestamp outside five-minute window")
    provider_id = canonical_provider_id(entry.get("provider_request_id"))
    status = entry.get("billing_status")
    if status == "not_billed":
        cost = Decimal(0)
        usage = {}
    elif status == "billed":
        try:
            input_tokens = int(entry["input_tokens"])
            output_tokens = int(entry["output_tokens"])
            if (
                str(input_tokens) != str(entry["input_tokens"])
                or str(output_tokens) != str(entry["output_tokens"])
                or input_tokens < 0
                or output_tokens < 0
            ):
                raise ValueError
            price = PRICE if model == MODEL else PRICE_PRO4
            tokens = TokenUsage(input_tokens, output_tokens, 0, 0, 0, "succeeded", provider_id)
            cost = Decimal(usage_cost(tokens, price.to_dict())) * Decimal(POLICY["vat_allowance"])
            usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        except (KeyError, TypeError, InvalidOperation, ValueError):
            raise ValueError("usage tokens invalid") from None
        if cost > Decimal(POLICY["reservation_usd"]):
            raise ValueError("cost exceeds reservation")
    else:
        raise ValueError("billing status must be billed or not_billed")
    receipt = {
        "model": model,
        "provider_model": entry["provider_model"],
        "provider_request_id": provider_id,
        "request_signature": signature,
        "request_timestamp_utc": entry["request_timestamp_utc"],
        "provider_timestamp_utc": entry["provider_timestamp_utc"],
        "billing_status": status,
        "price_snapshot": (PRICE if model == MODEL else PRICE_PRO4).to_dict(),
        "cost_with_vat_reserve_usd": str(cost),
        "settlement_origin": "provider_export_reconciliation",
        **usage,
    }
    return cost, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path, help="provider CSV or JSON export")
    parser.add_argument(
        "--apply", action="store_true", help="settle matched rows with audit entries"
    )
    args = parser.parse_args(argv)
    evidence_bytes = args.evidence.read_bytes()
    evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
    entries = records(args.evidence)
    by_id: dict[str, list[dict]] = {}
    provider_ids: dict[str, int] = {}
    for entry in entries:
        by_id.setdefault(str(entry.get("ledger_request_id", "")), []).append(entry)
        try:
            provider_id = canonical_provider_id(entry.get("provider_request_id"))
        except ValueError:
            continue
        else:
            provider_ids[provider_id] = provider_ids.get(provider_id, 0) + 1
    address = args.ledger.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
    settled = 0
    kept = 0
    with sqlite3.connect(address, uri=True, timeout=10) as db:
        UpstageProbe._authorized_limit(db)
        rows = db.execute(
            "SELECT request_id,signature,committed FROM probe_calls "
            "WHERE receipt IS NULL ORDER BY request_id"
        ).fetchall()
        prior_provider_ids = stored_provider_ids(db)
        print("request_id | decision | cost_usd | reason")
        for request_id, signature, committed in rows:
            candidates = by_id.get(request_id, [])
            if len(candidates) != 1:
                reason = "missing" if not candidates else "ambiguous"
                print(f"{request_id} | keep | - | {reason} evidence")
                kept += 1
                continue
            entry = candidates[0]
            try:
                if Decimal(committed) != Decimal(POLICY["reservation_usd"]):
                    raise ValueError("reservation amount mismatch")
                cost, receipt = decide(request_id, signature, entry)
                provider_id = receipt["provider_request_id"]
                if provider_ids.get(provider_id) != 1 or provider_id in prior_provider_ids:
                    raise ValueError("provider request ID reused")
                saved = args.ledger.with_name(args.ledger.name + ".responses") / (
                    canonical_hash(request_id) + ".json"
                )
                if saved.exists():
                    provider = json.loads(saved.read_text())["provider_response"]
                    if (
                        canonical_provider_id(provider["id"]) != provider_id
                        or provider["model"] != receipt["provider_model"]
                    ):
                        raise ValueError("saved provider response mismatch")
                receipt["evidence_sha256"] = evidence_hash
                receipt["evidence_record_sha256"] = canonical_hash(entry)
            except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
                print(f"{request_id} | keep | - | {exc}")
                kept += 1
                continue
            if args.apply:
                db.execute("BEGIN IMMEDIATE")
                UpstageProbe._authorized_limit(db)
                db.execute(
                    "CREATE TABLE IF NOT EXISTS probe_settlement_audit ("
                    "request_id TEXT PRIMARY KEY, settled_at TEXT NOT NULL, "
                    "evidence_sha256 TEXT NOT NULL, receipt TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS settlement_provider_request_id_unique "
                    "ON probe_settlement_audit(json_extract(receipt, '$.provider_request_id'))"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS settlement_provider_id_canonical_unique "
                    f"ON probe_settlement_audit({PROVIDER_ID_SQL_KEY})"
                )
                if provider_id_is_used(db, provider_id):
                    db.rollback()
                    print(f"{request_id} | keep | - | provider request ID reused")
                    kept += 1
                    continue
                payload = json.dumps(receipt, sort_keys=True)
                changed = db.execute(
                    "UPDATE probe_calls SET committed=?,receipt=? "
                    "WHERE request_id=? AND signature=? AND receipt IS NULL AND committed=?",
                    (str(cost), payload, request_id, signature, committed),
                ).rowcount
                if changed != 1:
                    db.rollback()
                    print(f"{request_id} | keep | - | reservation changed concurrently")
                    kept += 1
                    continue
                db.execute(
                    "INSERT INTO probe_settlement_audit VALUES (?,?,?,?)",
                    (request_id, datetime.now(UTC).isoformat(), evidence_hash, payload),
                )
                db.commit()
                prior_provider_ids.add(provider_id)
                settled += 1
            else:
                kept += 1
            decision = "settled" if args.apply else "match (dry-run)"
            print(f"{request_id} | {decision} | {cost} | {receipt['billing_status']}")
    print(f"settled={settled} kept={kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
