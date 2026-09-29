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


def _sentence_pdf(sentence: str) -> bytes:
    """A 3-page Helvetica PDF whose page-1 text is one explicitly SYNTHETIC sentence.

    Same construction as `tests.acceptance.test_parsing.pdf`; only the text differs.
    """
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    assert sentence.isascii() and "(" not in sentence and ")" not in sentence
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for number in range(1, 4):
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        text = sentence if number == 1 else f"Page {number} synthetic filler"
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _goal_review(tmp_path, monkeypatch, sentence: str | None = None) -> dict:
    from proofops.application.authorization import AuthContext

    from tests.acceptance import test_parsing
    from tests.integration.test_local_tag_runner import verified_setup

    if sentence is not None:
        # verified_setup imports `pdf` at call time; the synthetic parser reads page 1.
        monkeypatch.setattr(test_parsing, "pdf", lambda **_k: _sentence_pdf(sentence))
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

    def accept(base: int) -> int:
        resolved = runner.reviews.resolve_review(
            actor,
            review_id,
            {
                "base_tag_revision": base,
                "track": "goal",
                "reason": "trigger review CLI regression: real accepted goal review only",
                "elements": [element(eid) for eid in element_ids],
            },
            f'"{base}"',
            f"trigger-review-cli-{base + 1:04d}",
            reopen=base > 1,
        )
        return resolved["new_tag_revision"]

    assert accept(1) == 2
    company_id = service.store.snapshot(TENANT, run_id)["document"]["company"]["company_id"]
    return dict(
        service=service,
        run_id=run_id,
        runner=runner,
        claim=claim,
        claim_id=claim_id,
        company_id=company_id,
        rereview=lambda: accept(2),
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


# --------------------------------------------------------------------------- #
# Positive receipt-write path: an explicitly SYNTHETIC sentence carrying the
# assertion flows through the real parse -> extract -> tag -> accepted review
# path; nothing here is a real company's commitment or revenue share.
# --------------------------------------------------------------------------- #

C3_SENTENCE = (
    "Synthetic Co plans capital expenditure of USD 5 million for energy efficiency by 2030"
)
C4_SENTENCE = "Synthetic Co low carbon product revenue share is 22.5% in 2024"
POSITIVE = {
    "C3": dict(
        sentence=C3_SENTENCE,
        literals={
            "amount_literal": "USD 5 million",
            "investment_label": "capital expenditure",
            "currency": "USD",
            "normalized_amount": "5000000",
        },
        sustainability=("currency_amount", "5000000", "USD"),
        trigger="currency_amount",
    ),
    "C4": dict(
        sentence=C4_SENTENCE,
        literals={
            "classification_label": "low carbon product",
            "revenue_label": "revenue",
            "share_literal": "22.5%",
        },
        sustainability=("classification", "low carbon product", None),
        trigger="revenue_share",
    ),
}


def _positive_financial_context(tmp_path, company_id, item) -> str:
    """Explicitly synthetic financial side; no real DART binding is asserted."""
    if item == "C3":
        financial = dict(
            raw="synthetic capex", normalized="3000000", kind="currency_amount", unit="USD"
        )
        contexts = dict(
            c3_context={
                "currency": "USD",
                "target_period_start": None,
                "target_period_end": None,
                "capex_period_start": "2024-01-01",
                "capex_period_end": "2024-12-31",
                "capex_account_ids": [],
                "commitment_source_id": None,
                "funding_plan_source_id": None,
            }
        )
    else:
        financial = dict(
            raw="synthetic classification",
            normalized="low carbon product",
            kind="classification",
            unit=None,
        )
        contexts = dict(
            c4_context={
                "classification_name": "low carbon product",
                "definition_source_ids": ["fs-synthetic"],
                "calculation_source_ids": [],
            }
        )
    path = tmp_path / f"financial-{item}.json"
    path.write_text(
        json.dumps(
            {
                "synthetic": True,
                "company_id": company_id,
                "package_id": f"synthetic-trigger-review-{item}",
                "dart_corp_code": "00000000",
                "financial_document_version": "synthetic-financial-v0",
                "financial_fiscal_year": 2024,
                "consolidation": "consolidated",
                "financial_period_start": "2024-01-01",
                "financial_period_end": "2024-12-31",
                "financial_published_at": None,
                "rcept_no": None,
                "as_of_date": "2026-09-29",
                "financial": financial | {"source_id": "fs-synthetic"},
                "financial_sources": [
                    {
                        "source_id": "fs-synthetic",
                        "document_id": "synthetic-financial-v0",
                        "artifact_sha256": "2" * 64,
                        "locator": "chars:0:15",
                        "quote": "synthetic capex",
                    }
                ],
                **contexts,
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _canonical_review_body(data: dict) -> dict:
    """Review body exactly as the receipt hashes it (strict loader round trip)."""
    from proofops.application.linkage_trigger_review import (
        trigger_review_from_dict,
        trigger_review_to_dict,
    )

    return trigger_review_to_dict(trigger_review_from_dict(data))


@pytest.mark.parametrize("item", ["C3", "C4"])
def test_accepted_head_builds_schema_valid_packet_and_separate_pinned_receipt(
    item, tmp_path, monkeypatch, capsys
):
    from proofops.domain.reconciliation.common import validate_packet
    from proofops.domain.reconciliation.engine import canonical_sha256

    case = POSITIVE[item]
    (tmp_path / "run").mkdir()
    reviewed = _goal_review(tmp_path / "run", monkeypatch, case["sentence"])
    cli = _load_cli_module()
    _use_fixture_parser(monkeypatch, reviewed["runner"])
    claim = reviewed["claim"]
    assert claim.source_refs[0].quote == case["sentence"]  # the synthetic assertion itself

    draft_path = tmp_path / "draft.json"
    args = _head_args(reviewed, item=item, fact_name="target_metric", output=str(draft_path))
    assert cli._cmd_draft_trigger_review(args) == 0
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    filled = (
        draft
        | case["literals"]
        | {
            "source_id": draft["source_bindings"][0]["source_id"],
            "review_id": f"synthetic-trigger-review-{item}",
            "reviewed_by": "synthetic-delegated-reviewer",
            "reviewed_at": "2026-09-29",
            "review_origin": "ai_delegated",
        }
    )
    filled_path = tmp_path / "filled.json"
    filled_path.write_text(json.dumps(filled, ensure_ascii=False), encoding="utf-8")

    assert cli._cmd_validate_trigger_review(_head_args(reviewed, review=str(filled_path))) == 0
    checked = json.loads(capsys.readouterr().out)
    kind, normalized, unit = case["sustainability"]
    assert (checked["trigger_element"], checked["normalized"], checked["unit"]) == (
        case["trigger"],
        normalized,
        unit,
    )

    build = _cli_args(
        cli,
        tenant_id=TENANT,
        run_id=reviewed["run_id"],
        claim_id=reviewed["claim_id"],
        database_path=reviewed["service"].store.path,
        item=item,
        financial_context=_positive_financial_context(tmp_path, reviewed["company_id"], item),
    )
    receipt_path = tmp_path / "receipt.json"
    build.trigger_review = str(filled_path)
    build.trigger_review_receipt_out = str(receipt_path)
    assert cli._cmd_build_packet(build) == 0
    packet = json.loads(capsys.readouterr().out)

    # Schema-valid strict1.1 packet (B's validator and the contract JSON schema).
    validate_packet(packet)
    assert cli._validate_packet_shape(packet, contract_dir=cli.CONTRACT_DIR_DEFAULT) == []
    assert packet["synthetic"] is True and packet["item"] == item
    assert packet["identity"]["claim_id"] == reviewed["claim_id"]
    assert packet["identity"]["company_id"] == reviewed["company_id"]
    assert packet["sustainability"]["kind"] == kind
    assert packet["sustainability"]["normalized"] == normalized
    assert packet["sustainability"]["unit"] == unit
    assert packet["sustainability"]["raw"] == case["sentence"]
    assert case["trigger"] in packet["claim"]["trigger_elements"]
    assert packet["comparability"] == "unknown" and packet["search"]["state"] == "not_run"
    assert "review" not in packet and "receipt_schema_version" not in packet

    # Separate receipt, pinned to this exact packet, review and accepted head.
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt["receipt_schema_version"] == "linkage-trigger-review-1"
    assert receipt["item"] == item and receipt["synthetic"] is True
    assert receipt["packet_sha256"] == canonical_sha256(packet)
    assert receipt["review"] == filled
    assert receipt["review_sha256"] == canonical_sha256(_canonical_review_body(filled))
    assert receipt["review"]["tag_revision"] == 2
    assert receipt["review"]["fact_sha256"] == draft["fact_sha256"]
    assert receipt["review"]["source_sha256"] == claim.source_sha256
    assert receipt["derived"] == {
        "trigger_element": case["trigger"],
        "normalized": normalized,
        "unit": unit,
        "fact_sha256": draft["fact_sha256"],
    }
    assert receipt["review_kind"] == "ai_delegated_trigger_review_not_independent_gold"

    # Receipt collision fails closed and leaves the first receipt byte-identical.
    assert cli._cmd_build_packet(build) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "invalid_trigger_review_receipt_out"
    assert receipt_path.read_bytes() == receipt_bytes

    # A later accepted review supersedes revision 2: the old review is stale and
    # no new receipt is written.
    assert reviewed["rereview"]() == 3
    stale_receipt = tmp_path / "stale-receipt.json"
    build.trigger_review_receipt_out = str(stale_receipt)
    assert cli._cmd_build_packet(build) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "review_revision_stale"
    assert not stale_receipt.exists()
    assert cli._cmd_validate_trigger_review(_head_args(reviewed, review=str(filled_path))) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "review_revision_stale"
