"""Company-activity industry topic review (GAP-010 A): offline, no model or paid call."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path

import pytest
from proofops.adapters.local import industry_topic_store as store_module
from proofops.adapters.local.industry_topic_store import LocalIndustryTopicStore
from proofops.adapters.local.job_store import LocalSQLiteJobStore
from proofops.application import industry_topic_review as topics
from proofops.application.industry_topic_review import IndustryReviewRejected
from proofops.application.ingest.graph_fusion import fuse_candidates
from proofops.domain.provenance import canonical_hash

from tests.acceptance.test_parsing import TENANT, candidate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import review_industry_topics as cli  # noqa: E402

RUN = "22222222-2222-4222-8222-222222222222"
OTHER_TENANT = "99999999-9999-4999-8999-999999999999"
ACTIVITY = "The company operates financial services only and owns no manufacturing plants"
NOW = "2026-09-29T09:00:00+00:00"
DELEGATE = dict(kind="ai_delegated", id="industry-reviewer", authority="root dispatch delegation")
APPROVER = dict(kind="ai_delegated", id="industry-approver", authority="root approval delegation")
EMPTY_CROSSWALK = dict(version="impl2", verification_status="unverified", mappings=[])


_BATCH = candidate("span", [("P0", "paragraph", ACTIVITY, (70, 710, 500, 741), ())])


def graph_for(*, verified=True, source=b"original-pdf-bytes"):
    # One fixed parser run so source ids are stable across calls.
    batch = _BATCH
    graph = fuse_candidates(
        (replace(batch, source_sha256=sha256(source).hexdigest()),), tenant_id=TENANT
    )
    if verified:
        graph = replace(graph, blocks=tuple(replace(b, quality="verified") for b in graph.blocks))
    return graph


GRAPH = graph_for()


def run_state(graph=None, *, tenant=TENANT, crosswalk=EMPTY_CROSSWALK, industry=None, **pins):
    graph = graph or GRAPH
    base = dict(
        tenant_id=tenant,
        run_id=RUN,
        document_version_id=graph.document_version_id,
        object_version_id="object-1",
        source_sha256=graph.source_sha256,
        input_hash="i" * 64,
        parse_manifest_id=graph.parse_manifest_id,
        graph_sha256=canonical_hash(asdict(graph)),
        rulepack_sha256="r" * 64,
        crosswalk_sha256=canonical_hash(crosswalk),
        industry=industry or dict(system="unknown", code=None),
    )
    base.update(pins)
    return dict(pins=base, graph=graph, crosswalk=crosswalk)


@pytest.fixture
def env(tmp_path):
    jobs = LocalSQLiteJobStore(tmp_path / "state.sqlite3")
    current = {TENANT: run_state()}
    store = LocalIndustryTopicStore(jobs, lambda tenant, run: current[tenant])
    return jobs, store, current


def universe(topic_ids=("water", "waste", "ghg")):
    return dict(
        schema=topics.UNIVERSE_SCHEMA,
        scope="operator_declared_project_topics",
        official_mapping=False,
        topics=[dict(topic_id=t, label=f"{t} topic") for t in topic_ids],
        declared_by=DELEGATE,
        declared_at=NOW,
        reason="Operator-declared project topic list for this review",
    )


def evidence(quote="financial services only"):
    start = ACTIVITY.index(quote)
    source_id = GRAPH.blocks[0].source_id
    return [dict(source_id=source_id, char_start=start, char_end=start + len(quote), quote=quote)]


def declare(store, **kwargs):
    return store.declare_universe(
        TENANT, RUN, universe(**kwargs), expected_head=None, dry_run=False
    )


def review(store, topic="water", decision="not_applicable", expected=None, refs=None, **kw):
    return store.review(
        TENANT,
        RUN,
        topic_id=topic,
        decision=decision,
        reason=kw.pop("reason", "Company activity evidence reviewed for this topic"),
        evidence=evidence() if refs is None else refs,
        reviewer=kw.pop("reviewer", DELEGATE),
        reviewed_at=NOW,
        expected_head=expected,
        dry_run=kw.pop("dry_run", False),
    )["revision"]


def approve(store, head, topic="water", **kw):
    return store.approve(
        TENANT,
        RUN,
        topic_id=topic,
        approver=kw.pop("approver", APPROVER),
        approved_at=NOW,
        reason="Evidence shows no manufacturing or water-intensive activity",
        expected_head=head,
        dry_run=kw.pop("dry_run", False),
    )["revision"]


def row_count(jobs):
    with sqlite3.connect(jobs.path) as db:
        return db.execute("SELECT COUNT(*) FROM job_records").fetchone()[0]


def by_topic(report):
    return {t["topic_id"]: t for t in report["topics"]}


# --- denominator semantics -----------------------------------------------------------


def test_undeclared_universe_has_no_denominator(env):
    _, store, _ = env
    report = store.report(TENANT, RUN)
    assert report["universe_status"] == "universe_undeclared"
    assert report["denominator_count"] is None and report["topics"] == []
    assert "not an official" in report["disclaimer"]


def test_unknown_and_unreviewed_topics_stay_in_the_denominator(env):
    _, store, _ = env
    declare(store)
    review(store, topic="waste", decision="undetermined", refs=[])
    report = store.report(TENANT, RUN)
    states = {t["topic_id"]: t["state"] for t in report["topics"]}
    assert states == {"water": "unreviewed", "waste": "undetermined", "ghg": "unreviewed"}
    assert report["denominator_count"] == 3 and report["excluded_not_applicable_count"] == 0
    assert all(t["satisfaction"] is None for t in report["topics"])
    assert report["satisfaction_rate"] is None


def test_only_approved_not_applicable_with_evidence_leaves_the_denominator(env):
    _, store, _ = env
    declare(store)
    pending = review(store)
    report = store.report(TENANT, RUN)
    assert by_topic(report)["water"]["state"] == "not_applicable_pending_approval"
    assert report["denominator_count"] == 3
    approval = approve(store, pending["revision_sha256"])
    assert approval["approves_revision_sha256"] == pending["revision_sha256"]
    report = store.report(TENANT, RUN)
    water = by_topic(report)["water"]
    assert water["state"] == "not_applicable_approved" and water["in_denominator"] is False
    assert water["evidence_refs"][0]["verification_state"] == "verified"
    assert report["denominator_count"] == 2 and report["excluded_not_applicable_count"] == 1


def test_a_newer_review_makes_an_approval_stale(env):
    _, store, _ = env
    declare(store)
    approval = approve(store, review(store)["revision_sha256"])
    newer = review(store, expected=approval["revision_sha256"])
    report = store.report(TENANT, RUN)
    assert by_topic(report)["water"]["state"] == "not_applicable_pending_approval"
    assert report["denominator_count"] == 3
    # The old review hash can no longer be approved: the head moved.
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_HEAD_CONFLICT"):
        approve(store, approval["prior_sha256"])
    approve(store, newer["revision_sha256"])


def test_classification_alone_never_excludes_a_topic(env):
    _, store, current = env
    crosswalk = dict(
        version="synthetic-verified",
        verification_status="verified",
        mappings=[
            dict(
                industry_system="GICS",
                industry_code="40101010",
                topic_id="water",
                applicability="N_A",
                mandatory=None,
            )
        ],
    )
    current[TENANT] = run_state(crosswalk=crosswalk, industry=dict(system="GICS", code="40101010"))
    declare(store)
    water = by_topic(store.report(TENANT, RUN))["water"]
    assert water["candidate"]["candidate_applicability"] == "N_A"
    assert water["candidate"]["role"] == "candidate_only_never_excludes"
    assert water["state"] == "unreviewed" and water["in_denominator"] is True


def test_empty_unverified_crosswalk_stays_undetermined(env):
    _, store, _ = env
    declare(store)
    candidate = by_topic(store.report(TENANT, RUN))["water"]["candidate"]
    assert candidate["candidate_applicability"] == "undetermined"
    assert candidate["mapping_verified"] is False


# --- no fake approval -------------------------------------------------------------------


def test_approval_needs_exact_current_not_applicable_review(env):
    _, store, _ = env
    declare(store)
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_APPROVAL_TARGET_STALE"):
        approve(store, None)
    applicable = review(store, decision="applicable")
    with pytest.raises(IndustryReviewRejected, match="ONLY_FOR_NOT_APPLICABLE"):
        approve(store, applicable["revision_sha256"])
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_HEAD_CONFLICT"):
        approve(store, "0" * 64)


@pytest.mark.parametrize(
    "refs, code",
    [
        ([], "INDUSTRY_EVIDENCE_REQUIRED"),
        ([dict(evidence()[0], quote="wrong text")], "INDUSTRY_EVIDENCE_NOT_VERIFIED"),
        ([dict(evidence()[0], source_id="missing")], "INDUSTRY_EVIDENCE_NOT_VERIFIED"),
        ([dict(evidence()[0], extra=1)], "INDUSTRY_EVIDENCE_INVALID"),
    ],
)
def test_decisions_need_verified_company_activity_evidence(env, refs, code):
    _, store, _ = env
    declare(store)
    with pytest.raises(IndustryReviewRejected, match=code):
        review(store, decision="not_applicable", refs=refs)


def test_unverified_source_block_cannot_be_evidence(env):
    _, store, current = env
    current[TENANT] = run_state(graph_for(verified=False))
    declare(store)
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_EVIDENCE_NOT_VERIFIED"):
        review(store)


@pytest.mark.parametrize(
    "actor",
    [
        dict(kind="llm", id="x", authority="some delegation"),
        dict(kind="ai_delegated", id="x"),
        dict(kind="ai_delegated", id="", authority="some delegation"),
        dict(kind="ai_delegated", id="x", authority="no"),
    ],
)
def test_actor_identity_and_authority_are_required(env, actor):
    _, store, _ = env
    declare(store)
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_ACTOR_INVALID"):
        review(store, reviewer=actor)


def test_universe_must_be_operator_declared_project_scope(env):
    _, store, _ = env
    for bad in (dict(universe(), official_mapping=True), dict(universe(), scope="sasb_official")):
        with pytest.raises(IndustryReviewRejected, match="NOT_PROJECT_SCOPED"):
            store.declare_universe(TENANT, RUN, bad, expected_head=None, dry_run=False)
    declare(store)
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_TOPIC_NOT_IN_UNIVERSE"):
        review(store, topic="biodiversity")


# --- dry run, read-only lineage, concurrency -----------------------------------------------


def test_dry_run_and_report_write_nothing(env):
    jobs, store, _ = env
    before = row_count(jobs)
    store.declare_universe(TENANT, RUN, universe(), expected_head=None)  # default dry run
    assert row_count(jobs) == before
    declare(store)
    after_declare = row_count(jobs)
    review(store, dry_run=True)
    store.report(TENANT, RUN)
    assert row_count(jobs) == after_declare
    with sqlite3.connect(jobs.path) as db:
        kinds = {row[0] for row in db.execute("SELECT DISTINCT kind FROM job_records")}
    assert kinds <= {"industry_topic_universe", "industry_topic_universe_head"}


def test_concurrent_reviews_with_the_same_head_have_one_winner(env):
    _, store, _ = env
    declare(store)
    barrier, results = threading.Barrier(2), []

    def attempt(decision):
        barrier.wait()
        try:
            results.append(review(store, decision=decision)["decision"])
        except IndustryReviewRejected as exc:
            results.append(str(exc))

    threads = [
        threading.Thread(target=attempt, args=(d,)) for d in ("applicable", "not_applicable")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("INDUSTRY_HEAD_CONFLICT") == 1


# --- mutation, foreign tenant, tamper -----------------------------------------------------------


def test_source_mutation_or_stale_input_makes_everything_stale(env):
    _, store, current = env
    declare(store)
    approve(store, review(store)["revision_sha256"])
    current[TENANT] = run_state(graph_for(source=b"mutated-original"))
    report = store.report(TENANT, RUN)
    assert report["universe_status"] == "stale_inputs" and report["topics"] == []
    current[TENANT] = run_state(input_hash="j" * 64)
    assert store.report(TENANT, RUN)["universe_status"] == "stale_inputs"
    # A loader whose pins disagree with its own graph is refused outright.
    current[TENANT] = run_state(source_sha256="0" * 64)
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_RUN_IDENTITY_MISMATCH"):
        store.report(TENANT, RUN)


def test_foreign_tenant_sees_nothing_and_cannot_reuse_identity(env):
    _, store, current = env
    declare(store)
    approve(store, review(store)["revision_sha256"])
    current[OTHER_TENANT] = run_state()  # pins still name TENANT
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_RUN_IDENTITY_MISMATCH"):
        store.report(OTHER_TENANT, RUN)


def _rewrite(jobs, kind, key, mutate, *, rehash=True):
    with sqlite3.connect(jobs.path) as db:
        raw = db.execute(
            "SELECT value FROM job_records "
            "WHERE tenant_id=? AND run_id=? AND kind=? AND record_id=?",
            (TENANT, RUN, kind, key),
        ).fetchone()[0]
        body = json.loads(raw)
        mutate(body)
        if rehash:
            body.pop("revision_sha256")
            body["revision_sha256"] = canonical_hash(body)
        db.execute(
            "UPDATE job_records SET value=? "
            "WHERE tenant_id=? AND run_id=? AND kind=? AND record_id=?",
            (json.dumps(body).encode(), TENANT, RUN, kind, key),
        )


def test_tampered_or_rehashed_revision_cannot_manufacture_an_approval(env):
    jobs, store, _ = env
    declare(store)
    review(store, decision="applicable")
    # Flip the decision and re-hash: the head anchor no longer matches.
    _rewrite(
        jobs,
        "industry_topic_revision",
        "water:0000000001",
        lambda b: b.update(decision="not_applicable"),
    )
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_CHAIN_INVALID"):
        store.report(TENANT, RUN)
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_CHAIN_INVALID"):
        approve(store, None)
    # Editing without re-hashing is caught by the content hash.
    _rewrite(
        jobs,
        "industry_topic_revision",
        "water:0000000001",
        lambda b: b.update(reason="edited reason text for the topic"),
        rehash=False,
    )
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_REVISION_TAMPERED"):
        store.report(TENANT, RUN)


def test_forged_approval_row_without_head_is_refused(env):
    jobs, store, _ = env
    declare(store)
    head = review(store)
    forged = approve(store, head["revision_sha256"], dry_run=True)
    jobs_db = sqlite3.connect(jobs.path)
    with jobs_db:
        jobs_db.execute(
            "INSERT INTO job_records VALUES (?,?,?,?,?)",
            (
                TENANT,
                RUN,
                "industry_topic_revision",
                "water:0000000002",
                json.dumps(forged).encode(),
            ),
        )
    jobs_db.close()
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_CHAIN_INVALID"):
        store.report(TENANT, RUN)


# --- optional summary integration ----------------------------------------------------------


def test_summary_integration_stays_none_without_independent_satisfaction(env):
    _, store, _ = env
    assert topics.summary_applicability(store.report(TENANT, RUN)) is None
    declare(store)
    approve(store, review(store)["revision_sha256"])
    items = topics.summary_applicability(store.report(TENANT, RUN))
    assert {(i.element_id, i.applicability) for i in items} == {
        ("water", "N_A"),
        ("waste", "undetermined"),
        ("ghg", "undetermined"),
    }
    review(store, topic="ghg", decision="applicable")
    assert topics.summary_applicability(store.report(TENANT, RUN)) is None


# --- CLI ------------------------------------------------------------------------------------


def test_cli_defaults_to_dry_run_and_ai_delegated(tmp_path, monkeypatch, capsys):
    current = run_state()
    monkeypatch.setattr(store_module, "run_loader", lambda *a: (lambda t, r: current))
    database = tmp_path / "state.sqlite3"
    LocalSQLiteJobStore(database)
    (tmp_path / "topics.json").write_text(json.dumps(universe()["topics"]))
    base = ["--database-path", str(database), "--tenant-id", TENANT, "--run-id", RUN]
    who = ["--actor-id", "operator-1", "--authority", "root dispatch delegation"]
    declare_args = [
        "declare-universe",
        *base,
        "--universe",
        str(tmp_path / "topics.json"),
        "--reason",
        "Operator-declared project topics",
        *who,
    ]
    assert cli.main(declare_args) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["written"] is False
    assert dry["revision"]["universe"]["declared_by"]["kind"] == "ai_delegated"
    assert cli.main([*declare_args, "--apply"]) == 0
    capsys.readouterr()
    assert cli.main(["report", *base]) == 0
    assert json.loads(capsys.readouterr().out)["denominator_count"] == 3
    # Stale CAS is refused, not overwritten.
    assert cli.main([*declare_args, "--apply"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "INDUSTRY_HEAD_CONFLICT"


# --- review fixes (ctx_c8e7f9166642 findings) ---------------------------------------------

RUN2 = "33333333-3333-4333-8333-333333333333"


def redeclare(store, head, **kwargs):
    return store.declare_universe(
        TENANT, RUN, universe(**kwargs), expected_head=head, dry_run=False
    )["revision"]


def test_universe_shrink_is_refused_even_for_an_approved_exclusion(env):
    _, store, _ = env
    first = declare(store)["revision"]
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_UNIVERSE_SHRINK_REFUSED"):
        redeclare(store, first["revision_sha256"], topic_ids=("water",))
    approve(store, review(store)["revision_sha256"])
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_UNIVERSE_SHRINK_REFUSED"):
        redeclare(store, first["revision_sha256"], topic_ids=("waste", "ghg"))
    report = store.report(TENANT, RUN)
    assert report["topic_count"] == 3 and report["denominator_count"] == 2
    water = by_topic(report)["water"]
    assert water["state"] == "not_applicable_approved"  # still listed, explicitly excluded


def test_topic_redefinition_is_refused(env):
    _, store, _ = env
    first = declare(store)["revision"]
    relabeled = universe()
    relabeled["topics"][0] = dict(topic_id="water", label="Anything else")
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_TOPIC_REDEFINITION_REFUSED"):
        store.declare_universe(
            TENANT, RUN, relabeled, expected_head=first["revision_sha256"], dry_run=False
        )


def test_universe_expansion_keeps_old_topics_and_needs_fresh_reviews(env):
    _, store, _ = env
    first = declare(store)["revision"]
    approve(store, review(store)["revision_sha256"])
    second = redeclare(store, first["revision_sha256"], topic_ids=("water", "waste", "ghg", "air"))
    assert second["seq"] == 2 and second["prior_sha256"] == first["revision_sha256"]
    report = store.report(TENANT, RUN)
    assert [t["topic_id"] for t in report["topics"]] == ["water", "waste", "ghg", "air"]
    # Reviews pinned to the old universe no longer exclude anything: fail-safe.
    assert by_topic(report)["water"]["state"] == "stale_inputs"
    assert report["denominator_count"] == 4
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_HEAD_CONFLICT"):
        redeclare(store, first["revision_sha256"], topic_ids=("water", "waste", "ghg", "air", "x"))


def test_same_actor_approval_is_allowed_and_recorded_as_provenance(env):
    _, store, _ = env
    declare(store)
    same = approve(store, review(store)["revision_sha256"], approver=DELEGATE)
    provenance = same["approval_provenance"]
    assert provenance["same_actor_as_reviewer"] is True
    assert provenance["independent_or_human_approval_claimed"] is False
    assert provenance["role"] == "explicit_separate_approval_revision"
    water = by_topic(store.report(TENANT, RUN))["water"]
    assert water["state"] == "not_applicable_approved"
    assert water["approval_provenance"]["same_actor_as_reviewer"] is True
    other = approve(store, review(store, topic="waste")["revision_sha256"], topic="waste")
    assert other["approval_provenance"]["same_actor_as_reviewer"] is False


def _approved_chain(store):
    declare(store)
    first = review(store)
    return first, approve(store, first["revision_sha256"])


def test_topic_state_verifies_the_approved_review_not_just_its_position(env):
    _, store, _ = env
    first, approval = _approved_chain(store)
    pins = first["pins"]
    universe_sha = first["universe_sha256"]
    ok = topics.topic_state([first, approval], pins=pins, universe_sha256=universe_sha)
    assert ok["state"] == "not_applicable_approved"
    applicable = dict(first, decision="applicable")
    wrong_target = dict(approval, approves_revision_sha256="0" * 64)
    for chain in (
        [approval],
        [applicable, approval],
        [first, wrong_target],
        [dict(first, schema=topics.APPROVAL_SCHEMA), approval],
        [first, dict(approval, prior_sha256="0" * 64)],
    ):
        with pytest.raises(IndustryReviewRejected, match="INDUSTRY_APPROVAL_CHAIN_INVALID"):
            topics.topic_state(chain, pins=pins, universe_sha256=universe_sha)


@pytest.mark.parametrize("reason", ["", "too short", " padded reason for the topic review"])
def test_review_and_approval_need_a_substantive_reason(env, reason):
    _, store, _ = env
    declare(store)
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_REASON_REQUIRED"):
        review(store, reason=reason)


def test_no_minimum_quote_length_is_invented(env):
    """A verified exact quote is evidence of what the text says; no length score."""
    _, store, _ = env
    declare(store)
    assert review(store, refs=evidence(quote="only"))["decision"] == "not_applicable"


def test_crosswalk_change_makes_reviews_stale(env):
    _, store, current = env
    declare(store)
    approve(store, review(store)["revision_sha256"])
    current[TENANT] = run_state(crosswalk=dict(EMPTY_CROSSWALK, version="impl3"))
    report = store.report(TENANT, RUN)
    assert report["universe_status"] == "stale_inputs"
    assert report["topics"] == [] and report["excluded_not_applicable_count"] == 0


def test_records_do_not_cross_runs(env):
    _, store, current = env
    declare(store)
    approve(store, review(store)["revision_sha256"])
    current[TENANT] = run_state(run_id=RUN2)
    report = store.report(TENANT, RUN2)
    assert report["universe_status"] == "universe_undeclared"
    # A loader answering for another run is refused.
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_RUN_IDENTITY_MISMATCH"):
        store.report(TENANT, RUN)
