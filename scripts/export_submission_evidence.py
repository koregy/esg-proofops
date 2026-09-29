"""Read-only submission evidence export for ONE local pilot run state directory.

Summarizes what a run store actually holds, for PPT or code review, without
re-running anything:

* selected / parsed / claimed / source-verified / tagged / graded counts, each
  kept separate (a selected page is not a parsed page; a claim is not a grade);
* unknown / null grades are counted apart from ``E0``;
* per-stage job status and error codes (a failed or partial stage stays failed);
* live vs stored / synthetic markers exactly as the store recorded them;
* AI-delegated vs human review origins (rule pack, tags, decisions,
  classifications). A missing marker is reported as ``unrecorded``, not human;
* source URL (only when a receipt's sha256 matches the stored document),
  source hash and page count, run / revision / config hashes, and re-computed
  digests of immutable checkpoint artifacts and prepared parser files.

Safety: the state DB is opened with ``mode=ro`` and copied into memory with
the SQLite backup API, so a concurrently running pilot is never blocked or
written. No product store, service, app, network or model client is
constructed. Only allowlisted tables and keys are read; cursor HMAC keys,
upload tickets, export tickets, sessions, ``browser.json`` and budget ledgers
are never read. Output goes to a NEW ``--out-dir`` (refused if it exists).

Usage:

    uv run python scripts/export_submission_evidence.py \
        --state .local/k10 --out-dir .local/submission-20260929/evidence-export-k10 \
        --source-receipt .local/submission-20260929/kia-real/sources/sr-download-receipt.json \
        --label intermediate
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from proofops.application.reviews import (  # noqa: E402
    AI_DELEGATED_ORIGIN,
    AI_DELEGATED_REVIEW_STATUS,
    AI_DELEGATED_REVIEWER_PREFIX,
    HUMAN_ORIGIN,
    HUMAN_REVIEW_STATUS,
)
from proofops.application.tagging.manual_classification import (  # noqa: E402
    AI_DELEGATED_ORIGIN as AI_DELEGATED_CLASSIFICATION,
)
from submission_demo import _coverage_from_inspection  # noqa: E402

SCHEMA = "submission-evidence-export-v1"
GRADES = ("E0", "E1", "E2", "E3")
STAGES = ("parse", "extract", "tag")
# pilot.json keys safe to echo; authorization is reduced to its non-path fields.
_PILOT_KEYS = (
    "run_id",
    "document_version_id",
    "company_legal_name",
    "company_registration_identifier",
    "report_year",
    "period_start",
    "period_end",
    "claim_pages",
    "model",
    "live_tagging",
    "live_relations",
    "rulepack_approval",
    "production_ready",
    "extraction_batch_calls",
    "extraction_total_calls",
    "tagging_max_calls",
)
_AUTH_KEYS = ("kind", "authorized_usd", "amount_basis", "authorized_at", "expires_at", "scope")
_SETTINGS_BINDINGS = ("tagging_settings", "preliminary_settings", "relation_settings")


class ExportRefused(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_db(database: Path, *, attempts: int = 20, delay: float = 0.25) -> sqlite3.Connection:
    """Consistent in-memory copy of a live DB; never takes a write lock on it."""
    uri = database.resolve().as_uri() + "?mode=ro"
    last: Exception | None = None
    for _ in range(attempts):
        source = None
        try:
            source = sqlite3.connect(uri, uri=True, timeout=5)
            memory = sqlite3.connect(":memory:")
            source.backup(memory)
            return memory
        except sqlite3.OperationalError as exc:  # writer mid-commit / hot journal
            last = exc
            time.sleep(delay)
        finally:
            if source is not None:
                source.close()
    raise ExportRefused(f"state DB stayed busy; nothing exported ({last})")


def _loads(value):
    if value is None:
        return None
    return json.loads(value if isinstance(value, str) else bytes(value).decode("utf-8"))


def _tables(db) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _records(db, kind: str, run_id: str) -> list[tuple[str, object]]:
    rows = db.execute(
        "SELECT record_id, value FROM job_records WHERE kind=? AND run_id=? ORDER BY record_id",
        (kind, run_id),
    )
    return [(rid, value) for rid, value in rows]


def _approver_class(approved_by: str | None) -> str:
    if not approved_by:
        return "unapproved"
    if approved_by.startswith(AI_DELEGATED_REVIEWER_PREFIX):
        return "ai_delegated"
    # The store does not prove a person; report the recorded subject only.
    return "recorded_non_ai_subject"


def _stage_jobs(db, run_id: str) -> tuple[dict, dict]:
    stages: dict[str, list] = {s: [] for s in STAGES}
    envelopes: dict[str, dict] = {}
    for job_id, raw in _records(db, "job", run_id):
        job = _loads(raw)
        stage = job.get("message", {}).get("stage", "unknown")
        ref = job.get("artifact_ref") or None
        entry = dict(
            job_id=job_id,
            shard=job.get("message", {}).get("shard"),
            status=job.get("status"),
            attempt=job.get("attempt"),
            error_code=job.get("error_code"),
            artifact=None,
        )
        if ref:
            row = db.execute(
                "SELECT value FROM job_records WHERE kind='artifact' AND run_id=? AND record_id=?",
                (run_id, ref.get("key")),
            ).fetchone()
            data = (
                None if row is None else (row[0] if isinstance(row[0], bytes) else row[0].encode())
            )
            actual = None if data is None else hashlib.sha256(data).hexdigest()
            entry["artifact"] = dict(
                key=ref.get("key"),
                recorded_sha256=ref.get("sha256"),
                recomputed_sha256=actual,
                byte_size=ref.get("byte_size"),
                digest_verified=actual is not None and actual == ref.get("sha256"),
            )
            if (
                data is not None
                and entry["artifact"]["digest_verified"]
                and job.get("status") == "succeeded"
            ):
                envelopes[stage] = json.loads(data)
        stages.setdefault(stage, []).append(entry)
    return stages, envelopes


def _counter(values) -> dict:
    return dict(
        sorted(collections.Counter("null" if v is None else str(v) for v in values).items())
    )


def build_export(
    state: Path, *, source_receipt: Path | None = None, label: str | None = None
) -> dict:
    database = state / "state.sqlite3"
    if not database.is_file():
        raise ExportRefused(f"no state.sqlite3 in {state}")
    pilot = (
        _loads((state / "pilot.json").read_text("utf-8"))
        if (state / "pilot.json").is_file()
        else None
    )
    db = snapshot_db(database)
    try:
        return _build(db, state, pilot, source_receipt, label)
    finally:
        db.close()


def _build(
    db, state: Path, pilot: dict | None, source_receipt: Path | None, label: str | None
) -> dict:
    missing: list[str] = []
    tables = _tables(db)
    if "job_records" not in tables:
        raise ExportRefused("state DB has no job_records table; not a run store")
    runs = db.execute("SELECT run_id, value FROM job_records WHERE kind='run' AND record_id='META'")
    runs = [(rid, _loads(v)) for rid, v in runs]
    wanted = (pilot or {}).get("run_id")
    if wanted:
        runs = [r for r in runs if r[0] == wanted]
    if len(runs) != 1:
        raise ExportRefused(f"expected exactly one run (pilot run_id={wanted}); found {len(runs)}")
    run_id, meta = runs[0]

    snapshot = None
    if "run_snapshots" in tables:
        row = db.execute("SELECT payload FROM run_snapshots WHERE run_id=?", (run_id,)).fetchone()
        snapshot = _loads(row[0]) if row else None
    if snapshot is None:
        missing.append("run_snapshots")
    document = (snapshot or {}).get("document") or {}

    stages, envelopes = _stage_jobs(db, run_id)
    for stage in STAGES:
        if not stages.get(stage):
            missing.append(f"{stage}_job")
        elif stage not in envelopes and any(j["status"] == "succeeded" for j in stages[stage]):
            # A failed job without output is a stage failure (reported below);
            # a succeeded job whose checkpoint is absent or re-hashes wrong is missing input.
            missing.append(f"{stage}_checkpoint_verified")

    # --- coverage, each level kept separate -----------------------------------
    selected = meta.get("selected_pages") or []
    parse_cov = (envelopes.get("parse") or {}).get("coverage")
    discovery = (envelopes.get("extract") or {}).get("discovery") or {}
    claims = discovery.get("claims") if envelopes.get("extract") else None
    claim_ids = [c.get("claim_id") for c in claims or []]
    heads = {rid: _loads(v) for rid, v in _records(db, "claim_head", run_id)}
    tag_revs = {rid: _loads(v) for rid, v in _records(db, "tag_revision", run_id)}
    dec_revs = {rid: _loads(v) for rid, v in _records(db, "decision_revision", run_id)}

    current_tags, current_decisions = {}, {}
    for claim_id in claim_ids:
        head = heads.get(claim_id) or {}
        if head.get("tag_revision"):
            current_tags[claim_id] = tag_revs.get(f"{claim_id}:{head['tag_revision']:010d}")
        if head.get("decision_revision"):
            record = dec_revs.get(f"{claim_id}:{head['decision_revision']:010d}") or {}
            current_decisions[claim_id] = record.get("decision")
    decisions = [current_decisions.get(c) for c in claim_ids]
    graded = [
        d
        for d in decisions
        if d and d.get("decision_status") == "decided" and d.get("evidence_grade") in GRADES
    ]
    grade_counts = {g: sum(d.get("evidence_grade") == g for d in graded) for g in GRADES}
    coverage = dict(
        document_page_count=document.get("page_count"),
        selected_pages=len(selected),
        selected_scope=meta.get("scope"),
        parsed=None
        if parse_cov is None
        else dict(
            pages_total=parse_cov.get("pages_total"),
            pages_processed=parse_cov.get("pages_processed"),
            pages_unreadable=parse_cov.get("pages_unreadable"),
            pages_unprocessed=parse_cov.get("pages_unprocessed"),
        ),
        claims_extracted=None if claims is None else len(claim_ids),
        claims_source_verified=None
        if claims is None
        else sum(c.get("source_quality") == "verified" for c in claims),
        claims_source_quality=None
        if claims is None
        else _counter(c.get("source_quality") for c in claims),
        claims_tagged=None if claims is None else sum(t is not None for t in current_tags.values()),
        claims_with_decision=None if claims is None else sum(d is not None for d in decisions),
        claims_graded=None if claims is None else len(graded),
        grade_counts=None if claims is None else grade_counts,
        grade_null_or_undecided=None if claims is None else len(claim_ids) - len(graded),
        decision_status=None
        if claims is None
        else _counter((d or {}).get("decision_status", "no_decision") for d in decisions),
        tag_element_states=None
        if claims is None
        else _counter(
            e.get("state") for t in current_tags.values() if t for e in t.get("elements") or []
        ),
        run_recorded_coverage=meta.get("coverage"),
    )
    notes = []
    if parse_cov is None:
        notes.append(
            f"{len(selected)} page(s) were selected but no verified parse output exists; "
            "selection is not parsed or complete coverage"
        )
    if claims is None:
        notes.append("extract checkpoint absent or unverified: claim-level counts are null, not 0")
    coverage["notes"] = notes

    # --- live vs stored / synthetic ---------------------------------------------
    usage = [_loads(v) for _, v in _records(db, "usage", run_id)]
    settings_path = state / "settings.json"
    settings = _loads(settings_path.read_text("utf-8")) if settings_path.is_file() else None
    provenance = dict(
        extraction_mode=(snapshot or {}).get("extraction_mode"),
        extraction_profile_synthetic=((snapshot or {}).get("extraction_profile") or {}).get(
            "synthetic"
        ),
        extract_checkpoint_synthetic=discovery.get("synthetic")
        if envelopes.get("extract")
        else None,
        tagging_mode=(envelopes.get("tag") or {}).get("tagging_mode"),
        document_local_synthetic_storage=document.get("local_synthetic"),
        object_version_id=document.get("object_version_id"),
        execution_profile=meta.get("execution_profile"),
        binding_synthetic={
            key: ((settings or {}).get(key) or {}).get("binding", {}).get("synthetic")
            for key in _SETTINGS_BINDINGS
        },
        binding_model_ids={
            key: ((settings or {}).get(key) or {}).get("model_id") for key in _SETTINGS_BINDINGS
        },
        usage_totals={
            key: sum(int(u.get(key) or 0) for u in usage)
            for key in sorted({k for u in usage for k in u if k != "artifact_reused"})
        },
        decision_local_synthetic=_counter(d.get("local_synthetic") for d in decisions if d),
        note="Fields are reported as stored. model_calls counts store-recorded live calls; "
        "local_synthetic storage flags describe object storage, not model output.",
    )

    # --- AI-delegated vs human review -------------------------------------------
    rulepack = None
    if "rulepack_revisions" in tables and meta.get("rule_pack_id"):
        rows = db.execute(
            "SELECT revision, record_json FROM rulepack_revisions"
            " WHERE rule_pack_id=? ORDER BY revision",
            (meta["rule_pack_id"],),
        ).fetchall()
        matched = [
            _loads(r) for _, r in rows if _loads(r).get("sha256") == meta.get("rule_pack_sha256")
        ]
        record = matched[-1] if matched else {}
        rulepack = dict(
            rule_pack_id=meta.get("rule_pack_id"),
            sha256=meta.get("rule_pack_sha256"),
            status=record.get("status"),
            approved_by=record.get("approved_by"),
            approved_at=record.get("approved_at"),
            approval_class=_approver_class(record.get("approved_by")),
        )
    reviews = [_loads(v) for _, v in _records(db, "review_head", run_id)]
    classifications = [_loads(v) for _, v in _records(db, "preliminary_classification", run_id)]
    known_tag_origins = {AI_DELEGATED_ORIGIN, HUMAN_ORIGIN, "consensus"}
    review = dict(
        rulepack=rulepack,
        tag_origin=_counter(
            (t or {}).get("origin")
            if (t or {}).get("origin") in known_tag_origins
            else f"unrecorded:{(t or {}).get('origin')}"
            for t in current_tags.values()
        ),
        tag_ai_delegated_reviewers=_counter(
            t.get("reviewer_sub")
            for t in current_tags.values()
            if t and t.get("origin") == AI_DELEGATED_ORIGIN
        ),
        decision_review_status=_counter((d or {}).get("review_status") for d in decisions if d),
        decisions_ai_delegated_confirmed=sum(
            (d or {}).get("review_status") == AI_DELEGATED_REVIEW_STATUS for d in decisions
        ),
        decisions_human_confirmed=sum(
            (d or {}).get("review_status") == HUMAN_REVIEW_STATUS for d in decisions
        ),
        review_items=_counter(r.get("status") for r in reviews),
        preliminary_classification_origin=_counter(c.get("origin") for c in classifications),
        preliminary_classification_ai_delegated=sum(
            c.get("origin") == AI_DELEGATED_CLASSIFICATION for c in classifications
        ),
    )

    # --- source identity ----------------------------------------------------------
    source = dict(
        sha256=document.get("sha256"),
        page_count=document.get("page_count"),
        filename=(document.get("metadata") or {}).get("filename"),
        size_bytes=(document.get("metadata") or {}).get("size_bytes"),
        stored_object_sha256=None,
        stored_object_verified=None,
        url=None,
        url_status="no_receipt",
    )
    tenant, version = meta.get("tenant_id"), meta.get("document_version_id")
    original = state / "objects" / "original" / str(tenant) / f"{version}.pdf"
    if original.is_file():
        source["stored_object_sha256"] = sha256_file(original)
        source["stored_object_verified"] = source["stored_object_sha256"] == source["sha256"]
    else:
        missing.append("original_object")
    if source_receipt is not None:
        receipt = _loads(source_receipt.read_text("utf-8"))
        if receipt.get("sha256") and receipt.get("sha256") == source["sha256"]:
            source.update(
                url=receipt.get("url"),
                landing_page=receipt.get("landing_page"),
                retrieved_on=receipt.get("retrieved_on"),
                receipt_sha256=sha256_file(source_receipt),
                url_status="receipt_sha256_matches_document",
            )
        else:
            source["url_status"] = "receipt_sha256_mismatch_url_withheld"

    # --- hashes and immutable digests -------------------------------------------
    hashes = {
        key: meta.get(key)
        for key in (
            "revision",
            "mutation_epoch",
            "rule_pack_sha256",
            "parser_profile_hash",
            "model_binding_hash",
            "claim_snapshot_sha256",
            "tag_snapshot_sha256",
        )
        if key in meta
    }
    for key, value in sorted((snapshot or {}).items()):
        if isinstance(value, str) and (key.endswith("_hash") or key.endswith("_sha256")):
            hashes[f"snapshot.{key}"] = value
    if snapshot is not None:
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        hashes["run_snapshot_canonical_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    config_files = {
        name: sha256_file(state / name)
        for name in ("pilot.json", "parser.json", "settings.json")
        if (state / name).is_file()
    }
    prepared = []
    manifest_id = (envelopes.get("parse") or {}).get("parse_manifest_id")
    prepared_root = state / "parser-prepared" / str(tenant) / str(version)
    if manifest_id and (prepared_root / manifest_id).is_dir():
        for path in sorted((prepared_root / manifest_id).iterdir()):
            if path.is_file():
                prepared.append(
                    dict(name=path.name, sha256=sha256_file(path), bytes=path.stat().st_size)
                )
    elif manifest_id:
        missing.append("parser_prepared_manifest_dir")

    # --- pilot-reported inspections (first claims page only) -----------------------
    inspections = []
    for path in sorted(state.glob("inspection-*.json")):
        try:
            data = _loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        inspections.append(
            dict(
                file=path.name,
                sha256=sha256_file(path),
                pipeline_outcome=data.get("pipeline_outcome"),
                claims_http_status=data.get("claims_http_status"),
                first_page_coverage=_coverage_from_inspection(data) or None,
            )
        )

    failed = [
        dict(stage=stage, job_id=j["job_id"], status=j["status"], error_code=j["error_code"])
        for stage, jobs in stages.items()
        for j in jobs
        if j["status"] != "succeeded"
    ]
    complete = (
        meta.get("status") == "completed"
        and (meta.get("coverage") or {}).get("complete") is True
        and not failed
        and not missing
        and coverage["claims_graded"] is not None
        and coverage["claims_graded"] == coverage["claims_extracted"]
    )
    derived = "complete_per_store" if complete else "partial_or_failed"
    return dict(
        schema=SCHEMA,
        label=label or derived,
        derived_status=derived,
        state_dir=state.name,
        run=dict(
            run_id=run_id,
            tenant_id=tenant,
            document_version_id=version,
            status=meta.get("status"),
            current_stage=meta.get("current_stage"),
            tag_stage_status=meta.get("tag_stage_status"),
            cancellation_requested=meta.get("cancellation_requested"),
        ),
        pilot={k: (pilot or {}).get(k) for k in _PILOT_KEYS}
        | dict(
            authorization={k: ((pilot or {}).get("authorization") or {}).get(k) for k in _AUTH_KEYS}
            if isinstance((pilot or {}).get("authorization"), dict)
            else None
        )
        if pilot
        else None,
        stages=stages,
        stage_failures=failed,
        coverage=coverage,
        provenance=provenance,
        review=review,
        source=source,
        hashes=hashes,
        config_file_sha256=config_files,
        parser_prepared_files=prepared,
        pilot_inspections=inspections,
        missing_inputs=missing,
        excluded=[
            "run_cursor_key",
            "catalog_cursor_key",
            "upload tickets",
            "export tickets",
            "sessions/cookies/CSRF",
            "API keys",
            "browser.json",
            "budget ledger DB",
            "tag revision inputs",
            "raw model responses",
        ],
    )


def _fmt(value) -> str:
    return "null" if value is None else str(value)


def render_markdown(export: dict) -> str:
    cov, prov, rev, src = (
        export["coverage"],
        export["provenance"],
        export["review"],
        export["source"],
    )
    lines = [
        f"# Submission evidence export — {export['state_dir']}",
        "",
        f"**Label: `{export['label']}`** (derived from store: `{export['derived_status']}`). "
        "Generated read-only from the run store; counts are store facts, not model accuracy.",
        "",
        "## Run",
        f"- run `{export['run']['run_id']}` status `{_fmt(export['run']['status'])}`, "
        f"current stage `{_fmt(export['run']['current_stage'])}`",
    ]
    for stage, jobs in export["stages"].items():
        if not jobs:
            lines.append(f"- {stage}: **no job** (not reached)")
        for job in jobs:
            art = job["artifact"]
            digest = (
                "no artifact"
                if not art
                else (f"artifact `{art['recorded_sha256']}` verified={art['digest_verified']}")
            )
            lines.append(
                f"- {stage}: `{job['status']}` attempt {job['attempt']} "
                f"error `{_fmt(job['error_code'])}`; {digest}"
            )
    lines += [
        "",
        "## Coverage (each level separate; null = not produced, never 0)",
        "| level | value |",
        "|---|---|",
        f"| document pages | {_fmt(cov['document_page_count'])} |",
        f"| selected pages ({_fmt(cov['selected_scope'])}) | {cov['selected_pages']} |",
        f"| parsed pages processed / unreadable | "
        f"{_fmt((cov['parsed'] or {}).get('pages_processed'))} / "
        f"{_fmt((cov['parsed'] or {}).get('pages_unreadable'))} |",
        f"| claims extracted | {_fmt(cov['claims_extracted'])} |",
        f"| claims source-verified | {_fmt(cov['claims_source_verified'])} |",
        f"| claims tagged | {_fmt(cov['claims_tagged'])} |",
        f"| claims with a stored decision | {_fmt(cov['claims_with_decision'])} |",
        f"| claims graded (E0–E3) | {_fmt(cov['claims_graded'])} |",
        f"| grade counts | {_fmt(cov['grade_counts'])} |",
        f"| null grade / undecided (not E0) | {_fmt(cov['grade_null_or_undecided'])} |",
        f"| decision status | {_fmt(cov['decision_status'])} |",
        f"| tag element states | {_fmt(cov['tag_element_states'])} |",
        "",
        *[f"> {note}" for note in cov["notes"]],
        "",
        "## Live vs stored / synthetic",
        f"- extraction mode `{_fmt(prov['extraction_mode'])}`, profile synthetic "
        f"`{_fmt(prov['extraction_profile_synthetic'])}`, checkpoint synthetic "
        f"`{_fmt(prov['extract_checkpoint_synthetic'])}`, "
        f"tagging mode `{_fmt(prov['tagging_mode'])}`",
        f"- binding synthetic {prov['binding_synthetic']}; models {prov['binding_model_ids']}",
        f"- store usage totals {prov['usage_totals']}",
        "- document object storage local_synthetic="
        f"`{_fmt(prov['document_local_synthetic_storage'])}` "
        f"(`{_fmt(prov['object_version_id'])}`); this is storage, not model output",
        "",
        "## Review origin",
        f"- rule pack: {_fmt(rev['rulepack'])}",
        f"- tag origin {rev['tag_origin']}; "
        f"AI-delegated reviewers {rev['tag_ai_delegated_reviewers']}",
        f"- decision review status {rev['decision_review_status']} (AI-delegated "
        f"{rev['decisions_ai_delegated_confirmed']}, human {rev['decisions_human_confirmed']})",
        f"- review items {rev['review_items']}; "
        f"preliminary classification {rev['preliminary_classification_origin']}",
        "",
        "## Source",
        f"- sha256 `{_fmt(src['sha256'])}`, pages {_fmt(src['page_count'])}, "
        f"bytes {_fmt(src['size_bytes'])}",
        f"- stored object re-hash verified: `{_fmt(src['stored_object_verified'])}`",
        f"- URL ({src['url_status']}): {_fmt(src['url'])}",
        "",
        "## Hashes",
    ]
    lines += [f"- {k}: `{v}`" for k, v in export["hashes"].items()]
    lines += [f"- file {k}: `{v}`" for k, v in export["config_file_sha256"].items()]
    lines += [
        f"- parser-prepared {p['name']}: `{p['sha256']}`" for p in export["parser_prepared_files"]
    ]
    if export["pilot_inspections"]:
        lines += ["", "## Pilot inspection files (first claims page only, not report totals)"]
        lines += [
            f"- {i['file']}: outcome {i['pipeline_outcome']}, page {i['first_page_coverage']}"
            for i in export["pilot_inspections"]
        ]
    lines += [
        "",
        "## Missing inputs",
        f"- {export['missing_inputs'] or 'none'}",
        "",
        "## Excluded by design",
        f"- {', '.join(export['excluded'])}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--state", type=Path, required=True, help="Pilot state directory (read-only)"
    )
    parser.add_argument("--out-dir", type=Path, required=True, help="NEW directory to create")
    parser.add_argument("--source-receipt", type=Path, help="Download receipt JSON (url + sha256)")
    parser.add_argument(
        "--label",
        choices=("intermediate", "final-candidate"),
        help="Override label; default is derived from the store",
    )
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    state = args.state.resolve()
    if out.exists():
        parser.error(f"--out-dir already exists; refusing to overwrite: {out}")
    if out == state or state in out.parents:
        parser.error("--out-dir must be outside the state directory")
    try:
        export = build_export(state, source_receipt=args.source_receipt, label=args.label)
    except ExportRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    out.mkdir(parents=True)
    body = json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True)
    (out / "evidence-export.json").write_text(body + "\n", encoding="utf-8")
    (out / "evidence-export.md").write_text(render_markdown(export), encoding="utf-8")
    sums = "".join(
        f"{sha256_file(out / name)}  {name}\n"
        for name in ("evidence-export.json", "evidence-export.md")
    )
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8")
    print(
        json.dumps(
            dict(
                out_dir=str(out),
                label=export["label"],
                run_status=export["run"]["status"],
                missing_inputs=export["missing_inputs"],
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
