"""Opt-in static pushbutton gate: widened Upstage eligibility, strict rejections."""

from dataclasses import replace
from hashlib import sha256
from io import BytesIO

import pytest
from proofops.application.ingest.graph_fusion import fuse_candidates
from proofops.domain.provenance import canonical_hash

from tests.acceptance.test_parsing import FOREIGN, TENANT, candidate, pdf
from tests.integration.test_upstage_parse import fixed_pricing_date  # noqa: F401

TEXT = "Page 1 emissions 1234 tCO2e"
CLEAR = [520, 20, 540, 40]
UNSUPPORTED = dict(
    status="unresolved", reason="rendered_reader_unavailable", error="UnsupportedPlatform"
)


@pytest.fixture(autouse=True)
def _off_macos(monkeypatch):
    from proofops.adapters.local import source_verification

    # Same verdict on every platform: only the off-macOS scope is under test.
    monkeypatch.setattr(source_verification, "_rendered_text", lambda *a, **k: UNSUPPORTED)


def _source(case="static", rect=CLEAR, base=None):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf() if base is None else base)))
    page = writer.pages[0]
    appearance = DecodedStreamObject()
    appearance.set_data(b"0 0 1 rg 0 0 20 20 re f")
    appearance.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([NumberObject(v) for v in (0, 0, 20, 20)]),
        }
    )
    action = DictionaryObject(
        {NameObject("/S"): NameObject("/Named"), NameObject("/N"): NameObject("/NextPage")}
    )
    widget = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Btn"),
            NameObject("/Ff"): NumberObject(65536),
            NameObject("/T"): TextStringObject("next"),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): writer._add_object(appearance)}),
            NameObject("/A"): action,
        }
    )
    form = DictionaryObject({NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")})
    if case == "checkbox":
        widget[NameObject("/Ff")] = NumberObject(0)
    elif case == "javascript_action":
        widget[NameObject("/A")] = DictionaryObject(
            {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("")}
        )
    elif case == "additional_actions":
        widget[NameObject("/AA")] = DictionaryObject()
    elif case == "malformed_rect":
        widget[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in CLEAR[:3]])
    elif case == "empty_rect":
        widget[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in (520, 20, 520, 40)])
    elif case in {"no_zoom", "no_rotate", "toggle_no_view"}:
        flag = {"no_zoom": 8, "no_rotate": 16, "toggle_no_view": 256}[case]
        widget[NameObject("/F")] = NumberObject(4 | flag)
    elif case == "need_appearances":
        form[NameObject("/NeedAppearances")] = NameObject("/true")
    reference = writer._add_object(widget)
    form[NameObject("/Fields")] = ArrayObject([reference])
    annotations = ArrayObject([reference])
    if case == "unsupported_annotation":
        annotations.append(
            writer._add_object(
                DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/FreeText"),
                        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in CLEAR]),
                    }
                )
            )
        )
    elif case == "link":
        annotations.append(
            writer._add_object(
                DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Link"),
                        # A Link without appearance paints nothing, even over the text.
                        NameObject("/Rect"): ArrayObject(
                            [FloatObject(v) for v in (70, 710, 300, 740)]
                        ),
                    }
                )
            )
        )
    elif case == "page_actions":
        page[NameObject("/AA")] = DictionaryObject()
    page[NameObject("/Annots")] = annotations
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(form)
    if case == "optional_content":
        writer._root_object[NameObject("/OCProperties")] = DictionaryObject()
    elif case == "document_javascript":
        writer._root_object[NameObject("/Names")] = DictionaryObject(
            {NameObject("/JavaScript"): DictionaryObject()}
        )
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _attested(source):
    from proofops.adapters.local.source_verification import attest_native_sources

    batch = replace(
        candidate("widget", [("P", "paragraph", TEXT, (70, 710, 300, 740), ())]),
        source_sha256=sha256(source).hexdigest(),
    )
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    native = attest_native_sources(graph, source, tenant_id=TENANT, geometry_mode="glyph")
    return graph, native


def _snapshot(graph, *, opt_in):
    from proofops.adapters.local.native_widget_visibility import (
        native_widget_visibility_policy,
    )

    snapshot = dict(native_upstage_ocr_policy={}, selected_pages=[1])
    if opt_in:
        policy = native_widget_visibility_policy()
        snapshot.update(
            native_upstage_ocr_widget_visibility=policy,
            native_upstage_ocr_widget_visibility_hash=canonical_hash(policy),
        )
    return snapshot


