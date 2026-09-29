"""search-absence-link-v1: reviewed whole-document absence is the only route to ``absent``.

Real ReviewService + LocalSQLiteReviewStore + rules engine + the producer's
``LocalSearchCoverageStore`` (injected run loader only). The synthetic document is a
real one-page Helvetica PDF whose text layer sits inside the graph's block boxes; its
SHA-256 pins the graph, claim, receipts and reviews. No model, network, key or paid
call. The goal claim is the §7 exact-branch shape: target year and value present,
G3-G6 reviewed absent after complete search, G7/G8 triggers absent -> E1 / IMPL.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from proofops.adapters.local.search_coverage_store import LocalSearchCoverageStore
from proofops.application.claims import Claim, ExtractionProfile, ExtractionReceipt
from proofops.application.ingest.graph_fusion import (
    CandidateBatch,
    CandidateBlock,
    fuse_candidates,
)
from proofops.application.reviews import ReviewRejected
from proofops.application.tagging.absence_link import (
    AbsenceLinkRejected,
    absent_element,
    build_request,
    derive_absences,
)
from proofops.domain.documents import NativeSource, PageGeometry
from proofops.domain.provenance import canonical_hash

import tests.acceptance.test_tagging as tagging_fixture
from tests.acceptance.test_binding import span
from tests.acceptance.test_citations import MANIFEST, OTHER, RUN, TENANT, VERSION
from tests.acceptance.test_reviews import workspace
from tests.acceptance.test_rules import pack
from tests.integration.test_ai_delegated_review import _actor

DIMENSIONS = dict(
    entity="CompanyA",
    metric="reduction",
    facility="PlantA",
    scope="Scope 1",
    reporting_period="2030",
    boundary="domestic",
)
CLAIM_TEXT = " | ".join(DIMENSIONS.values()) + " | 40%"
OTHER_TEXT = "Water withdrawal is reported by site | 2024"
ABSENT_ELEMENTS = ("G3", "G4", "G5", "G6")
QUERIES = {
    "G3": ["baseline year"],
    "G4": ["organizational boundary"],
    "G5": ["progress to date"],
    "G6": ["transition plan"],
}
KW = dict(delegated_reviewer="absence-test", delegation_authority="explicit test delegation")


def pdf_bytes(lines, *, image=False, extra_blank_page=False):
    """One page; line i baseline sits inside block i's bottom-left box."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

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
    pages = [lines] + ([[]] if extra_blank_page else [])
    for number, page_lines in enumerate(pages, start=1):
        page = writer.add_blank_page(width=600, height=800)
        resources = {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        content = b"".join(
            f"BT /F1 9 Tf 12 {20 + i * 50} Td ({text}) Tj ET\n".encode()
            for i, text in enumerate(page_lines)
        )
        if image and number == 1:
            picture = DecodedStreamObject()
            picture.set_data(b"\x00\xff\x00\xff")
            picture.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(2),
                    NameObject("/Height"): NumberObject(2),
                    NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                }
            )
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Im1"): writer._add_object(picture)}
            )
            content += b" q 50 0 0 50 300 400 cm /Im1 Do Q"
        page[NameObject("/Resources")] = DictionaryObject(resources)
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/MediaBox")] = ArrayObject([NumberObject(v) for v in (0, 0, 600, 800)])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def corpus_for(source: bytes, *, second_quality="verified"):
    """test_binding.corpus shape, English text, pinned to real PDF bytes."""
    texts = [CLAIM_TEXT, OTHER_TEXT]
    candidates = tuple(
        CandidateBlock(
            "table_cell",
            NativeSource(
                VERSION,
                MANIFEST,
                RUN,
                str(i),
                1,
                None,
                (10, 10 + i * 50, 500, 40 + i * 50),
                "pdf_bottom_left_points",
                text,
                0,
                len(text),
            ),
            PageGeometry(600, 800, 0, (0, 0, 600, 800)),
            table_native_id="table-1",
            row_number=i,
            column_number=1,
        )
        for i, text in enumerate(texts)
    )
    batch = CandidateBatch(
        TENANT,
        VERSION,
        MANIFEST,
        sha256(source).hexdigest(),
        RUN,
        "synthetic",
        "1",
        "synthetic",
        "b" * 64,
        candidates,
        synthetic=True,
    )
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    graph = replace(
        graph,
        blocks=tuple(
            replace(b, quality="verified" if i == 0 else second_quality)
            for i, b in enumerate(graph.blocks)
        ),
    )
    refs = tuple(
        next(b for b in graph.blocks if b.sources[0].source_native_id == str(i)).source_ref()
        for i in range(2)
    )
    profile = ExtractionProfile("c" * 64, "d" * 64, pack().sha256, True)
    claim = Claim(
        OTHER,
        TENANT,
        VERSION,
        MANIFEST,
        graph.source_sha256,
        refs[0].quote,
        (refs[0],),
        "verified",
        (),
        ExtractionReceipt(refs[0].source_id, "f" * 64, None, None, profile, "ok"),
    )
    return graph, claim, refs


