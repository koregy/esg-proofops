"""Independent audit of the 2026-09-29 session budget fence and parser resume pins.

Offline only: no key is read (every --key-file points at an absent temp path),
no network, no real ledger.  Every ledger lives under ``tmp_path``; paid probe
constructors in the worker composition are replaced by recorders so a routing
bug can never create or touch the checkout's legacy ``budget.sqlite3``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from proofops.adapters.local import upstage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyze_report as ar  # noqa: E402

NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
APPROVED_AT = "2026-09-29T05:00:00+00:00"
EXPIRES_AT = "2026-09-29T15:00:00+00:00"
LEGACY_LEDGER = ROOT / ".local" / "upstage" / "budget.sqlite3"


def _clock(monkeypatch, instant: datetime) -> None:
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    monkeypatch.setattr(upstage, "datetime", Fixed)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    _clock(monkeypatch, NOW)
    saved = dict(os.environ)
    monkeypatch.delenv("LOCAL_UPSTAGE_LEDGER_PATH", raising=False)
    yield
    # The pilot's main() mutates os.environ directly; restore it exactly.
    os.environ.clear()
    os.environ.update(saved)


def _grant(ledger: Path, amount: str = "10", **overrides) -> dict:
    request = dict(
        amount_usd=amount,
        reason="User approved additional USD10 gross for Kia SR session",
        authorized_by="user via master coordinator",
        authorized_at=APPROVED_AT,
        expires_at=EXPIRES_AT,
    )
    request.update(overrides)
    return upstage.create_session_ledger(ledger, **request)


def _body(request_id: str) -> dict:
    return {"request_id": request_id}


def _calls(ledger: Path) -> int:
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM probe_calls").fetchone()[0]


# --- gross USD10 cap -----------------------------------------------------------


def test_ten_dollar_grant_is_gross_cap_counting_pending_and_vat_settlements(tmp_path):
    ledger = tmp_path / "submission.sqlite3"
    _grant(ledger, "10")
    probe = upstage.UpstageProbe("offline-dummy", ledger)
    for index in range(10):
        probe._reserve(f"r{index}", _body(f"r{index}"))
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("r10", _body("r10"))
    # Settlement stores gross (net * 1.10); only the unused remainder is freed.
    gross = Decimal("0.50") * Decimal("1.10")
    for index in range(2):
        probe._settle(f"r{index}", gross, {"cost_with_vat_reserve_usd": str(gross)})
    assert Decimal(probe.summary()["committed_usd"]) == Decimal("9.10")
    # 10 - 9.10 = 0.90 of gross headroom is below one USD1 reservation.
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("r10", _body("r10"))


def test_ten_dollar_grant_frees_exactly_one_reservation_after_two_half_settlements(tmp_path):
    ledger = tmp_path / "submission.sqlite3"
    _grant(ledger, "10")
    probe = upstage.UpstageProbe("offline-dummy", ledger)
    for index in range(10):
        probe._reserve(f"r{index}", _body(f"r{index}"))
    for index in range(2):
        probe._settle(f"r{index}", Decimal("0.50"), {"cost_with_vat_reserve_usd": "0.50"})
    probe._reserve("r10", _body("r10"))
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("r11", _body("r11"))
    assert probe.summary()["committed_usd"] == "10.00"
    assert probe.summary()["authorized_limit_usd"] == "10.00"


def test_session_grant_is_isolated_from_a_legacy_ledger_in_the_same_directory(tmp_path):
    legacy = tmp_path / "budget.sqlite3"
    upstage.UpstageProbe("offline-dummy", legacy)  # legacy USD10, no grant
    session = tmp_path / "submission.sqlite3"
    _grant(session, "2")
    fenced = upstage.UpstageProbe("offline-dummy", session)
    fenced._reserve("s0", _body("s0"))
    fenced._reserve("s1", _body("s1"))
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        fenced._reserve("s2", _body("s2"))
    assert _calls(legacy) == 0
    legacy_summary = upstage.UpstageProbe("offline-dummy", legacy).summary()
    assert Decimal(legacy_summary["authorized_limit_usd"]) == Decimal("10")
    # Legacy extensions never leak into the session ledger and vice versa.
    with pytest.raises(ValueError, match="SESSION_GRANT_NOT_EXTENSIBLE"):
        fenced.authorize_additional_budget("1", reason="top up")


# --- concurrency across OS processes --------------------------------------------

_CHILD = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from proofops.adapters.local import upstage
    ledger, index, start = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
    probe = upstage.UpstageProbe("offline-dummy", ledger)
    while time.time() < start:
        pass
    try:
        probe._reserve("p" + index, {"request_id": "p" + index})
        print("ok")
    except ValueError as exc:
        print(exc)
    """
)


