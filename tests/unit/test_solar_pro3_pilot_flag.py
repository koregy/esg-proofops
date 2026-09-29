"""Explicit ``--tagging-model`` for NEW pilot runs; Pro 4 default and resume pins unchanged.

Offline only: no subprocess, network, key or model call.
"""

from __future__ import annotations

import json
import sys

import pytest
from proofops.application.input_reservation import solar_pro4_capacity_policy
from proofops.application.input_reservation_pro3 import solar_pro3_capacity_policy

from evaluation import local_upstage_pilot as pilot
from evaluation.local_upstage_pilot import apply_resume_metadata, live_tagging_settings
from tests.unit.test_analyze_report import _args as report_args
from tests.unit.test_analyze_report import (
    ar,
    pdf,  # noqa: F401 - pytest fixture
)
from tests.unit.test_extraction_source_id_wiring import _args as pilot_args

ROLES = ("preliminary_settings", "tagging_settings", "relation_settings")


def _without_binding_ids(settings: dict) -> dict:
    return {
        key: ({k: v for k, v in value.items() if k != "binding"} if key in ROLES else value)
        for key, value in settings.items()
    }


def test_default_is_byte_identical_to_explicit_pro4():
    default = live_tagging_settings(12, relations=True)
    explicit = live_tagging_settings(12, relations=True, tagging_model="solar-pro4")
    assert _without_binding_ids(default) == _without_binding_ids(explicit)
    assert {default[role]["model_id"] for role in ROLES} == {"solar-pro4"}
    assert default["input_reservation_policy"] == solar_pro4_capacity_policy()
    refreshed = live_tagging_settings(12, capacity_refresh=True)
    assert refreshed["input_reservation_policy"] == solar_pro4_capacity_policy(refreshed=True)


def test_pro3_pins_every_role_and_the_pro3_policy():
    settings = live_tagging_settings(18, relations=True, tagging_model="solar-pro3")
    assert {settings[role]["model_id"] for role in ROLES} == {"solar-pro3"}
    assert settings["input_reservation_policy"] == solar_pro3_capacity_policy()
    pro4 = live_tagging_settings(18, relations=True)
    for role in ROLES:  # only the model changes; prompts/profiles/limits stay pinned
        same = {k: v for k, v in settings[role].items() if k not in ("binding", "model_id")}
        assert same == {k: v for k, v in pro4[role].items() if k not in ("binding", "model_id")}


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(tagging_model="solar-pro2"),
        dict(tagging_model=None),
        dict(tagging_model="solar-pro3", capacity_refresh=True),
    ],
)
def test_invalid_tagging_model_choices_are_rejected(kwargs):
    with pytest.raises(ValueError):
        live_tagging_settings(12, **kwargs)


def test_resume_restores_legacy_pro4_and_pinned_pro3():
    legacy = pilot_args()
    apply_resume_metadata(legacy, {"source_path": "/tmp/example.pdf"})
    assert legacy.tagging_model == "solar-pro4"
    pinned = pilot_args()
    apply_resume_metadata(
        pinned, {"source_path": "/tmp/example.pdf", "tagging_model": "solar-pro3"}
    )
    assert pinned.tagging_model == "solar-pro3"


@pytest.mark.parametrize(
    ("saved", "requested"),
    [({}, "solar-pro3"), ({"tagging_model": "solar-pro3"}, "solar-pro4")],
)
def test_resume_rejects_an_attempted_tagging_model_change(saved, requested):
    args = pilot_args(tagging_model=requested)
    with pytest.raises(ValueError, match="cannot change tagging-model"):
        apply_resume_metadata(args, {"source_path": "/tmp/example.pdf", **saved})


def test_resume_cli_rejects_switching_a_legacy_run_to_pro3(tmp_path, monkeypatch, capsys):
    state = tmp_path / "legacy"
    state.mkdir()
    (state / "pilot.json").write_text(json.dumps({"source_path": "/tmp/example.pdf"}))
    monkeypatch.setattr(
        sys,
        "argv",
        ["pilot", "--resume", "--state", str(state), "--tagging-model", "solar-pro3"],
    )
    with pytest.raises(SystemExit):
        pilot.main()
    assert "cannot change tagging-model" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ([], "--tagging-model solar-pro3 requires --live-tagging"),
        (["--live-tagging", "--capacity-policy-refresh"], "is a Pro 4 policy"),
    ],
)
def test_new_run_pro3_requires_live_tagging_and_no_pro4_refresh(
    tmp_path, monkeypatch, capsys, extra, message
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pilot",
            "--pdf",
            str(tmp_path / "missing.pdf"),
            "--state",
            str(tmp_path / "state"),
            "--report-year",
            "2024",
            "--period-start",
            "2024-01-01",
            "--period-end",
            "2024-12-31",
            "--tagging-model",
            "solar-pro3",
            *extra,
        ],
    )
    with pytest.raises(SystemExit):
        pilot.main()
    assert message in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


def test_analyze_report_passes_the_explicit_flag_only(pdf, tmp_path):  # noqa: F811
    legacy = ar.plan_run(report_args(pdf, state=tmp_path / "legacy"))
    assert "--tagging-model" not in legacy["argv"] and legacy["tagging_model"] == "solar-pro4"
    plan = ar.plan_run(report_args(pdf, state=tmp_path / "pro3", tagging_model="solar-pro3"))
    assert plan["argv"][plan["argv"].index("--tagging-model") + 1] == "solar-pro3"
    assert plan["tagging_model"] == "solar-pro3"
    # Extraction model is unchanged by the tagging choice.
    assert (
        plan["argv"][plan["argv"].index("--model") + 1]
        == legacy["argv"][legacy["argv"].index("--model") + 1]
    )


def test_analyze_report_cli_parses_the_flag():
    base = ["--pdf", "/tmp/x.pdf", "--report-year", "2024"]
    base += ["--period-start", "2024-01-01", "--period-end", "2024-12-31"]
    parser = ar.build_parser()
    assert parser.parse_args(base).tagging_model is None
    assert parser.parse_args([*base, "--tagging-model", "solar-pro3"]).tagging_model == "solar-pro3"
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--tagging-model", "solar-pro2"])
