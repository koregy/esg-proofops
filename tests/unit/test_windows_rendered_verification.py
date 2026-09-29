"""Opt-in Windows.Media.Ocr corroboration of native paragraph receipts.

Fake-reader tests pin eligibility, promotion, retry, unavailability, engine pinning
and replay refusal on a one-paragraph synthetic PDF whose base receipt (off macOS)
stops at ``UnsupportedPlatform``. Real-engine tests run only on Windows: the pinned
helper on the synthetic paragraph, and REAL crops of the private Kia original
(pages 2 and 28) whose exact outcome is recorded. No network, key or model call.
"""

from __future__ import annotations

import copy
import os
import sys
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pdfplumber
import pytest
from proofops.adapters.local import windows_ocr
from proofops.adapters.local import windows_rendered_verification as wrv
from proofops.adapters.local.source_verification import attest_native_sources
from proofops.application.evidence.citations import _normalized
from proofops.application.ingest.graph_fusion import fuse_candidates
from proofops.domain.provenance import canonical_hash
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from tests.acceptance.test_parsing import TENANT, candidate

pytestmark = pytest.mark.skipif(
    sys.platform == "darwin", reason="off-macOS base receipt (UnsupportedPlatform) required"
)

TEXT = "Scope 1 emissions were 12345 tCO2e in 2024"
ENGINE = dict(
    reader="Windows.Media.Ocr",
    language="ko",
    os_version="10.0.26200.0",
    os_build="26200",
    os_ubr="9457",
    max_image_dimension=10000,
)
PRIVATE = Path(
    os.environ.get(
        "PROOFOPS_PRIVATE_KIA_PDF",
        Path.home() / "Desktop/korea x aws/project/esg-proofops/2025_기아_지속가능경영보고서.pdf",
    )
)
PRIVATE_SHA = "d0d814d98c4aeedbbdb2bf8631b8981ae5cde94dec32aa32c57510420274da1f"


def _pdf(text: str = TEXT) -> tuple[bytes, tuple[float, float, float, float]]:
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    page = writer.add_blank_page(width=600, height=800)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 20 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    data = output.getvalue()
    with pdfplumber.open(BytesIO(data)) as document:
        words = document.pages[0].extract_words()
        height = document.pages[0].height
    x0, x1 = min(w["x0"] for w in words), max(w["x1"] for w in words)
    top, bottom = min(w["top"] for w in words), max(w["bottom"] for w in words)
    return data, (x0 - 2.0, height - bottom - 2.0, x1 + 3.0, height - top + 1.0)


@pytest.fixture
def engine(monkeypatch):
    """Seed the process engine probe; real probing is covered by the Windows tests."""
    cache = {windows_ocr.helper_sha256(): dict(ENGINE)}
    monkeypatch.setattr(windows_ocr, "_engine_cache", cache)
    return cache


@pytest.fixture
def fixture():
    source, box = _pdf()
    batch = replace(
        candidate("fixture", [("p", "paragraph", TEXT, box, ())]),
        source_sha256=sha256(source).hexdigest(),
    )
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    native = attest_native_sources(graph, source, tenant_id=TENANT, geometry_mode="glyph")
    return graph, source, native


class FakeReader:
    def __init__(self, *texts, engine=ENGINE, status="read"):
        self.texts, self.engine, self.status, self.images = list(texts), engine, status, []

    def __call__(self, png: bytes) -> dict:
        self.images.append(png)
        if self.status != "read":
            return dict(status="unresolved", reason=self.status)
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        return dict(status="read", text=text, engine=dict(self.engine))


def test_base_receipt_is_only_missing_the_rendered_reader(fixture):
    _graph, _source, native = fixture
    (record,) = native["records"]
    assert record["reason"] == "rendered_text_unresolved"
    assert record["rendered"]["error"] == "UnsupportedPlatform"
    assert wrv.eligible_windows_sources(native) == [record["source_id"]]


