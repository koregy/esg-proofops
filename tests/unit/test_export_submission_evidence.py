"""Offline tests for scripts/export_submission_evidence.py (read-only evidence export).

Stores are built in tmp_path with the job_records / run_snapshots layout the
local run store writes. Values are test-local stand-ins, not real run counts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import export_submission_evidence as ese  # noqa: E402

RUN = "run-1"
TENANT = "tenant-1"
VERSION = "version-1"
PDF = b"%PDF-1.7 test bytes"
PDF_SHA = hashlib.sha256(PDF).hexdigest()
SECRET = "cursor-secret-must-not-leak"


def _dump(value) -> bytes:
    return json.dumps(value, sort_keys=True).encode()


class Store:
    def __init__(self, state: Path, *, status="partial", selected=(1, 2, 3)):
        self.state = state
        state.mkdir()
        self.db = sqlite3.connect(state / "state.sqlite3")
        self.db.executescript(
            "CREATE TABLE job_records(tenant_id, run_id, kind, record_id, value,"
            " PRIMARY KEY(tenant_id, run_id, kind, record_id));"
            "CREATE TABLE run_snapshots(tenant_id, run_id, payload);"
            "CREATE TABLE rulepack_revisions("
            "tenant_id, rule_pack_id, revision, record_json, files_json);"
            "CREATE TABLE run_cursor_key(secret);"
        )
        self.db.execute("INSERT INTO run_cursor_key VALUES (?)", (SECRET,))
        self.meta = dict(
            run_id=RUN,
            tenant_id=TENANT,
            document_version_id=VERSION,
            status=status,
            selected_pages=list(selected),
            scope="declared_subset",
            revision=4,
            mutation_epoch=2,
            rule_pack_id="rp",
            rule_pack_sha256="rp-sha",
            parser_profile_hash="pp-hash",
            model_binding_hash="mb-hash",
            execution_profile="local-synthetic-only",
            coverage=dict(complete=status == "completed"),
        )
        snapshot = dict(
            document=dict(
                sha256=PDF_SHA,
                page_count=3,
                local_synthetic=True,
                object_version_id="local-synthetic:x",
                metadata=dict(filename="r.pdf"),
            ),
            extraction_mode="upstage_probe",
            extraction_profile=dict(synthetic=False),
            input_hash="input-hash",
            extraction_profile_hash="ep-hash",
        )
        self.db.execute(
            "INSERT INTO run_snapshots VALUES (?,?,?)", (TENANT, RUN, json.dumps(snapshot))
        )
        self.rulepack("ai-delegated-review:codex-coordinator")
        (state / "pilot.json").write_text(
            json.dumps(
                dict(
                    run_id=RUN,
                    model="solar-pro3",
                    authorization=dict(
                        kind="grant", authorized_usd="10.00", ledger="C:/secret/ledger.sqlite3"
                    ),
                )
            )
        )
        original = state / "objects" / "original" / TENANT
        original.mkdir(parents=True)
        (original / f"{VERSION}.pdf").write_bytes(PDF)

    def rulepack(self, approved_by):
        self.db.execute("DELETE FROM rulepack_revisions")
        self.db.execute(
            "INSERT INTO rulepack_revisions VALUES (?,?,?,?,?)",
            (
                TENANT,
                "rp",
                1,
                json.dumps(dict(sha256="rp-sha", status="active", approved_by=approved_by)),
                "[]",
            ),
        )

    def put(self, kind, record_id, value):
        self.db.execute(
            "INSERT OR REPLACE INTO job_records VALUES (?,?,?,?,?)",
            (TENANT, RUN, kind, record_id, value if isinstance(value, bytes) else _dump(value)),
        )

    def job(self, stage, status="succeeded", envelope=None, error=None, tamper=False):
        job_id = f"job-{stage}"
        ref = None
        if envelope is not None:
            data = _dump(envelope)
            key = f"job/{job_id}/attempt/1/checkpoint"
            ref = dict(key=key, sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
            self.put("artifact", key, data + (b" " if tamper else b""))
        self.put(
            "job",
            job_id,
            dict(
                message=dict(stage=stage, shard="full"),
                status=status,
                attempt=1,
                error_code=error,
                artifact_ref=ref,
            ),
        )

    def claims(self, claims, tags=(), decisions=()):
        prepared = self.state / "parser-prepared" / TENANT / VERSION / "pm"
        prepared.mkdir(parents=True)
        (prepared / "manifest.json").write_bytes(b"{}")
        self.job(
            "parse",
            envelope=dict(
                parse_manifest_id="pm",
                coverage=dict(
                    pages_total=3, pages_processed=3, pages_unreadable=0, pages_unprocessed=0
                ),
            ),
        )
        self.job("extract", envelope=dict(discovery=dict(claims=claims, synthetic=False)))
        self.job("tag", envelope=dict(tagging_mode="live"))
        heads = {}
        for claim_id, tag in tags:
            heads.setdefault(claim_id, dict(tag_revision=0, decision_revision=0))[
                "tag_revision"
            ] = 1
            self.put("tag_revision", f"{claim_id}:{1:010d}", dict(tag_revision=1, **tag))
        for claim_id, decision in decisions:
            heads.setdefault(claim_id, dict(tag_revision=1, decision_revision=0))[
                "decision_revision"
            ] = 1
            self.put(
                "decision_revision",
                f"{claim_id}:{1:010d}",
                dict(decision_revision=1, decision=decision),
            )
        for claim_id, head in heads.items():
            self.put("claim_head", claim_id, head)
        self.put("usage", "1:job-tag", dict(model_calls=4))

    def close(self):
        self.put("run", "META", self.meta)
        self.db.commit()
        self.db.close()
        return self.state


def _decision(status, grade, review="auto_confirmed"):
    return dict(
        decision_status=status, evidence_grade=grade, review_status=review, local_synthetic=False
    )


def _tag(origin, *states, reviewer=None):
    return dict(
        origin=origin,
        reviewer_sub=reviewer,
        inputs={"secret": "large"},
        elements=[dict(element_id=f"P{i}", state=s) for i, s in enumerate(states)],
    )


def _run(state, tmp_path, *extra):
    out = tmp_path / "out"
    rc = ese.main(["--state", str(state), "--out-dir", str(out), *extra])
    return rc, out


def test_partial_run_keeps_levels_null_grades_and_review_origins_apart(tmp_path):
    store = Store(tmp_path / "state")
    store.claims(
        claims=[
            dict(claim_id="a", source_quality="verified"),
            dict(claim_id="b", source_quality="verified"),
            dict(claim_id="c", source_quality="unverified"),
        ],
        tags=[
            ("a", _tag("human", "present")),
            ("b", _tag("consensus", "unknown", "conflict")),
            ("c", _tag("ai_delegated", "absent", reviewer="ai-delegated-review:op")),
        ],
        decisions=[
            ("a", _decision("decided", "E0", "human_confirmed")),
            ("b", _decision("blocked_evidence", None)),
        ],
    )
    state = store.close()
    before = hashlib.sha256((state / "state.sqlite3").read_bytes()).hexdigest()

    rc, out = _run(state, tmp_path)

    assert rc == 0
    assert hashlib.sha256((state / "state.sqlite3").read_bytes()).hexdigest() == before
    export = json.loads((out / "evidence-export.json").read_text("utf-8"))
    cov = export["coverage"]
    assert (cov["selected_pages"], cov["parsed"]["pages_processed"]) == (3, 3)
    assert cov["claims_extracted"] == 3 and cov["claims_source_verified"] == 2
    assert cov["claims_tagged"] == 3 and cov["claims_with_decision"] == 2
    assert cov["claims_graded"] == 1
    assert cov["grade_counts"] == {"E0": 1, "E1": 0, "E2": 0, "E3": 0}
    assert cov["grade_null_or_undecided"] == 2  # unknown/null is never folded into E0
    assert cov["decision_status"] == {"blocked_evidence": 1, "decided": 1, "no_decision": 1}
    assert cov["tag_element_states"] == {"absent": 1, "conflict": 1, "present": 1, "unknown": 1}
    review = export["review"]
    assert review["rulepack"]["approval_class"] == "ai_delegated"
    assert review["tag_origin"] == {"ai_delegated": 1, "consensus": 1, "human": 1}
    assert review["tag_ai_delegated_reviewers"] == {"ai-delegated-review:op": 1}
    assert (review["decisions_human_confirmed"], review["decisions_ai_delegated_confirmed"]) == (
        1,
        0,
    )
    assert export["provenance"]["usage_totals"] == {"model_calls": 4}
    assert export["provenance"]["extraction_profile_synthetic"] is False
    assert export["provenance"]["document_local_synthetic_storage"] is True
    assert export["label"] == export["derived_status"] == "partial_or_failed"
    assert all(j["artifact"]["digest_verified"] for s in export["stages"].values() for j in s)
    assert export["source"]["stored_object_verified"] is True
    assert export["hashes"]["snapshot.input_hash"] == "input-hash"
    assert export["pilot"]["authorization"] == dict(
        kind="grant",
        authorized_usd="10.00",
        amount_basis=None,
        authorized_at=None,
        expires_at=None,
        scope=None,
    )
    text = "".join(p.read_text("utf-8") for p in out.iterdir())
    for leaked in (SECRET, "C:/secret/ledger.sqlite3", '"large"'):
        assert leaked not in text
    sums = (out / "SHA256SUMS").read_text().split()
    assert sums[0] == hashlib.sha256((out / "evidence-export.json").read_bytes()).hexdigest()


def test_complete_label_requires_completed_run_and_every_claim_graded(tmp_path):
    store = Store(tmp_path / "state", status="completed")
    store.claims(
        claims=[dict(claim_id="a", source_quality="verified")],
        tags=[("a", _tag("consensus", "present"))],
        decisions=[("a", _decision("decided", "E2"))],
    )
    rc, out = _run(store.close(), tmp_path)
    export = json.loads((out / "evidence-export.json").read_text("utf-8"))
    assert rc == 0 and export["derived_status"] == "complete_per_store"
    assert export["missing_inputs"] == []
    assert export["parser_prepared_files"] == [
        dict(name="manifest.json", sha256=hashlib.sha256(b"{}").hexdigest(), bytes=2)
    ]


def test_failed_parse_is_a_stage_failure_with_null_counts_not_zero(tmp_path):
    store = Store(tmp_path / "state", status="failed", selected=range(1, 135))
    store.job("parse", status="failed", error="PARSER_TIMEOUT")
    rc, out = _run(store.close(), tmp_path, "--label", "intermediate")
    export = json.loads((out / "evidence-export.json").read_text("utf-8"))
    cov = export["coverage"]
    assert rc == 0 and export["label"] == "intermediate"
    assert export["derived_status"] == "partial_or_failed"
    assert cov["selected_pages"] == 134 and cov["parsed"] is None
    assert cov["claims_extracted"] is None and cov["grade_counts"] is None
    assert any("not parsed or complete" in n for n in cov["notes"])
    assert export["stage_failures"] == [
        dict(stage="parse", job_id="job-parse", status="failed", error_code="PARSER_TIMEOUT")
    ]
    assert export["missing_inputs"] == ["extract_job", "tag_job"]
    assert "PARSER_TIMEOUT" in (out / "evidence-export.md").read_text("utf-8")


def test_tampered_checkpoint_is_not_trusted(tmp_path):
    store = Store(tmp_path / "state")
    store.claims(claims=[dict(claim_id="a", source_quality="verified")])
    store.job("extract", envelope=dict(discovery=dict(claims=[dict(claim_id="a")])), tamper=True)
    rc, out = _run(store.close(), tmp_path)
    export = json.loads((out / "evidence-export.json").read_text("utf-8"))
    assert export["stages"]["extract"][0]["artifact"]["digest_verified"] is False
    assert "extract_checkpoint_verified" in export["missing_inputs"]
    assert export["coverage"]["claims_extracted"] is None


def test_source_url_only_from_matching_receipt_and_human_not_assumed(tmp_path):
    store = Store(tmp_path / "state")
    store.rulepack("local-browser-fixture")
    state = store.close()
    good = tmp_path / "good.json"
    good.write_text(json.dumps(dict(url="https://example.test/r.pdf", sha256=PDF_SHA)))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(dict(url="https://example.test/other.pdf", sha256="0" * 64)))
    matched = ese.build_export(state, source_receipt=good)
    withheld = ese.build_export(state, source_receipt=bad)
    assert matched["source"]["url"] == "https://example.test/r.pdf"
    assert withheld["source"]["url"] is None
    assert withheld["source"]["url_status"] == "receipt_sha256_mismatch_url_withheld"
    assert matched["review"]["rulepack"]["approval_class"] == "recorded_non_ai_subject"
    assert matched["missing_inputs"] == ["parse_job", "extract_job", "tag_job"]


def test_refuses_existing_out_dir_and_missing_state(tmp_path, capsys):
    state = Store(tmp_path / "state").close()
    (tmp_path / "out").mkdir()
    with pytest.raises(SystemExit):
        _run(state, tmp_path)
    assert list((tmp_path / "out").iterdir()) == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ese.main(["--state", str(empty), "--out-dir", str(tmp_path / "o2")]) == 2
    assert not (tmp_path / "o2").exists()
