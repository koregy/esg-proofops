"""NEW-run native Upstage OCR: run creation -> parser -> checkpoint -> offline load.

The real ``UpstageParseProbe`` runs against a temporary session ledger. Only its
``_post_parse`` transport is replaced by a pinned response, so reservation, settlement
and receipts are the production code paths. Off macOS the base receipt stops at
``UnsupportedPlatform``, which is exactly this path's eligibility. No network, no key.
"""

import json
import sys
from uuid import uuid4

import pytest
from proofops.adapters.local import native_upstage_ocr_store as records
from proofops.adapters.local.native_upstage_ocr import native_upstage_ocr_policy
from proofops.application.ports.jobs import JobMessage
from proofops.domain.provenance import canonical_hash

from tests.integration.test_live_tagging_runtime_config import _register
from tests.integration.test_raster_parser_worker import prose_pdf
from tests.integration.test_raster_runtime_config import AUTH, configured
from tests.integration.test_upstage_parse import fixed_pricing_date  # noqa: F401

pytestmark = pytest.mark.skipif(
    sys.platform == "darwin", reason="off-macOS base receipt (UnsupportedPlatform) required"
)
NATIVE = (
    "The company reduced emissions by 1234 tCO2e. "
    "This paragraph is deliberately long enough for parser prose."
)


def _response(text):
    elements = [] if text is None else [{"id": 0, "page": 1, "content": {"text": text}}]
    return {
        "model": "document-parse-260128",
        "usage": {"pages": 1, "standard": [1]},
        "elements": elements,
    }


def setup_run(tmp_path, monkeypatch, *, provider_text=NATIVE, pdf=None, max_calls=1):
    from proofops.adapters.local.run_artifacts import load_run_inputs
    from proofops.adapters.local.upstage_parse import UpstageParseProbe
    from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
    from proofops.application.telemetry import Telemetry
    from proofops_worker.local_runner import LocalParserRunner

    from tests.integration import test_run_lifecycle

    source = pdf if pdf is not None else prose_pdf(heading=True)
    monkeypatch.setattr(test_run_lifecycle, "pdf", lambda _: source)
    service, body = configured(tmp_path)
    policy = native_upstage_ocr_policy(max_calls=max_calls)
    grant = dict(
        service.registry.resolve_profile(AUTH, "runtime", service.raster_runtime_binding_id)
    )
    grant.update(
        runtime_binding_id=str(uuid4()),
        max_calls=max_calls,
        raster_policy_sha256=canonical_hash(policy),
    )
    service.raster_runtime_binding_id = service.raster_policy = None
    service.upstage_ocr_runtime_binding_id = _register(
        service, "runtime", grant, "runtime_binding_id"
    )
    service.upstage_ocr_policy = policy
    run_id = service.create(AUTH, body, str(uuid4()))["run_id"]
    _snapshot, _source, profile = load_run_inputs(
        service.store, service.uploads, tenant_id=AUTH.tenant_id, run_id=run_id
    )
    ledger = tmp_path / "shared-ledger.sqlite"
    probe = UpstageParseProbe("test-upstage-ocr-secret", ledger)
    calls = []

    def pinned(data, mode):
        calls.append((data, mode))
        if isinstance(provider_text, BaseException):
            raise provider_text
        return _response(provider_text)

    monkeypatch.setattr(probe, "_post_parse", pinned)
    runner = LocalParserRunner(
        service.store,
        service.uploads,
        OpenDataLoaderParser(tmp_path / "prepared"),
        profile=profile,
        telemetry=Telemetry(service="worker", env="test", stream=None, hash_key=b"x" * 32),
        clock=lambda: int(service.clock()) + 120,
        verify_paragraphs=True,
        upstage_ocr_probe=probe,
        upstage_ocr_ledger=ledger,
    )
    return service, runner, run_id, probe, calls


def _message(service, run_id):
    run = service.store.jobs.get_run(AUTH.tenant_id, run_id)
    if "parse_job" in run:
        return JobMessage(**run["parse_job"])
    return JobMessage(
        **service.store.jobs.pending_outbox(AUTH.tenant_id, run_id, now=int(service.clock()))[0][
            "message"
        ]
    )


def test_snapshot_pins_policy_and_excludes_raster(tmp_path, monkeypatch):
    service, _runner, run_id, _probe, _calls = setup_run(tmp_path, monkeypatch)
    snapshot = service.store.snapshot(AUTH.tenant_id, run_id)
    assert snapshot["native_upstage_ocr_policy"] == native_upstage_ocr_policy()
    assert snapshot["native_upstage_ocr_policy_hash"] == canonical_hash(
        native_upstage_ocr_policy()
    )
    assert not any(key.startswith("raster_ocr_") for key in snapshot)
    # Both configurations at once never create a run.
    with pytest.raises(ValueError):
        type(service)(
            service.store,
            service.uploads,
            service.registry,
            raster_runtime_binding_id=str(uuid4()),
            raster_policy={},
            upstage_ocr_runtime_binding_id=str(uuid4()),
            upstage_ocr_policy=native_upstage_ocr_policy(),
        )