@pytest.mark.parametrize("case", ["static", "link"])
def test_clear_static_pushbutton_widens_upstage_eligibility_only_on_opt_in(case):
    from proofops.adapters.local.native_upstage_ocr_widget import eligible_words
    from proofops.adapters.local.native_widget_visibility import attest_widget_visibility

    source = _source(case)
    graph, native = _attested(source)
    (base,) = native["records"]
    assert (base["status"], base["reason"]) == (
        "unresolved",
        "interactive_visibility_requires_review",
    )
    proof = attest_widget_visibility(native, graph, source, tenant_id=TENANT)
    (record,) = proof["records"]
    assert record["status"] == "eligible"
    assert " ".join(w["text"] for w in record["words"]) == TEXT
    assert proof["base_attestation_sha256"] == canonical_hash(native)
    legacy = eligible_words(_snapshot(graph, opt_in=False), native, graph, source, tenant_id=TENANT)
    widened = eligible_words(_snapshot(graph, opt_in=True), native, graph, source, tenant_id=TENANT)
    assert legacy == {}
    assert widened == {base["source_id"]: TEXT}
    # Eligibility is never verification: the graph itself is unchanged.
    assert all(block.quality == "unverified" for block in graph.blocks)


@pytest.mark.parametrize(
    "case,rect,reason",
    [
        ("static", [100, 715, 120, 735], "annotation_overlaps_paragraph"),
        ("static", [301, 715, 320, 735], "annotation_overlaps_paragraph"),
        ("checkbox", CLEAR, "form_not_static_pushbuttons"),
        ("javascript_action", CLEAR, "form_not_static_pushbuttons"),
        ("additional_actions", CLEAR, "form_not_static_pushbuttons"),
        ("need_appearances", CLEAR, "form_not_static_pushbuttons"),
        ("optional_content", CLEAR, "document_dynamic_content"),
        ("document_javascript", CLEAR, "document_dynamic_content"),
        ("page_actions", CLEAR, "page_dynamic_content"),
        ("unsupported_annotation", CLEAR, "annotation_unsupported"),
        ("no_zoom", CLEAR, "annotation_dynamic"),
        ("no_rotate", CLEAR, "annotation_dynamic"),
        ("toggle_no_view", CLEAR, "annotation_dynamic"),
        ("malformed_rect", CLEAR, "annotation_malformed"),
        ("empty_rect", CLEAR, "annotation_malformed"),
    ],
)
def test_overlapping_dynamic_unknown_or_malformed_controls_stay_unresolved(case, rect, reason):
    from proofops.adapters.local.native_upstage_ocr_widget import eligible_words
    from proofops.adapters.local.native_widget_visibility import attest_widget_visibility

    source = _source(case, rect)
    graph, native = _attested(source)
    assert native["records"][0]["reason"] == "interactive_visibility_requires_review"
    (record,) = attest_widget_visibility(native, graph, source, tenant_id=TENANT)["records"]
    assert (record["status"], record["reason"], record["words"]) == ("unresolved", reason, [])
    assert (
        eligible_words(_snapshot(graph, opt_in=True), native, graph, source, tenant_id=TENANT) == {}
    )


def test_native_text_gate_still_applies_after_the_visibility_gate():
    from proofops.adapters.local.native_widget_visibility import attest_widget_visibility
    from proofops.adapters.local.source_verification import attest_native_sources

    source = _source()
    batch = replace(
        candidate("wrong", [("P", "paragraph", TEXT + "5", (70, 710, 300, 740), ())]),
        source_sha256=sha256(source).hexdigest(),
    )
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    native = attest_native_sources(graph, source, tenant_id=TENANT, geometry_mode="glyph")
    (record,) = attest_widget_visibility(native, graph, source, tenant_id=TENANT)["records"]
    assert (record["status"], record["reason"]) == ("unresolved", "text_mismatch")


def test_forged_or_foreign_base_receipt_is_rejected():
    from proofops.adapters.local.native_widget_visibility import attest_widget_visibility

    source = _source()
    graph, native = _attested(source)
    forged = dict(native, records=[dict(native["records"][0], reason="made_up")])
    for args, tenant in (((forged, graph, source), TENANT), ((native, graph, source), FOREIGN)):
        with pytest.raises(ValueError):
            attest_widget_visibility(*args, tenant_id=tenant)
    with pytest.raises(ValueError):
        attest_widget_visibility(native, graph, source + b"x", tenant_id=TENANT)


def test_snapshot_opt_in_is_hash_pinned_and_fails_closed():
    from proofops.adapters.local.native_upstage_ocr_widget import widget_policy
    from proofops.application.runs import (
        validate_upstage_ocr_snapshot,
        validate_upstage_ocr_widget_policy,
    )

    snapshot = _snapshot(None, opt_in=True)
    policy = snapshot["native_upstage_ocr_widget_visibility"]
    assert widget_policy(snapshot) == policy
    assert widget_policy(_snapshot(None, opt_in=False)) is None
    assert validate_upstage_ocr_widget_policy(policy) == policy
    for broken in (
        dict(snapshot, native_upstage_ocr_widget_visibility_hash="0" * 64),
        dict(snapshot, native_upstage_ocr_widget_visibility=dict(policy, margin_pt=0.0)),
        dict(snapshot, native_upstage_ocr_widget_visibility=dict(policy, verifier_sha256="1" * 64)),
        dict(
            snapshot, native_upstage_ocr_widget_visibility=dict(policy, composition_sha256="1" * 64)
        ),
        {k: v for k, v in snapshot.items() if k != "native_upstage_ocr_widget_visibility"},
    ):
        with pytest.raises(ValueError):
            widget_policy(broken)
    with pytest.raises(ValueError):
        validate_upstage_ocr_widget_policy(dict(policy, margin_pt=10.0))
    # Half of the pair is never accepted by the snapshot shape check.
    with pytest.raises(ValueError, match="UPSTAGE_OCR_SNAPSHOT_INVALID"):
        validate_upstage_ocr_snapshot(
            {"native_upstage_ocr_widget_visibility_hash": canonical_hash(policy)}
        )


