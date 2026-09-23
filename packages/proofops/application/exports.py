"""Capture revisions once, then render only immutable inputs; no grading/model calls."""

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from itertools import chain
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from proofops.application.reporting import build_report_model, render_report
from proofops.domain.rulepacks import canonical_json

MAX_EXPORT_BYTES = 32 * 1024 * 1024

# manifest.revision_records encodings. A manifest without the field is the original
# v1 layout that always inlined original_inputs; every already-frozen export keeps
# reading unchanged because decode_revision_record still accepts that layout.
REVISION_RECORDS_V1 = "inline_original_inputs_v1"
REVISION_RECORDS_V2 = "shared_original_inputs_v2"
SHARED_ORIGINAL_INPUTS = "tag.inputs"


def encode_revision_record(tag, decision, inputs):
    """Keep every observed byte, storing the original input packet once when it is identical.

    The second copy is dropped only when the canonical bytes of ``tag["inputs"]`` and the
    revision-1 packet are equal, so unequal packets are always both preserved verbatim.
    """
    record = dict(tag=tag, decision=decision)
    stored = tag.get("inputs") if isinstance(tag, dict) else None
    if stored is not None and canonical_json(stored) == canonical_json(inputs):
        record["original_inputs_ref"] = SHARED_ORIGINAL_INPUTS
        return record
    record["original_inputs"] = inputs
    return record


def decode_revision_record(record, *, encoding=REVISION_RECORDS_V1):
    """Restore the byte-identical inline record, dispatching on the manifest encoding.

    Callers pass ``manifest.get("revision_records_encoding", REVISION_RECORDS_V1)`` so a
    legacy manifest stays v1 and an unknown encoding is refused instead of guessed.
    """
    if encoding not in (REVISION_RECORDS_V1, REVISION_RECORDS_V2):
        raise ValueError("unsupported revision_records encoding")
    if not isinstance(record, dict):
        raise ValueError("revision record must be an object")
    reference = record.get("original_inputs_ref")
    if reference is None:
        if "original_inputs_ref" in record or "original_inputs" not in record:
            raise ValueError("revision record is missing original_inputs")
        return dict(record)
    if encoding != REVISION_RECORDS_V2:
        raise ValueError("original_inputs reference is not valid for this encoding")
    if reference != SHARED_ORIGINAL_INPUTS or "original_inputs" in record:
        raise ValueError("unsupported original_inputs reference")
    tag = record.get("tag")
    if not isinstance(tag, dict) or not isinstance(tag.get("inputs"), dict):
        raise ValueError("original_inputs reference has no tag.inputs target")
    restored = dict(record)
    restored.pop("original_inputs_ref")
    restored["original_inputs"] = tag["inputs"]
    return restored


class ExportRejected(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def timestamp(now):
    return datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z")


def create_snapshot(store, tenant_id, export_id):
    for _ in range(4):  # First attempt, then at most three snapshot-only retries.
        frozen = store.frozen(tenant_id, export_id)
        if frozen is not None:
            return frozen
        captured = store.capture(tenant_id, export_id)
        if store.freeze(tenant_id, export_id, captured):
            return captured
    raise ExportRejected("EXPORT_SNAPSHOT_BUSY")


def build_export(snapshot, formats):
    model = build_report_model(snapshot["manifest"], snapshot["decisions"])
    manifest = canonical_json(snapshot["manifest"]).encode()
    stream = BytesIO()
    # Deflate keeps the preserved provenance whole while the artifact stays inside the
    # unchanged 32 MiB cap; every ZIP reader handles it and frozen artifacts are untouched.
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as bundle:
        members = [("manifest.json", manifest)]
        if snapshot["manifest"].get("submitted_reviews"):
            members.append(
                (
                    "submitted-reviews.json",
                    canonical_json(snapshot["manifest"]["submitted_reviews"]).encode(),
                )
            )
        for name, content in chain(
            members, ((f"report.{fmt}", render_report(model, fmt)) for fmt in formats)
        ):
            # Pre-write guard: every raw member must fit the cap next to the bytes already
            # archived. Deflate can add overhead on incompressible input, so this is not a
            # proof about the archive; the final check below measures the actual ZIP.
            if stream.tell() + len(content) > MAX_EXPORT_BYTES:
                raise ExportRejected("EXPORT_SIZE_LIMIT")
            # ZipInfo() defaults to ZIP_STORED and overrides the ZipFile compression.
            info = ZipInfo(name)
            info.compress_type = ZIP_DEFLATED
            bundle.writestr(info, content)
    content = stream.getvalue()
    if len(content) > MAX_EXPORT_BYTES:
        raise ExportRejected("EXPORT_SIZE_LIMIT")
    return content, sha256(manifest).hexdigest(), model["partial"]


class ExportService:
    def __init__(self, store):
        self.store = store

    def create(self, actor, run_id, body, key, *, now):
        if not isinstance(body, dict) or set(body) != {"formats", "allow_partial"}:
            raise ExportRejected("VALIDATION_ERROR", 422)
        formats = body["formats"]
        if (
            not isinstance(formats, list)
            or not 1 <= len(formats) <= 3
            or any(not isinstance(f, str) or f not in ("json", "csv", "html") for f in formats)
            or len(set(formats)) != len(formats)
            or type(body["allow_partial"]) is not bool
        ):
            raise ExportRejected("VALIDATION_ERROR", 422)
        if not isinstance(key, str) or not 16 <= len(key) <= 128:
            raise ExportRejected("IDEMPOTENCY_KEY_INVALID", 400)
        export_id = self.store.reserve(actor, run_id, body, key, now=now)
        result = self.get(actor, export_id, now=now)
        if result["state"] == "failed":
            raise ExportRejected(self.store.state(actor.tenant_id, export_id)["errors"][0])
        return result

    def get(self, actor, export_id, *, now):
        state = self.store.state(actor.tenant_id, export_id)
        if state["response"]["state"] in ("ready", "failed") or now < state["retry_at"]:
            return state["response"]
        try:
            snapshot = create_snapshot(self.store, actor.tenant_id, export_id)
            content, manifest_hash, partial = build_export(snapshot, state["body"]["formats"])
            if partial and not state["body"]["allow_partial"]:
                raise ExportRejected("REPORT_NOT_FINALIZABLE")
            return self.store.complete(actor.tenant_id, export_id, content, manifest_hash, partial)
        except ExportRejected as exc:
            self.store.defer(actor.tenant_id, export_id, exc.code, now=now)
            return self.store.get(actor.tenant_id, export_id)

    def authorize_download(self, actor, export_id, *, now):
        return self.store.authorize_download(actor, export_id, now=now)
