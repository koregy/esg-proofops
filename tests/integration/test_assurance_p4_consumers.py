"""P4 receipt heads at consumers: full re-derivation outside, identity re-pin inside.

A real P4 revision is published through ReviewService. The claim reader that serves
the claims API, lists, exports, summaries, comparisons and reconciliation
(``LocalClaimStore.current_tag``) and the rescore capture reader then read it. After
publication the original PDF bytes or the published statement are changed, including
a repinned statement hash; every read must refuse. The unmodified head is served,
a missing verifier refuses, and heads without a receipt are unchanged.
"""

from __future__ import annotations

import json
from contextvars import copy_context
from types import SimpleNamespace

import pytest
from proofops.adapters.local.assurance_head import (
    _PROOFS,
    AssuranceHeadRejected,
    AssuranceProofVerifier,
    assurance_proofs,
    check_assurance_tag,
)
from proofops.adapters.local.assurance_store import LocalAssuranceStore, _statement_to_payload
from proofops.adapters.local.claim_store import LocalClaimStore
from proofops.adapters.local.rescore_store import LocalSQLiteRescoreStore
from proofops.application.rescores import RescoreRejected
from proofops.application.tagging.assurance_link import build_request, derive_assurance_fact

from tests.acceptance.test_citations import RUN, TENANT
from tests.integration.test_assurance_p4_link import (  # noqa: F401  (fixture)
    _body_with_p4,
    _publish_statement,
    _resolve,
    statement_for,
    ws,
)

OTHER_RUN = "88888888-8888-4888-8888-888888888888"


@pytest.fixture
def published(ws):  # noqa: F811
    _, service, inputs, review, body, _, auth = ws
    statement = statement_for(inputs)
    _publish_statement(service, statement)
    legacy = service.store.history(TENANT, RUN, review["claim_id"])["tags"][0]
    request = build_request(inputs, statement)
    fact, _ = derive_assurance_fact(inputs, request, statement)
    _resolve(service, review, _body_with_p4(body, fact), statement, request)
    jobs = service.store.jobs
    # Real statement replay: LocalAssuranceStore.load re-extracts from the graph.
    statements = LocalAssuranceStore(SimpleNamespace(jobs=jobs))
    statements._trusted_graph = lambda tenant, run: inputs.original
    source = {"digest": inputs.original.source_sha256}
    jobs.assurance_verifier = AssuranceProofVerifier(
        jobs,
        load_inputs=lambda tenant, run, claim: inputs,
        load_statement=statements.load,
        source_digest=lambda tenant, version: source["digest"],
    )
    claims = LocalClaimStore(SimpleNamespace(jobs=jobs), None, None)
    rescore = LocalSQLiteRescoreStore(SimpleNamespace(jobs=jobs))
    return SimpleNamespace(
        service=service,
        jobs=jobs,
        inputs=inputs,
        review=review,
        statement=statement,
        source=source,
        claims=claims,
        rescore=rescore,
        legacy=legacy,
        claim_id=review["claim_id"],
        auth=auth,
    )


def _rescore_claims(env):
    with assurance_proofs(env.jobs, TENANT, RUN), env.jobs._transaction() as db:
        return env.rescore._claims(db, TENANT, RUN)


def _replace_statement(env, payload):
    with env.jobs._transaction() as db:
        db.execute(
            """UPDATE job_records SET value=? WHERE tenant_id=? AND run_id=?
            AND kind='assurance_statement' AND record_id='STATEMENT'""",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), TENANT, RUN),
        )


def test_unmodified_head_is_served_by_api_and_rescore_readers(published):
    env = published
    current = env.claims.current_tag(TENANT, RUN, env.claim_id)
    facts = {f["name"]: f for f in current["tag"]["confirmed_tags"]["facts"]}
    assert facts["assurance_covered"]["normalized_value"] == "covered"
    claims = _rescore_claims(env)
    assert claims[env.claim_id]["tag"]["assurance_review"]["status"] == "covered"
    assert _PROOFS.get() is None  # context always cleared


def test_changed_original_pdf_after_publication_is_refused(published):
    env = published
    # Registry metadata was repinned to the new bytes, so read_original itself passes;
    # the digest no longer equals the graph/statement/receipt source pins.
    env.source["digest"] = "f" * 64
    with pytest.raises(AssuranceHeadRejected, match="ASSURANCE_SOURCE_CHANGED"):
        env.claims.current_tag(TENANT, RUN, env.claim_id)
    with pytest.raises(RescoreRejected) as error:
        _rescore_claims(env)
    assert error.value.code == "ASSURANCE_SOURCE_CHANGED"


def test_changed_statement_with_repinned_hash_is_refused(published):
    env = published
    forged = _statement_to_payload(env.statement)
    forged["provider"] = "다른 보증기관"  # semantic_hash left at the pinned value
    _replace_statement(env, forged)
    with pytest.raises(AssuranceHeadRejected, match="ASSURANCE_REDERIVATION_FAILED"):
        env.claims.current_tag(TENANT, RUN, env.claim_id)
    with pytest.raises(RescoreRejected) as error:
        _rescore_claims(env)
    assert error.value.code.startswith("ASSURANCE_REDERIVATION_FAILED")

    # A different, internally valid statement with its own recomputed hash.
    other = _statement_to_payload(statement_for(env.inputs, provider="ISAE 3000"))
    _replace_statement(env, other)
    with pytest.raises(AssuranceHeadRejected, match="ASSURANCE_HEAD_REJECTED"):
        env.claims.current_tag(TENANT, RUN, env.claim_id)


