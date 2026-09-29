"""New-run widget visibility opt-in cannot silently alter a stored run."""

import sys

import pytest

from evaluation import local_upstage_pilot as pilot
from tests.unit.test_extraction_source_id_wiring import _args


def test_legacy_settings_do_not_gain_widget_policy():
    settings = pilot.upstage_ocr_settings(max_pages=10, max_calls=2)
    assert set(settings) == {"upstage_ocr_runtime_binding_id", "upstage_ocr_policy"}


def test_widget_policy_is_explicit_and_pinned():
    from proofops.adapters.local.native_widget_visibility import native_widget_visibility_policy

    settings = pilot.upstage_ocr_settings(max_pages=10, max_calls=2, widget_visibility=True)
    assert settings["upstage_ocr_widget_visibility"] == native_widget_visibility_policy()


def test_resume_restores_opt_in_but_never_adds_it():
    args = _args()
    pilot.apply_resume_metadata(args, {"source_path": "/tmp/report.pdf"})
    assert args.native_widget_visibility is False
    args = _args()
    pilot.apply_resume_metadata(
        args, {"source_path": "/tmp/report.pdf", "native_widget_visibility": True}
    )
    assert args.native_widget_visibility is True
    args = _args(native_widget_visibility=True)
    with pytest.raises(ValueError, match="cannot add native widget visibility"):
        pilot.apply_resume_metadata(args, {"source_path": "/tmp/report.pdf"})


def test_widget_flag_requires_native_upstage_before_state_creation(tmp_path, monkeypatch, capsys):
    state = tmp_path / "uncreated"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pilot",
            "--state",
            str(state),
            "--pdf",
            str(tmp_path / "missing.pdf"),
            "--report-year",
            "2025",
            "--period-start",
            "2025-01-01",
            "--period-end",
            "2025-12-31",
            "--native-widget-visibility",
        ],
    )
    with pytest.raises(SystemExit) as error:
        pilot.main()
    assert error.value.code == 2
    assert "--native-widget-visibility requires --native-upstage-ocr" in capsys.readouterr().err
    assert not state.exists()
