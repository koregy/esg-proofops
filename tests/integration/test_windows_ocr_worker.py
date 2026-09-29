"""NEW-run opt-in Windows OCR proof through the parser worker, checkpoint and reader.

A fake reader stands in for Windows.Media.Ocr (the real engine is covered by the
unit tests). Pins: default runs are unchanged, the proof travels beside the unchanged
base receipt/policy, the reader replays it, and tampering or a changed engine fails.
"""

import hashlib
import json
import sys

import pytest
from proofops.adapters.local import windows_ocr
from proofops.adapters.local import windows_rendered_verification as wrv
from proofops.adapters.local.run_artifacts import load_run_evidence, native_paragraph_policy
from proofops.adapters.parsing.opendataloader import ParseFailure
from proofops.application.ports.jobs import JobMessage
from proofops.domain.provenance import canonical_hash

from tests.acceptance.test_parsing import TENANT
from tests.integration.test_native_paragraph_worker import runner_setup_native

pytestmark = pytest.mark.skipif(
    sys.platform == "darwin", reason="off-macOS base receipt (UnsupportedPlatform) required"
)
ENGINE = dict(
    reader="Windows.Media.Ocr",
    language="ko",
    os_version="10.0.26200.0",
    os_build="26200",
    os_ubr="9457",
    max_image_dimension=10000,
)


@pytest.fixture
def fake_engine(monkeypatch):
    engines = {windows_ocr.helper_sha256(): dict(ENGINE)}
    monkeypatch.setattr(windows_ocr, "_engine_cache", engines)
    calls = []

    def reader(policy):
        def read(png):
            calls.append(png)
            return dict(status="read", text="not the native text", engine=dict(ENGINE))

        return read

    monkeypatch.setattr(wrv, "default_reader", reader)
    monkeypatch.setattr(wrv, "_replays", type(wrv._replays)())
    return engines, calls


def _published(service, run_id):
    message = JobMessage(**service.store.jobs.get_run(TENANT, run_id)["parse_job"])
    return message, json.loads(service.store.jobs.read_checkpoint(message))


def test_flag_requires_verify_paragraphs_and_excludes_typography(tmp_path, monkeypatch):
    from proofops_worker.local_runner import LocalParserRunner

    _service, _run, runner, *_ = runner_setup_native(tmp_path, monkeypatch, True)
    kwargs = dict(profile=runner.profile, telemetry=runner.telemetry)
    for bad in (
        dict(native_windows_ocr="yes", verify_paragraphs=True),
        dict(native_windows_ocr=True, verify_paragraphs=False),
        dict(native_windows_ocr=True, verify_paragraphs=True, native_typography_tolerance=True),
    ):
        with pytest.raises(ValueError):
            LocalParserRunner(runner.store, runner.uploads, runner.parser, **kwargs, **bad)


def test_opt_in_publishes_separate_proof_and_replays_it(tmp_path, monkeypatch, fake_engine):
    engines, calls = fake_engine
    base_dir, opted_dir = tmp_path / "base", tmp_path / "opted"
    base_dir.mkdir()
    opted_dir.mkdir()
    base_service, base_run, base_runner, *_ = runner_setup_native(base_dir, monkeypatch, True)
    assert base_runner.run_once(tenant_id=TENANT, run_id=base_run) == "committed"
    _, base_envelope = _published(base_service, base_run)
    assert not any(k.startswith("native_paragraph_windows") for k in base_envelope)

    service, run_id, runner, *_ = runner_setup_native(opted_dir, monkeypatch, True)
    runner.native_windows_ocr = True
    assert runner.run_once(tenant_id=TENANT, run_id=run_id) == "committed"
    message, envelope = _published(service, run_id)
    proof = envelope["native_paragraph_windows_ocr_proof"]
    # Base receipt, policy and schema keep their exact shape and hash.
    assert envelope["schema"] == base_envelope["schema"] == "local_parser_checkpoint_v4"
    assert envelope["native_paragraph_policy_sha256"] == canonical_hash(native_paragraph_policy())
    assert (
        envelope["native_paragraph_policy_sha256"]
        == (base_envelope["native_paragraph_policy_sha256"])
    )
    policy = wrv.native_paragraph_windows_ocr_policy()
    assert envelope["native_paragraph_windows_ocr_policy_sha256"] == canonical_hash(policy)
    assert proof == runner.last_windows_ocr_proof and proof["policy"]["engine"] == ENGINE
    assert proof["native_attestation_sha256"] == canonical_hash(
        envelope["native_paragraph_attestation"]
    )
    assert proof["output_graph_sha256"] == envelope["graph_sha256"]
    # The fake never agrees with native text, so nothing is promoted.
    assert proof["promoted_source_ids"] == []
    assert proof["eligible_source_ids"], "fixture must exercise at least one paragraph"
    assert len(calls) == 2 * len(proof["eligible_source_ids"])

    evidence = load_run_evidence(
        service.store, service.uploads, runner.parser, tenant_id=TENANT, run_id=run_id
    )
    assert evidence["windows_ocr_proof"] == proof

    # A Windows update (engine identity change) refuses the published proof.
    engines[windows_ocr.helper_sha256()] = {**ENGINE, "os_ubr": "9999"}
    monkeypatch.setattr(wrv, "_replays", type(wrv._replays)())
    with pytest.raises(ParseFailure, match="NATIVE_PARAGRAPH_WINDOWS_OCR_POLICY_MISMATCH"):
        load_run_evidence(
            service.store, service.uploads, runner.parser, tenant_id=TENANT, run_id=run_id
        )
    engines[windows_ocr.helper_sha256()] = dict(ENGINE)