def english_tags(ref, values=DIMENSIONS):
    return {name: span(ref, value) for name, value in values.items()}


def build_env(tmp_path, monkeypatch, *, source=None, second_quality="verified", pages=1):
    source = source or pdf_bytes([CLAIM_TEXT, OTHER_TEXT])
    monkeypatch.setattr(
        tagging_fixture, "corpus", lambda: corpus_for(source, second_quality=second_quality)
    )
    monkeypatch.setattr(tagging_fixture, "tags", english_tags)
    _, service, inputs, review, body, _, auth = workspace(tmp_path)
    state = SimpleNamespace(source=source, pages=pages, claim=inputs.context.claim)

    def loader(tenant_id, run_id, claim_id):
        if claim_id != inputs.context.claim.claim_id:
            raise ValueError("SEARCH_CLAIM_NOT_FOUND")
        return dict(
            identity=dict(
                tenant_id=TENANT,
                run_id=RUN,
                document_version_id=inputs.original.document_version_id,
                object_version_id="object-v1",
                input_hash="e" * 64,
                source_sha256=inputs.original.source_sha256,
                parse_manifest_id=inputs.original.parse_manifest_id,
                rulepack_sha256=inputs.rulepack.sha256,
            ),
            graph=inputs.original,
            claim=state.claim,
            source=state.source,
            selected_pages=list(range(1, state.pages + 1)),
            registered_page_count=state.pages,
        )

    store = LocalSearchCoverageStore(tmp_path / "search-coverage", loader)
    service.search_coverage = store
    return SimpleNamespace(
        service=service,
        inputs=inputs,
        review=review,
        body=body,
        store=store,
        state=state,
        auth=auth,
        jobs=service.store.jobs,
    )


def reviewed_items(env, elements=ABSENT_ELEMENTS, decision="absent_confirmed"):
    items = []
    for element in elements:
        receipt = env.store.produce(
            TENANT, RUN, env.inputs.context.claim.claim_id, element, QUERIES[element]
        )
        hits = sorted(
            {
                sid
                for entry in receipt["search_log"]
                for sid in entry["literal_hit_source_ids"] + entry["term_hit_source_ids"]
            }
        )
        review = dict(
            schema="search_absence_review_v1",
            receipt_sha256=receipt["artifact_sha256"],
            reviewed_corpus_sha256=receipt["corpus"]["corpus_sha256"],
            reviewed_block_count=receipt["corpus"]["block_count"],
            decision=decision if receipt["search_prerequisites_complete"] else "undetermined",
            rationale="Every corpus block was read; no disclosure of this element exists.",
            hit_dispositions={sid: "not the element; unrelated wording" for sid in hits},
            reviewer=dict(kind="ai_delegated", id="absence-test"),
            reviewed_at=datetime(2026, 9, 29, tzinfo=UTC).isoformat(),
        )
        stored = (
            env.store.record_review(TENANT, RUN, review)
            if receipt["search_prerequisites_complete"]
            else dict(artifact_sha256="0" * 64)
        )
        items.append(
            dict(
                element_id=element,
                absent_facts=sorted(
                    __import__("proofops.domain.rules.engine", fromlist=["MAPPINGS"]).MAPPINGS[
                        "goal"
                    ][element]
                ),
                receipt_sha256=receipt["artifact_sha256"],
                review_sha256=stored["artifact_sha256"],
            )
        )
    return items


