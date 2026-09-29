"""Industry topic review over a REAL parsed local run (no loader stub).

The actual ``run_loader`` is called twice: its pins (graph hash, rulepack crosswalk,
industry identity) must be stable, a stored review must replay, and a mutated
original must be refused. Local parser only; no model, network or paid call.
"""

from __future__ import annotations

from hashlib import sha256

import pytest
from proofops.adapters.local.industry_topic_store import LocalIndustryTopicStore, run_loader
from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
from proofops.application import industry_topic_review as topics
from proofops.application.industry_topic_review import IndustryReviewRejected

from tests.acceptance.test_parsing import TENANT
from tests.integration.test_local_parser_runner import runner_setup

NOW = "2026-09-29T09:00:00+00:00"
ACTOR = dict(kind="ai_delegated", id="industry-reviewer", authority="root dispatch delegation")


def _universe():
    return dict(
        schema=topics.UNIVERSE_SCHEMA,
        scope="operator_declared_project_topics",
        official_mapping=False,
        topics=[dict(topic_id="water", label="water topic"), dict(topic_id="ghg", label="ghg")],
        declared_by=ACTOR,
        declared_at=NOW,
        reason="Operator-declared project topic list for this review",
    )


def test_real_loader_pins_are_stable_and_reviews_replay(tmp_path, monkeypatch):
    service, run_id, runner, _, _ = runner_setup(tmp_path, monkeypatch)
    assert runner.run_once(tenant_id=TENANT, run_id=run_id) == "committed"
    loader = run_loader(service.store, service.uploads, OpenDataLoaderParser(tmp_path / "prepared"))
    first, second = loader(TENANT, run_id), loader(TENANT, run_id)
    assert first["pins"] == second["pins"]
    # The pinned crosswalk is absent from this fixture pack or empty; never filled in.
    assert first["crosswalk"] is None or first["crosswalk"]["mappings"] == []
    assert first["pins"]["industry"]["system"] in ("gics", "sasb", "custom", "unknown")

    store = LocalIndustryTopicStore(service.store.jobs, loader)
    store.declare_universe(TENANT, run_id, _universe(), expected_head=None, dry_run=False)
    # Parser-only blocks are not source-verified: a decision needing evidence is refused.
    block = next(b for b in first["graph"].blocks if b.raw_text)
    spec = dict(source_id=block.source_id, char_start=0, char_end=4, quote=block.raw_text[:4])
    with pytest.raises(IndustryReviewRejected, match="INDUSTRY_EVIDENCE_NOT_VERIFIED"):
        store.review(
            TENANT,
            run_id,
            topic_id="water",
            decision="not_applicable",
            reason="Company activity evidence reviewed for this topic",
            evidence=[spec],
            reviewer=ACTOR,
            reviewed_at=NOW,
            expected_head=None,
            dry_run=False,
        )
    store.review(
        TENANT,
        run_id,
        topic_id="water",
        decision="undetermined",
        reason="No verified company activity source is available yet",
        evidence=[],
        reviewer=ACTOR,
        reviewed_at=NOW,
        expected_head=None,
        dry_run=False,
    )
    report_a, report_b = store.report(TENANT, run_id), store.report(TENANT, run_id)
    assert report_a == report_b  # deterministic replay through the real loader twice
    states = {t["topic_id"]: t["state"] for t in report_a["topics"]}
    assert states == {"water": "undetermined", "ghg": "unreviewed"}
    assert report_a["denominator_count"] == 2
    assert all(
        t["candidate"]["candidate_applicability"] == "undetermined" for t in report_a["topics"]
    )

    # Mutate the stored original: the real loader refuses before anything is read.
    original = service.uploads.read_original(TENANT, first["pins"]["document_version_id"])
    stored = [
        path
        for path in tmp_path.rglob("*")
        if path.is_file() and path.stat().st_size == len(original) and path.read_bytes() == original
    ]
    assert stored, "stored original not found"
    for path in stored:
        path.chmod(0o644)
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    assert sha256(stored[0].read_bytes()).hexdigest() != first["pins"]["source_sha256"]
    with pytest.raises(Exception, match="MISMATCH|INTEGRITY"):
        store.report(TENANT, run_id)
