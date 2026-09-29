"""Operator inspection and dry planning cannot invoke a paid transport."""

import json

import pytest

from scripts.produce_assurance import main
from tests.integration.test_assurance_run import _committed_graph
from tests.integration.test_local_parser_runner import TENANT
from tests.integration.test_run_lifecycle import client  # noqa: F401


def test_cli_inspects_committed_sources_and_plans_without_credentials(
    tmp_path, monkeypatch, capsys
):
    from proofops.adapters.local import run_store, upstage
    from proofops.adapters.parsing import opendataloader
    from proofops.application import registry, uploads

    service, run_id, runner, graph = _committed_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(registry.Registry, "sqlite", lambda _: service.registry)
    monkeypatch.setattr(uploads, "UploadService", lambda *args: service.uploads)
    monkeypatch.setattr(run_store, "LocalSQLiteRunStore", lambda *args: service.store)
    monkeypatch.setattr(opendataloader, "OpenDataLoaderParser", lambda *args: runner.parser)

    def forbidden(*args, **kwargs):
        raise AssertionError("dry plan attempted to construct a paid probe")

    monkeypatch.setattr(upstage, "UpstageProbe", forbidden)
    block = next(block for block in graph.blocks if block.winner is not None)
    argv = ["--database-path", service.store.path, "--tenant-id", TENANT, "--run-id", run_id]
    assert main([*argv, "--list-page", str(block.page_num)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["status"] == "inspection_only"
    assert any(row["source_id"] == block.source_id for row in inspected["sources"])
    assert main([*argv, "--source-id", block.source_id]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "planned" and planned["model_calls"] == 0
    assert planned["source_ids"] == [block.source_id]
    assert planned["coverage"] == "not_assessed"
    with pytest.raises(ValueError):
        main([*argv, "--source-id", "unknown-source"])


def test_invoke_refuses_missing_ledger_without_creating_pool(tmp_path):
    database = tmp_path / "existing.db"
    database.touch()
    ledger = tmp_path / "missing-budget.db"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--database-path",
                str(database),
                "--tenant-id",
                TENANT,
                "--run-id",
                TENANT,
                "--source-id",
                "source",
                "--invoke",
                "--ledger",
                str(ledger),
            ]
        )
    assert exc.value.code == 2
    assert not ledger.exists()


def test_evaluation_import_retains_same_producer():
    from proofops.adapters.local.assurance_producer import run_assurance_producer

    from evaluation.assurance_run import run_assurance_producer as compatible

    assert compatible is run_assurance_producer