def applicability(inputs):
    return dict(
        policy="local_claim_applicability_v1",
        input_snapshot_sha256=canonical_hash(inputs.snapshot()),
        track="goal",
        claim_source_refs=[asdict(ref) for ref in inputs.context.claim.source_refs],
        source_authority="Whole claim read: no offset or science-based wording.",
        triggers=[
            dict(name="offset_or_carbon_neutral_claim", value=False, reason="no offset claim"),
            dict(name="science_based_claim", value=False, reason="no SBTi wording"),
        ],
    )


def goal_body(inputs, *, base_tag_revision=1, absent=ABSENT_ELEMENTS):
    ref = inputs.context.claim.source_refs[0]

    def present(element_id, text):
        return dict(
            element_id=element_id,
            state="present",
            evidence_refs=[asdict(span(ref, text))],
            normalized_value=text,
            credited_from=None,
            reason_code=None,
        )

    def unknown(element_id):
        return dict(
            element_id=element_id,
            state="unknown",
            evidence_refs=[],
            normalized_value=None,
            credited_from=None,
            reason_code=None,
        )

    elements = [present("G1", "2030"), present("G2", "40%")]
    elements += [absent_element(e) if e in absent else unknown(e) for e in ABSENT_ELEMENTS]
    elements += [unknown("G7"), unknown("G8")]
    return json.loads(
        json.dumps(
            dict(
                base_tag_revision=base_tag_revision,
                track="goal",
                reason="전체 문서 검색·검토 후 부재 확인",
                elements=elements,
            )
        )
    )


