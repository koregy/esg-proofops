"""P4 assurance-link-v1: a statement-pinned covered match is the only way P4 is published.

Real ReviewService + LocalSQLiteReviewStore + rules engine over the synthetic review
workspace. The graph's second table row is a synthetic assurance opinion
("회사A | LRQA | ISAE 3000 | 함유비율 | 공장A | limited | 2025 | ..."); the claim row's
source-bound dimensions are 회사A / 함유비율 / 공장A / 2025. No model or network call.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from proofops.adapters.local.assurance_head import (
    AssuranceHeadRejected,
    AssuranceProofVerifier,
    assurance_proofs,
    check_assurance_tag,
)
from proofops.adapters.local.assurance_store import _statement_to_payload
from proofops.application.assurance import extract_assurance
from proofops.application.ports.models import ModelBinding
from proofops.application.reviews import ReviewRejected
from proofops.application.tagging.assurance_link import (
    AssuranceLinkRejected,
    build_request,
    derive_assurance_fact,
    p4_element,
)
from proofops.domain.provenance import canonical_hash

import tests.acceptance.test_tagging as tagging_fixture
from tests.acceptance.test_binding import corpus, span
from tests.acceptance.test_citations import RUN, TENANT
from tests.acceptance.test_reviews import workspace
from tests.integration.test_ai_delegated_review import _actor

STATEMENT_ID = "77777777-7777-4777-8777-777777777777"
BINDING = ModelBinding("synthetic-assurance", "assurance", True)
OPINION = dict(product="LRQA", material="ISAE 3000", scope="limited")
FIELDS = dict(
    provider="LRQA",
    standard_raw="ISAE 3000",
    level="limited",
    reporting_period="2025",
    entities="회사A",
    facilities="공장A",
    covered_metrics="함유비율",
)
KW = dict(delegated_reviewer="assurance-test", delegation_authority="explicit test delegation")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(tagging_fixture, "corpus", lambda: corpus(**OPINION))
    return workspace(tmp_path)


def _opinion_block(graph):
    return next(b for b in graph.blocks if b.sources[0].source_native_id == "1")


def statement_for(inputs, **field_changes):
    graph = inputs.original
    block = _opinion_block(graph)
    ref = block.source_ref()
    fields = {**FIELDS, **field_changes}
    tagged = {name: (span(ref, text),) for name, text in fields.items() if text is not None}
    return extract_assurance(
        graph,
        (ref,),
        BINDING,
        tagged_fields=tagged,
        tenant_id=TENANT,
        statement_id=STATEMENT_ID,
        model_sha256="c" * 64,
        prompt_sha256="d" * 64,
        replicate_id=1,
    )


def _publish_statement(service, statement):
    jobs = service.store.jobs
    with jobs._transaction() as db:
        jobs._put(
            db,
            TENANT,
            RUN,
            "assurance_statement",
            "STATEMENT",
            _statement_to_payload(statement),
            immutable=True,
        )


def install(service, statement, *, digest=None, verifier=True):
    """Trusted loaders as composition wires them: review loader + consumer verifier."""
    service.load_assurance_statement = lambda tenant, run: statement
    jobs = service.store.jobs
    inputs = service.load_inputs(TENANT, RUN, None)
    jobs.assurance_verifier = (
        AssuranceProofVerifier(
            jobs,
            load_inputs=lambda tenant, run, claim: inputs,
            load_statement=lambda tenant, run: statement,
            source_digest=lambda tenant, version: digest or inputs.original.source_sha256,
        )
        if verifier
        else None
    )


def _resolve(service, review, body, statement, request=None, *, key=None, if_match='"1"', **kw):
    install(service, statement)
    return service.resolve_ai_delegated_review(
        _actor(),
        review["review_id"],
        json.loads(json.dumps(body)),
        if_match,
        key or f"assurance-{uuid4().hex}",
        **KW,
        **kw,
        **({"assurance_review": request} if request is not None else {}),
    )


def _body_with_p4(body, fact):
    elements = [e for e in body["elements"] if e["element_id"] != "P4"] + [p4_element(fact)]
    return dict(body, elements=json.loads(json.dumps(elements)))


def test_covered_statement_publishes_p4_and_reload_guard_accepts(ws):
    _, service, inputs, review, body, _, _ = ws
    snapshot_before = canonical_hash(inputs.snapshot())
    before = service.store.history(TENANT, RUN, review["claim_id"])
    statement = statement_for(inputs)
    _publish_statement(service, statement)
    request = build_request(inputs, statement)
    fact, receipt = derive_assurance_fact(inputs, request, statement)
    assert fact is not None and receipt["status"] == "covered"
    assert (fact.name, fact.state, fact.normalized_value, fact.source_scope) == (
        "assurance_covered",
        "present",
        "covered",
        "global_bound",
    )

    result = _resolve(service, review, _body_with_p4(body, fact), statement, request)
    after = service.store.history(TENANT, RUN, review["claim_id"])
    assert after["tags"][:-1] == before["tags"]  # prior revisions untouched
    head = after["tags"][-1]
    assert head["origin"] == "ai_delegated"  # never human provenance
    assert head["assurance_review"]["receipt_sha256"] == receipt["receipt_sha256"]
    facts = {f["name"]: f for f in head["confirmed_tags"]["facts"]}
    assert facts["assurance_covered"]["state"] == "present"
    assert facts["assurance_covered"]["normalized_value"] == "covered"
    grade_range = result["decision"]["grade_range"]
    assert grade_range is None or "P4" not in grade_range["open_elements"]
    assert "P4" not in result["decision"]["missing_elements"]
    assert canonical_hash(inputs.snapshot()) == snapshot_before  # old snapshot unchanged

    jobs = service.store.jobs
    with assurance_proofs(jobs, TENANT, RUN), jobs._transaction() as db:
        check_assurance_tag(db, jobs, TENANT, RUN, head)
        tampered = json.loads(json.dumps(head))
        tampered["assurance_review"]["match"]["status"] = "covered "
        with pytest.raises(AssuranceHeadRejected):
            check_assurance_tag(db, jobs, TENANT, RUN, tampered)
        no_proof = {k: v for k, v in head.items() if k != "assurance_review"}
        with pytest.raises(AssuranceHeadRejected):
            check_assurance_tag(db, jobs, TENANT, RUN, no_proof)
        # Heads without P4 present keep working with no new legacy requirement.
        check_assurance_tag(db, jobs, TENANT, RUN, before["tags"][0])


def test_carried_rereview_recomputes_receipt_and_rejects_mutated_statement(ws):
    _, service, inputs, review, body, _, _ = ws
    statement = statement_for(inputs)
    _publish_statement(service, statement)
    request = build_request(inputs, statement)
    fact, _ = derive_assurance_fact(inputs, request, statement)
    p4_body = _body_with_p4(body, fact)
    result = _resolve(service, review, p4_body, statement, request)
    fresh = service.store.get(TENANT, review["review_id"])
    reopen_body = dict(p4_body, base_tag_revision=result["new_tag_revision"])
    if_match = f'"{fresh["revision"]}"'

    # The statement changed after publication: the head itself no longer re-derives.
    mutated = statement_for(inputs, provider="ISAE 3000")
    with pytest.raises(ReviewRejected) as error:
        _resolve(service, review, reopen_body, mutated, if_match=if_match, reopen=True)
    assert error.value.code.startswith("ASSURANCE_REDERIVATION_FAILED")

    install(service, statement)
    service.load_assurance_statement = None
    with pytest.raises(ReviewRejected) as error:
        service.resolve_ai_delegated_review(
            _actor(), review["review_id"], reopen_body, if_match, str(uuid4()), reopen=True, **KW
        )
    assert error.value.code == "ASSURANCE_LOADER_UNAVAILABLE"

    carried = _resolve(service, review, reopen_body, statement, if_match=if_match, reopen=True)
    head = service.store.history(TENANT, RUN, review["claim_id"])["tags"][-1]
    assert carried["new_tag_revision"] == head["tag_revision"] == 3
    assert head["assurance_review"]["carried_from"]["origin"] == "ai_delegated"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"covered_metrics": "국내"}, "scope_mismatch_or_no_assurance"),
        ({"excluded_facilities": "공장A"}, "explicit_exclusion"),
        ({"level": None}, "insufficient_scope_information"),
    ],
)
def test_mismatched_excluded_or_partial_statement_is_refused_and_writes_nothing(
    ws, changes, reason
):
    _, service, inputs, review, body, _, _ = ws
    statement = statement_for(inputs, **changes)
    _publish_statement(service, statement)
    request = build_request(inputs, statement)
    fact, receipt = derive_assurance_fact(inputs, request, statement)
    assert fact is None and receipt["status"] == "unknown" and reason in receipt["reasons"]
    before = service.store.history(TENANT, RUN, review["claim_id"])
    with pytest.raises(ReviewRejected) as error:
        _resolve(service, review, body, statement, request)
    assert error.value.code == "ASSURANCE_NOT_COVERED"
    assert service.store.history(TENANT, RUN, review["claim_id"]) == before


def test_llm_or_typed_p4_without_derived_fact_is_refused(ws):
    _, service, inputs, review, body, _, _ = ws
    statement = statement_for(inputs)
    fact, _ = derive_assurance_fact(inputs, build_request(inputs, statement), statement)
    with pytest.raises(ReviewRejected) as error:
        _resolve(service, review, _body_with_p4(body, fact), statement)
    assert error.value.code == "DETERMINISTIC_CHECK_REQUIRED"


def test_forged_cross_tenant_stale_and_synthetic_statements_are_rejected(ws):
    _, service, inputs, review, body, _, _ = ws
    statement = statement_for(inputs)
    good = build_request(inputs, statement)

    forged_ref = replace(statement.source_refs[0], quote=statement.source_refs[0].quote + "!")
    forged = replace(statement, source_refs=(forged_ref,))
    cross = replace(statement, tenant_id="99999999-9999-4999-8999-999999999999")
    cases = [
        (forged, build_request(inputs, forged), "ASSURANCE_STATEMENT_SOURCE_REJECTED"),
        (cross, build_request(inputs, cross), "ASSURANCE_STATEMENT_IDENTITY_MISMATCH"),
        (statement, dict(good, input_snapshot_sha256="0" * 64), "ASSURANCE_STALE_INPUTS"),
        (
            statement,
            dict(good, statement_semantic_hash="0" * 64),
            "ASSURANCE_STATEMENT_PIN_MISMATCH",
        ),
        (statement, dict(good, policy_hash="0" * 64), "ASSURANCE_POLICY_MISMATCH"),
        (statement, dict(good, claim_source_refs=[]), "WHOLE_CLAIM_REQUIRED"),
        (None, good, "ASSURANCE_STATEMENT_UNAVAILABLE"),
    ]
    for candidate, request, code in cases:
        with pytest.raises(AssuranceLinkRejected) as error:
            derive_assurance_fact(inputs, request, candidate)
        assert str(error.value) == code

    live = replace(inputs, rule_context=replace(inputs.rule_context, local_synthetic=False))
    with pytest.raises(AssuranceLinkRejected) as error:
        derive_assurance_fact(live, build_request(live, statement), statement)
    assert str(error.value) == "ASSURANCE_SYNTHETIC_STATEMENT"

    before = service.store.history(TENANT, RUN, review["claim_id"])
    with pytest.raises(ReviewRejected) as error:
        _resolve(service, review, body, forged, build_request(inputs, forged))
    assert error.value.code == "ASSURANCE_STATEMENT_SOURCE_REJECTED"
    assert service.store.history(TENANT, RUN, review["claim_id"]) == before


def test_retry_identity_includes_assurance_request_and_replays_idempotently(ws):
    _, service, inputs, review, body, _, _ = ws
    statement = statement_for(inputs)
    _publish_statement(service, statement)
    request = build_request(inputs, statement)
    fact, _ = derive_assurance_fact(inputs, request, statement)
    p4_body = _body_with_p4(body, fact)
    key = f"assurance-{uuid4().hex}"
    first = _resolve(service, review, p4_body, statement, request, key=key)
    assert _resolve(service, review, p4_body, statement, request, key=key) == first
    with pytest.raises(ReviewRejected) as error:
        _resolve(
            service, review, p4_body, statement, dict(request, statement_id=str(uuid4())), key=key
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(service.store.history(TENANT, RUN, review["claim_id"])["tags"]) == 2


def test_statement_publication_mismatch_fails_closed_for_head_consumers(ws):
    _, service, inputs, review, body, _, _ = ws
    statement = statement_for(inputs)
    request = build_request(inputs, statement)
    fact, _ = derive_assurance_fact(inputs, request, statement)
    _resolve(service, review, _body_with_p4(body, fact), statement, request)
    head = service.store.history(TENANT, RUN, review["claim_id"])["tags"][-1]
    jobs = service.store.jobs
    with jobs._transaction() as db:
        # Proof exists but the run has no published statement to pin against.
        with pytest.raises(AssuranceHeadRejected):
            check_assurance_tag(db, jobs, TENANT, RUN, head)
    other = statement_for(inputs, provider="ISAE 3000")
    _publish_statement(service, other)
    with jobs._transaction() as db:
        with pytest.raises(AssuranceHeadRejected):
            check_assurance_tag(db, jobs, TENANT, RUN, head)
    assert asdict(statement) != asdict(other)


def test_derived_p4_fact_closes_open_range_to_e3(ws):
    """The engine alone grades: the derived fact alone closes the open P4 range to E3."""
    from proofops.application.evidence.span_citations import verify_source_ref
    from proofops.domain.rules.engine import ConfirmedFact, ConfirmedTags, evaluate

    _, service, inputs, review, body, _, _ = ws
    statement = statement_for(inputs)
    fact, _ = derive_assurance_fact(inputs, build_request(inputs, statement), statement)
    claim_ref = verify_source_ref(
        inputs.context.claim.source_refs[0], inputs.original, tenant_id=TENANT
    )
    local = tuple(
        ConfirmedFact(name, "present", (claim_ref,), TENANT, True, True)
        for name in (
            "quantitative_or_qualified_ordinal",
            "unit_or_qualified_ordinal",
            "comparison_baseline",
            "calculation_boundary",
            "method",
        )
    )
    run = inputs.tag_runs[0]
    tags = ConfirmedTags(
        TENANT,
        inputs.original.document_version_id,
        inputs.context.claim.claim_id,
        "performance",
        local,
        2,
        inputs.packet.packet_sha256,
        run.model_sha256,
        run.prompt_sha256,
        inputs.consensus.replicate_hashes,
        inputs.rulepack.ontology_version,
        None,
        None,
        False,
    )
    without = evaluate(tags, inputs.rule_context, inputs.rulepack)
    with_p4 = evaluate(replace(tags, facts=local + (fact,)), inputs.rule_context, inputs.rulepack)
    # Unknown P4 stays open (never absent): a range, not a grade.
    assert without.evidence_grade is None
    assert without.decision_status == "blocked_evidence"
    assert (without.grade_floor, without.grade_ceiling) == ("E2", "E3")
    assert without.grade_open_elements == ("P4",)
    assert (with_p4.decision_status, with_p4.evidence_grade) == ("decided", "E3")


def _cli(monkeypatch, service, inputs, statement):
    import scripts.link_assurance_p4 as cli

    install(service, statement)
    monkeypatch.setattr(
        cli,
        "compose",
        lambda path: dict(
            service=service,
            load_inputs=lambda tenant, run, claim: inputs,
            load_statement=lambda tenant, run: statement,
        ),
    )
    return cli.main


def test_cli_dry_run_apply_and_reload_verification(ws, monkeypatch, capsys, tmp_path):
    _, service, inputs, review, _, _, _ = ws
    statement = statement_for(inputs)
    _publish_statement(service, statement)
    main = _cli(monkeypatch, service, inputs, statement)
    argv = [
        "--state-db",
        str(tmp_path / "state.sqlite"),
        "--tenant-id",
        TENANT,
        "--review-id",
        review["review_id"],
        "--delegated-reviewer",
        "cli-assurance-test",
    ]
    before = service.store.history(TENANT, RUN, review["claim_id"])
    assert main(argv) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["receipt"]["status"] == "covered"
    assert service.store.history(TENANT, RUN, review["claim_id"]) == before

    assert main(argv + ["--apply", "--idempotency-key", "cli-assurance-apply-0001"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["verification"]["status"] == "verified"
    assert applied["verification"]["fact"]["normalized_value"] == "covered"
    assert applied["result"]["decision"]["review_status"] == "ai_delegated_confirmed"
    after = service.store.history(TENANT, RUN, review["claim_id"])
    assert after["tags"][0] == before["tags"][0]
    assert main(argv + ["--verify"]) == 0
    assert json.loads(capsys.readouterr().out)["verification"]["tag_revision"] == 2

    # A stale second apply (no explicit re-review) is refused; nothing is written.
    assert main(argv + ["--apply", "--idempotency-key", "cli-assurance-apply-0002"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "STALE_REVIEW_REVISION"
    assert service.store.history(TENANT, RUN, review["claim_id"]) == after


def test_cli_refuses_not_covered_statement_without_writing(ws, monkeypatch, capsys, tmp_path):
    _, service, inputs, review, _, _, _ = ws
    statement = statement_for(inputs, excluded_facilities="공장A")
    main = _cli(monkeypatch, service, inputs, statement)
    before = service.store.history(TENANT, RUN, review["claim_id"])
    code = main(
        [
            "--state-db",
            str(tmp_path / "state.sqlite"),
            "--tenant-id",
            TENANT,
            "--review-id",
            review["review_id"],
            "--delegated-reviewer",
            "cli-assurance-test",
            "--apply",
            "--idempotency-key",
            "cli-assurance-refuse-0001",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["error"] == "ASSURANCE_NOT_COVERED" and out["status"] == "unknown"
    assert "explicit_exclusion" in out["receipt"]["reasons"]
    assert service.store.history(TENANT, RUN, review["claim_id"]) == before