def test_positive_run_publishes_verified_source_and_replays_offline(tmp_path, monkeypatch):
    service, runner, run_id, probe, calls = setup_run(tmp_path, monkeypatch)
    identity = dict(tenant_id=AUTH.tenant_id, run_id=run_id)
    assert runner.run_once(**identity) == "committed"
    message = _message(service, run_id)
    checkpoint = service.store.jobs.read_checkpoint(message)
    payload = json.loads(checkpoint)
    assert payload["schema"] == "local_parser_checkpoint_v4"
    coverage = payload["native_paragraph_upstage_ocr_coverage"]
    assert len(coverage["eligible_source_ids"]) == 1, payload["native_paragraph_attestation"]
    assert coverage["corroborated_source_ids"] == coverage["eligible_source_ids"]
    assert coverage["skipped_source_ids"] == [] and coverage["complete"] is True
    assert payload["native_paragraph_upstage_ocr_policy_sha256"] == canonical_hash(
        native_upstage_ocr_policy()
    )
    (ref,) = payload["native_paragraph_upstage_ocr_artifacts"]
    assert len(calls) == 1
    # The base receipt is unchanged: its record still says rendered reader unavailable.
    (record,) = [
        r
        for r in payload["native_paragraph_attestation"]["records"]
        if r["source_id"] in coverage["eligible_source_ids"]
    ]
    assert record["rendered"]["error"] == "UnsupportedPlatform"
    # Offline load: reopen durable storage; any provider call fails the test.
    monkeypatch.setattr(probe, "parse", lambda *a, **k: pytest.fail("replay called provider"))
    runner.store = type(service.store)(service.store.path)
    graph = runner.load_graph(**identity)
    verified = [b for b in graph.blocks if b.source_id in coverage["eligible_source_ids"]]
    assert [b.quality for b in verified] == ["verified"]
    # The graph text is the native text, never the provider's.
    assert verified[0].raw_text.startswith("The company reduced emissions by 1234 tCO2e.")
    usage = service.store.jobs.list_usage(**identity)
    assert sum(item["model_calls"] for item in usage) == 1
    assert ref["request_id"] in {i for item in usage for i in item.get("raster_request_ids", [])}
    assert service.store.jobs.read_checkpoint(message) == checkpoint


@pytest.mark.parametrize(
    "provider_text",
    [
        "The company reduced emissions by 1235 tCO2e. "
        "This paragraph is deliberately long enough for parser prose.",
        "The company reduced emissions by 1234 tCO2e.",
        "Thecompanyreducedemissionsby1234tCO2e.Thisparagraphisdeliberatelylongenoughforparserprose.",
        "",
        None,
    ],
)
def test_ocr_mismatch_or_hidden_text_stays_unresolved(tmp_path, monkeypatch, provider_text):
    service, runner, run_id, _probe, calls = setup_run(
        tmp_path, monkeypatch, provider_text=provider_text
    )
    identity = dict(tenant_id=AUTH.tenant_id, run_id=run_id)
    assert runner.run_once(**identity) == "committed"
    payload = json.loads(service.store.jobs.read_checkpoint(_message(service, run_id)))
    coverage = payload["native_paragraph_upstage_ocr_coverage"]
    assert coverage["corroborated_source_ids"] == []
    assert coverage["unresolved_source_ids"] == coverage["eligible_source_ids"] != []
    graph = runner.load_graph(**identity)
    assert all(
        b.quality != "verified" for b in graph.blocks if b.source_id in coverage["eligible_source_ids"]
    )
    assert len(calls) == 1


def test_unsettled_call_never_publishes_or_resends(tmp_path, monkeypatch):
    service, runner, run_id, probe, calls = setup_run(
        tmp_path, monkeypatch, provider_text=TimeoutError("ambiguous response")
    )
    identity = dict(tenant_id=AUTH.tenant_id, run_id=run_id)
    message = _message(service, run_id)
    assert runner.run_once(**identity) in {"failed", "retry"}
    assert service.store.jobs.read_checkpoint(message) is None
    assert len(records.requests_for(service.store.jobs, message)) == 1
    assert probe.summary()["unsettled_calls"] == 1
    usage = service.store.jobs.list_usage(**identity)
    assert len(usage[-1]["raster_pending_request_ids"]) == 1
    with pytest.raises(ValueError, match="UPSTAGE_OCR_REQUEST_PENDING"):
        records.stored_entries(service.store.jobs, message)
    assert len(calls) == 1
    # A retry of the same job reuses the registration and still refuses to publish.
    runner.run_once(**identity)
    assert service.store.jobs.read_checkpoint(message) is None
    assert len(calls) == 1


