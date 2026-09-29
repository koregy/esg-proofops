"""Immutable, replay-verifiable sidecar of Upstage Document Parse candidates.

Imports the completed batches of one ``scripts/parse_report_api.py`` output
directory (schema ``upstage-parse-batches-v1``) into a SEPARATE evidence
artifact. Nothing here touches an ODL manifest, a run graph or citations:

* ``origin/`` holds byte-exact copies of the parse-run manifest and, per
  batch, the exact split PDF, receipt, provider response and attempt/failure
  records. Original sha256, request/response hashes and page maps are kept.
* ``derived/`` holds, per converted batch, ``NNN.candidates.json``
  (``asdict(CandidateBatch)``) and ``NNN.conversion.json`` (converter report),
  plus ``review.csv`` for an offline reviewer.
* ``sidecar.json`` pins the sha256 of every other file, the binding ids, the
  source sha256 and the per-batch outcome. Its own sha256 is the pin a run
  snapshot or reviewer records.

Loading re-reads every file without following links, requires the exact file
set and digests, then REPLAYS the whole import from ``origin/`` and demands
byte equality of every derived file and of ``sidecar.json``. Any difference
fails closed. Candidates stay ``candidate_only`` / not source-verified /
citation not approved; tables carry table-level boxes only, never cells.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

from proofops.adapters.local.upstage_parse import MAX_PDF_BYTES, PARSE_MODEL_PINNED
from proofops.adapters.parsing.upstage_candidates import (
    CONVERTER_VERSION,
    UpstageCandidateError,
    convert_upstage_parse,
)
from proofops.application.ingest.graph_fusion import (
    CandidateBatch,
    SourceArtifact,
    candidates_from_snapshot,
)
from proofops.domain.provenance import canonical_hash
from proofops.domain.values import _require_uuid

SCHEMA = "upstage_candidate_sidecar_v1"
ORIGIN_SCHEMA = "upstage-parse-batches-v1"
ORIGIN_LABEL = {"quality": "candidate_only", "verification": "not_source_verified"}
LABELS = {
    "quality": "candidate_only",
    "source_verification": "not_run",
    "citation_approved": False,
    "graph_effect": "none_separate_artifact",
    "table_geometry": "table_level_only_no_cells",
}
# Stable namespace for ids derived when no run binding is supplied (offline review only).
OFFLINE_NAMESPACE = uuid.UUID("6f1c3b52-5a0e-4c61-9d7e-2f0b8c4e7a19")
# Genuine provider/geometry limits that exclude one batch without failing the import.
# Every other converter code is an integrity failure and aborts.
EXCLUDABLE_CODES = frozenset({"UPSTAGE_PAGE_GEOMETRY_UNSUPPORTED"})
MAX_BATCHES = 200
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RECEIPT_EXTRA = ("index", "request_id", "split_sha256", "response_file_sha256")


class UpstageSidecarError(ValueError):
    pass


def _fail(code: str) -> NoReturn:
    raise UpstageSidecarError(code)


@dataclass(frozen=True, slots=True)
class SidecarBinding:
    """Identity the candidates are attached to; ``offline_derived`` binds to no run."""

    tenant_id: str
    document_id: str
    document_version_id: str
    parse_manifest_id: str
    object_version_id: str
    kind: str = "explicit"

    def __post_init__(self):
        for name in ("tenant_id", "document_id", "document_version_id", "parse_manifest_id"):
            _require_uuid(name, getattr(self, name))
        if (
            self.kind not in ("explicit", "offline_derived")
            or not isinstance(self.object_version_id, str)
            or not 1 <= len(self.object_version_id) <= 1024
        ):
            raise UpstageSidecarError("UPSTAGE_SIDECAR_BINDING_INVALID")


def offline_binding(source_sha256: str, origin_manifest_sha256: str) -> SidecarBinding:
    """Deterministic ids for a sidecar reviewed outside any run (never a real tenant)."""
    seed = f"{source_sha256}:{origin_manifest_sha256}"
    tenant, document, version, manifest = (
        str(uuid.uuid5(OFFLINE_NAMESPACE, f"{role}:{seed}")) for role in range(4)
    )
    return SidecarBinding(
        tenant, document, version, manifest, f"sha256:{source_sha256}", "offline_derived"
    )


@dataclass(frozen=True, slots=True)
class UpstageSidecar:
    manifest: dict = field(repr=False)
    manifest_sha256: str
    batches: tuple[CandidateBatch, ...] = field(repr=False)
    reports: tuple[dict, ...] = field(repr=False)


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _dump(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )


def _no_constant(value):
    raise ValueError(value)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _json(data: bytes, code: str):
    try:
        return json.loads(
            data.decode("utf-8"), parse_constant=_no_constant, object_pairs_hook=_unique_pairs
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail(code)


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


class _Tree:
    """Reads files below one root, never following a link at any component."""

    def __init__(self, root: Path, code: str):
        self.code = code
        if not isinstance(root, Path) or _is_link(root) or not root.is_dir():
            _fail(code)
        self.root = root
        self.real = root.resolve(strict=True)
        self.total = 0

    def path(self, rel: str) -> Path:
        parts = rel.split("/") if isinstance(rel, str) else []
        if not parts or not all(_SEGMENT.match(p) and p not in (".", "..") for p in parts):
            _fail(self.code)
        current = self.root
        for part in parts:
            current = current / part
            if _is_link(current):
                _fail(self.code)
        return current

    def exists(self, rel: str) -> bool:
        path = self.path(rel)
        return os.path.lexists(path)

    def read(self, rel: str, limit: int) -> bytes:
        path = self.path(rel)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                _fail(self.code)
            if not path.resolve(strict=True).is_relative_to(self.real):
                _fail(self.code)
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
        except OSError:
            _fail(self.code)
        self.total += len(data)
        if len(data) > limit or self.total > MAX_TOTAL_BYTES:
            _fail(self.code)
        return data

    def listing(self) -> set[str]:
        """Every regular file below the root; any link or special file fails."""
        found: set[str] = set()
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            base = Path(directory)
            for name in (*dirs, *files):
                candidate = base / name
                if _is_link(candidate):
                    _fail(self.code)
            for name in files:
                candidate = base / name
                if not stat.S_ISREG(candidate.lstat().st_mode):
                    _fail(self.code)
                found.add(candidate.relative_to(self.root).as_posix())
        return found


def _check_origin_manifest(manifest, source: SourceArtifact) -> list[dict]:
    code = "UPSTAGE_ORIGIN_MANIFEST_INVALID"
    if not isinstance(manifest, dict) or manifest.get("schema") != ORIGIN_SCHEMA:
        _fail(code)
    if any(manifest.get(k) != v for k, v in ORIGIN_LABEL.items()):
        _fail(code)
    if manifest.get("model") != PARSE_MODEL_PINNED or manifest.get("mode") not in (
        "standard",
        "enhanced",
    ):
        _fail("UPSTAGE_ORIGIN_MODEL_OR_MODE_INVALID")
    origin = manifest.get("source")
    if not isinstance(origin, dict) or origin.get("sha256") != source.sha256:
        _fail("UPSTAGE_SOURCE_HASH_MISMATCH")
    if origin.get("bytes") != len(source.content):
        _fail("UPSTAGE_SOURCE_HASH_MISMATCH")
    batches, selected = manifest.get("batches"), manifest.get("selected_pages")
    if (
        not isinstance(batches, list)
        or not 1 <= len(batches) <= MAX_BATCHES
        or not isinstance(selected, list)
    ):
        _fail(code)
    covered: list[int] = []
    for number, batch in enumerate(batches, 1):
        if not isinstance(batch, dict):
            _fail(code)
        pages = batch.get("pages")
        if (
            batch.get("index") != number
            or batch.get("file") != f"batches/{number:03d}.pdf"
            or not isinstance(batch.get("split_sha256"), str)
            or not isinstance(pages, list)
            or not 1 <= len(pages) <= 10
            or any(
                not isinstance(p, dict) or p.get("batch_page") != i for i, p in enumerate(pages, 1)
            )
        ):
            _fail(code)
        try:
            _require_uuid("request_id", batch.get("request_id"))
        except ValueError:
            _fail(code)
        covered.extend(p.get("physical_page") for p in pages)
    if covered != selected or any(type(p) is not int or p < 1 for p in covered):
        _fail("UPSTAGE_PAGE_MAPPING_INVALID")
    if covered != sorted(set(covered)) or covered[-1] > origin.get("page_count", 0):
        _fail("UPSTAGE_PAGE_MAPPING_INVALID")
    return batches


def _review_rows(index: int, request_id: str, conversion) -> list[list]:
    rows = []
    reports = {row["native_id"]: row for row in conversion.report["elements"]}
    for block in conversion.batch.blocks:
        src, row = block.source, reports[block.source.source_native_id]
        text = src.raw_text
        # Neutralize spreadsheet formulas; text_sha256 still pins the untouched text.
        shown = "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
        bbox = [""] * 4 if src.native_bbox is None else [round(v, 3) for v in src.native_bbox]
        rows.append(
            [
                index,
                request_id,
                src.physical_page,
                src.printed_page_label,
                src.source_native_id,
                row["category"],
                block.kind,
                "table_level_box_no_cells" if block.kind == "table" else "element_box",
                row["located"],
                *bbox,
                row["text_sha256"],
                row["html_sha256"],
                "candidate_only",
                "not_run",
                "false",
                shown,
            ]
        )
    return rows


REVIEW_HEADER = [
    "batch",
    "request_id",
    "physical_page",
    "printed_page_label",
    "native_id",
    "provider_category",
    "kind",
    "geometry_level",
    "located",
    "pdf_x0_pt",
    "pdf_y0_pt",
    "pdf_x1_pt",
    "pdf_y1_pt",
    "text_sha256",
    "html_sha256",
    "quality",
    "source_verification",
    "citation_approved",
    "provider_text",
]


def build_sidecar(
    origin: Path,
    source_content: bytes,
    binding: SidecarBinding | None = None,
    *,
    synthetic: bool = False,
) -> tuple[dict, dict[str, bytes]]:
    """Pure-over-files import of one parse-run directory into sidecar file bytes."""
    if not isinstance(source_content, bytes) or type(synthetic) is not bool:
        _fail("UPSTAGE_SOURCE_HASH_MISMATCH")
    tree = _Tree(origin, "UPSTAGE_ORIGIN_PATH_UNSAFE")
    manifest_bytes = tree.read("manifest.json", MAX_JSON_BYTES)
    manifest_sha = _sha(manifest_bytes)
    manifest = _json(manifest_bytes, "UPSTAGE_ORIGIN_MANIFEST_INVALID")
    source_sha = _sha(source_content)
    if binding is None:
        binding = offline_binding(source_sha, manifest_sha)
    source = SourceArtifact(
        binding.tenant_id,
        binding.document_id,
        binding.document_version_id,
        source_sha,
        binding.object_version_id,
        source_content,
        synthetic,
    )
    batches = _check_origin_manifest(manifest, source)
    files: dict[str, bytes] = {"origin/manifest.json": manifest_bytes}
    outcomes, review, totals = [], [], {"elements": 0, "located": 0, "tables": 0}
    for batch in batches:
        stem = batch["file"][: -len(".pdf")]
        entry = {
            "index": batch["index"],
            "request_id": batch["request_id"],
            "physical_pages": [p["physical_page"] for p in batch["pages"]],
            "split_sha256": batch["split_sha256"],
        }
        for suffix in ("attempt", "failure"):
            if tree.exists(f"{stem}.{suffix}.json"):
                data = tree.read(f"{stem}.{suffix}.json", MAX_JSON_BYTES)
                record = _json(data, "UPSTAGE_ORIGIN_RECORD_INVALID")
                if not isinstance(record, dict) or record.get("request_id") != batch["request_id"]:
                    _fail("UPSTAGE_ORIGIN_RECORD_INVALID")
                files[f"origin/{stem}.{suffix}.json"] = data
        if not tree.exists(f"{stem}.receipt.json"):
            entry["state"] = "blocked" if tree.exists(f"{stem}.attempt.json") else "planned"
            outcomes.append(entry)
            continue
        receipt_bytes = tree.read(f"{stem}.receipt.json", MAX_JSON_BYTES)
        response_bytes = tree.read(f"{stem}.response.json", MAX_JSON_BYTES)
        split = tree.read(batch["file"], MAX_PDF_BYTES)
        receipt = _json(receipt_bytes, "UPSTAGE_RECEIPT_SHAPE_INVALID")
        response = _json(response_bytes, "UPSTAGE_RESPONSE_HASH_MISMATCH")
        if not isinstance(receipt, dict) or "raw_response" in receipt:
            _fail("UPSTAGE_RECEIPT_SHAPE_INVALID")
        if (
            receipt.get("index") != batch["index"]
            or receipt.get("request_id") != batch["request_id"]
            or receipt.get("split_sha256") != batch["split_sha256"]
            or any(receipt.get(k) != v for k, v in ORIGIN_LABEL.items())
            or receipt.get("mode") != manifest["mode"]
        ):
            _fail("UPSTAGE_RECEIPT_BINDING_MISMATCH")
        if receipt.get("response_file_sha256") != _sha(response_bytes):
            _fail("UPSTAGE_RESPONSE_HASH_MISMATCH")
        if _sha(split) != batch["split_sha256"]:
            _fail("UPSTAGE_SPLIT_HASH_MISMATCH")
        # Kept byte-exact for converted AND excluded batches so replay sees the same state.
        for name, data in (
            ("pdf", split),
            ("receipt.json", receipt_bytes),
            ("response.json", response_bytes),
        ):
            files[f"origin/{stem}.{name}"] = data
        entry.update(
            receipt_file_sha256=_sha(receipt_bytes),
            response_file_sha256=_sha(response_bytes),
            response_sha256=receipt.get("response_sha256"),
            request_sha256=receipt.get("request_sha256"),
        )
        try:
            conversion = convert_upstage_parse(
                {**receipt, "raw_response": response},
                source=source,
                subset_pdf=split,
                physical_pages=tuple(entry["physical_pages"]),
                parse_manifest_id=binding.parse_manifest_id,
                receipt_sha256=entry["receipt_file_sha256"],
            )
        except UpstageCandidateError as exc:
            if str(exc) not in EXCLUDABLE_CODES:
                _fail(str(exc))
            entry.update(state="excluded", code=str(exc))
            outcomes.append(entry)
            continue
        number = f"{batch['index']:03d}"
        files[f"derived/{number}.candidates.json"] = _dump(asdict(conversion.batch))
        files[f"derived/{number}.conversion.json"] = _dump(conversion.report)
        counts = conversion.report["counts"]
        entry.update(
            state="converted",
            parser_run_id=conversion.batch.parser_run_id,
            config_hash=conversion.batch.config_hash,
            counts={k: counts[k] for k in ("elements", "located", "unlocated", "tables")},
            table_cell_candidates=counts["table_cell_candidates"],
        )
        for key in totals:
            totals[key] += counts[key]
        outcomes.append(entry)
        review.extend(_review_rows(batch["index"], batch["request_id"], conversion))
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(REVIEW_HEADER)
    writer.writerows(review)
    files["derived/review.csv"] = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
    states = [o["state"] for o in outcomes]
    sidecar = dict(
        schema=SCHEMA,
        **LABELS,
        converter_version=CONVERTER_VERSION,
        binding=asdict(binding),
        source=dict(
            sha256=source.sha256,
            bytes=len(source.content),
            page_count=manifest["source"].get("page_count"),
            synthetic=source.synthetic,
        ),
        origin=dict(
            schema=ORIGIN_SCHEMA,
            manifest_sha256=manifest_sha,
            mode=manifest["mode"],
            model=manifest["model"],
            selected_pages=manifest["selected_pages"],
        ),
        batches=outcomes,
        totals=dict(
            batches=len(outcomes),
            **{s: states.count(s) for s in ("converted", "excluded", "blocked", "planned")},
            **totals,
            table_cell_candidates=0,
        ),
        files={name: _sha(data) for name, data in sorted(files.items())},
    )
    return sidecar, files


def _remove(path: Path) -> None:
    def writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)

    shutil.rmtree(path, onexc=writable)


def write_sidecar(out_dir: Path, sidecar: dict, files: dict[str, bytes]) -> str:
    """Write read-only files into a fresh sibling, then publish by one rename."""
    if os.path.lexists(out_dir) or not out_dir.parent.is_dir() or _is_link(out_dir.parent):
        _fail("UPSTAGE_SIDECAR_OUT_EXISTS_OR_UNSAFE")
    staging = out_dir.parent / f".{out_dir.name}.partial-{uuid.uuid4().hex}"
    manifest_bytes = _dump(sidecar)
    try:
        staging.mkdir()
        for name, data in {**files, "sidecar.json": manifest_bytes}.items():
            target = staging.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o444)
        if os.path.lexists(out_dir):
            _fail("UPSTAGE_SIDECAR_OUT_EXISTS_OR_UNSAFE")
        os.rename(staging, out_dir)
    except BaseException:
        if staging.exists():
            _remove(staging)
        raise
    return _sha(manifest_bytes)


def import_parse_run(
    parse_dir: Path,
    out_dir: Path,
    source_content: bytes,
    binding: SidecarBinding | None = None,
    *,
    synthetic: bool = False,
    require_complete: bool = False,
) -> tuple[dict, str]:
    """Import completed batches into a new sidecar directory; never mutates ``parse_dir``."""
    parse_real = parse_dir.resolve()
    out_real = out_dir.parent.resolve() / out_dir.name
    if out_real.is_relative_to(parse_real) or parse_real.is_relative_to(out_real):
        _fail("UPSTAGE_SIDECAR_OUT_EXISTS_OR_UNSAFE")
    sidecar, files = build_sidecar(parse_dir, source_content, binding, synthetic=synthetic)
    if require_complete and sidecar["totals"]["converted"] != sidecar["totals"]["batches"]:
        _fail("UPSTAGE_PARSE_RUN_INCOMPLETE")
    if sidecar["totals"]["converted"] == 0:
        _fail("UPSTAGE_NO_CONVERTED_BATCHES")
    return sidecar, write_sidecar(out_dir, sidecar, files)


def load_sidecar(
    sidecar_dir: Path,
    source_content: bytes,
    *,
    expected_sha256: str | None = None,
    expected_binding: SidecarBinding | None = None,
) -> UpstageSidecar:
    """Verify every file, then replay the import from ``origin/`` and require equality."""
    tree = _Tree(sidecar_dir, "UPSTAGE_SIDECAR_PATH_UNSAFE")
    manifest_bytes = tree.read("sidecar.json", MAX_JSON_BYTES)
    manifest_sha = _sha(manifest_bytes)
    if expected_sha256 is not None and manifest_sha != expected_sha256:
        _fail("UPSTAGE_SIDECAR_PIN_MISMATCH")
    manifest = _json(manifest_bytes, "UPSTAGE_SIDECAR_MANIFEST_INVALID")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != SCHEMA
        or any(manifest.get(k) != v for k, v in LABELS.items())
        or not isinstance(manifest.get("files"), dict)
        or not isinstance(manifest.get("binding"), dict)
        or not isinstance(manifest.get("source"), dict)
    ):
        _fail("UPSTAGE_SIDECAR_MANIFEST_INVALID")
    pinned = manifest["files"]
    if tree.listing() != {*pinned, "sidecar.json"}:
        _fail("UPSTAGE_SIDECAR_FILESET_MISMATCH")
    for name, digest in pinned.items():
        if _sha(tree.read(name, max(MAX_JSON_BYTES, MAX_PDF_BYTES))) != digest:
            _fail("UPSTAGE_SIDECAR_FILE_HASH_MISMATCH")
    try:
        binding = SidecarBinding(**manifest["binding"])
        synthetic = manifest["source"].get("synthetic")
        if type(synthetic) is not bool:
            raise ValueError
    except (TypeError, ValueError):
        _fail("UPSTAGE_SIDECAR_MANIFEST_INVALID")
    if not isinstance(source_content, bytes) or _sha(source_content) != manifest["source"].get(
        "sha256"
    ):
        _fail("UPSTAGE_SOURCE_HASH_MISMATCH")
    if expected_binding is not None and binding != expected_binding:
        _fail("UPSTAGE_SIDECAR_BINDING_INVALID")
    replayed, files = build_sidecar(
        tree.path("origin"), source_content, binding, synthetic=synthetic
    )
    if _dump(replayed) != manifest_bytes:
        _fail("UPSTAGE_SIDECAR_REPLAY_MISMATCH")
    for name, data in files.items():
        if _sha(data) != pinned.get(name):
            _fail("UPSTAGE_SIDECAR_REPLAY_MISMATCH")
    batches, reports = [], []
    for entry in manifest["batches"]:
        if entry["state"] != "converted":
            continue
        number = f"{entry['index']:03d}"
        stored = json.loads(files[f"derived/{number}.candidates.json"])
        restored = candidates_from_snapshot([stored])[0]
        if canonical_hash(asdict(restored)) != canonical_hash(stored):
            _fail("UPSTAGE_SIDECAR_REPLAY_MISMATCH")
        batches.append(restored)
        reports.append(json.loads(files[f"derived/{number}.conversion.json"]))
    return UpstageSidecar(manifest, manifest_sha, tuple(batches), tuple(reports))
