"""Same-session total-cap amendment: offline, no key, no network, tmp ledgers only."""

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

import amend_upstage_session as amend_cli  # noqa: E402

APPROVED_AT = "2026-09-29T05:00:00+00:00"
EXPIRES_AT = "2026-09-29T15:00:00+00:00"  # midnight KST
AMENDED_AT = "2026-09-29T07:00:00+00:00"
NOW = datetime(2026, 9, 29, 7, 30, tzinfo=UTC)
STATEMENT = "User: student plan, Solar Pro2/Pro3 and Document Parse unlimited"


def _receipt(cost: str) -> dict:
    return {"cost_with_vat_reserve_usd": cost, "input_tokens": 1, "output_tokens": 1}


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]

    monkeypatch.setattr(upstage, "datetime", FixedDateTime)
    return state


@pytest.fixture
def ledger(tmp_path, clock):
    path = tmp_path / "session.sqlite3"
    clock["now"] = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
    upstage.create_session_ledger(
        path,
        amount_usd="10",
        reason="User approved additional USD10 today",
        authorized_by="user via master coordinator",
        authorized_at=APPROVED_AT,
        expires_at=EXPIRES_AT,
    )
    probe = upstage.UpstageProbe("test-secret", path)
    for index in range(4):
        probe._reserve(f"r{index}", {"request_id": f"r{index}"})
    for index in range(3):
        probe._settle(f"r{index}", Decimal("0.5"), _receipt("0.5"))
    clock["now"] = NOW
    return path


def _amend(ledger: Path, expected: str = "10.00", new: str = "20", **overrides) -> dict:
    request = dict(
        expected_current_total_usd=expected,
        new_total_usd=new,
        reason="User literal: today total USD20",
        authorized_by="user via master coordinator",
        authorized_at=AMENDED_AT,
    )
    request.update(overrides)
    return upstage.amend_session_total(ledger, **request)


def _rows(ledger: Path, table: str) -> list:
    with sqlite3.connect(ledger) as db:
        return db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def _limit(ledger: Path) -> Decimal:
    with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return upstage.UpstageProbe._authorized_limit(db)


def _has_amendments(ledger: Path) -> bool:
    with sqlite3.connect(ledger) as db:
        return upstage._has_table(db, "probe_session_amendments")


# --- happy path -------------------------------------------------------------


def test_amendment_raises_total_and_preserves_grant_costs_and_expiry(ledger):
    grant_before = _rows(ledger, "probe_session_grant")
    calls_before = _rows(ledger, "probe_calls")
    body = _amend(ledger)
    assert body["seq"] == 1 and body["previous_total_usd"] == "10.00"
    assert body["new_total_usd"] == "20.00" and body["expires_at"] == EXPIRES_AT
    assert body["committed_or_reserved_at_amendment_usd"] == "2.50"
    assert (body["calls_at_amendment"], body["unsettled_at_amendment"]) == (4, 1)
    assert body["declared_entitlement"] is None
    assert _rows(ledger, "probe_session_grant") == grant_before
    assert _rows(ledger, "probe_calls") == calls_before
    assert _limit(ledger) == Decimal("20.00")
    summary = upstage.UpstageProbe("test-secret", ledger).summary()
    assert summary["authorized_limit_usd"] == "20.00"
    assert summary["committed_usd"] == "2.50"
    assert summary["session_grant"]["authorized_usd"] == "10.00"
    assert summary["session_grant"]["expires_at"] == EXPIRES_AT
    assert summary["session_amendments"] == [body]


def test_amended_cap_is_still_enforced_no_unlimited_fallback(ledger):
    _amend(ledger, entitlement_statement=STATEMENT, entitlement_services=["solar-pro3"])
    probe = upstage.UpstageProbe("test-secret", ledger)
    # 2.5 committed/reserved; 17 more USD1 reservations fit under 20.
    for index in range(17):
        probe._reserve(f"n{index}", {"request_id": f"n{index}"})
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("over", {"request_id": "over"})


def test_amendment_keeps_original_expiry_for_new_reservations(ledger, clock):
    _amend(ledger)
    probe = upstage.UpstageProbe("test-secret", ledger)
    clock["now"] = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("late", {"request_id": "late"})
    probe._settle("r3", Decimal("0.01"), {"cost_with_vat_reserve_usd": "0.01"})


def test_entitlement_is_unverified_metadata_that_changes_no_cost(ledger):
    committed_before = upstage.UpstageProbe("test-secret", ledger).summary()["committed_usd"]
    body = _amend(
        ledger,
        entitlement_statement=STATEMENT,
        entitlement_services=["solar-pro3", "document-parse", "solar-pro2"],
    )
    entitlement = body["declared_entitlement"]
    assert entitlement["services"] == ["document-parse", "solar-pro2", "solar-pro3"]
    assert entitlement["verification"] == "user-declared-not-provider-verified"
    assert entitlement["changes_cap"] is False
    assert entitlement["changes_cost_estimates"] is False
    assert entitlement["paid_fallback_when_cap_reached"] is False
    assert body["cost_estimate_basis"] == "standard-price-snapshot-gross"
    summary = upstage.UpstageProbe("test-secret", ledger).summary()
    assert summary["committed_usd"] == committed_before
    assert summary["authorized_limit_usd"] == "20.00"


