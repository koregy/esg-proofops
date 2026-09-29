"""C3/C4 trigger review CLI against a REAL review-published run (synthetic fixture).

A real accepted review (through the unchanged `ReviewService`) moves the claim
to the goal track with `target_metric` (G2) present on its verified evidence.
The CLI drafts a trigger review whose pins all come from that head, validates a
filled review against the current head and feeds it to build-packet. The
fixture PDF sentence and every financial value are SYNTHETIC; nothing here
claims a real commitment, Kia or otherwise.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.integration.test_linkage_exchange_cli_real_run import (
    _cli_args,
    _load_cli_module,
    _use_fixture_parser,
)
from tests.integration.test_local_parser_runner import TENANT


def _goal_review(tmp_path, monkeypatch) -> dict:
    from proofops.application.authorization import AuthContext

    from tests.integration.test_local_tag_runner import verified_setup

    service, run_id, runner, _now, _stream = verified_setup(tmp_path, monkeypatch)
    assert runner.run_once(tenant_id=TENANT, run_id=run_id) == "needs_review"
    with service.store.jobs._transaction() as db:
        heads = service.store.jobs._all(db, TENANT, run_id, "review_head")
    review_id, claim_id = heads[0]["review_id"], heads[0]["claim_id"]
    claim = runner.claims.get(TENANT, run_id, claim_id)
    refs = [asdict(ref) for ref in claim.source_refs]
    element_ids = [f"G{n}" for n in range(1, 9)]

    def element(element_id):
        present = element_id == "G2"
        return {
            "element_id": element_id,
            "state": "present" if present else "unknown",
            "evidence_refs": refs if present else [],
            "normalized_value": claim.source_refs[0].quote if present else None,
            "credited_from": None,
            "reason_code": None,
        }

    actor = AuthContext(
        "operator-0", TENANT, "reviewer", frozenset({"viewer", "reviewer"}), "session-0"
    )
    resolved = runner.reviews.resolve_review(
        actor,
        review_id,
        {
            "base_tag_revision": 1,
            "track": "goal",
            "reason": "trigger review CLI regression: real accepted goal review only",
            "elements": [element(eid) for eid in element_ids],
        },
        '"1"',
        "trigger-review-cli-0002",
        reopen=False,
    )
    assert resolved["new_tag_revision"] == 2
    company_id = service.store.snapshot(TENANT, run_id)["document"]["company"]["company_id"]
    return dict(
        service=service,
        run_id=run_id,
        runner=runner,
        claim=claim,
        claim_id=claim_id,
        company_id=company_id,
    )


def _head_args(reviewed, **extra):
    return argparse.Namespace(
        tenant_id=TENANT,
        run_id=reviewed["run_id"],
        claim_id=reviewed["claim_id"],
        database_path=reviewed["service"].store.path,
        synthetic=True,
        **extra,
    )


def test_trigger_review_draft_validate_and_build_against_the_accepted_head(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "run").mkdir()
    reviewed = _goal_review(tmp_path / "run", monkeypatch)
    cli = _load_cli_module()
    _use_fixture_parser(monkeypatch, reviewed["runner"])
    quote = reviewed["claim"].source_refs[0].quote

    # Draft: pins come from the head; semantic literals stay null.
    draft_path = tmp_path / "draft.json"
    args = _head_args(reviewed, item="C3", fact_name="target_metric", output=str(draft_path))
    assert cli._cmd_draft_trigger_review(args) == 0
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft["tag_revision"] == 2
    assert draft["company_id"] == reviewed["company_id"]
    assert draft["source_sha256"] == reviewed["claim"].source_sha256
    assert draft["source_bindings"][0]["quote"] == quote
    assert draft["amount_literal"] is None and draft["review_origin"] is None
    with pytest.raises(FileExistsError):  # a draft is never overwritten
        cli._cmd_draft_trigger_review(args)
    # A draft for a fact that is not a listed C3 source is refused.
    bad = _head_args(reviewed, item="C3", fact_name="baseline_value", output=str(tmp_path / "x"))
    assert cli._cmd_draft_trigger_review(bad) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "unsupported_trigger_fact"

    # An unfilled draft never validates.
    assert cli._cmd_validate_trigger_review(_head_args(reviewed, review=str(draft_path))) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "invalid_trigger_review"

    # A filled review: the fixture sentence carries no investment amount, so the
    # bridge refuses on semantics instead of inventing one.
    filled = draft | {
        "source_id": draft["source_bindings"][0]["source_id"],
        "amount_literal": "10조원",
        "investment_label": "투자",
        "currency": "KRW",
        "normalized_amount": "10000000000000",
        "review_id": "synthetic-trigger-review-1",
        "reviewed_by": "synthetic-delegated-reviewer",
        "reviewed_at": "2026-09-29",
        "review_origin": "ai_delegated",
    }
    filled_path = tmp_path / "filled.json"
    filled_path.write_text(json.dumps(filled, ensure_ascii=False), encoding="utf-8")
    assert "10조원" not in quote
    assert cli._cmd_validate_trigger_review(_head_args(reviewed, review=str(filled_path))) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "c3_amount_not_in_source"

    # Stale revision and cross-tenant pins block before any semantics.
    for overrides, reason in (
        ({"tag_revision": 1}, "review_revision_stale"),
        ({"tenant_id": "99999999-9999-4999-8999-999999999999"}, "review_tenant_mismatch"),
        ({"fact_sha256": "0" * 64}, "review_fact_stale"),
    ):
        path = tmp_path / f"{reason}.json"
        path.write_text(json.dumps(filled | overrides, ensure_ascii=False), encoding="utf-8")
        assert cli._cmd_validate_trigger_review(_head_args(reviewed, review=str(path))) == 1
        assert json.loads(capsys.readouterr().out)["reason"] == reason

    # build-packet carries the review into the builder and blocks the same way.
    financial = tmp_path / "financial.json"
    financial.write_text(
        json.dumps(
            {
                "synthetic": True,
                "company_id": reviewed["company_id"],
                "package_id": "synthetic-trigger-review",
                "dart_corp_code": "00000000",
                "financial_document_version": "synthetic-financial-v0",
                "financial_fiscal_year": 2024,
                "consolidation": "consolidated",
                "financial_period_start": "2024-01-01",
                "financial_period_end": "2024-12-31",
                "financial_published_at": None,
                "rcept_no": None,
                "as_of_date": "2026-09-29",
                "financial": {
                    "raw": "synthetic capex",
                    "normalized": "3000000000000",
                    "kind": "currency_amount",
                    "unit": "KRW",
                    "source_id": "fs-synthetic",
                },
                "financial_sources": [
                    {
                        "source_id": "fs-synthetic",
                        "document_id": "synthetic-financial-v0",
                        "artifact_sha256": "2" * 64,
                        "locator": "chars:0:15",
                        "quote": "synthetic capex",
                    }
                ],
                "c3_context": {
                    "currency": "KRW",
                    "target_period_start": None,
                    "target_period_end": None,
                    "capex_period_start": "2024-01-01",
                    "capex_period_end": "2024-12-31",
                    "capex_account_ids": [],
                    "commitment_source_id": None,
                    "funding_plan_source_id": None,
                },
            }
        ),
        encoding="utf-8",
    )
    build = _cli_args(
        cli,
        tenant_id=TENANT,
        run_id=reviewed["run_id"],
        claim_id=reviewed["claim_id"],
        database_path=reviewed["service"].store.path,
        item="C3",
        financial_context=str(financial),
    )
    build.trigger_review = str(filled_path)
    build.trigger_review_receipt_out = None
    assert cli._cmd_build_packet(build) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "c3_amount_not_in_source"

    # Without a review the historical C3 gap is unchanged.
    build.trigger_review = None
    assert cli._cmd_build_packet(build) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "no_verified_trigger"
    assert not Path(tmp_path / "receipt.json").exists()