def test_matching_reading_promotes_without_touching_base_receipt(engine, fixture):
    graph, source, native = fixture
    before = canonical_hash(native)
    reader = FakeReader(TEXT)
    result, proof = wrv.apply_windows_ocr(native, graph, source, tenant_id=TENANT, reader=reader)
    assert canonical_hash(native) == before
    (block,) = result.blocks
    assert block.quality == "verified"
    assert proof["promoted_source_ids"] == [block.source_id]
    (record,) = proof["records"]
    assert record["status"] == "verified" and len(record["attempts"]) == 1
    attempt = record["attempts"][0]
    assert attempt["image_sha256"] == sha256(reader.images[0]).hexdigest()
    assert attempt["padding_px"] == 0 and "engine" not in attempt
    assert proof["policy"]["engine"] == ENGINE
    assert proof["policy"]["render"]["resolution_dpi"] == 216
    assert proof["policy"]["base"] == wrv.native_paragraph_policy()
    assert proof["policy_sha256"] == canonical_hash(proof["policy"])
    assert proof["artifact_sha256"] == canonical_hash(
        {k: v for k, v in proof.items() if k != "artifact_sha256"}
    )
    # Scalar text only: the reader got pixels, never native text.
    assert reader.images[0].startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "texts",
    [
        ("Scope 1 emissions were 12346 tCO2e in 2024",),
        ("Scope 1 emissions were 12,345 tCO2e in 2024",),
        ("", ""),
        ("Scope l emissions were 12345 tCO2e in 2024", "Scope 1 emissions"),
    ],
)
def test_any_difference_stays_unresolved_after_one_padded_retry(engine, fixture, texts):
    graph, source, native = fixture
    reader = FakeReader(*texts)
    result, proof = wrv.apply_windows_ocr(native, graph, source, tenant_id=TENANT, reader=reader)
    assert [b.quality for b in result.blocks] == ["unverified"]
    (record,) = proof["records"]
    assert record["status"] == "unresolved"
    assert record["reason"] == "windows_rendered_text_mismatch"
    assert [a["padding_px"] for a in record["attempts"]] == [0, 6]
    assert proof["promoted_source_ids"] == []


def test_retry_that_matches_is_recorded_with_both_attempts(engine, fixture):
    graph, source, native = fixture
    reader = FakeReader("Scope l emissions", TEXT)
    result, proof = wrv.apply_windows_ocr(native, graph, source, tenant_id=TENANT, reader=reader)
    assert [b.quality for b in result.blocks] == ["verified"]
    assert [a["padding_px"] for a in proof["records"][0]["attempts"]] == [0, 6]


@pytest.mark.parametrize(
    ("reader", "reason"),
    [
        (FakeReader(TEXT, status="windows_ocr_timeout"), "windows_ocr_timeout"),
        (FakeReader(TEXT, engine={**ENGINE, "os_ubr": "9999"}), "windows_ocr_engine_changed"),
        (FakeReader(TEXT, engine={**ENGINE, "language": "en-US"}), "windows_ocr_engine_changed"),
        (lambda png: None, "windows_ocr_unavailable"),
    ],
)
def test_unavailable_or_changed_engine_is_recorded_unresolved(engine, fixture, reader, reason):
    graph, source, native = fixture
    result, proof = wrv.apply_windows_ocr(native, graph, source, tenant_id=TENANT, reader=reader)
    assert [b.quality for b in result.blocks] == ["unverified"]
    (record,) = proof["records"]
    assert record["status"] == "unresolved" and record["reason"] == reason
    assert len(record["attempts"]) == 1 and "text" not in record["attempts"][0]


def test_only_platform_unsupported_records_are_eligible(fixture):
    _graph, _source, native = fixture
    for change in (
        # macOS Vision disagreement: never overridden by another engine.
        lambda r: r.update(rendered=dict(status="read", text="x", image_sha256="0" * 64)),
        lambda r: r.update(reason="clipped_or_rotated_words"),
        lambda r: r.update(reason="text_mismatch"),
        lambda r: r.update(status="verified", reason=None),
        lambda r: r.update(rendered=dict(status="unresolved", reason="render_limit")),
        lambda r: r.update(rendered_attempts=[r["rendered"], r["rendered"]]),
        lambda r: r.update(words=[]),
    ):
        edited = copy.deepcopy(native)
        change(edited["records"][0])
        assert wrv.eligible_windows_sources(edited) == []


