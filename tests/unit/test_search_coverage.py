"""Full-document search-coverage receipt producer (R00 §12 common guard).

Synthetic PDFs are real bytes read by pdfplumber; the run state is injected so
each prerequisite can be broken one at a time. No model, network, key or paid call.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from proofops.adapters.local import search_coverage_store as store_module
from proofops.adapters.local.search_coverage_store import LocalSearchCoverageStore
from proofops.application.evidence import search_coverage as coverage
from proofops.application.ingest.graph_fusion import QualityIssue, fuse_candidates
from proofops.domain.provenance import canonical_hash
from proofops.domain.rulepacks import canonical_json

from tests.acceptance.test_parsing import TENANT, candidate
from tests.integration.test_batch_attestation_cache import discovery_for

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import produce_search_coverage as cli  # noqa: E402

RUN = "22222222-2222-4222-8222-222222222222"
OTHER_TENANT = "99999999-9999-4999-8999-999999999999"
LINES = (
    "Scope 1 emissions fell 12 percent in 2024",
    "Our 2030 target is a 40 percent reduction",
    "Water withdrawal is reported by site",
)
BOX = (70, 710, 400, 741)
QUERIES = ["baseline year", "기준연도"]


def pdf(lines, *, image_page=None, blank_page=None):
    """One Helvetica line per page; optionally one page with an image XObject."""
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
    for number, text in enumerate(lines, start=1):
        page = writer.add_blank_page(width=600, height=800)
        resources = {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        rows = (text,) if isinstance(text, str) else text
        content = (
            b""
            if number == blank_page
            else b" ".join(
                f"BT /F1 12 Tf 72 {720 - 40 * row} Td ({line}) Tj ET".encode()
                for row, line in enumerate(rows)
            )
        )
        if number == image_page:
            image = DecodedStreamObject()
            image.set_data(b"\x00\xff\x00\xff")
            image.update(
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
                {NameObject("/Im1"): writer._add_object(image)}
            )
            content += b" q 100 0 0 100 100 300 cm /Im1 Do Q"
        page[NameObject("/Resources")] = DictionaryObject(resources)
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/MediaBox")] = ArrayObject([NumberObject(v) for v in (0, 0, 600, 800)])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def graph_for(source, texts, *, verified=True, pages=None):
    """One paragraph block per page, pinned to the PDF bytes; ``verified`` stands in
    for the native replay result the real loader returns."""
    batch = candidate("span", [(f"P{i}", "paragraph", t, BOX, ()) for i, t in enumerate(texts)])
    numbers = pages or range(1, len(texts) + 1)
    batch = replace(
        batch,
        source_sha256=sha256(source).hexdigest(),
        blocks=tuple(
            replace(block, source=replace(block.source, physical_page=page))
            for block, page in zip(batch.blocks, numbers, strict=True)
        ),
    )
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    if verified:
        graph = replace(graph, blocks=tuple(replace(b, quality="verified") for b in graph.blocks))
    return graph


def claim_of(graph, revision=1):
    block = graph.blocks[0]
    ref = replace(block.source_ref(), char_start=0, char_end=7, quote=block.raw_text[:7])
    return replace(discovery_for(graph, (ref,)).claims[0], revision=revision)


def state(source=None, *, graph=None, selected=None, tenant=TENANT, **identity):
    source = source if source is not None else pdf(LINES)
    graph = graph if graph is not None else graph_for(source, LINES)
    ident = dict(
        tenant_id=tenant,
        run_id=RUN,
        document_version_id=graph.document_version_id,
        object_version_id="object-1",
        input_hash="i" * 64,
        source_sha256=graph.source_sha256,
        parse_manifest_id=graph.parse_manifest_id,
        rulepack_sha256="r" * 64,
    )
    ident.update(identity)
    return dict(
        identity=ident,
        graph=graph,
        claim=claim_of(graph),
        source=source,
        selected_pages=selected or [1, 2, 3],
        registered_page_count=3,
    )


def make_store(tmp_path, current):
    """``current`` is a one-item list so a test can swap the run state later."""
    return LocalSearchCoverageStore(tmp_path / "coverage", lambda t, r, c: current[0])


def produce(store, element="G3", queries=QUERIES):
    return store.produce(TENANT, RUN, "0", element, list(queries))


def review_for(receipt, decision="absent_confirmed", **overrides):
    hits = {
        s: "not a baseline year for this claim"
        for e in receipt["search_log"]
        for s in e["literal_hit_source_ids"] + e["term_hit_source_ids"]
    }
    review = dict(
        schema=coverage.REVIEW_SCHEMA,
        receipt_sha256=receipt["artifact_sha256"],
        reviewed_corpus_sha256=receipt["corpus"]["corpus_sha256"],
        reviewed_block_count=receipt["corpus"]["block_count"],
        decision=decision,
        rationale="Every block of all three pages was read; none states a baseline year.",
        hit_dispositions=hits,
        reviewer=dict(kind="ai_delegated", id="delegated-reviewer-1"),
        reviewed_at=datetime(2026, 9, 29, 9, tzinfo=UTC).isoformat(),
    )
    review.update(overrides)
    return review


# --- positive ------------------------------------------------------------------


def test_complete_known_document_is_eligible_only_for_delegated_review(tmp_path):
    store = make_store(tmp_path, [state()])
    receipt = produce(store)
    assert receipt["search_prerequisites_complete"] is True
    assert [p["status"] for p in receipt["pages"]] == ["complete"] * 3
    assert receipt["element_state_effect"] == "none"
    assert receipt["attribution_scope"] == "claim_local"
    # Exhaustive: every block examined for every query, no top-k.
    assert all(e["examined_block_count"] == 3 for e in receipt["search_log"])
    # Without a review the consumer still gets unknown, even with zero hits.
    view = store.absence_prerequisite(TENANT, RUN, "0", "G3", receipt["artifact_sha256"])
    assert view["element_state_candidate"] == "unknown"
    request = store.review_request(TENANT, RUN, receipt["artifact_sha256"])
    assert request["schema"] == coverage.REVIEW_REQUEST_SCHEMA
    assert request["corpus"]["block_count"] == 3 and "Zero lexical hits" in request["instructions"]
    recorded = store.record_review(TENANT, RUN, review_for(receipt))
    view = store.absence_prerequisite(
        TENANT, RUN, "0", "G3", receipt["artifact_sha256"], recorded["artifact_sha256"]
    )
    assert view["element_state_candidate"] == "absent"


def test_hits_must_each_be_disposed_and_non_absent_decisions_stay_unknown(tmp_path):
    store = make_store(tmp_path, [state()])
    receipt = produce(store, element="G1", queries=["2030 target", "percent"])
    hits = {s for e in receipt["search_log"] for s in e["literal_hit_source_ids"]}
    assert hits  # "2030 target" literally on page 2
    with pytest.raises(ValueError, match="SEARCH_REVIEW_HITS_UNADDRESSED"):
        store.record_review(TENANT, RUN, review_for(receipt, hit_dispositions={}))
    for decision in ("not_absent", "undetermined"):
        recorded = store.record_review(TENANT, RUN, review_for(receipt, decision=decision))
        view = store.absence_prerequisite(
            TENANT, RUN, "0", "G1", receipt["artifact_sha256"], recorded["artifact_sha256"]
        )
        assert view["element_state_candidate"] == "unknown"


def test_receipt_is_deterministic_and_write_is_idempotent(tmp_path):
    store = make_store(tmp_path, [state()])
    first, second = produce(store), produce(store)
    assert canonical_json(first) == canonical_json(second)
    replayed, _ = store.replay(TENANT, RUN, first["artifact_sha256"])
    assert replayed == first
    files = list((tmp_path / "coverage").rglob("*.json"))
    assert len(files) == 1 and files[0].name == first["artifact_sha256"] + ".json"


# --- incomplete documents stay unknown ------------------------------------------


@pytest.mark.parametrize(
    "build, reason",
    [
        # Page 3 was never parsed: native text exists with no block.
        (
            lambda: state(graph=graph_for(pdf(LINES), LINES[:2])),
            "no_parsed_blocks",
        ),
        (lambda: state(selected=[1, 2]), "page_not_in_run_scope"),
        (lambda: state(pdf(LINES, image_page=2)), "image_region_unread"),
        (lambda: state(pdf(LINES, blank_page=3)), "no_native_text_layer"),
        (
            lambda: state(graph=graph_for(pdf(LINES), LINES, verified=False)),
            "block_not_source_verified",
        ),
        (
            lambda: state(graph=graph_for(pdf(LINES), (LINES[0], "Our 2031 target", LINES[2]))),
            "block_text_not_native_word_sequence",
        ),
        # A truncated block over the whole line: words exist but the text omits them.
        (
            lambda: state(graph=graph_for(pdf(LINES), (LINES[0], "Our", LINES[2]))),
            "block_text_not_native_word_sequence",
        ),
    ],
)
def test_incomplete_prerequisites_never_allow_absence(tmp_path, build, reason):
    store = make_store(tmp_path, [build()])
    receipt = produce(store)
    assert receipt["search_prerequisites_complete"] is False
    assert any(reason in page["reasons"] for page in receipt["pages"]), receipt["pages"]
    view = store.absence_prerequisite(TENANT, RUN, "0", "G3", receipt["artifact_sha256"])
    assert view["element_state_candidate"] == "unknown" and view["incomplete_pages"]
    with pytest.raises(ValueError, match="SEARCH_PREREQUISITES_INCOMPLETE"):
        store.review_request(TENANT, RUN, receipt["artifact_sha256"])
    # Even a well-formed "absent" review cannot be recorded against it.
    with pytest.raises(ValueError, match="SEARCH_PREREQUISITES_INCOMPLETE"):
        store.record_review(TENANT, RUN, review_for(receipt))


def test_open_quality_issue_on_a_page_keeps_it_incomplete(tmp_path):
    source = pdf(LINES)
    graph = graph_for(source, LINES)
    issue = QualityIssue("q1", "table_unresolved", 2, (graph.blocks[1].source_id,), "open", "x")
    store = make_store(tmp_path, [state(source, graph=replace(graph, issues=(issue,)))])
    receipt = produce(store)
    assert receipt["incomplete_pages"] == [2]
    assert "quality_issue_on_page" in receipt["pages"][1]["reasons"]


# --- replay refuses every changed input ---------------------------------------------


def test_changed_original_pdf_stale_input_and_new_claim_revision_are_refused(tmp_path):
    current = [state()]
    store = make_store(tmp_path, current)
    receipt = produce(store)
    sha = receipt["artifact_sha256"]
    original = current[0]
    # Different original bytes (identity still claims the old hash).
    current[0] = dict(original, source=pdf((LINES[0], LINES[1], "Water use by site")))
    with pytest.raises(ValueError, match="SEARCH_SOURCE_MISMATCH"):
        store.replay(TENANT, RUN, sha)
    # Stale run input: the frozen snapshot hash moved.
    current[0] = state(input_hash="j" * 64)
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.replay(TENANT, RUN, sha)
    # A new claim revision is a different claim input.
    current[0] = dict(original, claim=claim_of(original["graph"], revision=2))
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.replay(TENANT, RUN, sha)
    current[0] = original
    assert store.replay(TENANT, RUN, sha)[0] == receipt


def test_cross_tenant_and_wrong_claim_or_element_are_refused(tmp_path):
    current = [state()]
    store = make_store(tmp_path, current)
    receipt = produce(store)
    sha = receipt["artifact_sha256"]
    # The file is simply not in another tenant's directory...
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_NOT_FOUND"):
        store.replay(OTHER_TENANT, RUN, sha)
    # ...and a copy placed there still binds the original tenant.
    source = store._dir(TENANT, RUN, "receipts") / f"{sha}.json"
    target = store._dir(OTHER_TENANT, RUN, "receipts")
    target.mkdir(parents=True)
    (target / source.name).write_bytes(source.read_bytes())
    current[0] = state(tenant=OTHER_TENANT)
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.replay(OTHER_TENANT, RUN, sha)
    current[0] = state()
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.absence_prerequisite(TENANT, RUN, "0", "G1", sha)
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.absence_prerequisite(TENANT, RUN, "1", "G3", sha)


def test_tampered_repinned_receipt_is_refused(tmp_path):
    store = make_store(tmp_path, [state(selected=[1, 2])])
    receipt = produce(store)
    assert receipt["search_prerequisites_complete"] is False
    forged = dict(receipt, search_prerequisites_complete=True, incomplete_pages=[])
    forged = {k: v for k, v in forged.items() if k != "artifact_sha256"}
    forged["artifact_sha256"] = canonical_hash(forged)
    directory = store._dir(TENANT, RUN, "receipts")
    (directory / f"{forged['artifact_sha256']}.json").write_text(canonical_json(forged))
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.replay(TENANT, RUN, forged["artifact_sha256"])
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.absence_prerequisite(TENANT, RUN, "0", "G3", forged["artifact_sha256"])
    # A file whose name and content hash disagree is refused too.
    (directory / ("0" * 64 + ".json")).write_text(canonical_json(receipt))
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_RECEIPT_MISMATCH"):
        store.replay(TENANT, RUN, "0" * 64)


def test_tampered_stored_review_is_refused(tmp_path):
    store = make_store(tmp_path, [state()])
    receipt = produce(store)
    recorded = store.record_review(TENANT, RUN, review_for(receipt, decision="undetermined"))
    path = store._dir(TENANT, RUN, "reviews") / f"{recorded['artifact_sha256']}.json"
    body = json.loads(path.read_text())
    body["decision"] = "absent_confirmed"
    path.chmod(0o644)
    path.write_text(canonical_json(body))
    with pytest.raises(ValueError, match="SEARCH_REVIEW_INVALID"):
        store.absence_prerequisite(
            TENANT, RUN, "0", "G3", receipt["artifact_sha256"], recorded["artifact_sha256"]
        )


# --- input validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "queries",
    [[], ["same", "same"], [" padded"], ["!!!"], ["x" * 201], [f"q{i}" for i in range(33)]],
)
def test_query_list_must_be_explicit_and_bounded(tmp_path, queries):
    with pytest.raises(ValueError, match="SEARCH_QUERIES_INVALID"):
        produce(make_store(tmp_path, [state()]), queries=queries)


def test_unknown_element_and_mismatched_identity_are_refused(tmp_path):
    with pytest.raises(ValueError, match="SEARCH_ELEMENT_INVALID"):
        produce(make_store(tmp_path, [state()]), element="Z9")
    with pytest.raises(ValueError, match="SEARCH_COVERAGE_IDENTITY_MISMATCH"):
        produce(make_store(tmp_path, [state(document_version_id="other")]))


@pytest.mark.parametrize(
    "overrides, code",
    [
        (dict(rationale="too short"), "SEARCH_REVIEW_RATIONALE_REQUIRED"),
        (dict(reviewed_corpus_sha256="0" * 64), "SEARCH_REVIEW_SCOPE_MISMATCH"),
        (dict(reviewed_block_count=2), "SEARCH_REVIEW_SCOPE_MISMATCH"),
        (dict(receipt_sha256="0" * 64), "SEARCH_COVERAGE_RECEIPT_NOT_FOUND"),
        (dict(decision="absent"), "SEARCH_REVIEW_INVALID"),
        (dict(reviewer=dict(kind="llm", id="x")), "SEARCH_REVIEW_INVALID"),
        (dict(reviewed_at="2026-09-29T09:00:00"), "SEARCH_REVIEW_INVALID"),
        (dict(extra=True), "SEARCH_REVIEW_INVALID"),
    ],
)
def test_review_must_cover_the_whole_corpus_with_rationale(tmp_path, overrides, code):
    store = make_store(tmp_path, [state()])
    receipt = produce(store)
    with pytest.raises(ValueError, match=code):
        store.record_review(TENANT, RUN, review_for(receipt, **overrides))


def test_page_facts_come_from_the_original_bytes_only():
    facts = store_module.pdf_page_facts(pdf(LINES, image_page=2))
    assert [f["page"] for f in facts] == [1, 2, 3]
    assert [f["images"] for f in facts] == [0, 1, 0]
    assert all(f["words"] for f in facts)
    first = facts[0]["words"][0]
    assert first["index"] == 0 and first["text"] == "Scope" and len(first["bbox"]) == 4
    with pytest.raises(ValueError, match="PAGE_FACTS_INVALID"):
        coverage.evaluate_pages(
            graph_for(pdf(LINES), LINES), facts[:2], registered_page_count=3, selected_pages=[1]
        )


# --- CLI -------------------------------------------------------------------------------


def test_cli_produce_replay_and_prerequisite(tmp_path, monkeypatch, capsys):
    current = [state()]
    monkeypatch.setattr(store_module, "run_loader", lambda *a: (lambda t, r, c: current[0]))
    database = tmp_path / "state.sqlite3"
    database.write_bytes(b"")
    base = ["--database-path", str(database), "--tenant-id", TENANT, "--run-id", RUN]
    produce_args = ["produce", *base, "--claim-id", "0", "--element", "G3"]
    assert cli.main([*produce_args, "--query", "baseline year", "--check", "--summary"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["written"] is False and checked["search_prerequisites_complete"] is True
    assert not (tmp_path / "search-coverage").exists()
    assert cli.main([*produce_args, "--query", "baseline year"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    sha = receipt["artifact_sha256"]
    assert cli.main(["replay", *base, "--receipt-sha256", sha, "--summary"]) == 0
    assert json.loads(capsys.readouterr().out)["receipt_sha256"] == sha
    prerequisite = ["prerequisite", *base, "--claim-id", "0", "--element", "G3"]
    assert cli.main([*prerequisite, "--receipt-sha256", sha]) == 0
    assert json.loads(capsys.readouterr().out)["element_state_candidate"] == "unknown"
    # Empty query list is refused by the producer, not silently accepted.
    assert cli.main(produce_args) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "SEARCH_QUERIES_INVALID"


# --- word-level soundness regressions (root review) ---------------------------------

LINE_A = "reduce emissions 40 percent by 2030"
LINE_B = "2030 by percent 40 emissions reduce"  # same words, same character bag
BOX_A = (70, 710, 400, 741)  # bottom-left points around the baseline at y=720
BOX_B = (70, 670, 400, 701)  # around the baseline at y=680


def blocks_graph(source, specs):
    """``specs``: (page, text, bottom-left box); every block verified."""
    batch = candidate(
        "span", [(f"B{i}", "paragraph", t, box, ()) for i, (_, t, box) in enumerate(specs)]
    )
    batch = replace(
        batch,
        source_sha256=sha256(source).hexdigest(),
        blocks=tuple(
            replace(block, source=replace(block.source, physical_page=page))
            for block, (page, _, _) in zip(batch.blocks, specs, strict=True)
        ),
    )
    graph = fuse_candidates((batch,), tenant_id=TENANT)
    return replace(graph, blocks=tuple(replace(b, quality="verified") for b in graph.blocks))


def two_line_state(specs, *, lines=(LINE_A, LINE_B), duplicate_first=False):
    source = pdf((lines, LINES[1], LINES[2]))
    rest = [(2, LINES[1], BOX), (3, LINES[2], BOX)]
    graph = blocks_graph(source, [*specs, *rest])
    if duplicate_first:
        # Fusion already merges identical candidates, so inject a duplicated
        # canonical block (same text/box, another source_id) directly.
        copy = replace(graph.blocks[0], source_id="ffffffff-ffff-4fff-8fff-ffffffffffff")
        graph = replace(graph, blocks=(*graph.blocks, copy))
    return state(source, graph=graph)


def page_one(tmp_path, specs, **kwargs):
    receipt = produce(make_store(tmp_path, [two_line_state(specs, **kwargs)]))
    return receipt, receipt["pages"][0]


def test_real_generated_multi_line_pdf_is_complete_word_by_word(tmp_path):
    receipt, page = page_one(tmp_path, [(1, LINE_A, BOX_A), (1, LINE_B, BOX_B)])
    assert page["status"] == "complete", page
    assert page["native_word_count"] == page["attributed_word_count"] == 12
    assert receipt["search_prerequisites_complete"] is True


def test_same_character_bag_cannot_mask_an_omitted_phrase(tmp_path):
    """One block claims both lines' words at line A's box; line B has no block.
    The page's character multiset still matches exactly (the old check passed)."""
    _, page = page_one(tmp_path, [(1, f"{LINE_A} {LINE_B}", BOX_A)])
    assert page["status"] == "incomplete"
    assert "native_word_not_attributed_to_verified_block" in page["reasons"]
    assert "block_text_not_native_word_sequence" in page["reasons"]
    assert page["unattributed_word_indices"] == list(range(6, 12))


