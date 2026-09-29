"""Worker path for an explicit Solar Pro 3 tagging run: fake HTTP, no key, no spend.

The snapshot selects the probe; the transports, capacity policy, price snapshot
and provider-model check all follow ``solar-pro3``. A Pro 4 probe can never
serve a Pro 3 run (or vice versa), and a mixed or unknown model stops before any
probe is built.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from proofops.adapters.aws.usage import LocalSQLiteUsageStore
from proofops.adapters.local.upstage import MODEL, MODEL_PRO4, PRICE, PRICE_PRO4, UpstageProbe
from proofops.application.budget import BudgetLimits, RoleLimit
from proofops.application.input_reservation import solar_pro4_capacity_policy
from proofops.application.input_reservation_pro3 import solar_pro3_capacity_policy
from proofops.application.registry import artifact_sha256
from proofops.domain.provenance import canonical_hash
from proofops_worker.live_tagging import LiveTaggingRuntime
from proofops_worker.tagging_model import select_tagging_probe, snapshot_tagging_model

PRO3_NOW = datetime(2026, 9, 29, 10, tzinfo=UTC)


def _pro3_snapshot(tmp_path, monkeypatch, *, model=MODEL, policy=None):
    from proofops.application.tagging.relations import SYSTEM_PROMPT as RELATION_PROMPT

    from tests.integration.test_upstage_preliminary_transport import configured as transport_setup
    from tests.integration.test_upstage_tagger_preflight import configured as approvals

    adapter, _, _, _, _, claim, graph = transport_setup(tmp_path, monkeypatch)
    policy = solar_pro3_capacity_policy() if policy is None else policy
    preliminary = replace(
        adapter._settings,
        model_id=model,
        binding=replace(adapter._settings.binding, binding_id=str(UUID(int=500))),
    )
    tagging = replace(
        preliminary,
        binding=replace(preliminary.binding, binding_id=str(UUID(int=501))),
        model_profile="upstage-compact-ids-frozen-unicode-v1",
        system_prompt="Tag only.",
        schema_json=Path("contracts/jsonschema/llm_tags.schema.json").read_text(),
    )
    relation = replace(
        preliminary,
        binding=replace(preliminary.binding, binding_id=str(UUID(int=502))),
        model_profile="upstage-relation-source-quotes-v1",
        system_prompt=RELATION_PROMPT,
        schema_json=Path("contracts/jsonschema/source_relations.schema.json").read_text(),
    )
    authorization = approvals()
    window = dict(approved_at="2026-09-29T09:00:00Z", expires_at="2026-10-01T00:00:00Z")
    consent = dict(authorization["consent"], **window, allowed_source_sha256=[graph.source_sha256])
    roles = {"preliminary": preliminary, "tagging": tagging, "relation": relation}
    runtimes = {
        prefix: dict(
            authorization["binding"],
            **window,
            runtime_binding_id=selected.binding.binding_id,
            model_id=selected.model_id,
            tagging_settings_sha256=canonical_hash(asdict(selected)),
            input_reservation_policy_sha256=canonical_hash(policy),
        )
        for prefix, selected in roles.items()
    }
    rights = {"rights_profile_id": "report-test", "tenant_id": graph.tenant_id}
    snapshot = dict(
        tenant_id=graph.tenant_id,
        run_id=str(UUID(int=700)),
        tagging_mode="upstage_local",
        document=dict(
            version_id=graph.document_version_id,
            sha256=graph.source_sha256,
            metadata=dict(rights_profile_id="report-test"),
        ),
        consent=consent,
        rights=rights,
        input_reservation_policy=policy,
        input_reservation_policy_hash=canonical_hash(policy),
    )
    for prefix, selected in roles.items():
        snapshot.update(
            {
                prefix + "_settings": asdict(selected),
                prefix + "_settings_hash": canonical_hash(asdict(selected)),
                prefix + "_runtime": runtimes[prefix],
                prefix + "_runtime_artifact_hash": artifact_sha256(runtimes[prefix]),
            }
        )
    snapshot["input_hash"] = canonical_hash(snapshot)
    profiles = {("runtime", r["runtime_binding_id"]): r for r in runtimes.values()} | {
        ("consent", consent["consent_profile_id"]): consent,
        ("rights", "report-test"): rights,
    }
    registry = SimpleNamespace(
        resolve_profile=lambda auth, kind, identifier: profiles[(kind, identifier)]
    )
    monkeypatch.setattr("proofops_worker.live_tagging.Registry.sqlite", lambda path: registry)
    return snapshot, policy, claim, graph


def _runtime(tmp_path, snapshot, policy, graph, probe):
    budget = LocalSQLiteUsageStore(tmp_path / "usage.sqlite3")
    bound = policy["reservation_input_tokens"]
    budget.create_budget(
        graph.tenant_id,
        snapshot["run_id"],
        graph.document_version_id,
        BudgetLimits(bound * 6, 6144, (RoleLimit("tagger", 6, bound, 1024, bound + 1024),)),
    )
    jobs = SimpleNamespace(
        can_call=lambda *a, **k: True, heartbeat=lambda *a, **k: None, list_usage=lambda *a: []
    )
    runner = SimpleNamespace(
        store=SimpleNamespace(path=tmp_path / "app.sqlite3", usage=budget, jobs=jobs),
        clock=lambda: PRO3_NOW.timestamp(),
    )
    lease = SimpleNamespace(message=SimpleNamespace(job_id=str(UUID(int=701))))
    usage: dict = {}
    runtime = LiveTaggingRuntime(
        runner,
        snapshot,
        graph,
        lease,
        usage,
        probe=probe,
        ledger=tmp_path / "budget.sqlite3",
        receipts=tmp_path / "live",
    )
    return runtime, usage


def _fake_provider(probe, claim, calls, *, served_model):
    def post(body):
        calls.append(body)
        return dict(
            id=f"provider-{len(calls)}",
            model=served_model,
            usage=dict(prompt_tokens=20, completion_tokens=10),
            choices=[
                dict(
                    finish_reason="stop",
                    message=dict(
                        content=json.dumps(
                            dict(
                                claim_id=claim.claim_id,
                                track="management",
                                safe_harbor_category=None,
                                track_confidence=0.8,
                                dimensions=dict(entity=None, metric=None, reporting_period=None),
                            )
                        )
                    ),
                )
            ],
        )

    probe._post = post


def test_snapshot_selects_the_pro3_probe_for_all_three_roles(tmp_path, monkeypatch):
    snapshot, policy, claim, graph = _pro3_snapshot(tmp_path, monkeypatch)
    built = []

    def make(model):
        built.append(model)
        return UpstageProbe("test-not-a-key", tmp_path / "budget.sqlite3", model=model)

    # The worker keeps its eager Pro 4 probe for legacy runs; Pro 3 is built on demand.
    probes = {MODEL_PRO4: make(MODEL_PRO4)}
    built.clear()
    probe = select_tagging_probe(snapshot, probes, make)
    assert built == [MODEL] and probe.model == MODEL
    assert select_tagging_probe(snapshot, probes, make) is probe and built == [MODEL]
    runtime, usage = _runtime(tmp_path, snapshot, policy, graph, probe)
    for transport in (
        runtime.preliminary_transport,
        runtime.element_transport,
        runtime.relation_transport,
    ):
        assert transport._probe is probe
    assert runtime._capacity(MODEL) == 2**18

    calls: list = []
    _fake_provider(probe, claim, calls, served_model="solar-pro3-260323")
    result = runtime.preliminary(claim, graph)
    assert result is not None and result[0].track == "management"
    assert len(calls) == usage["settled_calls"] == 3
    assert {body["model"] for body in calls} == {MODEL}
    with sqlite3.connect(tmp_path / "budget.sqlite3") as db:
        settled = [json.loads(row[0]) for row in db.execute("SELECT receipt FROM probe_calls")]
    # Same shared ledger; each call settled at the actual Pro 3 price snapshot.
    assert len(settled) == 3
    assert {row["model"] for row in settled} == {MODEL}
    assert all(row["price_snapshot"] == PRICE.to_dict() for row in settled)
    assert PRICE.to_dict() != PRICE_PRO4.to_dict()


def test_pro4_probe_cannot_serve_a_pro3_run(tmp_path, monkeypatch):
    snapshot, policy, _, graph = _pro3_snapshot(tmp_path, monkeypatch)
    pro4 = UpstageProbe("test-not-a-key", tmp_path / "budget.sqlite3", model=MODEL_PRO4)
    with pytest.raises(ValueError):
        _runtime(tmp_path, snapshot, policy, graph, pro4)
    with pytest.raises(ValueError, match="LIVE_TAGGING_MODEL_MISMATCH"):
        select_tagging_probe(snapshot, {MODEL: pro4}, lambda model: pytest.fail("built"))


def test_provider_answering_with_pro4_is_rejected_for_a_pro3_run(tmp_path, monkeypatch):
    snapshot, policy, claim, graph = _pro3_snapshot(tmp_path, monkeypatch)
    probe = UpstageProbe("test-not-a-key", tmp_path / "budget.sqlite3", model=MODEL)
    runtime, _ = _runtime(tmp_path, snapshot, policy, graph, probe)
    calls: list = []
    _fake_provider(probe, claim, calls, served_model="solar-pro4-260806")
    try:
        result = runtime.preliminary(claim, graph)
    except Exception:  # noqa: BLE001 - a stop is acceptable; a Pro 4 result is not
        result = None
    assert result is None
    records = runtime.preliminary_records[claim.claim_id]
    assert len(calls) == 1 and records[0]["status"] == "needs_review"
    assert records[0]["stable_reason"]["error_code"] == (
        "UPSTAGE_RECEIPT_INVALID_RESERVATION_RETAINED"
    )


def test_pro3_run_with_pro4_policy_stops_before_spend(tmp_path, monkeypatch):
    snapshot, policy, _, graph = _pro3_snapshot(
        tmp_path, monkeypatch, policy=solar_pro4_capacity_policy(refreshed=True)
    )
    probe = UpstageProbe("test-not-a-key", tmp_path / "budget.sqlite3", model=MODEL)
    with pytest.raises(ValueError):
        _runtime(tmp_path, snapshot, policy, graph, probe)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda s: s["relation_settings"].update(model_id=MODEL_PRO4), "MISMATCH"),
        (
            lambda s: [s[k].update(model_id="solar-pro2") for k in s if k.endswith("_settings")],
            "UNSUPPORTED",
        ),
        (lambda s: [s.pop(k) for k in list(s) if k.endswith("_settings")], "MISMATCH"),
    ],
)
def test_mixed_unknown_or_missing_model_stops_before_any_probe(tmp_path, monkeypatch, mutate, code):
    snapshot, _, _, _ = _pro3_snapshot(tmp_path, monkeypatch)
    mutate(snapshot)
    with pytest.raises(ValueError, match=code):
        select_tagging_probe(snapshot, {}, lambda model: pytest.fail("built"))


def test_legacy_pro4_snapshot_still_selects_pro4(tmp_path, monkeypatch):
    snapshot, _, _, _ = _pro3_snapshot(tmp_path, monkeypatch, model=MODEL_PRO4)
    assert snapshot_tagging_model(snapshot) == MODEL_PRO4
    legacy = {k: v for k, v in snapshot.items() if not k.startswith("relation_")}
    assert snapshot_tagging_model(legacy) == MODEL_PRO4