def resolve(env, request, *, body=None, key=None, if_match='"1"', **kw):
    return env.service.resolve_ai_delegated_review(
        _actor(),
        env.review["review_id"],
        body or goal_body(env.inputs),
        if_match,
        key or f"absence-{uuid4().hex}",
        applicability_review=applicability(env.inputs),
        absence_review=request,
        **KW,
        **kw,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    return build_env(tmp_path, monkeypatch)


def test_reviewed_complete_search_publishes_absent_facts_and_e1_impl(env):
    items = reviewed_items(env)
    request = build_request(env.inputs, "goal", items)
    before = env.service.store.history(TENANT, RUN, env.inputs.context.claim.claim_id)
    result = resolve(env, request)
    decision = result["decision"]
    assert (decision["evidence_grade"], decision["label"], decision["sublabel"]) == (
        "E1",
        "INCOMPLETE",
        "IMPL",
    )
    assert decision["review_status"] == "ai_delegated_confirmed"
    history = env.service.store.history(TENANT, RUN, env.inputs.context.claim.claim_id)
    assert history["tags"][:-1] == before["tags"]
    head = history["tags"][-1]
    facts = {f["name"]: f for f in head["confirmed_tags"]["facts"]}
    for name in ("baseline_year", "baseline_value", "scope", "org_boundary"):
        assert facts[name]["state"] == "absent"
        assert facts[name]["search_coverage_verified"] is True
    assert [item["element_id"] for item in head["absence_review"]["items"]] == list(ABSENT_ELEMENTS)


# --------------------------------------------------------------------------- #
# Shared consumer helpers (also used by the P4 HTTP tamper test)
# --------------------------------------------------------------------------- #


def claims_http_client(auth, jobs, claim, inputs):
    """Real claims router over the real proof-guarded ``LocalClaimStore.current_tag``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from proofops.adapters.local.claim_store import LocalClaimStore
    from proofops_api.auth import SESSION_COOKIE_NAME
    from proofops_api.routers.claims import build_claims_router

    def no_snapshot(tenant_id, run_id):
        raise KeyError("snapshot")

    def no_tag_job(tenant_id, run_id):
        raise KeyError("tag_job")

    class Claims(LocalClaimStore):
        def load(self, tenant_id, run_id):
            return SimpleNamespace(claims=[claim])

        def submitted_reviews(self, *args, **kwargs):
            return []

    claims = Claims(SimpleNamespace(jobs=jobs, snapshot=no_snapshot), None, None)
    tags = SimpleNamespace(
        load_inputs=lambda tenant, run, claim_id: inputs, load_snapshot=no_tag_job
    )
    app = FastAPI()
    app.include_router(build_claims_router(claims, auth, tags=tags))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "admin-session")
    return client


def install_absence_verifier(env):
    from proofops.adapters.local.assurance_head import AbsenceProofVerifier

    env.jobs.absence_verifier = AbsenceProofVerifier(
        env.jobs, load_inputs=lambda tenant, run, claim: env.inputs, evidence=env.store
    )


def published(env):
    request = build_request(env.inputs, "goal", reviewed_items(env))
    return request, resolve(env, request)


def detail(client, claim_id):
    return client.get(f"/v1/runs/{RUN}/claims/{claim_id}")


# --------------------------------------------------------------------------- #
# Consumer reload, HTTP roundtrip and tamper
# --------------------------------------------------------------------------- #


def test_api_roundtrip_reloads_absent_elements_and_refuses_changed_source(env):
    _, result = published(env)
    install_absence_verifier(env)
    claim = env.inputs.context.claim
    client = claims_http_client(env.auth, env.jobs, claim, env.inputs)
    response = detail(client, claim.claim_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["ETag"] == f'"{result["new_tag_revision"]}"'
    states = {e["element_id"]: e["state"] for e in body["elements"]}
    assert [states[e] for e in ABSENT_ELEMENTS] == ["absent"] * 4
    assert body["claim"]["decision"]["sublabel"] == "IMPL"

    # Original PDF replaced after publication (same text, different bytes).
    env.state.source = pdf_bytes([CLAIM_TEXT, OTHER_TEXT]) + b"%tampered"
    assert detail(client, claim.claim_id).status_code == 409


def test_consumers_refuse_repinned_receipt_missing_verifier_and_keep_legacy(env):
    from proofops.adapters.local.assurance_head import (
        AssuranceHeadRejected,
        assurance_proofs,
        check_assurance_tag,
    )
    from proofops.adapters.local.claim_store import LocalClaimStore

    claim_id = env.inputs.context.claim.claim_id
    legacy = env.service.store.history(TENANT, RUN, claim_id)["tags"][0]
    request, _ = published(env)
    claims = LocalClaimStore(SimpleNamespace(jobs=env.jobs), None, None)
    with pytest.raises(AssuranceHeadRejected, match="ABSENCE_EVIDENCE_UNAVAILABLE"):
        claims.current_tag(TENANT, RUN, claim_id)
    install_absence_verifier(env)
    assert claims.current_tag(TENANT, RUN, claim_id)["tag"]["absence_review"]

    # Re-pinned receipt file: content changed, stored under its old content address.
    item = request["items"][0]
    path = env.store._dir(TENANT, RUN, "receipts") / f"{item['receipt_sha256']}.json"
    stored = json.loads(path.read_bytes())
    stored["queries"] = ["baseline year", "base year"]
    path.chmod(0o644)
    path.write_bytes(json.dumps(stored).encode())
    with pytest.raises(AssuranceHeadRejected, match="ABSENCE_REDERIVATION_FAILED"):
        claims.current_tag(TENANT, RUN, claim_id)

    with env.jobs._transaction() as db:
        check_assurance_tag(db, env.jobs, TENANT, RUN, legacy)  # untouched, no proof needed
    with pytest.raises(AssuranceHeadRejected, match="ABSENCE_HEAD_REJECTED"):
        with env.jobs._transaction() as db:
            tampered = env.jobs._get(db, TENANT, RUN, "tag_revision", f"{claim_id}:{2:010}")
            tampered["absence_review"] = dict(tampered["absence_review"], items=[])
            check_assurance_tag(db, env.jobs, TENANT, RUN, tampered)
    assert assurance_proofs  # imported for symmetry with P4 consumer tests


# --------------------------------------------------------------------------- #
# Refusals: nothing is written and every element stays as it was
# --------------------------------------------------------------------------- #


def _refused(env, request, code, **kw):
    claim_id = env.inputs.context.claim.claim_id
    before = env.service.store.history(TENANT, RUN, claim_id)
    with pytest.raises(ReviewRejected) as error:
        resolve(env, request, **kw)
    assert error.value.code.startswith(code), error.value.code
    assert env.service.store.history(TENANT, RUN, claim_id) == before


def test_claim_local_packet_or_typed_absence_never_proves_absence(env):
    claim_id = env.inputs.context.claim.claim_id
    before = env.service.store.history(TENANT, RUN, claim_id)
    with pytest.raises(ReviewRejected) as error:
        env.service.resolve_ai_delegated_review(
            _actor(),
            env.review["review_id"],
            goal_body(env.inputs),
            '"1"',
            f"absence-{uuid4().hex}",
            applicability_review=applicability(env.inputs),
            **KW,
        )
    assert error.value.code == "COVERAGE_OR_APPLICABILITY_REQUIRED"
    assert env.service.store.history(TENANT, RUN, claim_id) == before


@pytest.mark.parametrize(
    "setup",
    [
        dict(pages=2),  # registered page 2 has no text layer / blocks
        dict(image=True),  # an image region nobody read
        dict(unparsed_row=True),  # a table row in the text layer that no block holds
    ],
)
def test_incomplete_page_image_or_table_keeps_unknown(tmp_path, monkeypatch, setup):
    pages = setup.get("pages", 1)
    lines = [CLAIM_TEXT, OTHER_TEXT] + (["Base year 2019 | 100"] if "unparsed_row" in setup else [])
    source = pdf_bytes(lines, image=setup.get("image", False), extra_blank_page=pages == 2)
    env = build_env(tmp_path, monkeypatch, source=source, pages=pages)
    items = reviewed_items(env)
    receipt = env.store.replay(TENANT, RUN, items[0]["receipt_sha256"])[0]
    assert receipt["search_prerequisites_complete"] is False
    _refused(env, build_request(env.inputs, "goal", items), "ABSENCE_SEARCH_INCOMPLETE")


def test_not_absent_review_and_stale_claim_or_source_are_refused(env):
    items = reviewed_items(env, decision="not_absent")
    _refused(env, build_request(env.inputs, "goal", items), "ABSENCE_NOT_CONFIRMED")

    good = build_request(env.inputs, "goal", reviewed_items(env))
    env.state.claim = replace(env.inputs.context.claim, revision=2)  # new claim revision
    _refused(env, good, "ABSENCE_EVIDENCE_REJECTED")
    env.state.claim = env.inputs.context.claim
    env.state.source = env.state.source + b"%changed"  # original PDF changed
    _refused(env, good, "ABSENCE_EVIDENCE_REJECTED")


def test_request_pins_stale_inputs_compound_and_excluded_elements(env):
    items = reviewed_items(env)
    good = build_request(env.inputs, "goal", items)
    _refused(env, dict(good, input_snapshot_sha256="0" * 64), "ABSENCE_STALE_INPUTS")
    _refused(env, dict(good, rulepack_sha256="0" * 64), "ABSENCE_RULEPACK_MISMATCH")
    _refused(env, dict(good, claim_source_refs=[]), "WHOLE_CLAIM_REQUIRED")
    _refused(env, dict(good, policy_hash="0" * 64), "ABSENCE_POLICY_MISMATCH")
    partial = [dict(items[0], absent_facts=["baseline_year"]), *items[1:]]
    _refused(env, dict(good, items=partial), "ABSENCE_REQUEST_INVALID")  # compound G3
    swapped = [dict(items[0], element_id="G4", absent_facts=["org_boundary", "scope"])]
    _refused(env, dict(good, items=swapped), "ABSENCE_RECEIPT_IDENTITY_MISMATCH")
    p4 = dict(
        element_id="P4",
        absent_facts=["assurance_covered"],
        receipt_sha256=items[0]["receipt_sha256"],
        review_sha256=items[0]["review_sha256"],
    )
    with pytest.raises(AbsenceLinkRejected, match="ABSENCE_REQUEST_INVALID"):
        derive_absences(env.inputs, build_request(env.inputs, "performance", [p4]), env.store)


def test_present_or_conflict_base_primitive_is_never_overridden(env):
    from proofops.domain.values import LlmElement

    items = reviewed_items(env, elements=("G3",))
    base = replace(
        env.inputs.consensus,
        candidate_elements=(
            *env.inputs.consensus.candidate_elements,
            LlmElement("G3", "conflict", (), None, None, None),
        ),
    )
    inputs = replace(env.inputs, consensus=base)
    with pytest.raises(AbsenceLinkRejected, match="ABSENCE_BASE_NOT_UNKNOWN"):
        derive_absences(inputs, build_request(inputs, "goal", items), env.store)


def test_cross_tenant_copy_and_missing_port_are_refused(env):
    items = reviewed_items(env)
    request = build_request(env.inputs, "goal", items)
    other = "99999999-9999-4999-8999-999999999999"
    source = env.store._dir(TENANT, RUN, "receipts") / f"{items[0]['receipt_sha256']}.json"
    target = env.store._dir(other, RUN, "receipts")
    target.mkdir(parents=True)
    (target / source.name).write_bytes(source.read_bytes())
    with pytest.raises(ValueError):
        env.store.replay(other, RUN, items[0]["receipt_sha256"])
    env.service.search_coverage = None
    _refused(env, request, "ABSENCE_EVIDENCE_UNAVAILABLE")


def test_global_numeric_credit_stays_forbidden(env):
    """GAP-004: a year from another block cannot become the claim's baseline."""
    other_block = next(
        b for b in env.inputs.original.blocks if b.sources[0].source_native_id == "1"
    )
    body = goal_body(env.inputs, absent=("G4", "G5", "G6"))
    g3 = next(e for e in body["elements"] if e["element_id"] == "G3")
    g3.update(
        state="present",
        evidence_refs=[asdict(span(other_block.source_ref(), "2024"))],
        normalized_value="2024",
    )
    request = build_request(env.inputs, "goal", reviewed_items(env, elements=("G4", "G5", "G6")))
    claim_id = env.inputs.context.claim.claim_id
    before = env.service.store.history(TENANT, RUN, claim_id)
    with pytest.raises(ReviewRejected) as error:
        resolve(env, request, body=body)
    assert error.value.code in {"BINDING_REJECTED", "SOURCE_REJECTED", "SOURCE_VALUE_MISMATCH"}
    assert env.service.store.history(TENANT, RUN, claim_id) == before


def test_rereview_replays_receipt_idempotency_and_stale_revision(env):
    install_absence_verifier(env)
    request = build_request(env.inputs, "goal", reviewed_items(env))
    key = f"absence-{uuid4().hex}"
    first = resolve(env, request, key=key)
    assert resolve(env, request, key=key) == first
    with pytest.raises(ReviewRejected) as error:
        resolve(env, dict(request, items=request["items"][:1]), key=key)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(ReviewRejected) as error:
        resolve(env, request)  # a second resolve without an explicit re-review
    assert error.value.code == "STALE_REVIEW_REVISION"

    fresh = env.service.store.get(TENANT, env.review["review_id"])
    body = goal_body(env.inputs, base_tag_revision=first["new_tag_revision"])
    carried = env.service.resolve_ai_delegated_review(
        _actor(),
        env.review["review_id"],
        body,
        f'"{fresh["revision"]}"',
        f"absence-{uuid4().hex}",
        applicability_review=applicability(env.inputs),
        reopen=True,
        **KW,
    )
    head = env.service.store.history(TENANT, RUN, env.inputs.context.claim.claim_id)["tags"][-1]
    assert carried["decision"]["sublabel"] == "IMPL"
    assert head["absence_review"]["carried_from"]["origin"] == "ai_delegated"


# --------------------------------------------------------------------------- #
# Trusted CLI
# --------------------------------------------------------------------------- #


def _cli(monkeypatch, env, tmp_path, items, *extra):
    from proofops.adapters.local.claim_store import LocalClaimStore

    import scripts.link_absence_review as cli

    install_absence_verifier(env)
    monkeypatch.setattr(
        cli,
        "compose",
        lambda path, root: dict(
            service=env.service,
            load_inputs=lambda tenant, run, claim: env.inputs,
            coverage=env.store,
            claims=LocalClaimStore(SimpleNamespace(jobs=env.jobs), None, None),
        ),
    )
    correction = tmp_path / "correction.json"
    correction.write_text(json.dumps(goal_body(env.inputs)), encoding="utf-8")
    trigger = tmp_path / "applicability.json"
    trigger.write_text(json.dumps(applicability(env.inputs)), encoding="utf-8")
    argv = [
        "--state-db",
        str(tmp_path / "state.sqlite"),
        "--tenant-id",
        TENANT,
        "--review-id",
        env.review["review_id"],
        "--track",
        "goal",
        "--correction-json",
        str(correction),
        "--applicability-review-json",
        str(trigger),
        "--delegated-reviewer",
        "cli-absence-test",
    ]
    for item in items:
        argv += ["--item", f"{item['element_id']}={item['receipt_sha256']}:{item['review_sha256']}"]
    return cli.main(argv + list(extra))


def test_cli_dry_run_apply_and_reload(env, monkeypatch, capsys, tmp_path):
    items = reviewed_items(env)
    claim_id = env.inputs.context.claim.claim_id
    before = env.service.store.history(TENANT, RUN, claim_id)
    assert _cli(monkeypatch, env, tmp_path, items) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True
    assert [i["element_id"] for i in dry["receipt"]["items"]] == list(ABSENT_ELEMENTS)
    assert env.service.store.history(TENANT, RUN, claim_id) == before

    assert _cli(monkeypatch, env, tmp_path, items, "--apply", "--idempotency-key", "k" * 20) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["result"]["decision"]["sublabel"] == "IMPL"
    assert applied["reloaded_tag_revision"] == 2


def test_cli_refuses_unconfirmed_review_without_writing(env, monkeypatch, capsys, tmp_path):
    items = reviewed_items(env, decision="undetermined")
    claim_id = env.inputs.context.claim.claim_id
    before = env.service.store.history(TENANT, RUN, claim_id)
    code = _cli(monkeypatch, env, tmp_path, items, "--apply", "--idempotency-key", "k" * 20)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["status"] == "unknown"
    assert out["error"] == "ABSENCE_NOT_CONFIRMED"
    assert env.service.store.history(TENANT, RUN, claim_id) == before


@pytest.mark.parametrize("drift", [["baseline_year"], None])
def test_receipt_primitives_must_equal_item_primitives(env, drift):
    """Producer ``element_primitives`` is required and must equal ``absent_facts``."""
    items = reviewed_items(env, elements=("G3",))
    replay = env.store.replay

    def drifted(tenant_id, run_id, receipt_sha256):
        receipt, state = replay(tenant_id, run_id, receipt_sha256)
        receipt = dict(receipt)
        if drift is None:
            receipt.pop("element_primitives", None)
        else:
            receipt["element_primitives"] = drift
        return receipt, state

    env.store.replay = drifted
    with pytest.raises(AbsenceLinkRejected, match="ABSENCE_PRIMITIVES_MISMATCH"):
        derive_absences(env.inputs, build_request(env.inputs, "goal", items), env.store)
