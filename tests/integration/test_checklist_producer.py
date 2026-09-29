"""GAP-001 · A checklist-item producer through the REAL review service (no mocks).

Synthetic workspace only: no model, network or paid call. The producer builds the
``safe_harbor_review`` request; ``--apply`` goes through
``ReviewService.resolve_ai_delegated_review`` which replays every reference again.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from proofops.application.tagging.checklist_producer import (
    PRODUCER,
    ChecklistProducerRejected,
    build_checklist_review,
    decision_template,
)
from proofops.domain.provenance import canonical_hash

from tests.acceptance.test_citations import RUN, TENANT
from tests.acceptance.test_reviews import workspace
from tests.integration.test_safe_harbor_review import (
    CATEGORY,
    ITEMS,
    safe_harbor_workspace,
    safe_record,
)

REVIEWER = "coordinator@orca.local"


def _cli(ws, capsys, *argv):
    from scripts.produce_safe_harbor_checklist import main

    parts = dict(service=ws[1], load_inputs=lambda tenant, run, claim: ws[2])
    code = main(["--tenant-id", TENANT, "--review-id", ws[3]["review_id"], *argv], parts=parts)
    return code, json.loads(capsys.readouterr().out)


def _decision(ws, states, *, ref=None):
    template = decision_template(ws[2])
    decision = template["decision"]
    digest = ref or template["selectable_refs"][0]["ref_sha256"]
    for name, state in zip(ITEMS, states, strict=True):
        decision["items"][name] = dict(
            state=state,
            ref_sha256=[] if state == "unknown" else [digest],
            reason=f"Operator decision for {name} from the replayed packet.",
        )
    return decision


def _write(tmp_path, decision):
    path = tmp_path / f"decision-{uuid4()}.json"
    path.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _history(ws):
    return ws[1].store.history(TENANT, RUN, ws[3]["claim_id"])


def test_template_lists_fixed_items_as_unknown_and_writes_nothing(tmp_path, monkeypatch, capsys):
    ws = safe_harbor_workspace(tmp_path, monkeypatch)
    before = _history(ws)
    code, out = _cli(ws, capsys, "template")
    assert code == 0 and out["decision"]["category"] == CATEGORY
    assert out["decision"]["producer"] == PRODUCER
    assert set(out["decision"]["items"]) == set(ITEMS)
    assert {item["state"] for item in out["decision"]["items"].values()} == {"unknown"}
    assert out["selectable_refs"] and all(
        ref["scope"] in ("local_claim", "same_table") for ref in out["selectable_refs"]
    )
    assert _history(ws) == before


def test_dry_run_builds_exact_request_and_preview_without_writing(tmp_path, monkeypatch, capsys):
    ws = safe_harbor_workspace(tmp_path, monkeypatch)
    before = _history(ws)
    code, out = _cli(
        ws,
        capsys,
        "build",
        "--decision-json",
        _write(tmp_path, _decision(ws, ("present", "present"))),
    )
    assert code == 0 and out["dry_run"] is True
    request = out["safe_harbor_review"]
    assert [fact["name"] for fact in request["facts"]] == list(ITEMS)
    assert all(fact["search_coverage_verified"] is False for fact in request["facts"])
    preview = out["producer_receipt"]["preview"]
    assert preview == dict(
        reasonable_basis_documented=True,
        evidence_grade=None,
        label=None,
        legal_effect="not_determined",
        mapping_status="unresolved",
        gap_ids=["GAP-001"],
    )
    assert _history(ws) == before


@pytest.mark.parametrize(
    "states,expected", [(("present", "present"), True), (("unknown", "present"), None)]
)
def test_apply_goes_through_the_real_service_and_keeps_prior_revisions(
    tmp_path, monkeypatch, capsys, states, expected
):
    ws = safe_harbor_workspace(tmp_path, monkeypatch)
    before = _history(ws)
    code, out = _cli(
        ws,
        capsys,
        "build",
        "--decision-json",
        _write(tmp_path, _decision(ws, states)),
        "--apply",
        "--delegated-reviewer",
        REVIEWER,
        "--idempotency-key",
        str(uuid4()),
    )
    assert code == 0, out
    assert out["applied"] is True and out["review_status"] == "ai_delegated_confirmed"
    after = _history(ws)
    assert after["tags"][: len(before["tags"])] == before["tags"]
    head = after["tags"][-1]
    assert head["origin"] == "ai_delegated"
    assert head["safe_harbor_review"]["request"] == out["safe_harbor_review"]
    record = safe_record(ws)
    assert record.reasonable_basis_documented is expected
    assert record.legal_effect == "not_determined" and record.mapping_status == "unresolved"
    # Completeness never becomes a grade, label or legal effect.
    assert out["decision_evidence_grade"] is None and out["decision_label"] is None


def test_stale_head_or_repeat_without_re_review_never_writes(tmp_path, monkeypatch, capsys):
    ws = safe_harbor_workspace(tmp_path, monkeypatch)
    path = _write(tmp_path, _decision(ws, ("present", "present")))
    apply = ("--apply", "--delegated-reviewer", REVIEWER)
    assert (
        _cli(
            ws, capsys, "build", "--decision-json", path, *apply, "--idempotency-key", str(uuid4())
        )[0]
        == 0
    )
    after_first = _history(ws)
    # A resolved review cannot be written again without --re-review, and a stale
    # If-Match is refused by the service even with --re-review.
    code, out = _cli(
        ws, capsys, "build", "--decision-json", path, *apply, "--idempotency-key", str(uuid4())
    )
    assert (code, out["error"], out["status"]) == (1, "STALE_REVIEW_REVISION", 412)
    fresh = _write(tmp_path, _decision(ws, ("present", "present")))
    code, out = _cli(
        ws,
        capsys,
        "build",
        "--decision-json",
        fresh,
        *apply,
        "--idempotency-key",
        str(uuid4()),
        "--if-match",
        '"1"',
        "--re-review",
    )
    assert (code, out["error"], out["status"]) == (1, "STALE_REVIEW_REVISION", 412)
    assert _history(ws) == after_first
    # The same decision with the current If-Match and --re-review appends a revision.
    current = ws[1].store.get(TENANT, ws[3]["review_id"])["revision"]
    unknown = _write(tmp_path, _decision(ws, ("unknown", "present")))
    code, out = _cli(
        ws,
        capsys,
        "build",
        "--decision-json",
        unknown,
        *apply,
        "--idempotency-key",
        str(uuid4()),
        "--if-match",
        f'"{current}"',
        "--re-review",
    )
    assert code == 0, out
    final = _history(ws)
    assert final["tags"][: len(after_first["tags"])] == after_first["tags"]
    assert safe_record(ws).reasonable_basis_documented is None


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d["items"][ITEMS[0]].update(state="absent"), "CHECKLIST_ABSENCE_NOT_ENABLED"),
        (lambda d: d["items"][ITEMS[0]].update(ref_sha256=["0" * 64]), "CHECKLIST_SOURCE_REJECTED"),
        (lambda d: d.update(input_snapshot_sha256="f" * 64), "CHECKLIST_STALE_INPUTS"),
        (lambda d: d.update(claim_id=str(uuid4())), "CHECKLIST_CLAIM_MISMATCH"),
        (lambda d: d.update(category="emissions_estimate"), "SAFE_HARBOR_CATEGORY_MISMATCH"),
        (lambda d: d["items"].pop(ITEMS[1]), "CHECKLIST_ITEMS_MISMATCH"),
        (lambda d: d["items"].update(extra=d["items"][ITEMS[0]]), "CHECKLIST_ITEMS_MISMATCH"),
        (lambda d: d["items"][ITEMS[0]].update(ref_sha256=[]), "CHECKLIST_EVIDENCE_STATE_MISMATCH"),
        (lambda d: d["items"][ITEMS[0]].update(reason=""), "CHECKLIST_DECISION_INVALID"),
        (lambda d: d.update(producer="other"), "CHECKLIST_DECISION_INVALID"),
    ],
    ids=[
        "absent",
        "foreign-ref",
        "stale-snapshot",
        "other-claim",
        "other-category",
        "missing-item",
        "extra-item",
        "present-without-ref",
        "no-reason",
        "producer",
    ],
)
def test_invalid_decisions_are_blocked_and_write_nothing(
    tmp_path, monkeypatch, capsys, mutate, code
):
    ws = safe_harbor_workspace(tmp_path, monkeypatch)
    decision = _decision(ws, ("present", "present"))
    mutate(decision)
    before = _history(ws)
    exit_code, out = _cli(
        ws,
        capsys,
        "build",
        "--decision-json",
        _write(tmp_path, decision),
        "--apply",
        "--delegated-reviewer",
        REVIEWER,
        "--idempotency-key",
        str(uuid4()),
    )
    assert exit_code == 1 and out == {"ok": False, "status": "blocked", "error": code}
    assert _history(ws) == before


def test_tampered_packet_ref_is_rejected_by_hash(tmp_path, monkeypatch):
    ws = safe_harbor_workspace(tmp_path, monkeypatch)
    raw = copy.deepcopy(ws[2].packet.to_dict()["claim_source_refs"][0])
    raw["quote"] = "injected text"
    decision = _decision(ws, ("present", "present"), ref=canonical_hash(raw))
    with pytest.raises(ChecklistProducerRejected, match="CHECKLIST_SOURCE_REJECTED"):
        build_checklist_review(ws[2], decision, source_authority="R00 §12 adoption")


def test_legacy_pack_run_is_blocked_with_exact_reason(tmp_path, capsys):
    ws = workspace(tmp_path)  # checked-in pack: reasonable_basis_boolean_mapping is null
    code, out = _cli(ws, capsys, "template")
    assert code == 1 and out["error"] == "CHECKLIST_POLICY_PACK_REQUIRED"


def test_non_safe_harbor_claim_is_refused(tmp_path, monkeypatch):
    ws = safe_harbor_workspace(tmp_path, monkeypatch)
    packet = dict(ws[2].packet.to_dict(), safe_harbor_category=None)
    inputs = SimpleNamespace(
        rulepack=ws[2].rulepack,
        packet=SimpleNamespace(to_dict=lambda: packet),
        tag_runs=ws[2].tag_runs,
    )
    with pytest.raises(ChecklistProducerRejected, match="NOT_SAFE_HARBOR_CLAIM"):
        decision_template(inputs)
