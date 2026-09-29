"""numeric-link-v1 through the REAL ReviewService, review store and head consumers.

Synthetic parser output: one table (지표/Scope/사업장/연도/산정방식/조직경계/단위/분모/값)
plus a narrative performance claim "... 배출량 40 공시." whose number is compared with
the table cell. No model, network or paid call.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from proofops.adapters.local.assurance_head import (
    AssuranceHeadRejected,
    NumericProofVerifier,
    check_numeric_integrity,
)
from proofops.adapters.local.claim_store import LocalClaimStore
from proofops.application.claims import ClaimScope, discover_atomic_claims
from proofops.application.evidence.citations import verify_source_ref
from proofops.application.ingest.graph_fusion import fuse_candidates
from proofops.application.reviews import ReviewRejected
from proofops.application.tagging.numeric_link import (
    POLICY,
    NumericLinkRejected,
    build_request,
    derive_numeric_fact,
    p6_element,
)
from proofops_agent.extraction import SyntheticClaimExtractor

import tests.acceptance.test_tagging as tagging_fixture
from tests.acceptance.test_binding import DIMENSIONS, span, tags
from tests.acceptance.test_citations import RUN
from tests.acceptance.test_numeric import MANIFEST, TENANT, VERSION, row
from tests.acceptance.test_parsing import candidate
from tests.acceptance.test_reviews import workspace
from tests.acceptance.test_tables import table
from tests.integration.test_ai_delegated_review import _actor
from tests.integration.test_numeric_p6_link import binding_for

HEADER = ["지표", "Scope", "사업장", "연도", "산정방식", "조직경계", "단위", "분모", "값"]
STATEMENT = (
    "회사A 제품A 재활용 플라스틱 함유비율 공장A 서울 Scope 1 2025 국내 "
    "시장기반 연결 배출량 40 tCO2e 공시."
)
KW = dict(delegated_reviewer="numeric-test", delegation_authority="explicit test delegation")


def graph_and_claim(table_value):
    graph = fuse_candidates(
        (
            table([HEADER, row(table_value)]),
            candidate("claim", [("C", "paragraph", STATEMENT, (1, 500, 590, 520), ())]),
        ),
        tenant_id=TENANT,
    )
    # Explicit synthetic source-quality confirmation, as in AT-012.
    graph = replace(graph, blocks=tuple(replace(b, quality="verified") for b in graph.blocks))
    found = discover_atomic_claims(
        graph, ClaimScope(TENANT, VERSION, MANIFEST), extractor=SyntheticClaimExtractor()
    )
    claim = next(c for c in found.claims if "배출량 40" in c.quote)
    refs = tuple(verify_source_ref(r, graph, tenant_id=TENANT) for r in claim.source_refs)
    return graph, replace(claim, source_quality="verified", source_refs=refs)


def numeric_ws(tmp_path, monkeypatch, table_value="40"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    graph, claim = graph_and_claim(table_value)
    monkeypatch.setattr(tagging_fixture, "corpus", lambda: (graph, claim, (claim.source_refs[0],)))
    monkeypatch.setattr(tagging_fixture, "span", lambda ref, text: span(ref, "40"))
    values = DIMENSIONS | {"entity": "서울", "metric": "배출량", "boundary": "연결"}
    monkeypatch.setattr(tagging_fixture, "tags", lambda ref: tags(ref, values))
    return workspace(tmp_path)


def request_for(ws, **changes):
    return build_request(ws[2], binding_for(ws[2], "40", **changes))


def body_with(ws, element):
    body = copy.deepcopy(ws[4])
    body["elements"] = [element if e["element_id"] == "P6" else e for e in body["elements"]]
    return body


def resolve(ws, body, request=None, *, if_match='"1"', reopen=False):
    return ws[1].resolve_ai_delegated_review(
        _actor(),
        ws[3]["review_id"],
        body,
        if_match,
        str(uuid4()),
        numeric_review=request,
        reopen=reopen,
        **KW,
    )


def history(ws):
    return ws[1].store.history(TENANT, RUN, ws[3]["claim_id"])


def head(ws):
    return history(ws)["tags"][-1]


def install_verifier(ws, inputs=None):
    jobs = ws[1].store.jobs
    jobs.numeric_verifier = NumericProofVerifier(
        jobs, load_inputs=lambda tenant, run, claim: inputs or ws[2]
    )
    return LocalClaimStore(SimpleNamespace(jobs=jobs), None, None)


@pytest.mark.parametrize(
    "table_value,state,status",
    [("40", "present", "consistent"), ("55", "conflict", "inconsistent")],
)
def test_derived_p6_is_published_and_reloads_through_the_proof_guard(
    tmp_path, monkeypatch, table_value, state, status
):
    ws = numeric_ws(tmp_path, monkeypatch, table_value)
    before = history(ws)
    request = request_for(ws)
    fact, _ = derive_numeric_fact(ws[2], request)
    result = resolve(ws, body_with(ws, p6_element(fact)), request)
    assert result["decision"]["review_status"] == "ai_delegated_confirmed"
    after = history(ws)
    assert after["tags"][: len(before["tags"])] == before["tags"]
    tag = head(ws)
    p6 = next(e for e in tag["elements"] if e["element_id"] == "P6")
    assert (p6["state"], p6["reason_code"], p6["normalized_value"]) == (state, POLICY, status)
    fact_row = next(f for f in tag["confirmed_tags"]["facts"] if f["name"] == "numerical_check")
    assert (fact_row["state"], fact_row["source_scope"]) == (state, "computed_check")
    assert tag["numeric_review"]["result"]["status"] == status
    assert check_numeric_integrity(tag) is True

    claims = LocalClaimStore(SimpleNamespace(jobs=ws[1].store.jobs), None, None)
    with pytest.raises(AssuranceHeadRejected, match="NUMERIC_VERIFIER_UNAVAILABLE"):
        claims.current_tag(TENANT, RUN, ws[3]["claim_id"])
    claims = install_verifier(ws)
    assert claims.current_tag(TENANT, RUN, ws[3]["claim_id"])["tag"]["numeric_review"]


def test_changed_source_table_fails_the_reader_replay(tmp_path, monkeypatch):
    ws = numeric_ws(tmp_path, monkeypatch)
    request = request_for(ws)
    fact, _ = derive_numeric_fact(ws[2], request)
    resolve(ws, body_with(ws, p6_element(fact)), request)
    # The same claim over a graph whose table cell now says 55 (source changed).
    other_graph, _ = graph_and_claim("55")
    changed = replace(ws[2], original=other_graph)
    claims = install_verifier(ws, changed)
    with pytest.raises(AssuranceHeadRejected, match="NUMERIC_REDERIVATION_FAILED"):
        claims.current_tag(TENANT, RUN, ws[3]["claim_id"])


def test_numeric_reason_code_without_receipt_is_rejected():
    forged = dict(elements=[dict(element_id="P6", reason_code=POLICY)], confirmed_tags={})
    with pytest.raises(AssuranceHeadRejected, match="NUMERIC_PROOF_MISSING"):
        check_numeric_integrity(forged)
    assert check_numeric_integrity(dict(elements=[], confirmed_tags={})) is False
    fact_only = dict(
        elements=[], confirmed_tags={"facts": [{"name": "numerical_check", "state": "present"}]}
    )
    with pytest.raises(AssuranceHeadRejected, match="NUMERIC_PROOF_MISSING"):
        check_numeric_integrity(fact_only)


def test_stored_head_edits_are_rejected(tmp_path, monkeypatch):
    ws = numeric_ws(tmp_path, monkeypatch)
    request = request_for(ws)
    fact, _ = derive_numeric_fact(ws[2], request)
    resolve(ws, body_with(ws, p6_element(fact)), request)
    tag = head(ws)

    def p6(t):
        return next(e for e in t["elements"] if e["element_id"] == "P6")

    def fact_row(t):
        return next(f for f in t["confirmed_tags"]["facts"] if f["name"] == "numerical_check")

    for mutate in (
        lambda t: t["numeric_review"].update(fact_state="conflict"),
        lambda t: p6(t).update(state="conflict"),
        lambda t: p6(t).update(credited_from="forged"),
        lambda t: fact_row(t).update(evidence_refs=[]),
        lambda t: fact_row(t).update(normalized_value="inconsistent"),
        lambda t: t["numeric_review"]["result"].update(status="inconsistent"),
    ):
        edited = copy.deepcopy(tag)
        mutate(edited)
        with pytest.raises(AssuranceHeadRejected, match="NUMERIC_HEAD_REJECTED"):
            check_numeric_integrity(edited)


@pytest.mark.parametrize("state", ["present", "conflict"])
def test_typed_p6_without_a_derived_check_is_refused(tmp_path, monkeypatch, state):
    ws = numeric_ws(tmp_path, monkeypatch)
    before = history(ws)
    p1 = next(e for e in ws[4]["elements"] if e["element_id"] == "P1")
    typed = dict(
        element_id="P6",
        state=state,
        evidence_refs=copy.deepcopy(p1["evidence_refs"]),
        normalized_value=None,
        credited_from=None,
        reason_code=None,
    )
    with pytest.raises(ReviewRejected, match="DETERMINISTIC_CHECK_REQUIRED"):
        resolve(ws, body_with(ws, typed))
    assert history(ws) == before


def test_body_that_disagrees_with_the_derived_result_is_refused(tmp_path, monkeypatch):
    ws = numeric_ws(tmp_path, monkeypatch)
    before = history(ws)
    request = request_for(ws)
    fact, _ = derive_numeric_fact(ws[2], request)
    element = p6_element(fact) | {"state": "conflict", "normalized_value": "inconsistent"}
    with pytest.raises(ReviewRejected, match="NUMERIC_ELEMENT_MISMATCH"):
        resolve(ws, body_with(ws, element), request)
    assert history(ws) == before


@pytest.mark.parametrize(
    "changes", [{"unit": "tCO2"}, {"subject": "부산"}, {"reporting_period": "2024"}]
)
def test_mismatched_dimensions_are_undecided_and_never_written(tmp_path, monkeypatch, changes):
    ws = numeric_ws(tmp_path, monkeypatch)
    before = history(ws)
    request = request_for(ws, **changes)
    with pytest.raises(
        NumericLinkRejected, match="NUMERIC_(DIMENSION_UNGROUNDED|CONTEXT_CONFLICT)"
    ):
        derive_numeric_fact(ws[2], request)
    unknown = next(e for e in ws[4]["elements"] if e["element_id"] == "P6")
    with pytest.raises(ReviewRejected, match="NUMERIC_(DIMENSION_UNGROUNDED|CONTEXT_CONFLICT)"):
        resolve(ws, body_with(ws, unknown), request)
    assert history(ws) == before


def test_stale_snapshot_request_is_refused(tmp_path, monkeypatch):
    ws = numeric_ws(tmp_path, monkeypatch)
    before = history(ws)
    request = request_for(ws) | {"input_snapshot_sha256": "f" * 64}
    unknown = next(e for e in ws[4]["elements"] if e["element_id"] == "P6")
    with pytest.raises(ReviewRejected, match="NUMERIC_STALE_INPUTS"):
        resolve(ws, body_with(ws, unknown), request)
    assert history(ws) == before


def test_p6_never_changes_grade_label_or_range(tmp_path, monkeypatch):
    baseline_ws = numeric_ws(tmp_path / "a", monkeypatch)
    baseline = resolve(baseline_ws, copy.deepcopy(baseline_ws[4]))["decision"]
    for sub, value in (("b", "40"), ("c", "55")):
        ws = numeric_ws(tmp_path / sub, monkeypatch, value)
        request = request_for(ws)
        fact, _ = derive_numeric_fact(ws[2], request)
        linked = resolve(ws, body_with(ws, p6_element(fact)), request)["decision"]
        for key in ("evidence_grade", "label", "grade_range"):
            assert linked.get(key) == baseline.get(key), key


def test_carried_rereview_replays_the_receipt(tmp_path, monkeypatch):
    ws = numeric_ws(tmp_path, monkeypatch)
    request = request_for(ws)
    fact, _ = derive_numeric_fact(ws[2], request)
    body = body_with(ws, p6_element(fact))
    resolve(ws, body, request)
    first = head(ws)
    # The service reads the current head through the same fail-closed proof guard.
    install_verifier(ws)
    current = ws[1].store.get(TENANT, ws[3]["review_id"])["revision"]
    again = dict(body, base_tag_revision=first["tag_revision"])
    resolve(ws, again, if_match=f'"{current}"', reopen=True)
    carried = head(ws)
    assert carried["tag_revision"] == first["tag_revision"] + 1
    assert carried["numeric_review"]["receipt_sha256"] == first["numeric_review"]["receipt_sha256"]
    assert "carried_from" in carried["numeric_review"]
    assert history(ws)["tags"][-2] == first


def test_fresh_and_carried_context_review_cannot_override_numeric_p6(tmp_path, monkeypatch):
    ws = numeric_ws(tmp_path, monkeypatch)
    request = request_for(ws)
    fact, _ = derive_numeric_fact(ws[2], request)
    body = body_with(ws, p6_element(fact))
    before = history(ws)
    with pytest.raises(ReviewRejected, match="NUMERIC_CONFLICTS_CONTEXT_REVIEW"):
        ws[1].resolve_ai_delegated_review(
            _actor(),
            ws[3]["review_id"],
            body,
            '"1"',
            str(uuid4()),
            numeric_review=request,
            context_review={},
            **KW,
        )
    assert history(ws) == before
    resolve(ws, body, request)
    install_verifier(ws)
    prior = head(ws)
    review_revision = ws[1].store.get(TENANT, ws[3]["review_id"])["revision"]
    with pytest.raises(ReviewRejected, match="CONTEXT_CANNOT_OVERRIDE_NUMERIC_RESULT"):
        ws[1].resolve_ai_delegated_review(
            _actor(),
            ws[3]["review_id"],
            dict(body, base_tag_revision=prior["tag_revision"]),
            f'"{review_revision}"',
            str(uuid4()),
            context_review={},
            reopen=True,
            **KW,
        )
    assert head(ws) == prior


def test_cross_tenant_and_changed_idempotency_request_do_not_publish(tmp_path, monkeypatch):
    ws = numeric_ws(tmp_path, monkeypatch)
    request = request_for(ws)
    fact, _ = derive_numeric_fact(ws[2], request)
    body = body_with(ws, p6_element(fact))
    foreign = replace(_actor(), tenant_id="22222222-2222-4222-8222-222222222222")
    before = history(ws)
    with pytest.raises((KeyError, ReviewRejected)):
        ws[1].resolve_ai_delegated_review(
            foreign,
            ws[3]["review_id"],
            body,
            '"1"',
            str(uuid4()),
            numeric_review=request,
            **KW,
        )
    assert history(ws) == before
    key = str(uuid4())
    ws[1].resolve_ai_delegated_review(
        _actor(),
        ws[3]["review_id"],
        body,
        '"1"',
        key,
        numeric_review=request,
        **KW,
    )
    accepted = history(ws)
    changed = copy.deepcopy(request)
    changed["binding"]["unit"] = "tCO2"
    with pytest.raises((ReviewRejected, ValueError)):
        ws[1].resolve_ai_delegated_review(
            _actor(),
            ws[3]["review_id"],
            body,
            '"1"',
            key,
            numeric_review=changed,
            **KW,
        )
    assert history(ws) == accepted


def test_cli_dry_run_then_apply(tmp_path, monkeypatch, capsys):
    from scripts.link_numeric_review import main

    ws = numeric_ws(tmp_path, monkeypatch)
    parts = dict(service=ws[1], load_inputs=lambda tenant, run, claim: ws[2])
    base = ["--tenant-id", TENANT, "--review-id", ws[3]["review_id"]]
    assert main(["observations", *base], parts=parts) == 0
    listed = json.loads(capsys.readouterr().out)
    assert any(o["quality"] == "verified" for o in listed["observations"])
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(binding_for(ws[2], "40")), encoding="utf-8")
    before = history(ws)
    assert main(["link", *base, "--binding-json", str(path)], parts=parts) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["receipt"]["result"]["status"] == "consistent"
    assert history(ws) == before
    apply = ["--apply", "--delegated-reviewer", "numeric-cli", "--idempotency-key", str(uuid4())]
    code = main(["link", *base, "--binding-json", str(path), *apply], parts=parts)
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["applied"] is True and head(ws)["numeric_review"]
    mismatch = tmp_path / "mismatch.json"
    mismatch.write_text(json.dumps(binding_for(ws[2], "40", unit="tCO2")), encoding="utf-8")
    assert main(["link", *base, "--binding-json", str(mismatch)], parts=parts) == 1
    assert "NUMERIC_DIMENSION_UNGROUNDED" in json.loads(capsys.readouterr().out)["error"]


def test_all_three_review_cli_compositions_wire_mixed_head_verifiers(tmp_path):
    from scripts.link_absence_review import compose as absence_compose
    from scripts.link_assurance_p4 import compose as assurance_compose
    from scripts.link_numeric_review import compose as numeric_compose

    for name in ("assurance", "absence", "numeric"):
        (tmp_path / name).mkdir()
    compositions = (
        assurance_compose(tmp_path / "assurance" / "state.sqlite3"),
        absence_compose(tmp_path / "absence" / "state.sqlite3", None),
        numeric_compose(tmp_path / "numeric" / "state.sqlite3"),
    )
    for parts in compositions:
        jobs = parts["service"].store.jobs
        assert jobs.assurance_verifier is not None
        assert jobs.absence_verifier is not None
        assert jobs.numeric_verifier is not None


def test_real_cli_compositions_give_review_service_the_verifiers_loaders(tmp_path):
    """Re-review replays carried receipts in build(): the service must hold the same
    trusted loaders (statement re-extraction, search-coverage replay) as the readers."""
    from scripts.link_absence_review import compose as absence_compose
    from scripts.link_assurance_p4 import compose as assurance_compose
    from scripts.link_numeric_review import compose as numeric_compose

    for name in ("assurance", "absence", "numeric"):
        (tmp_path / name).mkdir()
    compositions = (
        assurance_compose(tmp_path / "assurance" / "state.sqlite3"),
        absence_compose(tmp_path / "absence" / "state.sqlite3", None),
        numeric_compose(tmp_path / "numeric" / "state.sqlite3"),
    )
    for parts in compositions:
        service = parts["service"]
        jobs = service.store.jobs
        loader = service.load_assurance_statement
        assert loader is not None and service.search_coverage is not None
        assert loader.__self__ is jobs.assurance_verifier.load_statement.__self__
        assert type(loader.__self__).__name__ == "LocalAssuranceStore"
        assert service.search_coverage is jobs.absence_verifier.evidence
        assert type(service.search_coverage).__name__ == "LocalSearchCoverageStore"


OPINION = "LRQA ISAE 3000 limited 서울 공장A 배출량 2025"


def mixed_ws(tmp_path, monkeypatch):
    """Numeric fixture plus one explicit synthetic assurance-opinion paragraph."""
    graph = fuse_candidates(
        (
            table([HEADER, row("40")]),
            candidate("claim", [("C", "paragraph", STATEMENT, (1, 500, 590, 520), ())]),
            candidate("opinion", [("O", "paragraph", OPINION, (1, 300, 590, 320), ())]),
        ),
        tenant_id=TENANT,
    )
    graph = replace(graph, blocks=tuple(replace(b, quality="verified") for b in graph.blocks))
    found = discover_atomic_claims(
        graph, ClaimScope(TENANT, VERSION, MANIFEST), extractor=SyntheticClaimExtractor()
    )
    claim = next(c for c in found.claims if "배출량 40" in c.quote)
    refs = tuple(verify_source_ref(r, graph, tenant_id=TENANT) for r in claim.source_refs)
    claim = replace(claim, source_quality="verified", source_refs=refs)
    monkeypatch.setattr(tagging_fixture, "corpus", lambda: (graph, claim, (claim.source_refs[0],)))
    monkeypatch.setattr(tagging_fixture, "span", lambda ref, text: span(ref, "40"))
    values = DIMENSIONS | {"entity": "서울", "metric": "배출량", "boundary": "연결"}
    monkeypatch.setattr(tagging_fixture, "tags", lambda ref: tags(ref, values))
    return workspace(tmp_path)


def opinion_statement(inputs):
    from proofops.application.assurance import extract_assurance
    from proofops.application.ports.models import ModelBinding

    block = next(b for b in inputs.original.blocks if b.raw_text == OPINION)
    ref = block.source_ref()
    fields = dict(
        provider="LRQA",
        standard_raw="ISAE 3000",
        level="limited",
        entities="서울",
        facilities="공장A",
        covered_metrics="배출량",
        reporting_period="2025",
    )
    return extract_assurance(
        inputs.original,
        (ref,),
        ModelBinding("synthetic-assurance", "assurance", True),
        tagged_fields={name: (span(ref, text),) for name, text in fields.items()},
        tenant_id=TENANT,
        statement_id=str(uuid4()),
        model_sha256="c" * 64,
        prompt_sha256="d" * 64,
        replicate_id=1,
    )


def test_mixed_p4_numeric_head_rereview_needs_and_uses_the_statement_loader(tmp_path, monkeypatch):
    from proofops.adapters.local.assurance_head import AssuranceProofVerifier
    from proofops.adapters.local.assurance_store import _statement_to_payload
    from proofops.application.tagging.assurance_link import (
        build_request as build_p4_request,
    )
    from proofops.application.tagging.assurance_link import (
        derive_assurance_fact,
        p4_element,
    )

    ws = mixed_ws(tmp_path, monkeypatch)
    service, inputs, jobs = ws[1], ws[2], ws[1].store.jobs
    statement = opinion_statement(inputs)
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
    p4_request = build_p4_request(inputs, statement)
    p4_fact, _ = derive_assurance_fact(inputs, p4_request, statement)
    assert p4_fact is not None, "synthetic opinion must fully cover the claim"
    numeric_request = request_for(ws)
    numeric_fact, _ = derive_numeric_fact(inputs, numeric_request)
    body = body_with(ws, p6_element(numeric_fact))
    body["elements"] = [
        p4_element(p4_fact) if e["element_id"] == "P4" else e for e in body["elements"]
    ]
    body = json.loads(json.dumps(body))
    service.load_assurance_statement = lambda tenant, run: statement
    service.resolve_ai_delegated_review(
        _actor(),
        ws[3]["review_id"],
        body,
        '"1"',
        str(uuid4()),
        assurance_review=p4_request,
        numeric_review=numeric_request,
        **KW,
    )
    mixed = head(ws)
    assert mixed["assurance_review"] and mixed["numeric_review"]

    # Readers of the mixed head need both verifiers (as every CLI compose wires them).
    install_verifier(ws)
    jobs.assurance_verifier = AssuranceProofVerifier(
        jobs,
        load_inputs=lambda tenant, run, claim: inputs,
        load_statement=lambda tenant, run: statement,
        source_digest=lambda tenant, version: inputs.original.source_sha256,
    )
    current = service.store.get(TENANT, ws[3]["review_id"])["revision"]
    again = dict(body, base_tag_revision=mixed["tag_revision"])
    before = history(ws)

    def reopen(if_match):
        return service.resolve_ai_delegated_review(
            _actor(), ws[3]["review_id"], again, if_match, str(uuid4()), reopen=True, **KW
        )

    # Pre-fix numeric CLI: no statement loader on the service -> fail closed, no write.
    service.load_assurance_statement = None
    with pytest.raises(ReviewRejected) as error:
        reopen(f'"{current}"')
    assert error.value.code == "ASSURANCE_LOADER_UNAVAILABLE"
    assert history(ws) == before

    # Fixed compose: the loader is present, so both carried receipts replay exactly.
    service.load_assurance_statement = lambda tenant, run: statement
    with pytest.raises(ReviewRejected) as error:
        reopen(f'"{current - 1}"')  # immutable If-Match: a stale revision never writes
    assert error.value.code == "STALE_REVIEW_REVISION"
    assert history(ws) == before
    reopen(f'"{current}"')
    carried = head(ws)
    assert carried["tag_revision"] == mixed["tag_revision"] + 1
    assert (
        carried["assurance_review"]["receipt_sha256"]
        == (mixed["assurance_review"]["receipt_sha256"])
    )
    assert carried["numeric_review"]["receipt_sha256"] == mixed["numeric_review"]["receipt_sha256"]
    assert "carried_from" in carried["assurance_review"]
    assert "carried_from" in carried["numeric_review"]
    assert history(ws)["tags"][:-1] == before["tags"]
