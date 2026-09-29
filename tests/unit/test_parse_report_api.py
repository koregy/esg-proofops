"""Offline tests for scripts/parse_report_api.py (bounded Document Parse batches).

The real ``UpstageParseProbe`` runs against temp session-grant ledgers; only its
``_post_parse`` transport is faked, so no network call or real key is used.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from proofops.adapters.local import upstage, upstage_parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import parse_report_api as cli  # noqa: E402

KEY = "fake-test-key-never-printed"
CLOCK = {"now": datetime(2026, 9, 29, 6, 0, tzinfo=UTC)}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return CLOCK["now"]

    CLOCK["now"] = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
    for module in (upstage, upstage_parse, cli):
        monkeypatch.setattr(module, "datetime", FixedDateTime)


def _pdf(path: Path, pages: int = 12) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for number in range(pages):
        page = writer.add_blank_page(width=200 + number, height=300)
        if number == 1:
            page.rotate(90)
            page.cropbox.lower_left = (10, 20)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def _ledger(path: Path, amount: str = "5") -> Path:
    upstage.create_session_ledger(
        path,
        amount_usd=amount,
        reason="User approved additional parse budget for tests",
        authorized_by="test",
        authorized_at="2026-09-29T05:00:00+00:00",
        expires_at="2026-09-29T15:00:00+00:00",
    )
    return path


class FakeTransport(upstage_parse.UpstageParseProbe):
    calls: list[int] = []
    fail_on: int | None = None

    def _post_parse(self, pdf_bytes: bytes, mode: str) -> dict:
        from pypdf import PdfReader

        FakeTransport.calls.append(len(pdf_bytes))
        if len(FakeTransport.calls) == FakeTransport.fail_on:
            raise ValueError("UPSTAGE_HTTP_500")
        pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        return {
            "api": "2.0",
            "model": upstage_parse.PARSE_MODEL_PINNED,
            "usage": {"pages": pages, mode: list(range(1, pages + 1))},
            "elements": [{"id": 0, "page": 1, "category": "paragraph"}],
        }


@pytest.fixture
def env(tmp_path):
    FakeTransport.calls = []
    FakeTransport.fail_on = None
    (tmp_path / "keys").mkdir()
    key_file = tmp_path / "keys" / ".env"
    key_file.write_text(f"OTHER=1\nUPSTAGE_API_KEY={KEY}\n", encoding="utf-8")
    return {
        "pdf": _pdf(tmp_path / "report.pdf"),
        "ledger": _ledger(tmp_path / "ledger" / "grant.sqlite3"),
        "key": key_file,
        "out": tmp_path / "out",
    }


def _argv(env, *extra, pages=("--all-pages",)):
    return [
        "--pdf", str(env["pdf"]), *pages,
        "--ledger", str(env["ledger"]), "--key-file", str(env["key"]),
        "--out-dir", str(env["out"]), *extra,
    ]  # fmt: skip


def _run(env, *extra, **kwargs) -> int:
    return cli.main(_argv(env, *extra, **kwargs), probe_factory=FakeTransport)


def _rows(ledger: Path):
    with closing(sqlite3.connect(ledger)) as db:
        return db.execute(
            "SELECT request_id, committed, receipt IS NULL FROM probe_calls"
        ).fetchall()


def test_dry_plan_default_reads_no_key_and_writes_nothing(env, capsys):
    env["key"].unlink()
    assert _run(env, pages=("--pages", "1-2,12")) == 0
    plan = json.loads(capsys.readouterr().out)
    assert not env["out"].exists() and FakeTransport.calls == []
    assert plan["dry_run"] and plan["ledger"]["state"] == "session_grant"
    assert plan["model"] == "document-parse-260128" and plan["quality"] == "candidate_only"
    (batch,) = plan["batches"]
    assert batch["request_id"] is None
    assert [p["physical_page"] for p in batch["pages"]] == [1, 2, 12]
    rotated = batch["pages"][1]
    assert rotated["rotation"] == 90 and rotated["cropbox"][:2] == [10.0, 20.0]
    assert batch["pages"][2]["mediabox"] == [0.0, 0.0, 211.0, 300.0]


def test_invoke_writes_immutable_manifest_receipts_and_settles_one_ledger(env, capsys):
    assert _run(env, "--invoke") == 0
    status = json.loads(capsys.readouterr().out)
    out = env["out"]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [len(b["pages"]) for b in manifest["batches"]] == [10, 2]
    assert manifest["source"]["sha256"] == cli._sha(env["pdf"].read_bytes())
    assert manifest["ledger"]["path"] == str(env["ledger"].resolve())
    assert [r["state"] for r in status["batches"]] == ["completed", "completed"]
    assert status["settled_gross_usd"] == "0.132"
    rows = _rows(env["ledger"])
    assert sorted(r[0] for r in rows) == sorted(b["request_id"] for b in manifest["batches"])
    assert all(not pending for *_, pending in rows)
    receipt = json.loads((out / "batches" / "002.receipt.json").read_text(encoding="utf-8"))
    assert receipt["split_sha256"] == manifest["batches"][1]["split_sha256"]
    assert receipt["usage"] == {"pages": 2, "standard": [1, 2]}
    assert "raw_response" not in receipt and receipt["verification"] == "not_source_verified"
    for path in out.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()
            assert path.suffix != ".sqlite3"
    with pytest.raises(PermissionError):
        (out / "manifest.json").write_text("{}")


def test_existing_output_and_changed_resume_are_refused(env):
    assert _run(env, "--invoke", "--max-batches", "1") == 0
    with pytest.raises(SystemExit):
        _run(env, "--invoke")
    with pytest.raises(SystemExit):
        _run(env, "--invoke", "--resume", "--mode", "enhanced")
    with pytest.raises(SystemExit):
        _run(env, "--invoke", "--resume", pages=("--pages", "1-11"))
    assert len(FakeTransport.calls) == 1


def test_resume_reuses_validated_responses_and_sends_only_remaining(env):
    assert _run(env, "--invoke", "--max-batches", "1") == 0
    assert _run(env, "--invoke", "--resume") == 0
    assert len(FakeTransport.calls) == 2 and len(_rows(env["ledger"])) == 2
    assert _run(env, "--invoke", "--resume") == 0
    assert len(FakeTransport.calls) == 2


def test_tampered_saved_response_is_refused_on_resume(env):
    assert _run(env, "--invoke", "--max-batches", "1") == 0
    response = env["out"] / "batches" / "001.response.json"
    response.chmod(0o644)
    response.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(SystemExit):
        _run(env, "--invoke", "--resume")
    assert len(FakeTransport.calls) == 1


def test_failure_stops_preserves_reservation_and_is_never_repeated(env, capsys):
    FakeTransport.fail_on = 2
    assert _run(env, "--invoke", "--batch-pages", "5") == 1
    status = json.loads(capsys.readouterr().err.split("\n", 0)[0])
    assert [r["state"] for r in status["batches"]] == ["completed", "blocked", "planned"]
    assert status["stopped"] == "UPSTAGE_HTTP_500"
    failure = json.loads((env["out"] / "batches" / "002.failure.json").read_text("utf-8"))
    assert failure["ledger_row_present"] and failure["reservation"] == "retained_or_unknown"
    assert sorted(r[1] for r in _rows(env["ledger"])) == ["0.055", "1.00"]
    FakeTransport.fail_on = None
    with pytest.raises(SystemExit):
        _run(env, "--invoke", "--resume", "--batch-pages", "5")
    assert len(FakeTransport.calls) == 2


def test_pending_attempt_without_receipt_is_never_repeated(env):
    assert _run(env, "--invoke", "--max-batches", "1") == 0
    stem = env["out"] / "batches" / "002"
    Path(f"{stem}.attempt.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        _run(env, "--invoke", "--resume")
    assert len(FakeTransport.calls) == 1


@pytest.mark.parametrize("case", ["absent", "legacy", "expired", "headroom", "key", "price"])
def test_gates_refuse_before_key_read_call_or_output(env, tmp_path, case):
    if case == "absent":
        env["ledger"] = tmp_path / "missing.sqlite3"
    elif case == "legacy":
        env["ledger"] = tmp_path / "legacy" / "budget.sqlite3"
        upstage.UpstageProbe("x", env["ledger"])
    elif case == "expired":
        CLOCK["now"] = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
    elif case == "headroom":
        env["ledger"] = _ledger(tmp_path / "small" / "grant.sqlite3", amount="1.13")
    elif case == "key":
        env["key"].write_text(f"UPSTAGE_API_KEY={KEY}\nUPSTAGE_API_KEY=other\n")
    elif case == "price":
        CLOCK["now"] = upstage.PRICE_RECHECK_AT
    with pytest.raises(SystemExit):
        _run(env, "--invoke")
    assert FakeTransport.calls == []
    assert not env["out"].exists()


def test_out_dir_may_not_hold_ledger_and_resume_needs_invoke(env, tmp_path):
    env["out"] = env["ledger"].parent
    with pytest.raises(SystemExit):
        _run(env, "--invoke")
    with pytest.raises(SystemExit):
        _run(env, "--resume")
    with pytest.raises(SystemExit):
        _run(env, "--batch-pages", "11")


def test_headroom_is_one_reservation_plus_each_batch_max_gross(env, tmp_path):
    # 12 standard pages: USD1 + 0.110 + 0.022 = 1.132 (1.13 is refused above).
    env["ledger"] = _ledger(tmp_path / "exact" / "grant.sqlite3", amount="1.14")
    assert _run(env, "--invoke") == 0
    assert len(FakeTransport.calls) == 2