@pytest.mark.parametrize(
    "statement, services",
    [(STATEMENT, ["solar-pro4"]), (STATEMENT, []), (None, ["solar-pro3"]), (" ", ["solar-pro3"])],
)
def test_invalid_entitlement_rejected(ledger, statement, services):
    with pytest.raises(ValueError, match="ENTITLEMENT_INVALID"):
        _amend(ledger, entitlement_statement=statement, entitlement_services=services)
    assert not _has_amendments(ledger)


def test_chain_of_two_amendments(ledger):
    first = _amend(ledger)
    second = _amend(ledger, expected="20.00", new="25", reason="User raised to USD25")
    assert second["seq"] == 2 and second["previous_total_usd"] == "20.00"
    assert second["previous_sha256"] == upstage._text_sha256(upstage._canonical_json(first))
    assert second["grant_sha256"] == first["grant_sha256"]
    assert _limit(ledger) == Decimal("25.00")


def test_check_mode_writes_nothing(ledger):
    body = _amend(ledger, check=True)
    assert body["new_total_usd"] == "20.00"
    assert not _has_amendments(ledger)
    assert _limit(ledger) == Decimal("10.00")


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    "expected, new, code",
    [
        ("5", "20", "AMENDMENT_CAS_MISMATCH"),
        ("10", "10", "AMENDMENT_MUST_INCREASE"),
        ("10", "9", "AMENDMENT_MUST_INCREASE"),
        ("10", "30.01", "AUTHORIZATION_EXCEEDS_CEILING"),
        ("10", "0", "AUTHORIZATION_AMOUNT_INVALID"),
        ("10", "1e2", "AUTHORIZATION_AMOUNT_INVALID"),
    ],
)
def test_cas_decrease_and_ceiling_refused(ledger, expected, new, code):
    with pytest.raises(ValueError, match=code):
        _amend(ledger, expected=expected, new=new)
    assert not _has_amendments(ledger)
    assert _limit(ledger) == Decimal("10.00")


def test_stale_cas_after_first_amendment_refused(ledger):
    _amend(ledger)
    with pytest.raises(ValueError, match="AMENDMENT_CAS_MISMATCH"):
        _amend(ledger, expected="10.00", new="25")
    assert len(_rows(ledger, "probe_session_amendments")) == 1


def test_concurrent_amendments_single_winner(ledger):
    barrier = threading.Barrier(2)

    def attempt(new):
        barrier.wait()
        try:
            return _amend(ledger, new=new)["new_total_usd"]
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(2) as pool:
        results = sorted(pool.map(attempt, ["20", "15"]))
    assert results.count("AMENDMENT_CAS_MISMATCH") == 1
    assert len(_rows(ledger, "probe_session_amendments")) == 1


def test_expired_grant_cannot_be_amended(ledger, clock):
    clock["now"] = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="SESSION_GRANT_EXPIRED"):
        _amend(ledger)
    assert not _has_amendments(ledger)


@pytest.mark.parametrize(
    "authorized_at",
    [
        "2026-09-29T04:59:59+00:00",  # before the original grant approval
        "2026-09-29T15:00:00+00:00",  # at expiry
        "2026-09-29T08:00:00+00:00",  # far in the future
        "2026-09-29T07:00:00",  # naive
    ],
)
def test_invalid_authorization_time(ledger, authorized_at):
    with pytest.raises(ValueError, match="AUTHORIZATION_TIME_INVALID"):
        _amend(ledger, authorized_at=authorized_at)


def test_legacy_ledger_refused_and_foreign_table_fails_closed(tmp_path, clock):
    legacy = tmp_path / "legacy.sqlite3"
    upstage.UpstageProbe("test-secret", legacy)
    with pytest.raises(ValueError, match="SESSION_GRANT_REQUIRED"):
        _amend(legacy)
    assert not _has_amendments(legacy)
    assert _limit(legacy) == Decimal("10")
    with sqlite3.connect(legacy) as db:
        for statement in upstage._AMENDMENT_DDL:
            db.execute(statement)
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        _limit(legacy)
    with pytest.raises(ValueError, match="LEDGER_NOT_FOUND"):
        _amend(tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()


def test_unamended_session_ledger_behaviour_unchanged(ledger):
    assert _limit(ledger) == Decimal("10.00")
    assert "session_amendments" not in upstage.UpstageProbe("test-secret", ledger).summary()


# --- tamper -----------------------------------------------------------------


def test_triggers_block_update_and_delete(ledger):
    _amend(ledger)
    with sqlite3.connect(ledger) as db, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM probe_session_amendments")
    with sqlite3.connect(ledger) as db, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE probe_session_amendments SET body='{}'")


def _drop_triggers(db):
    db.execute("DROP TRIGGER probe_session_amendments_no_update")
    db.execute("DROP TRIGGER probe_session_amendments_no_delete")


def _rewrite(ledger: Path, seq: int, **changes):
    with sqlite3.connect(ledger) as db:
        _drop_triggers(db)
        body = json.loads(
            db.execute("SELECT body FROM probe_session_amendments WHERE seq=?", (seq,)).fetchone()[
                0
            ]
        )
        body.update(changes)
        db.execute(
            "UPDATE probe_session_amendments SET body=? WHERE seq=?",
            (upstage._canonical_json(body), seq),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"new_total_usd": "30.00"},
        {"expires_at": "2026-09-30T15:00:00+00:00"},
        {"previous_total_usd": "20.00"},
        {"grant_sha256": "0" * 64},
        {"reason": "edited"},  # breaks the next row's chain
        {"preserves_costs_and_reservations": False},
        {"declared_entitlement": {"changes_cap": True}},
    ],
)
def test_tampered_amendment_fails_closed(ledger, changes):
    _amend(ledger)
    _amend(ledger, expected="20.00", new="25")
    _rewrite(ledger, 1, **changes)
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        _limit(ledger)
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        upstage.UpstageProbe("test-secret", ledger)._reserve("x", {"request_id": "x"})


