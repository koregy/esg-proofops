"""--serve-bootstrap serves upload pages for a never-run seed without weakening --serve."""

import sys

import pytest

from evaluation import local_upstage_pilot as pilot

NEVER_RAN = dict(run_id="seed", status="cancelled", revision=2)


def refusal(claims_status, meta, *, bootstrap, worker=False, pipeline="not_run"):
    return pilot.serve_refusal(
        claims_status, meta, bootstrap=bootstrap, worker=worker, pipeline_status=pipeline
    )


def test_ordinary_serve_keeps_claims_200_requirement():
    assert refusal(200, dict(status="completed", current_stage="review"), bootstrap=False) is None
    for meta in (NEVER_RAN, dict(status="queued")):
        message = refusal(409, meta, bootstrap=False)
        assert message is not None and "claims HTTP 409" in message


@pytest.mark.parametrize("worker", [False, True])
def test_bootstrap_serves_cancelled_seed_that_never_ran(worker):
    assert refusal(409, NEVER_RAN, bootstrap=True, worker=worker) is None


def test_bootstrap_queued_seed_only_without_worker():
    queued = dict(status="queued")
    assert refusal(409, queued, bootstrap=True, worker=False) is None
    message = refusal(409, queued, bootstrap=True, worker=True)
    assert message is not None and "cancel it first" in message


@pytest.mark.parametrize(
    "meta",
    [
        dict(status="completed", current_stage="review"),
        dict(status="partial", current_stage="tag"),
        dict(status="failed"),
        dict(status="running"),
        dict(status="cancelled", current_stage="extract"),
        dict(status="cancelled", parse_job={}),
    ],
)
@pytest.mark.parametrize("worker", [False, True])
def test_bootstrap_keeps_integrity_errors_fail_closed(meta, worker):
    message = refusal(409, meta, bootstrap=True, worker=worker)
    assert message is not None and "never ran" in message


def test_bootstrap_refuses_after_pipeline_ran():
    for status in ("completed", "failed"):
        assert refusal(409, NEVER_RAN, bootstrap=True, pipeline=status) is not None


@pytest.mark.parametrize(
    ("extra", "needle"),
    [([], "requires --serve"), (["--serve", "--invoke"], "never invokes models")],
)
def test_bootstrap_cli_is_explicit_and_never_invokes(monkeypatch, capsys, tmp_path, extra, needle):
    state = tmp_path / "state"
    argv = ["pilot", "--state", str(state), "--serve-bootstrap", "--port", "1", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as error:
        pilot.main()
    assert error.value.code == 2
    assert needle in capsys.readouterr().err
    assert not state.exists()


def test_bootstrap_login_lands_on_upload_page_with_same_cookie_policy():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from proofops.adapters.local.auth_store import InMemorySessionStore

    sessions = InMemorySessionStore()
    app = FastAPI()
    app.get("/__local/secret")(
        lambda: pilot.local_login_response(sessions, "user", "tenant", "run", "/documents/new")
    )
    app.get("/__local/default")(
        lambda: pilot.local_login_response(sessions, "user", "tenant", "run")
    )
    client = TestClient(app, base_url="https://localhost", follow_redirects=False)
    landed = client.get("/__local/secret")
    assert landed.status_code == 303 and landed.headers["location"] == "/documents/new"
    assert all(
        flag in landed.headers["set-cookie"] for flag in ("Secure", "HttpOnly", "SameSite=strict")
    )
    assert client.get("/__local/default").headers["location"] == "/runs/run/claims"


def submission(*, bootstrap, claim_pages=None):
    return pilot.local_submission_body(
        worker_enabled=True,
        candidate_rule_pack_id="pack",
        selected_pages=[1],
        claim_pages=claim_pages,
        bootstrap=bootstrap,
    )


def test_local_submission_offers_only_declared_subset_scope():
    # The pilot composes upstage_probe, whose run creation rejects scope=full.
    for bootstrap in (False, True):
        body = submission(bootstrap=bootstrap)
        assert body["supported_scopes"] == ["declared_subset"]
        assert body["scope_reason"] == "upstage_probe_declared_subset_only"


def test_bootstrap_submission_withholds_seed_default_pages():
    body = submission(bootstrap=True)
    assert body["selected_pages"] == [] and body["page_selection"] == "explicit_required"
    assert body["worker_enabled"] is True and body["candidate_rule_pack_id"] == "pack"


def test_saved_run_submission_keeps_declared_pages_and_claim_pin():
    body = submission(bootstrap=False, claim_pages=[1])
    assert body["selected_pages"] == [1] and body["page_selection"] == "saved_run_pages"
    assert body["required_pages"] == [1]
    assert submission(bootstrap=True)["required_pages"] == []
