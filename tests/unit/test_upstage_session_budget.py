"""Explicit additional-current-session Upstage grant: offline, no key, no network."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from proofops.adapters.local import upstage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import authorize_upstage_session as auth  # noqa: E402

APPROVED_AT = "2026-09-29T05:00:00+00:00"
EXPIRES_AT = "2026-09-29T15:00:00+00:00"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 29, 6, 0, tzinfo=UTC)

    monkeypatch.setattr(upstage, "datetime", FixedDateTime)


def _grant(ledger: Path, amount: str = "5", **overrides) -> dict:
    request = dict(
        amount_usd=amount,
        reason="User approved additional USD5 for Kia SR session",
        authorized_by="user via master coordinator",
        authorized_at=APPROVED_AT,
        expires_at=EXPIRES_AT,
    )
    request.update(overrides)
    return upstage.create_session_ledger(ledger, **request)


def _cli(ledger: Path, *extra: str, amount: str = "5", confirm: str | None = None) -> list[str]:
    return [
        "--ledger",
        str(ledger),
        "--additional-usd",
        amount,
        "--confirm-additional-usd",
        amount if confirm is None else confirm,
        "--reason",
        "User approved additional spend",
        "--authorized-by",
        "user via master coordinator",
        "--authorized-at",
        APPROVED_AT,
        "--expires-at",
        EXPIRES_AT,
        *extra,
    ]


def _body(request_id: str) -> dict:
    return {"model": "solar-pro3", "request_id": request_id}


# --- negative authorization -------------------------------------------------


@pytest.mark.parametrize(
    "extra, confirm",
    [
        ((), None),  # missing --acknowledge-prior-usage-unknown
        (("--acknowledge-prior-usage-unknown",), "10"),  # confirmation mismatch
    ],
)
def test_cli_refuses_absent_acknowledgement_or_confirmation(tmp_path, extra, confirm):
    ledger = tmp_path / "budget.sqlite3"
    with pytest.raises(SystemExit) as exc:
        auth.main(_cli(ledger, *extra, confirm=confirm))
    assert exc.value.code == 2
    assert not ledger.exists()


def test_cli_refuses_missing_required_receipt_fields(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    with pytest.raises(SystemExit):
        auth.main(["--ledger", str(ledger), "--additional-usd", "5"])
    assert not ledger.exists()


@pytest.mark.parametrize(
    "overrides, code",
    [
        (dict(amount_usd="0"), "AUTHORIZATION_AMOUNT_INVALID"),
        (dict(amount_usd="-5"), "AUTHORIZATION_AMOUNT_INVALID"),
        (dict(amount_usd="abc"), "AUTHORIZATION_AMOUNT_INVALID"),
        (dict(amount_usd="NaN"), "AUTHORIZATION_AMOUNT_INVALID"),
        (dict(amount_usd="5.001"), "AUTHORIZATION_AMOUNT_INVALID"),
        (dict(amount_usd="1e1"), "AUTHORIZATION_AMOUNT_INVALID"),
        (dict(amount_usd="30.01"), "AUTHORIZATION_EXCEEDS_CEILING"),
        (dict(amount_usd="31"), "AUTHORIZATION_EXCEEDS_CEILING"),
        (dict(reason="  "), "AUTHORIZATION_REASON_REQUIRED"),
        (dict(reason="x" * 513), "AUTHORIZATION_REASON_REQUIRED"),
        (dict(authorized_by=""), "AUTHORIZATION_OPERATOR_REQUIRED"),
        (dict(authorized_at="2026-09-29T05:00:00"), "AUTHORIZATION_TIME_INVALID"),
        (dict(authorized_at="yesterday"), "AUTHORIZATION_TIME_INVALID"),
        (dict(authorized_at="2026-09-30T05:00:00+00:00"), "AUTHORIZATION_TIME_INVALID"),
        (dict(expires_at="2026-09-29T15:00:00"), "AUTHORIZATION_TIME_INVALID"),
        (dict(expires_at="2026-09-29T04:00:00+00:00"), "AUTHORIZATION_EXPIRY_INVALID"),
        (dict(expires_at="2026-09-30T05:00:01+00:00"), "AUTHORIZATION_EXPIRY_INVALID"),
        (dict(expires_at="2026-09-29T05:30:00+00:00"), "AUTHORIZATION_EXPIRY_INVALID"),
    ],
)
def test_malformed_authorization_writes_nothing(tmp_path, overrides, code):
    ledger = tmp_path / "upstage" / "budget.sqlite3"
    request = dict(
        amount_usd="5",
        reason="ok",
        authorized_by="user",
        authorized_at=APPROVED_AT,
        expires_at=EXPIRES_AT,
    )
    request.update(overrides)
    with pytest.raises(ValueError, match=code):
        upstage.create_session_ledger(ledger, **request)
    assert not ledger.exists()
    assert not ledger.parent.exists() or not any(ledger.parent.iterdir())


def test_check_mode_validates_without_writing(tmp_path, capsys):
    ledger = tmp_path / "budget.sqlite3"
    assert auth.main(_cli(ledger, "--acknowledge-prior-usage-unknown", "--check")) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["written"] is False and printed["grant"]["authorized_usd"] == "5.00"
    assert not ledger.exists()


# --- no overwrite / no historical rewrite -----------------------------------


def test_existing_legacy_ledger_is_never_overwritten(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    upstage.UpstageProbe("test-secret", ledger)  # historical-style ledger
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="LEDGER_ALREADY_EXISTS"):
        _grant(ledger)
    with pytest.raises(SystemExit):
        auth.main(_cli(ledger, "--acknowledge-prior-usage-unknown"))
    assert ledger.read_bytes() == before
    assert upstage.UpstageProbe("test-secret", ledger).summary()["authorized_limit_usd"] == "10.00"


def test_existing_session_ledger_and_any_file_are_not_replaced(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    _grant(ledger, "5")
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="LEDGER_ALREADY_EXISTS"):
        _grant(ledger, "20")
    assert ledger.read_bytes() == before
    other = tmp_path / "junk.sqlite3"
    other.write_bytes(b"not a ledger")
    with pytest.raises(ValueError, match="LEDGER_ALREADY_EXISTS"):
        _grant(other)
    assert other.read_bytes() == b"not a ledger"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["budget.sqlite3", "junk.sqlite3"]


def test_grant_metadata_is_additional_session_with_unknown_prior_usage(tmp_path, capsys):
    ledger = tmp_path / "budget.sqlite3"
    assert auth.main(_cli(ledger, "--acknowledge-prior-usage-unknown")) == 0
    grant = json.loads(capsys.readouterr().out)["grant"]
    assert grant["scope"] == "additional-current-session"
    assert grant["prior_cumulative_usage"] == "unknown"
    assert grant["revises_prior_limits"] is False
    assert grant["authorized_at"] == APPROVED_AT
    assert grant["expires_at"] == EXPIRES_AT
    assert grant["amount_basis"] == "gross-including-10pct-vat"
    summary = upstage.UpstageProbe("test-secret", ledger).summary()
    assert summary["authorized_limit_usd"] == "5.00"
    assert summary["session_grant"] == grant
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM probe_extensions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM probe_calls").fetchone()[0] == 0


# --- cap enforcement ----------------------------------------------------------


def test_five_dollar_grant_caps_at_five_including_pending(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    _grant(ledger, "5")
    probe = upstage.UpstageProbe("test-secret", ledger)
    for index in range(5):
        probe._reserve(f"r{index}", _body(f"r{index}"))  # pending USD1 each, unknown cost
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("r5", _body("r5"))
    assert probe.summary()["committed_usd"] == "5.00"
    assert probe.summary()["unsettled_calls"] == 5


def test_settled_cost_frees_only_unused_part_of_five(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    _grant(ledger, "5")
    probe = upstage.UpstageProbe("test-secret", ledger)
    for index in range(4):
        probe._reserve(f"r{index}", _body(f"r{index}"))
        probe._settle(f"r{index}", Decimal("0.9"), {"cost_with_vat_reserve_usd": "0.9"})
    probe._reserve("r4", _body("r4"))  # 3.6 + 1 pending = 4.6
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("r5", _body("r5"))  # 5.6 would exceed 5 (base 10 would allow)


def test_grant_cannot_be_extended_or_mixed_with_general_extension(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    _grant(ledger, "5")
    probe = upstage.UpstageProbe("test-secret", ledger)
    with pytest.raises(ValueError, match="SESSION_GRANT_NOT_EXTENSIBLE"):
        probe.authorize_additional_budget("5.00", reason="more")
    assert probe.summary()["authorized_limit_usd"] == "5.00"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "INSERT INTO probe_extensions (additional_usd, reason, authorized_at) VALUES (?,?,?)",
            ("5.00", '{"reason":"x","scope":"general"}', APPROVED_AT),
        )
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        probe._reserve("r0", _body("r0"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: {**g, "authorized_usd": "50.00"},
        lambda g: {**g, "authorized_usd": "5"},
        lambda g: {**g, "scope": "general"},
        lambda g: {**g, "prior_cumulative_usage": "0"},
        lambda g: {**g, "revises_prior_limits": True},
        lambda g: {**g, "amount_basis": "net"},
        lambda g: {**g, "expires_at": "2026-10-09T05:00:00+00:00"},
        lambda g: {k: v for k, v in g.items() if k != "reason"},
    ],
)
def test_tampered_grant_fails_closed(tmp_path, mutate):
    ledger = tmp_path / "budget.sqlite3"
    grant = _grant(ledger, "5")
    body = json.dumps(mutate(grant), sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE probe_session_grant SET body=? WHERE id=1", (body,))
    probe = upstage.UpstageProbe("test-secret", ledger)
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        probe._reserve("r0", _body("r0"))


def test_concurrent_reservations_cannot_overspend_grant(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    _grant(ledger, "5")
    upstage.UpstageProbe("test-secret", ledger)
    barrier = threading.Barrier(12)

    def attempt(index: int) -> str:
        probe = upstage.UpstageProbe("test-secret", ledger)
        barrier.wait()
        try:
            probe._reserve(f"c{index}", _body(f"c{index}"))
            return "ok"
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(12)))
    assert results.count("ok") == 5
    assert set(results) == {"ok", "BUDGET_EXHAUSTED"}
    assert upstage.UpstageProbe("test-secret", ledger).summary()["committed_usd"] == "5.00"


def test_legacy_ledger_without_grant_keeps_historical_limit(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    probe = upstage.UpstageProbe("test-secret", ledger)
    assert "session_grant" not in probe.summary()
    probe.authorize_additional_budget("10.00", reason="legacy flow unchanged")
    assert probe.summary()["authorized_limit_usd"] == "20.00"


def test_expired_grant_refuses_new_reservations_but_allows_settlement(tmp_path, monkeypatch):
    ledger = tmp_path / "budget.sqlite3"
    _grant(ledger, "10")
    probe = upstage.UpstageProbe("test-secret", ledger)
    probe._reserve("before", _body("before"))

    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 29, 15, 0, tzinfo=UTC)

    monkeypatch.setattr(upstage, "datetime", AfterExpiry)
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("after", _body("after"))
    probe._settle("before", Decimal("0.01"), {"cost_with_vat_reserve_usd": "0.01"})
    summary = probe.summary()
    assert summary["calls"] == 1 and summary["unsettled_calls"] == 0
    assert summary["committed_usd"] == "0.01"


# --- exact-ledger plumbing ----------------------------------------------------


def test_worker_ledger_is_exact_env_path_or_legacy_default(tmp_path, monkeypatch):
    from proofops_worker import composition

    monkeypatch.delenv("LOCAL_UPSTAGE_LEDGER_PATH", raising=False)
    legacy = composition.budget_ledger_path()
    assert legacy.parts[-3:] == (".local", "upstage", "budget.sqlite3")
    exact = tmp_path / "submission-20260929.sqlite3"
    monkeypatch.setenv("LOCAL_UPSTAGE_LEDGER_PATH", str(exact))
    assert composition.budget_ledger_path() == exact
    for bad in ("", "relative/ledger.sqlite3"):
        monkeypatch.setenv("LOCAL_UPSTAGE_LEDGER_PATH", bad)
        with pytest.raises(ValueError, match="SHARED_BUDGET_LEDGER_REQUIRED"):
            composition.budget_ledger_path()


def test_pilot_records_actual_session_grant_never_legacy_authorization(tmp_path):
    sys.path.insert(0, str(ROOT))
    from evaluation.local_upstage_pilot import session_ledger_authorization

    with pytest.raises(ValueError, match="not found"):
        session_ledger_authorization(tmp_path / "missing.sqlite3")
    legacy = tmp_path / "legacy.sqlite3"
    upstage.UpstageProbe("test-secret", legacy)
    with pytest.raises(ValueError, match="explicit session grant"):
        session_ledger_authorization(legacy)
    ledger = tmp_path / "submission.sqlite3"
    grant = _grant(ledger, "10")
    record = session_ledger_authorization(ledger)
    assert record["kind"] == "upstage_session_grant"
    assert record["ledger"] == str(ledger.resolve())
    assert record["authorized_usd"] == "10.00" and record["expired_now"] is False
    assert {key: record[key] for key in grant} == grant