def test_forged_receipt_and_wrong_schema_are_refused(engine, fixture):
    graph, source, native = fixture
    forged = copy.deepcopy(native)
    forged["records"][0]["words"][0]["text"] = "Scope9"
    with pytest.raises(ValueError):
        wrv.apply_windows_ocr(forged, graph, source, tenant_id=TENANT, reader=FakeReader(TEXT))
    with pytest.raises(ValueError, match="NATIVE_PARAGRAPH_ATTESTATION_REQUIRED"):
        wrv.apply_windows_ocr(
            {**native, "schema": "native_paragraph_attestation_v1"},
            graph,
            source,
            tenant_id=TENANT,
            reader=FakeReader(TEXT),
        )


def test_replay_requires_identical_engine_and_reading(engine, fixture, monkeypatch):
    graph, source, native = fixture
    result, proof = wrv.apply_windows_ocr(
        native, graph, source, tenant_id=TENANT, reader=FakeReader(TEXT)
    )
    monkeypatch.setattr(wrv, "_replays", type(wrv._replays)())
    assert (
        wrv.replay_windows_ocr(
            proof, native, graph, source, tenant_id=TENANT, reader=FakeReader(TEXT)
        )
        == result
    )
    # Cached replay still re-reads one crop: a changed or missing engine is refused.
    for reader in (
        FakeReader(TEXT, engine={**ENGINE, "os_build": "26300"}),
        FakeReader(TEXT, status="windows_ocr_unavailable"),
        FakeReader("Scope 1 emissions were 12346 tCO2e in 2024"),
    ):
        with pytest.raises(ValueError, match="WINDOWS_OCR_PROOF_MISMATCH"):
            wrv.replay_windows_ocr(proof, native, graph, source, tenant_id=TENANT, reader=reader)
    monkeypatch.setattr(wrv, "_replays", type(wrv._replays)())
    with pytest.raises(ValueError, match="WINDOWS_OCR_PROOF_MISMATCH"):
        wrv.replay_windows_ocr(
            proof,
            native,
            graph,
            source,
            tenant_id=TENANT,
            reader=FakeReader(TEXT, status="windows_ocr_unavailable"),
        )


def test_replay_refuses_tampered_proof_and_changed_policy(engine, fixture):
    graph, source, native = fixture
    _result, proof = wrv.apply_windows_ocr(
        native, graph, source, tenant_id=TENANT, reader=FakeReader("wrong")
    )
    forged = copy.deepcopy(proof)
    forged["promoted_source_ids"] = forged["eligible_source_ids"]
    forged["records"][0].update(status="verified", reason=None)
    forged["artifact_sha256"] = canonical_hash(
        {k: v for k, v in forged.items() if k != "artifact_sha256"}
    )
    with pytest.raises(ValueError, match="WINDOWS_OCR_PROOF_MISMATCH"):
        wrv.replay_windows_ocr(
            forged, native, graph, source, tenant_id=TENANT, reader=FakeReader("wrong")
        )
    unsigned = {**proof, "promoted_source_ids": proof["eligible_source_ids"]}
    with pytest.raises(ValueError, match="WINDOWS_OCR_PROOF_INVALID"):
        wrv.replay_windows_ocr(
            unsigned, native, graph, source, tenant_id=TENANT, reader=FakeReader("wrong")
        )
    # A Windows update changes the pinned engine identity, so the policy differs.
    engine[windows_ocr.helper_sha256()] = {**ENGINE, "os_ubr": "9999"}
    with pytest.raises(ValueError, match="WINDOWS_OCR_PROOF_INVALID"):
        wrv.replay_windows_ocr(
            proof, native, graph, source, tenant_id=TENANT, reader=FakeReader("wrong")
        )