def test_reordered_same_bag_text_is_refused(tmp_path):
    _, page = page_one(tmp_path, [(1, LINE_B, BOX_A), (1, LINE_A, BOX_B)])
    assert page["reasons"] == ["block_text_not_native_word_sequence"]
    assert len(page["blocks_text_mismatch"]) == 2


def test_duplicated_block_cannot_stand_in_for_an_omitted_line(tmp_path):
    """Line A's block appears twice and line B has none; the old bag check was
    satisfied because B uses the same characters as A."""
    _, page = page_one(tmp_path, [(1, LINE_A, BOX_A)], duplicate_first=True)
    assert page["block_count"] == 2
    assert page["status"] == "incomplete"
    assert len(page["deduplicated_source_ids"]) == 1
    assert page["reasons"] == ["native_word_not_attributed_to_verified_block"]


def test_an_exact_duplicate_block_is_deduplicated_not_double_counted(tmp_path):
    _, page = page_one(tmp_path, [(1, LINE_A, BOX_A), (1, LINE_B, BOX_B)], duplicate_first=True)
    assert page["status"] == "complete", page
    assert len(page["deduplicated_source_ids"]) == 1
    assert page["attributed_word_count"] == 12


def test_geometry_mismatch_is_refused(tmp_path):
    # Correct text, box moved away from the line it claims.
    _, page = page_one(tmp_path, [(1, LINE_A, (70, 400, 400, 431)), (1, LINE_B, BOX_B)])
    assert "block_geometry_without_native_words" in page["reasons"]
    assert "native_word_not_attributed_to_verified_block" in page["reasons"]
    # Correct text, boxes swapped between the two lines.
    _, page = page_one(tmp_path, [(1, LINE_A, BOX_B), (1, LINE_B, BOX_A)])
    assert page["reasons"] == ["block_text_not_native_word_sequence"]


