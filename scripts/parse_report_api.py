"""Bounded Upstage Document Parse batches for one report PDF (candidate output only).

Thin CLI over the EXISTING ``UpstageParseProbe``: it splits selected physical
pages into <=10-page PDFs and sends each batch through the probe, which keeps
the pinned model, the 10MiB/60s transport limits, the USD1 reservation, no
retries/redirects and settlement in the ONE ledger named by ``--ledger``.
No budget pool, transport or pricing is added here.

Default is a dry plan (no key read, no network, nothing written). ``--invoke``
requires, before the key file is even opened: a ledger holding one valid,
non-expired session grant (legacy ledgers are refused) whose headroom covers
USD1 plus the max gross of every batch to be sent, and a fresh ``--out-dir``. The key file must
contain exactly one ``UPSTAGE_API_KEY=`` entry; the key is never printed or written.

Output (``--out-dir``), every file written once (``x`` mode) except status.json:
* ``manifest.json``   immutable plan: source sha256/size/page count, mode,
  pinned model, ledger path + grant digest, per-batch split sha256, request
  UUID and exact batch-page -> physical-page map with mediabox/cropbox/rotation.
* ``batches/NNN.pdf``            the exact split bytes sent.
* ``batches/NNN.attempt.json``   written BEFORE reservation/network.
* ``batches/NNN.response.json``  provider response as returned.
* ``batches/NNN.receipt.json``   settled receipt (no raw response) pinning the
  response digests, usage and cost.
* ``batches/NNN.failure.json``   sanitized failure code; reservation retained.
* ``status.json``    derived summary (rewritten), pins manifest/receipt digests.

``--resume`` reuses only hash-validated receipts/responses, refuses any change
to source/pages/mode/ledger/grant, and never repeats a batch that has an
attempt without a receipt (unknown/pending/failed). A failure stops the run.
Everything is labelled ``candidate_only`` / ``not_source_verified``: no source
verification, graph or claims are produced here.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parents[1]
if str(TEAM_ROOT) not in sys.path:
    sys.path.insert(0, str(TEAM_ROOT))

from analyze_report import PlanError, describe_ledger, parse_pages  # noqa: E402

SCHEMA = "upstage-parse-batches-v1"
LABEL = {"quality": "candidate_only", "verification": "not_source_verified"}
KEY_PREFIX = "UPSTAGE_API_KEY="


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _box(box) -> list[float]:
    return [float(value) for value in box]


def _page_meta(page) -> dict:
    return {
        "mediabox": _box(page.mediabox),
        "cropbox": _box(page.cropbox),
        "rotation": int(page.rotation),
        "user_unit": float(page.user_unit),
    }


def build_plan(pdf: Path, pages: list[int] | None, mode: str, batch_pages: int):
    """Return (manifest-without-request-ids, split bytes per batch); writes nothing."""
    import pypdf
    from proofops.adapters.local.upstage_parse import (
        COST_PER_PAGE,
        MAX_PDF_BYTES,
        PARSE_MODEL_PINNED,
    )

    if not pdf.is_file():
        raise PlanError(f"--pdf not found: {pdf}")
    raw = pdf.read_bytes()
    reader = pypdf.PdfReader(io.BytesIO(raw))
    if reader.is_encrypted:
        raise PlanError("encrypted PDFs are not supported")
    total = len(reader.pages)
    selected = list(range(1, total + 1)) if pages is None else pages
    if not selected or selected[-1] > total:
        raise PlanError(f"pages must be within 1..{total}")
    batches, splits = [], []
    for start in range(0, len(selected), batch_pages):
        physical = selected[start : start + batch_pages]
        writer = pypdf.PdfWriter()
        for number in physical:
            writer.add_page(reader.pages[number - 1])
        buffer = io.BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
        if len(data) > MAX_PDF_BYTES:
            raise PlanError(f"batch {len(batches) + 1} is over 10MiB; lower --batch-pages")
        split = pypdf.PdfReader(io.BytesIO(data))
        page_map = []
        for offset, number in enumerate(physical):
            meta = _page_meta(reader.pages[number - 1])
            if _page_meta(split.pages[offset]) != meta:
                raise PlanError(f"page {number} geometry changed when split")
            page_map.append({"batch_page": offset + 1, "physical_page": number, **meta})
        batches.append(
            {
                "index": len(batches) + 1,
                "file": f"batches/{len(batches) + 1:03d}.pdf",
                "split_sha256": _sha(data),
                "split_bytes": len(data),
                "pages": page_map,
                "max_gross_usd": str(COST_PER_PAGE[mode] * len(physical)),
                "request_id": None,
            }
        )
        splits.append(data)
    manifest = {
        "schema": SCHEMA,
        **LABEL,
        "source": {
            "file_name": pdf.name,
            "sha256": _sha(raw),
            "bytes": len(raw),
            "page_count": total,
        },
        "selected_pages": selected,
        "all_pages": pages is None,
        "mode": mode,
        "model": PARSE_MODEL_PINNED,
        "batch_pages": batch_pages,
        "coordinates_note": "provider coordinates are normalized to the rendered "
        "batch page (cropbox, rotation applied); map via pages[].physical_page",
        "batches": batches,
    }
    return manifest, splits


def ledger_gate(ledger: Path, batches: list[dict]) -> dict:
    """Refuse unless a valid, non-expired grant covers the worst case of these batches.

    Calls are sequential and each settles at exactly its max_gross_usd, while a
    failed call keeps its USD1 reservation and stops the run, so the worst case
    is one USD1 reservation plus every batch's max gross.
    """
    from proofops.adapters.local.upstage import POLICY, PRICE_RECHECK_AT, read_session_grant

    if datetime.now(UTC) >= PRICE_RECHECK_AT:
        raise PlanError("Upstage price snapshot needs a recheck before any paid call")
    state = describe_ledger(ledger)
    if state["state"] != "session_grant":
        raise PlanError(f"--ledger needs one valid session grant (state: {state['state']})")
    if state["expired"]:
        raise PlanError("session grant expired; a new grant needs user approval")
    needed = Decimal(POLICY["reservation_usd"]) + sum(
        (Decimal(batch["max_gross_usd"]) for batch in batches), Decimal(0)
    )
    if Decimal(state["headroom_usd"]) < needed:
        raise PlanError(f"ledger headroom {state['headroom_usd']} < worst case USD{needed}")
    with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as db:
        state["grant_sha256"] = _sha(json.dumps(read_session_grant(db), sort_keys=True).encode())
    return state


def read_key(path: Path) -> str:
    """Exactly one non-empty UPSTAGE_API_KEY entry; the value never leaves this call."""
    if not path.is_file():
        raise PlanError(f"--key-file not found: {path}")
    keys = [
        line[len(KEY_PREFIX) :].strip().strip('"').strip("'")
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.startswith(KEY_PREFIX)
    ]
    if len(keys) != 1 or not keys[0] or any(c.isspace() for c in keys[0]):
        raise PlanError("--key-file must hold exactly one non-empty UPSTAGE_API_KEY entry")
    return keys[0]


def _write_once(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(0o444)


def _dump(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def batch_state(out: Path, batch: dict) -> str:
    """planned | completed (hash-validated) | blocked (attempt without receipt)."""
    from proofops.domain.provenance import canonical_hash

    stem = out / batch["file"][: -len(".pdf")]
    receipt_path = Path(f"{stem}.receipt.json")
    if receipt_path.exists():
        receipt = _load(receipt_path)
        response_bytes = Path(f"{stem}.response.json").read_bytes()
        if (
            receipt["request_id"] != batch["request_id"]
            or receipt["split_sha256"] != batch["split_sha256"]
            or receipt["response_file_sha256"] != _sha(response_bytes)
            or receipt["response_sha256"] != canonical_hash(json.loads(response_bytes))
        ):
            raise PlanError(f"batch {batch['index']} saved response fails hash validation")
        return "completed"
    return "blocked" if Path(f"{stem}.attempt.json").exists() else "planned"


def write_status(out: Path, manifest: dict) -> dict:
    rows, gross = [], Decimal(0)
    for batch in manifest["batches"]:
        stem = out / batch["file"][: -len(".pdf")]
        row = {"index": batch["index"], "request_id": batch["request_id"]}
        row["state"] = batch_state(out, batch)
        if row["state"] == "completed":
            receipt_bytes = Path(f"{stem}.receipt.json").read_bytes()
            receipt = json.loads(receipt_bytes)
            gross += Decimal(receipt["cost_with_vat_reserve_usd"])
            row.update(
                receipt_file_sha256=_sha(receipt_bytes),
                response_file_sha256=receipt["response_file_sha256"],
                usage=receipt["usage"],
            )
        elif row["state"] == "blocked":
            failure = Path(f"{stem}.failure.json")
            row["failure"] = _load(failure)["code"] if failure.exists() else "unknown_pending"
        rows.append(row)
    status = {
        "schema": SCHEMA + "-status",
        **LABEL,
        "manifest_sha256": _sha((out / "manifest.json").read_bytes()),
        "settled_gross_usd": str(gross),
        "batches": rows,
    }
    tmp = out / "status.json.tmp"
    tmp.write_bytes(_dump(status))
    os.replace(tmp, out / "status.json")
    return status


def run_batch(probe, out: Path, manifest: dict, batch: dict) -> None:
    stem = out / batch["file"][: -len(".pdf")]
    data = (out / batch["file"]).read_bytes()
    if _sha(data) != batch["split_sha256"]:
        raise PlanError(f"batch {batch['index']} split PDF fails hash validation")
    attempt = {"request_id": batch["request_id"], "started_at": datetime.now(UTC).isoformat()}
    _write_once(Path(f"{stem}.attempt.json"), _dump(attempt))
    try:
        result = probe.parse(data, request_id=batch["request_id"], mode=manifest["mode"])
    except ValueError as exc:
        failure = {**attempt, "code": str(exc), "reservation": "retained_or_unknown"}
        with closing(sqlite3.connect(probe.ledger.as_uri() + "?mode=ro", uri=True)) as db:
            failure["ledger_row_present"] = bool(
                db.execute(
                    "SELECT 1 FROM probe_calls WHERE request_id=?", (batch["request_id"],)
                ).fetchone()
            )
        _write_once(Path(f"{stem}.failure.json"), _dump(failure))
        raise
    response_bytes = _dump(result["raw_response"])
    _write_once(Path(f"{stem}.response.json"), response_bytes)
    receipt = {key: value for key, value in result.items() if key != "raw_response"}
    receipt.update(
        LABEL,
        index=batch["index"],
        request_id=batch["request_id"],
        split_sha256=batch["split_sha256"],
        response_file_sha256=_sha(response_bytes),
    )
    _write_once(Path(f"{stem}.receipt.json"), _dump(receipt))


def _prepare(args, manifest: dict, splits: list[bytes], ledger_state: dict) -> dict:
    """Create (or on --resume re-validate) the immutable manifest; return it."""
    out = args.out_dir
    pinned = dict(
        manifest,
        ledger={"path": str(args.ledger), "grant_sha256": ledger_state["grant_sha256"]},
    )
    if args.resume:
        saved = _load(out / "manifest.json")
        comparable = dict(saved, batches=[dict(b, request_id=None) for b in saved["batches"]])
        if comparable != pinned:
            raise PlanError("--resume cannot change source, pages, mode, model or ledger")
        return saved
    for batch in pinned["batches"]:
        batch["request_id"] = str(uuid.uuid4())
    (out / "batches").mkdir(parents=True)
    _write_once(out / "manifest.json", _dump(pinned))
    for batch, data in zip(pinned["batches"], splits, strict=True):
        _write_once(out / batch["file"], data)
    return pinned


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parse_report_api", description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--pages", help="1-based physical pages/ranges, e.g. 2,5-9")
    scope.add_argument("--all-pages", action="store_true")
    parser.add_argument("--ledger", type=Path, required=True, help="session-grant ledger")
    parser.add_argument("--key-file", type=Path, help="required with --invoke")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["standard", "enhanced"], default="standard")
    parser.add_argument("--batch-pages", type=int, default=10, help="1..10 pages per call")
    parser.add_argument("--max-batches", type=int, help="send at most N batches this run")
    parser.add_argument("--invoke", action="store_true", help="make paid calls")
    parser.add_argument("--resume", action="store_true", help="continue an existing out-dir")
    return parser


def main(argv: list[str] | None = None, *, probe_factory=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for name in ("pdf", "ledger", "out_dir", "key_file"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).expanduser().resolve())
    try:
        if not 1 <= args.batch_pages <= 10:
            raise PlanError("--batch-pages must be 1..10")
        if args.max_batches is not None and args.max_batches < 1:
            raise PlanError("--max-batches must be >= 1")
        pages = None if args.all_pages else parse_pages(args.pages)
        manifest, splits = build_plan(args.pdf, pages, args.mode, args.batch_pages)
        if not args.invoke:
            if args.resume:
                raise PlanError("--resume requires --invoke")
            plan = {**manifest, "dry_run": True, "ledger": describe_ledger(args.ledger)}
            plan["out_dir_exists"] = args.out_dir.exists()
            print(json.dumps(plan, indent=2))
            return 0
        return _invoke(args, manifest, splits, probe_factory)
    except PlanError as exc:
        parser.error(str(exc))
    return 2


def _invoke(args, manifest: dict, splits: list[bytes], probe_factory) -> int:
    out = args.out_dir
    if args.key_file is None:
        raise PlanError("--invoke requires --key-file")
    for inside in (args.ledger, args.key_file):
        if inside.is_relative_to(out):
            raise PlanError("--out-dir must not contain the ledger or key file")
    if args.resume != out.exists():
        raise PlanError("--out-dir exists; pass --resume" if out.exists() else "nothing to resume")
    states = ["planned"] * len(manifest["batches"])
    if args.resume:
        saved = _load(out / "manifest.json")
        states = [batch_state(out, batch) for batch in saved["batches"]]
        if "blocked" in states:
            first = states.index("blocked") + 1
            raise PlanError(f"batch {first} has an unknown/failed attempt; never repeated")
    todo = [i for i, state in enumerate(states) if state == "planned"][: args.max_batches]
    ledger_state = ledger_gate(args.ledger, [manifest["batches"][i] for i in todo])
    key = read_key(args.key_file) if todo else None
    manifest = _prepare(args, manifest, splits, ledger_state)
    if todo:
        from proofops.adapters.local.upstage_parse import UpstageParseProbe

        probe = (probe_factory or UpstageParseProbe)(key, args.ledger)
        del key
        try:
            for i in todo:
                run_batch(probe, out, manifest, manifest["batches"][i])
        except ValueError as exc:
            status = write_status(out, manifest)
            print(json.dumps({"stopped": str(exc), **status}, indent=2), file=sys.stderr)
            return 1
    print(json.dumps(write_status(out, manifest), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