@pytest.mark.parametrize("tamper", ["coverage", "receipt", "policy", "graph", "drop"])
def test_reader_rejects_tampered_publication(tmp_path, monkeypatch, tamper):
    from proofops.adapters.parsing.opendataloader import ParseFailure

    service, runner, run_id, probe, calls = setup_run(tmp_path, monkeypatch)
    identity = dict(tenant_id=AUTH.tenant_id, run_id=run_id)
    assert runner.run_once(**identity) == "committed"
    runner.load_graph(**identity)
    payload = json.loads(service.store.jobs.read_checkpoint(_message(service, run_id)))
    coverage = payload["native_paragraph_upstage_ocr_coverage"]
    if tamper == "coverage":
        coverage["unresolved_source_ids"] = coverage["eligible_source_ids"]
        coverage["corroborated_source_ids"] = []
    elif tamper == "receipt":
        payload["native_paragraph_upstage_ocr_artifacts"][0]["receipt_sha256"] = "0" * 64
    elif tamper == "policy":
        payload["native_paragraph_upstage_ocr_policy_sha256"] = "0" * 64
    elif tamper == "graph":
        payload["graph_sha256"] = "0" * 64
    else:
        for key in list(payload):
            if key.startswith("native_paragraph_upstage_ocr_"):
                payload.pop(key)
    monkeypatch.setattr(
        service.store.jobs, "read_checkpoint", lambda *a, **k: json.dumps(payload).encode()
    )
    monkeypatch.setattr(probe, "parse", lambda *a, **k: pytest.fail("reader invoked provider"))
    with pytest.raises((ParseFailure, ValueError)):
        runner.load_graph(**identity)
    assert len(calls) == 1


def test_changed_pinned_code_refuses_stale_publication(tmp_path, monkeypatch):
    from proofops.adapters.local import native_upstage_ocr

    service, runner, run_id, probe, _calls = setup_run(tmp_path, monkeypatch)
    identity = dict(tenant_id=AUTH.tenant_id, run_id=run_id)
    assert runner.run_once(**identity) == "committed"
    live = native_upstage_ocr.native_upstage_ocr_policy

    def changed(**kwargs):
        return {**live(**kwargs), "helper_sha256": "f" * 64}

    monkeypatch.setattr(native_upstage_ocr, "native_upstage_ocr_policy", changed)
    with pytest.raises(ValueError):
        runner.load_graph(**identity)


def test_probe_without_snapshot_policy_is_refused_before_any_job(tmp_path, monkeypatch):
    from proofops_worker.local_runner import LocalParserRunner

    from tests.integration.test_local_parser_runner import TENANT, runner_setup

    service, run_id, runner, _now, _stream = runner_setup(tmp_path, monkeypatch)
    from proofops.adapters.local.upstage_parse import UpstageParseProbe

    ledger = tmp_path / "ledger.sqlite3"
    runner = LocalParserRunner(
        service.store,
        service.uploads,
        runner.parser,
        profile=runner.profile,
        telemetry=runner.telemetry,
        clock=runner.clock,
        verify_paragraphs=True,
        upstage_ocr_probe=UpstageParseProbe("test-secret", ledger),
        upstage_ocr_ledger=ledger,
    )
    with pytest.raises(ValueError, match="UPSTAGE_OCR_RUNTIME_NOT_SUPPORTED"):
        runner.run_once(tenant_id=TENANT, run_id=run_id)
    assert "parse_job" not in service.store.jobs.get_run(TENANT, run_id)


def test_worker_composition_flag_rules(tmp_path, monkeypatch):
    from proofops_worker.composition import build_composition

    for kwargs in (
        dict(native_upstage_ocr=True),
        dict(native_upstage_ocr=True, verify_paragraphs=True, raster_ocr=True),
        dict(native_upstage_ocr=True, verify_paragraphs=True, native_typography_tolerance=True),
        dict(native_upstage_ocr=True, verify_paragraphs=True, stage="extract"),
        dict(native_upstage_ocr="yes", verify_paragraphs=True),
    ):
        with pytest.raises(ValueError):
            build_composition(**kwargs)


def test_macos_style_base_reader_refuses_windows_produced_proof(tmp_path, monkeypatch):
    """Portability: the base receipt pins its reader verdict (UnsupportedPlatform).

    A host whose base reader actually reads (macOS Vision) recomputes a different base
    receipt, so the published checkpoint fails closed instead of being reinterpreted.
    Simulated in this test only by the reader's return value; production code is unchanged.
    """
    from collections import OrderedDict

    from proofops.adapters.local import native_replay_cache, source_verification
    from proofops.adapters.parsing.opendataloader import ParseFailure

    service, runner, run_id, probe, calls = setup_run(tmp_path, monkeypatch)
    identity = dict(tenant_id=AUTH.tenant_id, run_id=run_id)
    assert runner.run_once(**identity) == "committed"
    monkeypatch.setattr(native_replay_cache, "_replays", OrderedDict())
    monkeypatch.setattr(
        source_verification,
        "_rendered_text",
        lambda page, box, **_: dict(status="read", text=NATIVE, image_sha256="0" * 64),
    )
    monkeypatch.setattr(probe, "parse", lambda *a, **k: pytest.fail("reader invoked provider"))
    with pytest.raises(ParseFailure, match="NATIVE_PARAGRAPH_REPLAY_INVALID"):
        runner.load_graph(**identity)
    assert len(calls) == 1