def test_policy_needs_a_working_engine(monkeypatch):
    monkeypatch.setattr(windows_ocr, "_engine_cache", {})
    monkeypatch.setattr(
        windows_ocr,
        "read_windows_ocr",
        lambda png, **_: dict(status="unresolved", reason="windows_ocr_unavailable"),
    )
    with pytest.raises(windows_ocr.WindowsOcrUnavailable):
        wrv.native_paragraph_windows_ocr_policy()


def test_helper_refuses_changed_script_and_non_windows(monkeypatch):
    result = windows_ocr.read_windows_ocr(b"\x89PNG", expected_helper_sha256="0" * 64)
    expected = (
        "windows_ocr_helper_changed"
        if sys.platform == "win32"
        else ("windows_ocr_unsupported_platform")
    )
    assert result == dict(status="unresolved", reason=expected)
    assert windows_ocr.read_windows_ocr(b"", expected_helper_sha256="0" * 64)["status"] == (
        "unresolved"
    )


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows.Media.Ocr required")


@windows_only
def test_real_engine_probe_and_synthetic_paragraph(fixture):
    graph, source, native = fixture
    identity = windows_ocr.engine_identity(expected_helper_sha256=windows_ocr.helper_sha256())
    assert identity["reader"] == "Windows.Media.Ocr" and identity["language"].startswith("ko")
    result, proof = wrv.apply_windows_ocr(native, graph, source, tenant_id=TENANT)
    (record,) = proof["records"]
    assert record["attempts"][0]["status"] == "read"
    assert proof["policy"]["engine"] == identity
    # Whatever the engine reads, the verdict is exact equality with the native words.
    final = record["attempts"][-1]["text"]
    assert (record["status"] == "verified") == (_normalized(final) == _normalized(TEXT))
    assert [b.quality == "verified" for b in result.blocks] == [record["status"] == "verified"]
    assert wrv.replay_windows_ocr(proof, native, graph, source, tenant_id=TENANT) == result


def _private_crop(page_number, x_range, top_range):
    with pdfplumber.open(PRIVATE) as document:
        page = document.pages[page_number - 1]
        words = [
            w
            for w in page.extract_words()
            if x_range[0] <= w["x0"] <= x_range[1] and top_range[0] <= w["top"] <= top_range[1]
        ]
        box = (
            min(w["x0"] for w in words) - 1,
            min(w["top"] for w in words) - 1,
            max(w["x1"] for w in words) + 1,
            max(w["bottom"] for w in words) + 1,
        )
        with page.to_image(resolution=wrv.RESOLUTION_DPI).original as image:
            crops = [wrv._crop_png(image, box, padding)[0] for padding in wrv.PADDINGS]
    return " ".join(w["text"] for w in words), crops


@windows_only
@pytest.mark.skipif(not PRIVATE.is_file(), reason="private Kia original not present")
@pytest.mark.parametrize(
    ("page", "x_range", "top_range", "must_contain"),
    [
        (2, (0, 845), (145, 172), "지속가능경영보고서"),
        (28, (560, 845), (155, 315), "시스템"),
    ],
)
def test_real_private_paragraph_crops(page, x_range, top_range, must_contain):
    assert sha256(PRIVATE.read_bytes()).hexdigest() == PRIVATE_SHA
    raw, crops = _private_crop(page, x_range, top_range)
    helper = windows_ocr.helper_sha256()
    readings = [windows_ocr.read_windows_ocr(png, expected_helper_sha256=helper) for png in crops]
    assert all(r["status"] == "read" for r in readings)
    identity = windows_ocr.engine_identity(expected_helper_sha256=helper)
    assert all(r["engine"] == identity for r in readings)
    # Scalar text only, and the engine really read this crop's paragraph.
    assert all(set(r) == {"status", "text", "engine"} for r in readings)
    assert any(must_contain in r["text"] for r in readings)
    # Recorded 2026-09-29 on build 26200.9457: Korean glyph substitutions (탄->단,
    # 동->능 ...) make exact equality fail at the pinned 216 dpi, so these real
    # paragraphs stay unresolved -- the path fails closed rather than promoting.
    assert all(_normalized(r["text"]) != _normalized(raw) for r in readings)