def test_pinned_verifiers_are_untouched_so_old_runs_replay():
    from pathlib import Path

    from proofops.adapters.local import frozen_native_replay, native_upstage_ocr
    from proofops.adapters.local.run_artifacts import native_paragraph_policy

    # The live base verifier is still the vendored, hash-pinned cpu_swift bundle.
    bundle = frozen_native_replay._PINNED_VERIFIERS["native_paragraph_glyph_v2_cpu_swift"]
    assert (
        native_paragraph_policy()["verifier_sha256"]
        == bundle["expected_files"]["source_verification.py"]
    )
    # The opt-in lives in separate files; the Upstage v1 helper keeps its own bytes.
    text = Path(native_upstage_ocr.__file__).read_text(encoding="utf-8")
    assert "widget" not in text


def test_api_settings_pass_the_opt_in_through_only_with_upstage_ocr():
    from proofops.adapters.local import native_upstage_ocr
    from proofops.adapters.local.native_widget_visibility import (
        native_widget_visibility_policy,
    )
    from proofops_api.local_runtime import _upstage_ocr

    widget = native_widget_visibility_policy()
    binding = "11111111-1111-4111-8111-111111111111"
    settings = dict(
        upstage_ocr_runtime_binding_id=binding,
        upstage_ocr_policy=native_upstage_ocr.native_upstage_ocr_policy(),
    )
    assert "upstage_ocr_widget_visibility" not in _upstage_ocr(settings, "upstage_probe")
    opted = dict(settings, upstage_ocr_widget_visibility=widget)
    assert _upstage_ocr(opted, "upstage_probe") == opted
    for bad in (
        {"upstage_ocr_widget_visibility": widget},
        dict(settings, upstage_ocr_widget_visibility=dict(widget, schema="other")),
    ):
        with pytest.raises(ValueError):
            _upstage_ocr(bad, "upstage_probe")


@pytest.mark.skipif(
    __import__("sys").platform == "darwin", reason="off-macOS Upstage eligibility only"
)
@pytest.mark.parametrize("opt_in", [False, True])
@pytest.mark.usefixtures("fixed_pricing_date")
def test_worker_run_corroborates_static_pushbutton_page_only_on_opt_in(
    tmp_path, monkeypatch, opt_in
):
    import json

    from proofops.adapters.local.native_widget_visibility import (
        native_widget_visibility_policy,
    )
    from proofops.application.runs import RunService

    from tests.integration import test_native_upstage_ocr_worker as worker
    from tests.integration.test_raster_parser_worker import prose_pdf

    source = _source(base=prose_pdf(heading=True))
    if opt_in:
        # The RunService kwarg the launcher sets; frozen into the run snapshot.
        original = RunService.__init__

        def opted(self, *args, **kwargs):
            original(self, *args, **kwargs)
            self.upstage_ocr_widget_visibility = native_widget_visibility_policy()

        monkeypatch.setattr(RunService, "__init__", opted)
    service, runner, run_id, _probe, calls = worker.setup_run(tmp_path, monkeypatch, pdf=source)
    snapshot = service.store.snapshot(worker.AUTH.tenant_id, run_id)
    assert ("native_upstage_ocr_widget_visibility" in snapshot) is opt_in
    identity = dict(tenant_id=worker.AUTH.tenant_id, run_id=run_id)
    assert runner.run_once(**identity) == "committed"
    payload = json.loads(service.store.jobs.read_checkpoint(worker._message(service, run_id)))
    reasons = {r["reason"] for r in payload["native_paragraph_attestation"]["records"]}
    assert "interactive_visibility_requires_review" in reasons
    coverage = payload["native_paragraph_upstage_ocr_coverage"]
    runner.store = type(service.store)(service.store.path)
    graph = runner.load_graph(**identity)
    if not opt_in:
        assert coverage["eligible_source_ids"] == [] and calls == []
        assert all(b.quality != "verified" for b in graph.blocks if b.kind == "paragraph")
        return
    assert len(coverage["eligible_source_ids"]) == 1 and len(calls) == 1
    assert coverage["corroborated_source_ids"] == coverage["eligible_source_ids"]
    verified = [b for b in graph.blocks if b.source_id in coverage["eligible_source_ids"]]
    assert [b.quality for b in verified] == ["verified"]
    assert verified[0].raw_text.startswith("The company reduced emissions by 1234 tCO2e.")