def test_concurrent_processes_cannot_reserve_past_the_grant(tmp_path, monkeypatch):
    import time

    # Children run the real clock, so this grant is minted on the real clock too.
    monkeypatch.setattr(upstage, "datetime", datetime)
    ledger = tmp_path / "submission.sqlite3"
    now = datetime.now(UTC)
    upstage.create_session_ledger(
        ledger,
        amount_usd="3",
        reason="audit concurrency",
        authorized_by="audit",
        authorized_at=(now - timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    start = time.time() + 3.0
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(ledger), str(index), str(start)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for index in range(8)
    ]
    results = []
    for child in children:
        out, err = child.communicate(timeout=60)
        assert child.returncode == 0, err
        results.append(out.strip())
    assert results.count("ok") == 3, results
    assert set(results) == {"ok", "BUDGET_EXHAUSTED"}, results
    assert _calls(ledger) == 3


# --- malformed grants fail closed -----------------------------------------------


def _rewrite_grant(ledger: Path, body: str) -> None:
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE probe_session_grant SET body=? WHERE id=1", (body,))


def _canon(grant: dict) -> str:
    return json.dumps(grant, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _mutate_field(key, value):
    return lambda ledger, grant: _rewrite_grant(ledger, _canon({**grant, key: value}))


def _empty_table(ledger, grant):
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM probe_session_grant")


def _two_rows(ledger, grant):
    with sqlite3.connect(ledger) as db:
        db.execute("DROP TABLE probe_session_grant")
        db.execute("CREATE TABLE probe_session_grant (id INTEGER PRIMARY KEY, body TEXT)")
        db.execute("INSERT INTO probe_session_grant VALUES (1, ?)", (_canon(grant),))
        db.execute("INSERT INTO probe_session_grant VALUES (2, ?)", (_canon(grant),))


def _row_id_two(ledger, grant):
    with sqlite3.connect(ledger) as db:
        db.execute("DROP TABLE probe_session_grant")
        db.execute("CREATE TABLE probe_session_grant (id INTEGER PRIMARY KEY, body TEXT)")
        db.execute("INSERT INTO probe_session_grant VALUES (2, ?)", (_canon(grant),))


def _extension_row(ledger, grant):
    with sqlite3.connect(ledger) as db:
        db.execute(
            "INSERT INTO probe_extensions (additional_usd, reason, authorized_at) VALUES (?,?,?)",
            ("5", '{"reason":"x","scope":"general"}', APPROVED_AT),
        )


def _policy_changed(ledger, grant):
    with sqlite3.connect(ledger) as db:
        db.execute(
            "UPDATE probe_policy SET body=? WHERE id=1",
            (json.dumps({**upstage.POLICY, "limit_usd": "20.00"}, sort_keys=True),),
        )


MALFORMED = {
    "empty_grant_table": _empty_table,
    "two_grant_rows": _two_rows,
    "grant_row_id_not_1": _row_id_two,
    "non_canonical_spacing": lambda ledger, grant: _rewrite_grant(ledger, json.dumps(grant)),
    "not_json": lambda ledger, grant: _rewrite_grant(ledger, "{not json"),
    "extra_key": lambda ledger, grant: _rewrite_grant(ledger, _canon({**grant, "x": 1})),
    "unquantized_amount": _mutate_field("authorized_usd", "10"),
    "amount_over_ceiling": _mutate_field("authorized_usd", "31.00"),
    "zero_amount": _mutate_field("authorized_usd", "0.00"),
    "negative_amount": _mutate_field("authorized_usd", "-1.00"),
    "numeric_amount": _mutate_field("authorized_usd", 10),
    "naive_authorized_at": _mutate_field("authorized_at", "2026-09-29T05:00:00"),
    "naive_expires_at": _mutate_field("expires_at", "2026-09-29T15:00:00"),
    "expires_equals_approval": _mutate_field("expires_at", APPROVED_AT),
    "expires_before_approval": _mutate_field("expires_at", "2026-09-29T04:00:00+00:00"),
    "window_24h_plus_1s": _mutate_field("expires_at", "2026-09-30T05:00:01+00:00"),
    "garbage_recorded_at": _mutate_field("recorded_at", "yesterday"),
    "reason_padded": _mutate_field("reason", " padded"),
    "empty_operator": _mutate_field("authorized_by", ""),
    "hard_ceiling_raised": _mutate_field("hard_ceiling_usd", "100.00"),
    "net_basis": _mutate_field("amount_basis", "net-excluding-vat"),
    "historical_ledger_claimed": _mutate_field("historical_ledger", "present"),
    "general_extension_added": _extension_row,
    "policy_limit_changed": _policy_changed,
}


@pytest.mark.parametrize("mutate", MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_grant_fails_closed_everywhere_without_side_effects(tmp_path, mutate):
    from evaluation.local_upstage_pilot import session_ledger_authorization

    ledger = tmp_path / "submission.sqlite3"
    grant = _grant(ledger, "10")
    mutate(ledger, grant)
    probe = upstage.UpstageProbe.__new__(upstage.UpstageProbe)
    probe.ledger = ledger  # skip __init__ so the audit cannot repair the file
    with pytest.raises(ValueError, match="BUDGET_POLICY_MISMATCH"):
        probe._reserve("r0", _body("r0"))
    assert _calls(ledger) == 0
    assert ar.describe_ledger(ledger)["state"] == "invalid"
    with pytest.raises(ValueError, match="invalid"):
        session_ledger_authorization(ledger)
    with pytest.raises(ValueError):
        probe.authorize_additional_budget("1", reason="never")
    assert _calls(ledger) == 0


# --- expiry ----------------------------------------------------------------------


def test_expiry_boundary_is_exclusive_and_blocks_plan_and_pilot(tmp_path, monkeypatch):
    ledger = tmp_path / "submission.sqlite3"
    _grant(ledger, "10")
    probe = upstage.UpstageProbe("offline-dummy", ledger)
    expires = datetime.fromisoformat(EXPIRES_AT)
    _clock(monkeypatch, expires - timedelta(microseconds=1))
    probe._reserve("last", _body("last"))
    _clock(monkeypatch, expires)
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        probe._reserve("at-expiry", _body("at-expiry"))
    status = ar.describe_ledger(ledger)
    assert status["expired"] is True and status["can_reserve"] is False
    # Settlement of the in-flight reservation stays allowed after expiry.
    probe._settle("last", Decimal("0.02"), {"cost_with_vat_reserve_usd": "0.02"})
    assert probe.summary()["unsettled_calls"] == 0


def test_expired_grant_refuses_launcher_invoke(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    pdf = tmp_path / "report.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as stream:
        writer.write(stream)
    ledger = tmp_path / "submission.sqlite3"
    _grant(ledger, "10")
    key = tmp_path / "absent.env"
    key.write_text("UPSTAGE_API_KEY=offline-dummy\n")  # never sent anywhere
    _clock(monkeypatch, datetime.fromisoformat(EXPIRES_AT) + timedelta(seconds=1))
    monkeypatch.setattr(ar, "BUDGET_LEDGER", tmp_path / "absent-legacy.sqlite3")
    args = Namespace(
        pdf=pdf,
        pages="1",
        claim_pages=None,
        auto_scope=False,
        auto_scope_max_pages=None,
        report_year=2024,
        period_start="2024-01-01",
        period_end="2024-12-31",
        state=tmp_path / "run",
        key_file=key,
        invoke=True,
        serve=False,
        port=8766,
        extraction_total_calls=None,
        tagging_max_calls=48,
        budget_ledger=ledger,
    )
    with pytest.raises(ar.PlanError, match="session grant expired"):
        ar.plan_run(args)
    assert not (tmp_path / "absent-legacy.sqlite3").exists()


def _run_pilot(monkeypatch, argv: list[str]):
    from evaluation import local_upstage_pilot as pilot

    monkeypatch.setattr(sys, "argv", ["local_upstage_pilot", *argv])
    return pilot.main()


def test_pilot_refuses_new_paid_run_on_expired_grant_before_any_state(tmp_path, monkeypatch):
    ledger = tmp_path / "submission.sqlite3"
    _grant(ledger, "10")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    _clock(monkeypatch, datetime.fromisoformat(EXPIRES_AT))
    state = tmp_path / "state"
    with pytest.raises(SystemExit):
        _run_pilot(
            monkeypatch,
            [
                "--pdf", str(pdf), "--state", str(state),
                "--key-file", str(tmp_path / "absent.env"),
                "--report-year", "2024", "--period-start", "2024-01-01",
                "--period-end", "2024-12-31", "--budget-ledger", str(ledger), "--invoke",
            ],
        )  # fmt: skip
    assert not state.exists()
    assert "LOCAL_UPSTAGE_LEDGER_PATH" not in os.environ
    assert _calls(ledger) == 0


# --- never initialize a missing ledger -------------------------------------------


class _Recorder:
    """Stands in for every paid transport; records the ledger, touches nothing."""

    built: list[tuple[str, Path]] = []

    def __init__(self, api_key, ledger, *, model=upstage.MODEL):
        type(self).built.append((type(self).__name__, Path(ledger)))
        self.ledger = Path(ledger)
        self.model = model

    def complete(self, *args, **kwargs):  # pragma: no cover - never invoked
        raise AssertionError("no paid call in the audit")


class _TextRecorder(_Recorder):
    pass


class _ParseRecorder(_Recorder):
    pass


def _worker_env(tmp_path, monkeypatch, ledger: Path | None) -> None:
    from proofops.application.ingest.graph_fusion import ParserProfile
    from proofops_agent.upstage_extraction import _profile

    profile = ParserProfile("11111111-1111-4111-8111-111111111111")
    config = tmp_path / "parser.json"
    config.write_text(json.dumps(profile.config_snapshot()))
    settings = tmp_path / "settings.json"
    from dataclasses import asdict

    settings.write_text(
        json.dumps(
            {
                "extraction_limits": {"max_output_tokens": 512},
                "extraction_profile": asdict(_profile(upstage.MODEL)),
            }
        )
    )
    monkeypatch.setenv("LOCAL_PARSER_PROFILE_PATH", str(config))
    monkeypatch.setenv("LOCAL_RUN_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(tmp_path / "db" / "state.sqlite3"))
    monkeypatch.setenv("UPSTAGE_API_KEY", "offline-dummy")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_ADAPTER", "synthetic")
    if ledger is None:
        monkeypatch.delenv("LOCAL_UPSTAGE_LEDGER_PATH", raising=False)
    else:
        monkeypatch.setenv("LOCAL_UPSTAGE_LEDGER_PATH", str(ledger))
    from proofops.adapters.local import upstage_parse

    _Recorder.built = []
    monkeypatch.setattr(upstage, "UpstageProbe", _TextRecorder)
    monkeypatch.setattr(upstage_parse, "UpstageParseProbe", _ParseRecorder)


PAID_STAGES = {
    "parse_notes": ("parse", {}, dict(review_table_notes=True)),
    "parse_raster": ("parse", {}, dict(verify_paragraphs=True, raster_ocr=True)),
    "extract": ("extract", {"LOCAL_EXTRACTION_MODE": "upstage_probe"}, {}),
    "tag": ("tag", {"LOCAL_TAGGING_MODE": "upstage_local"}, {}),
}


@pytest.mark.parametrize("stage", PAID_STAGES, ids=PAID_STAGES)
def test_every_paid_stage_routes_only_to_the_exact_session_ledger(tmp_path, monkeypatch, stage):
    from proofops_worker import composition

    ledger = tmp_path / "upstage" / "submission.sqlite3"
    ledger.parent.mkdir()
    _grant(ledger, "10")
    _worker_env(tmp_path, monkeypatch, ledger)
    name, env, kwargs = PAID_STAGES[stage]
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    runner = composition.build_composition(stage=name, **kwargs)
    assert _Recorder.built, "paid stage built no transport"
    assert {path for _, path in _Recorder.built} == {ledger}
    if stage == "parse_raster":
        assert runner.raster_ledger == ledger and runner.note_ledger == ledger


@pytest.mark.parametrize("stage", PAID_STAGES, ids=PAID_STAGES)
def test_every_paid_stage_refuses_a_missing_ledger_without_creating_it(
    tmp_path, monkeypatch, stage
):
    from proofops_worker import composition

    missing = tmp_path / "upstage" / "submission.sqlite3"
    _worker_env(tmp_path, monkeypatch, missing)
    name, env, kwargs = PAID_STAGES[stage]
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="SHARED_BUDGET_LEDGER_REQUIRED"):
        composition.build_composition(stage=name, **kwargs)
    assert _Recorder.built == []
    assert not missing.exists() and not missing.parent.exists()


def test_read_only_paths_never_create_a_missing_ledger(tmp_path):
    from evaluation.local_upstage_pilot import session_ledger_authorization

    missing = tmp_path / "upstage" / "budget.sqlite3"
    assert ar.describe_ledger(missing) == {"state": "absent"}
    with pytest.raises(ValueError, match="ACCOUNTING_UNAVAILABLE"):
        upstage.request_usage(missing, ["r0"])
    with pytest.raises(ValueError, match="not found"):
        session_ledger_authorization(missing)
    grant_check = upstage.session_grant_request(
        missing,
        amount_usd="10",
        reason="check only",
        authorized_by="audit",
        authorized_at=APPROVED_AT,
        expires_at=EXPIRES_AT,
    )
    assert grant_check["authorized_usd"] == "10.00"
    assert not missing.exists() and not missing.parent.exists()


# --- parser resource + capacity pins on resume -----------------------------------

_BASE_SAVED = {
    "source_path": "C:/unused/report.pdf",
    "selected_pages": [1, 2],
    "parser_max_output_bytes": 67108864,
    "parser_timeout_seconds": 300,
    "parser_memory_bytes": 2147483648,
    "capacity_refresh": True,
}


def _resume_args(**overrides) -> Namespace:
    base = dict(
        pdf=None,
        report_year=None,
        period_start=None,
        period_end=None,
        pages="1",
        claim_pages=None,
        parser_max_output_bytes=None,
        parser_timeout_seconds=None,
        parser_memory_bytes=None,
        model=upstage.MODEL,
        extraction_total_calls=None,
        raster_ocr=False,
        max_calls=20,
        tagging_max_calls=12,
    )
    base.update(overrides)
    return Namespace(**base)


def test_resume_restores_pinned_resources_and_capacity_policy():
    from evaluation.local_upstage_pilot import apply_resume_metadata

    args = _resume_args()
    apply_resume_metadata(args, dict(_BASE_SAVED))
    assert args.parser_timeout_seconds == 300
    assert args.parser_memory_bytes == 2147483648
    assert args.parser_max_output_bytes == 67108864
    assert args.capacity_refresh is True


@pytest.mark.parametrize(
    "override",
    [
        {"parser_timeout_seconds": 120},
        {"parser_timeout_seconds": 900},
        {"parser_memory_bytes": 768 * 1024 * 1024},
        {"parser_memory_bytes": 4 * 1024**3},
        {"parser_max_output_bytes": 20_000_000},
    ],
)
def test_resume_cannot_change_parser_resources(override):
    from evaluation.local_upstage_pilot import apply_resume_metadata

    with pytest.raises(ValueError, match="--resume cannot change"):
        apply_resume_metadata(_resume_args(**override), dict(_BASE_SAVED))


def test_legacy_run_without_pins_cannot_gain_resources_on_resume():
    from evaluation.local_upstage_pilot import apply_resume_metadata

    legacy = {k: v for k, v in _BASE_SAVED.items() if not k.startswith("parser_")}
    for name, value in (("parser_timeout_seconds", 300), ("parser_memory_bytes", 2 * 1024**3)):
        with pytest.raises(ValueError, match="--resume cannot change"):
            apply_resume_metadata(_resume_args(**{name: value}), dict(legacy))
    args = _resume_args()
    apply_resume_metadata(args, dict(legacy))
    assert args.parser_timeout_seconds is None and args.parser_memory_bytes is None


def _frozen_state(tmp_path: Path, ledger: Path | None, **extra) -> tuple[Path, Path, bytes]:
    state = tmp_path / "state"
    state.mkdir()
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    manifest = {**_BASE_SAVED, "source_path": str(pdf), **extra}
    if ledger is not None:
        manifest["budget_ledger"] = str(ledger)
    (state / "pilot.json").write_text(json.dumps(manifest))
    parser_json = b'{"frozen":"parser-profile"}'
    (state / "parser.json").write_bytes(parser_json)
    return state, pdf, parser_json


def _existing_run_argv(state, pdf, tmp_path, *extra):
    return [
        "--pdf", str(pdf), "--state", str(state),
        "--key-file", str(tmp_path / "absent.env"),
        "--report-year", "2024", "--period-start", "2024-01-01",
        "--period-end", "2024-12-31", *extra,
    ]  # fmt: skip


class _StopBeforeApp(Exception):
    pass


@pytest.mark.parametrize(
    "extra",
    [
        ("--parser-timeout-seconds", "120"),
        ("--parser-memory-bytes", str(768 * 1024 * 1024)),
    ],
)
def test_existing_run_never_rewrites_frozen_parser_profile(tmp_path, monkeypatch, extra):
    """A conflicting resource on an existing state reaches the app only via the
    frozen parser.json; the explicit manifest comparison runs after create_app."""
    # proofops_api.main builds an app at import; keep that import inside tmp_path.
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(tmp_path / "import" / "state.sqlite3"))
    for name in ("LOCAL_PARSER_PROFILE_PATH", "LOCAL_RUN_SETTINGS_PATH"):
        monkeypatch.delenv(name, raising=False)
    import proofops_api.main as api_main

    def stop():
        raise _StopBeforeApp

    monkeypatch.setattr(api_main, "create_app", stop)
    ledger = tmp_path / "submission.sqlite3"
    _grant(ledger, "10")
    state, pdf, frozen = _frozen_state(tmp_path, ledger)
    with pytest.raises(_StopBeforeApp):
        _run_pilot(monkeypatch, _existing_run_argv(state, pdf, tmp_path, *extra))
    assert (state / "parser.json").read_bytes() == frozen
    assert os.environ["LOCAL_PARSER_PROFILE_PATH"] == str(state / "parser.json")
    # The frozen session ledger (not the legacy default) was selected first.
    assert os.environ["LOCAL_UPSTAGE_LEDGER_PATH"] == str(ledger)
    assert not LEGACY_LEDGER.exists()
    assert _calls(ledger) == 0


def test_resume_rejects_changed_resource_and_keeps_parser_json(tmp_path, monkeypatch):
    state, pdf, frozen = _frozen_state(tmp_path, None)
    with pytest.raises(SystemExit):
        _run_pilot(
            monkeypatch,
            [
                "--state", str(state), "--resume",
                "--key-file", str(tmp_path / "absent.env"),
                "--parser-memory-bytes", str(4 * 1024**3),
            ],
        )  # fmt: skip
    assert (state / "parser.json").read_bytes() == frozen


def test_existing_run_cannot_swap_or_add_a_budget_ledger(tmp_path, monkeypatch):
    frozen_ledger = tmp_path / "submission.sqlite3"
    _grant(frozen_ledger, "10")
    other = tmp_path / "other.sqlite3"
    _grant(other, "10")
    state, pdf, _ = _frozen_state(tmp_path, frozen_ledger)
    with pytest.raises(SystemExit):
        _run_pilot(
            monkeypatch,
            _existing_run_argv(state, pdf, tmp_path, "--budget-ledger", str(other)),
        )
    legacy_state_root = tmp_path / "legacy"
    legacy_state_root.mkdir()
    legacy_state, legacy_pdf, _ = _frozen_state(legacy_state_root, None)
    with pytest.raises(SystemExit):
        _run_pilot(
            monkeypatch,
            _existing_run_argv(
                legacy_state, legacy_pdf, legacy_state_root, "--budget-ledger", str(other)
            ),
        )
    assert "LOCAL_UPSTAGE_LEDGER_PATH" not in os.environ
    assert _calls(frozen_ledger) == _calls(other) == 0


def test_resume_cannot_add_capacity_refresh(tmp_path, monkeypatch):
    state, _, _ = _frozen_state(tmp_path, None, capacity_refresh=False)
    with pytest.raises(SystemExit):
        _run_pilot(
            monkeypatch,
            [
                "--state", str(state), "--resume",
                "--key-file", str(tmp_path / "absent.env"),
                "--capacity-policy-refresh",
            ],
        )  # fmt: skip


def test_capacity_refresh_is_forwarded_and_selects_the_refreshed_policy(tmp_path):
    from proofops.application.input_reservation import solar_pro4_capacity_policy

    argv = ar.build_pilot_argv(
        pdf=tmp_path / "r.pdf",
        pages=[1],
        claim_pages=None,
        report_year=2024,
        period_start="2024-01-01",
        period_end="2024-12-31",
        state=tmp_path / "s",
        key_file=tmp_path / "absent.env",
        invoke=False,
        serve=False,
        port=8766,
        capacity_refresh=True,
        parser_timeout_seconds=300,
        parser_memory_bytes=2 * 1024**3,
        budget_ledger=tmp_path / "submission.sqlite3",
    )
    assert "--capacity-policy-refresh" in argv
    assert argv[argv.index("--parser-timeout-seconds") + 1] == "300"
    assert argv[argv.index("--parser-memory-bytes") + 1] == str(2 * 1024**3)
    assert argv[argv.index("--budget-ledger") + 1] == str(tmp_path / "submission.sqlite3")
    assert "--capacity-policy-refresh" not in ar.build_pilot_argv(
        pdf=tmp_path / "r.pdf",
        pages=[1],
        claim_pages=None,
        report_year=2024,
        period_start="2024-01-01",
        period_end="2024-12-31",
        state=tmp_path / "s",
        key_file=tmp_path / "absent.env",
        invoke=False,
        serve=False,
        port=8766,
    )
    legacy, refreshed = solar_pro4_capacity_policy(), solar_pro4_capacity_policy(refreshed=True)
    assert legacy["expires_at"] != refreshed["expires_at"]
    assert {k: v for k, v in legacy.items() if k not in {"captured_at", "expires_at"}} == {
        k: v for k, v in refreshed.items() if k not in {"captured_at", "expires_at"}
    }
