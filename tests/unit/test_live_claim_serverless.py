"""One fake-call path through the serverless claim demo; no network or paid model."""

import importlib.util
import json
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
import yaml

MODULE = Path(__file__).resolve().parents[2] / "api/live-claim.py"
SPEC = importlib.util.spec_from_file_location("live_claim", MODULE)
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


def test_rule_pack_snapshot_matches_repo_config():
    root = MODULE.parents[1]
    manifest = yaml.safe_load((root / "config/rule_pack_manifest.yaml").read_text())
    assert set(live.PACK.files) == set(manifest["files"])
    for name in manifest["files"]:
        assert live.PACK.file_content(name) == yaml.safe_load((root / "config" / name).read_text())


def test_fake_pipeline_and_guards(monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "local-only")
    called = []

    def fake(system, user, max_tokens):
        called.append((user, max_tokens))
        if len(called) == 1:
            content = {
                "claim_id": user["claim_id"],
                "track": "goal",
                "safe_harbor_category": None,
                "dimensions": {},
            }
        else:
            content = {
                "elements": [
                    {
                        "name": name,
                        "state": "present"
                        if name in ("target_year", "target_metric")
                        else "absent",
                        "quote": (
                            "2030년"
                            if name == "target_year"
                            else "온실가스 배출량"
                            if name == "target_metric"
                            else None
                        ),
                    }
                    for name in user["elements"]
                ]
            }
        return {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }

    claim = "2030년 온실가스 배출량을 줄이겠습니다."
    with pytest.raises(live.LiveError) as error:
        live.run_claim({"claim": claim}, access_code="wrong", call_model=fake)
    assert error.value.code == "ACCESS_DENIED" and not called
    result = live.run_claim({"claim": claim}, access_code="local-only", call_model=fake)
    assert len(called) == 2
    assert result["decision"]["decision_status"] == "blocked_evidence"
    assert result["decision"]["evidence_grade"] is None
    assert result["decision"]["label"] is None
    assert result["decision"]["grade_range"]["floor"] == "E1"
    assert result["decision"]["grade_range"]["ceiling"] == "E3"
    assert result["steps"][1]["elements"][0]["engine_state"] in ("unknown", "present")
    assert all(
        item["engine_state"] == "unknown"
        for item in result["steps"][1]["elements"]
        if item["candidate_state"] == "absent"
    )
    assert "원문 PDF 검증 없음" in result["notice"]


def test_unmatched_quote_rejected(monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "local-only")

    def fake(system, user, max_tokens):
        content = (
            {"claim_id": user["claim_id"], "track": "goal", "safe_harbor_category": None}
            if "sources" in user
            else {
                "elements": [
                    {"name": name, "state": "present", "quote": "없는 근거"}
                    for name in user["elements"]
                ]
            }
        )
        return {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }

    with pytest.raises(live.LiveError) as error:
        live.run_claim(
            {"claim": "2030년 배출을 줄입니다"}, access_code="local-only", call_model=fake
        )
    assert error.value.code == "TAG_QUOTE_NOT_IN_CLAIM"


def test_http_handler_with_fake_model(monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "local-only")

    def fake(system, user, max_tokens):
        content = {"claim_id": user["claim_id"], "track": None, "safe_harbor_category": None}
        return {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }

    monkeypatch.setattr(live, "_provider", fake)
    server = HTTPServer(("127.0.0.1", 0), live.handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/live-claim",
            json.dumps({"claim": "2030년 배출을 줄입니다"}).encode(),
            {"Content-Type": "application/json", "X-Demo-Access-Code": "local-only"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["status"] == "needs_review"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