def test_missing_verifier_or_context_fails_closed_and_legacy_head_is_unchanged(published):
    env = published
    env.jobs.assurance_verifier = None
    with pytest.raises(AssuranceHeadRejected, match="ASSURANCE_LOADER_UNAVAILABLE"):
        env.claims.current_tag(TENANT, RUN, env.claim_id)
    with pytest.raises(RescoreRejected) as error:
        _rescore_claims(env)
    assert error.value.code == "ASSURANCE_LOADER_UNAVAILABLE"
    head_key = f"{env.claim_id}:{2:010}"
    with env.jobs._transaction() as db:
        tag = env.jobs._get(db, TENANT, RUN, "tag_revision", head_key)
        # Consumer transaction opened without the outside proof step.
        with pytest.raises(AssuranceHeadRejected, match="ASSURANCE_PROOF_UNAVAILABLE"):
            check_assurance_tag(db, env.jobs, TENANT, RUN, tag)
        # A head without a receipt needs no verifier and is returned as stored.
        check_assurance_tag(db, env.jobs, TENANT, RUN, env.legacy)
        stored = env.jobs._get(db, TENANT, RUN, "tag_revision", f"{env.claim_id}:{1:010}")
    assert stored == env.legacy


def test_head_change_after_proof_is_stale_and_other_run_is_not_covered(published):
    env = published
    with assurance_proofs(env.jobs, TENANT, RUN):
        fresh = env.service.store.get(TENANT, env.review["review_id"])
        body = dict(
            base_tag_revision=2,
            track="performance",
            reason="재검토: 보증 연결 유지",
            elements=json.loads(
                json.dumps(
                    env.service.store.history(TENANT, RUN, env.claim_id)["tags"][-1]["elements"]
                )
            ),
        )
        _resolve(
            env.service,
            env.review,
            body,
            env.statement,
            if_match=f'"{fresh["revision"]}"',
            reopen=True,
        )
        with env.jobs._transaction() as db:
            head = env.jobs._get(db, TENANT, RUN, "tag_revision", f"{env.claim_id}:{3:010}")
            with pytest.raises(AssuranceHeadRejected, match="ASSURANCE_PROOF_STALE"):
                check_assurance_tag(db, env.jobs, TENANT, RUN, head)
            with pytest.raises(AssuranceHeadRejected):
                check_assurance_tag(db, env.jobs, TENANT, OTHER_RUN, head)
    # Once outside the stale context, a fresh proof covers the new head.
    assert env.claims.current_tag(TENANT, RUN, env.claim_id)["tag"]["tag_revision"] == 3


def test_proof_context_is_cleared_on_error_and_isolated_per_context(published):
    env = published
    with pytest.raises(RuntimeError):
        with assurance_proofs(env.jobs, TENANT, RUN):
            assert _PROOFS.get() is not None
            raise RuntimeError("consumer failure")
    assert _PROOFS.get() is None

    def inside():
        with assurance_proofs(env.jobs, TENANT, RUN):
            return _PROOFS.get()

    assert copy_context().run(inside) is not None
    assert _PROOFS.get() is None


@pytest.mark.parametrize(
    "module,method",
    [
        ("claim_store", "LocalClaimStore.current_tag"),
        ("claim_store", "LocalClaimStore.page"),
        ("export_store", "LocalExportStore.capture"),
        ("summary_store", "LocalSummaryStore.get"),
        ("comparison_store", "LocalComparisonStore.create"),
        ("reconciliation_store", "LocalReconciliationStore._verified_claim"),
        ("rescore_store", "LocalSQLiteRescoreStore.capture"),
        ("rescore_store", "LocalSQLiteRescoreStore.commit"),
        ("analysis_store", "LocalAnalysisStore._first_page"),
        ("review_store", "LocalSQLiteReviewStore.resolve"),
    ],
)
def test_every_head_consumer_entry_computes_proofs_before_its_transaction(module, method):
    import importlib
    import inspect

    owner, name = method.split(".")
    source = inspect.getsource(
        getattr(getattr(importlib.import_module(f"proofops.adapters.local.{module}"), owner), name)
    )
    assert "assurance_proofs(" in source
    assert source.index("assurance_proofs(") < source.rindex("_transaction()")


def test_http_claim_detail_serves_p4_and_refuses_tampered_source_or_statement(published):
    """HTTP-level: the real claims router reads heads only through the proof guard."""
    from tests.integration.test_absence_review_link import claims_http_client

    env = published
    client = claims_http_client(env.auth, env.jobs, env.inputs.context.claim, env.inputs)
    url = f"/v1/runs/{RUN}/claims/{env.claim_id}"
    response = client.get(url)
    assert response.status_code == 200, response.text
    p4 = next(e for e in response.json()["elements"] if e["element_id"] == "P4")
    assert (p4["state"], p4["normalized_value"]) == ("present", "covered")

    env.source["digest"] = "f" * 64  # original PDF replaced, metadata repinned
    refused = client.get(url)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "ARTIFACT_UNAVAILABLE"
    env.source["digest"] = env.inputs.original.source_sha256
    assert client.get(url).status_code == 200

    forged = _statement_to_payload(env.statement)
    forged["provider"] = "다른 보증기관"
    _replace_statement(env, forged)
    assert client.get(url).status_code == 409