def test_deleted_or_reordered_amendment_fails_closed(ledger):
    _amend(ledger)
    _amend(ledger, expected="20.00", new="25")
    with sqlite3.connect(ledger) as db:
        _drop_triggers(db)
        db.execute("DELETE FROM probe_session_amendments WHERE seq=1")
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        _limit(ledger)


def test_tampered_original_grant_after_amendment_fails_closed(ledger):
    _amend(ledger)
    with sqlite3.connect(ledger) as db:
        grant = json.loads(db.execute("SELECT body FROM probe_session_grant").fetchone()[0])
        grant["reason"] = "rewritten approval"
        db.execute("UPDATE probe_session_grant SET body=?", (upstage._canonical_json(grant),))
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        _limit(ledger)


def test_non_canonical_amendment_fails_closed(ledger):
    _amend(ledger)
    with sqlite3.connect(ledger) as db:
        _drop_triggers(db)
        text = db.execute("SELECT body FROM probe_session_amendments").fetchone()[0]
        db.execute("UPDATE probe_session_amendments SET body=?", (json.dumps(json.loads(text)),))
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        _limit(ledger)


# --- effective cap consumers ------------------------------------------------


def test_consumers_see_effective_cap_and_original_grant(ledger):
    import analyze_report as ar

    from evaluation.local_upstage_pilot import session_ledger_authorization

    before = session_ledger_authorization(ledger)
    _amend(ledger)
    status = ar.describe_ledger(ledger)
    assert status["state"] == "session_grant"
    assert status["authorized_limit_usd"] == "20.00"
    assert Decimal(status["headroom_usd"]) == Decimal("17.5")
    assert status["expires_at"] == EXPIRES_AT and status["can_reserve"] is True
    # The pilot audit record still names the unchanged original grant.
    assert session_ledger_authorization(ledger) == before
    assert upstage.request_usage(ledger, ["r0", "r3"])["committed_or_reserved_usd"] == "1.50"


# --- CLI --------------------------------------------------------------------


def _cli(ledger: Path, *extra: str, new: str = "20", confirm: str | None = None) -> list[str]:
    return [
        "--ledger",
        str(ledger),
        "--expected-current-total-usd",
        "10.00",
        "--new-total-usd",
        new,
        "--confirm-new-total-usd",
        new if confirm is None else confirm,
        "--reason",
        "User literal: today total USD20",
        "--authorized-by",
        "user via master coordinator",
        "--authorized-at",
        AMENDED_AT,
        *extra,
    ]


@pytest.mark.parametrize(
    "extra, confirm",
    [
        ((), None),  # missing --acknowledge-same-session-expiry
        (("--acknowledge-same-session-expiry",), "21"),
        (("--acknowledge-same-session-expiry", "--entitlement-service", "solar-pro3"), None),
    ],
)
def test_cli_refusals_write_nothing(ledger, extra, confirm):
    with pytest.raises(SystemExit):
        amend_cli.main(_cli(ledger, *extra, confirm=confirm))
    assert not _has_amendments(ledger)


def test_cli_check_then_write(ledger, capsys):
    ack = "--acknowledge-same-session-expiry"
    entitlement = (
        "--entitlement-statement",
        STATEMENT,
        "--entitlement-service",
        "solar-pro3",
        "--entitlement-service",
        "document-parse",
    )
    assert amend_cli.main(_cli(ledger, ack, *entitlement, "--check")) == 0
    assert json.loads(capsys.readouterr().out)["written"] is False
    assert not _has_amendments(ledger)
    assert amend_cli.main(_cli(ledger, ack, *entitlement)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] is True and out["amendment"]["new_total_usd"] == "20.00"
    assert _limit(ledger) == Decimal("20.00")
    with pytest.raises(SystemExit):  # CAS now stale
        amend_cli.main(_cli(ledger, ack, new="25"))