def test_overlapping_non_identical_blocks_are_refused(tmp_path):
    _, page = page_one(
        tmp_path,
        [(1, LINE_A, BOX_A), (1, "reduce emissions", (70, 705, 300, 745)), (1, LINE_B, BOX_B)],
    )
    assert "native_word_attributed_to_overlapping_blocks" in page["reasons"]
    assert page["status"] == "incomplete"


def test_substituted_word_is_refused(tmp_path):
    _, page = page_one(
        tmp_path,
        [(1, LINE_A.replace("40", "41"), BOX_A), (1, LINE_B, BOX_B)],
    )
    assert page["reasons"] == ["block_text_not_native_word_sequence"]


def test_client_word_facts_are_validated_and_digest_bound():
    facts = store_module.pdf_page_facts(pdf(LINES))
    graph = graph_for(pdf(LINES), LINES)
    forged = [dict(facts[0], words=facts[0]["words"][:-1]), *facts[1:]]
    with pytest.raises(ValueError, match="PAGE_FACTS_INVALID"):
        coverage.evaluate_pages(graph, forged, registered_page_count=3, selected_pages=[1, 2, 3])
    reindexed = [dict(facts[0], words=[dict(w, index=w["index"] + 1) for w in facts[0]["words"]])]
    with pytest.raises(ValueError, match="PAGE_FACTS_INVALID"):
        coverage.evaluate_pages(
            graph, [*reindexed, *facts[1:]], registered_page_count=3, selected_pages=[1, 2, 3]
        )


def test_excluded_elements_and_explicit_primitive_assertions(tmp_path):
    store = make_store(tmp_path, [state()])
    for element in ("P4", "P6"):
        with pytest.raises(ValueError, match="SEARCH_ELEMENT_EXCLUDED"):
            produce(store, element=element)
    receipt = produce(store, element="G3")
    assert receipt["element_primitives"] == ["baseline_value", "baseline_year"]
    request = store.review_request(TENANT, RUN, receipt["artifact_sha256"])
    assert request["asserted_primitives"] == ["baseline_value", "baseline_year"]
    assert "EVERY primitive" in request["instructions"]