def test_tampered_proof_is_rejected_on_load(tmp_path, monkeypatch, fake_engine):
    service, run_id, runner, *_ = runner_setup_native(tmp_path, monkeypatch, True)
    runner.native_windows_ocr = True
    assert runner.run_once(tenant_id=TENANT, run_id=run_id) == "committed"
    message, envelope = _published(service, run_id)
    from proofops.adapters.local.run_artifacts import checkpoint_native_attestation

    assert checkpoint_native_attestation(envelope) is not None
    for change in (
        lambda e: e.pop("native_paragraph_windows_ocr_proof"),
        lambda e: e.update(native_paragraph_windows_ocr_policy_sha256="0" * 64),
        lambda e: e["native_paragraph_windows_ocr_proof"].update(tenant_id="other"),
        lambda e: e.update(
            native_paragraph_typography_policy_sha256="0" * 64,
            native_paragraph_typography_proof={},
        ),
    ):
        edited = json.loads(json.dumps(envelope))
        change(edited)
        with pytest.raises(ParseFailure):
            checkpoint_native_attestation(edited)


def test_promotion_survives_checkpoint_and_reload(tmp_path, monkeypatch, fake_engine):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    service, run_id, runner, *_ = runner_setup_native(first, monkeypatch, True)
    runner.native_windows_ocr = True
    assert runner.run_once(tenant_id=TENANT, run_id=run_id) == "committed"
    _, envelope = _published(service, run_id)
    eligible = envelope["native_paragraph_windows_ocr_proof"]["eligible_source_ids"]
    # Same fixture bytes -> same crops; answer each crop with its native words.
    rows = {r["source_id"]: r for r in envelope["native_paragraph_attestation"]["records"]}
    answers = {
        record["attempts"][0]["image_sha256"]: " ".join(
            w["text"] for w in rows[record["source_id"]]["words"]
        )
        for record in envelope["native_paragraph_windows_ocr_proof"]["records"]
    }
    assert len(answers) == len(eligible)

    def echo(policy):
        def read(png):
            text = answers.get(hashlib.sha256(png).hexdigest(), "unknown crop")
            return dict(status="read", text=text, engine=dict(ENGINE))

        return read

    monkeypatch.setattr(wrv, "default_reader", echo)
    monkeypatch.setattr(wrv, "_replays", type(wrv._replays)())
    service, run_id, runner, *_ = runner_setup_native(second, monkeypatch, True)
    runner.native_windows_ocr = True
    assert runner.run_once(tenant_id=TENANT, run_id=run_id) == "committed"
    _, envelope = _published(service, run_id)
    proof = envelope["native_paragraph_windows_ocr_proof"]
    # Source ids are run-scoped; the same paragraphs are eligible and all promoted.
    assert len(proof["eligible_source_ids"]) == len(eligible)
    eligible = proof["eligible_source_ids"]
    assert proof["promoted_source_ids"] == eligible
    evidence = load_run_evidence(
        service.store, service.uploads, runner.parser, tenant_id=TENANT, run_id=run_id
    )
    verified = {b.source_id for b in evidence["graph"].blocks if b.quality == "verified"}
    assert set(eligible) <= verified
    assert {b.kind for b in evidence["graph"].blocks if b.source_id in eligible} == {"paragraph"}
    # Only paragraphs: no table cell or other kind is promoted by this path.
    assert all(
        b.quality != "verified"
        or b.source_id in set(eligible) | set(proof["base_verified_source_ids"])
        for b in evidence["graph"].blocks
    )
