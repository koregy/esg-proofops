"""Offline Upstage parse-run -> immutable candidate sidecar import, verify and replay.

The parse-run fixture is produced by the real ``parse_report_api.build_plan`` and the
same file writers as ``run_batch``; only the provider response is synthetic. No key,
ledger, network or AWS is used.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import uuid
from pathlib import Path

import pytest
from proofops.adapters.local.upstage_candidate_store import (
    SidecarBinding,
    UpstageSidecarError,
    build_sidecar,
    import_parse_run,
    load_sidecar,
)
from proofops.adapters.local.upstage_parse import PARSE_MODEL_PINNED
from proofops.adapters.parsing.upstage_candidates import convert_upstage_parse
from proofops.application.ingest.graph_fusion import SourceArtifact
from proofops.domain.provenance import canonical_hash
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, NumberObject

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import import_parse_api_candidates as importer  # noqa: E402
import parse_report_api as parse_cli  # noqa: E402

BINDING = SidecarBinding(
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
    "v1",
)


def _pdf(count: int, rotated=()) -> bytes:
    writer = PdfWriter()
    for number in range(1, count + 1):
        page = writer.add_blank_page(width=800, height=600)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 10 Td (page {number}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        if number in rotated:
            page[NameObject("/Rotate")] = NumberObject(90)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _box(x0, y0, x1, y1):
    return [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]


def _response(pages: int, mode="standard") -> dict:
    elements = []
    for page in range(1, pages + 1):
        elements += [
            dict(
                id=len(elements),
                page=page,
                category="paragraph",
                content=dict(text=f"=Scope 1 {page} tCO2e", html="<p/>"),
                coordinates=_box(0.1, 0.1, 0.5, 0.2),
            ),
            dict(
                id=len(elements) + 1,
                page=page,
                category="table",
                content=dict(text="| a | 1 |", html="<table><tr><td>1</td></tr></table>"),
                coordinates=_box(0.2, 0.5, 0.9, 0.9),
            ),
            dict(id=len(elements) + 2, page=page, category="list", content=dict(text="- a")),
        ]
    return dict(
        api="2.0",
        model=PARSE_MODEL_PINNED,
        usage={"pages": pages, mode: list(range(1, pages + 1))},
        elements=elements,
    )


def _complete(out: Path, manifest: dict, index: int) -> None:
    batch = manifest["batches"][index - 1]
    stem = out / batch["file"][: -len(".pdf")]
    split = (out / batch["file"]).read_bytes()
    pages = len(batch["pages"])
    raw = _response(pages, manifest["mode"])
    attempt = {"request_id": batch["request_id"], "started_at": "2026-09-29T06:00:00+00:00"}
    parse_cli._write_once(Path(f"{stem}.attempt.json"), parse_cli._dump(attempt))
    response_bytes = parse_cli._dump(raw)
    parse_cli._write_once(Path(f"{stem}.response.json"), response_bytes)
    receipt = dict(
        model=PARSE_MODEL_PINNED,
        provider_model=PARSE_MODEL_PINNED,
        provider_model_hash=hashlib.sha256(PARSE_MODEL_PINNED.encode()).hexdigest(),
        mode=manifest["mode"],
        pages=pages,
        usage=raw["usage"],
        cost_with_vat_reserve_usd="0.011",
        response_sha256=canonical_hash(raw),
        request_sha256=canonical_hash(
            dict(
                model=PARSE_MODEL_PINNED,
                mode=manifest["mode"],
                pdf_sha256=hashlib.sha256(split).hexdigest(),
                pages=pages,
                bytes_len=len(split),
            )
        ),
        **parse_cli.LABEL,
        index=index,
        request_id=batch["request_id"],
        split_sha256=batch["split_sha256"],
        response_file_sha256=hashlib.sha256(response_bytes).hexdigest(),
    )
    parse_cli._write_once(Path(f"{stem}.receipt.json"), parse_cli._dump(receipt))


def _run(tmp: Path, *, total=12, rotated=(), completed=(1, 2), blocked=(3,)) -> tuple:
    tmp.mkdir(parents=True, exist_ok=True)
    source = tmp / "src.pdf"
    source.write_bytes(_pdf(total, rotated))
    manifest, splits = parse_cli.build_plan(source, list(range(1, total + 1)), "standard", 5)
    out = tmp / "parse-run"
    for batch in manifest["batches"]:
        batch["request_id"] = str(uuid.uuid4())
    manifest["ledger"] = {"path": "ledger.sqlite3", "grant_sha256": "0" * 64}
    (out / "batches").mkdir(parents=True)
    parse_cli._write_once(out / "manifest.json", parse_cli._dump(manifest))
    for batch, data in zip(manifest["batches"], splits, strict=True):
        parse_cli._write_once(out / batch["file"], data)
    for index in completed:
        _complete(out, manifest, index)
    for index in blocked:
        stem = out / manifest["batches"][index - 1]["file"][: -len(".pdf")]
        record = {"request_id": manifest["batches"][index - 1]["request_id"]}
        parse_cli._write_once(Path(f"{stem}.attempt.json"), parse_cli._dump(record))
        failure = {**record, "code": "UPSTAGE_PARSE_TIMEOUT"}
        parse_cli._write_once(Path(f"{stem}.failure.json"), parse_cli._dump(failure))
    parse_cli.write_status(out, manifest)
    return source.read_bytes(), out, manifest


def _tree_digest(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _writable(path: Path) -> None:
    path.chmod(stat.S_IWRITE | stat.S_IREAD)


def _rewrite(path: Path, data: bytes) -> None:
    _writable(path)
    path.write_bytes(data)


def _repin(sidecar: Path) -> None:
    """Attacker path: recompute every pinned digest after editing a file."""
    path = sidecar / "sidecar.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"] = {
        name: hashlib.sha256((sidecar / name).read_bytes()).hexdigest()
        for name in manifest["files"]
    }
    _rewrite(path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode())


@pytest.fixture
def imported(tmp_path):
    content, parse_dir, manifest = _run(tmp_path)
    out = tmp_path / "sidecar"
    sidecar, sha = import_parse_run(parse_dir, out, content, BINDING, synthetic=True)
    return content, parse_dir, manifest, out, sidecar, sha


@pytest.mark.parametrize("bad_batch", [None, [], "batch", 7])
def test_non_object_batch_fails_with_sanitized_manifest_error(tmp_path, bad_batch):
    content, parse_dir, manifest = _run(tmp_path)
    manifest["batches"][0] = bad_batch
    _rewrite(parse_dir / "manifest.json", json.dumps(manifest).encode())
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_ORIGIN_MANIFEST_INVALID"):
        import_parse_run(parse_dir, tmp_path / "out", content, BINDING, synthetic=True)


def test_imports_completed_batches_as_separate_candidate_only_artifact(imported):
    content, parse_dir, origin, out, sidecar, sha = imported
    assert sidecar["citation_approved"] is False
    assert sidecar["source_verification"] == "not_run"
    assert sidecar["graph_effect"] == "none_separate_artifact"
    assert sidecar["totals"] == dict(
        batches=3,
        converted=2,
        excluded=0,
        blocked=1,
        planned=0,
        elements=30,
        located=20,
        tables=10,
        table_cell_candidates=0,
    )
    blocked = sidecar["batches"][2]
    assert blocked["state"] == "blocked" and blocked["physical_pages"] == [11, 12]
    first = sidecar["batches"][0]
    assert first["physical_pages"] == [1, 2, 3, 4, 5]
    assert first["split_sha256"] == origin["batches"][0]["split_sha256"]
    receipt = json.loads((parse_dir / "batches/001.receipt.json").read_text(encoding="utf-8"))
    assert first["request_sha256"] == receipt["request_sha256"]
    assert first["response_sha256"] == receipt["response_sha256"]
    assert (
        sidecar["origin"]["manifest_sha256"]
        == hashlib.sha256((parse_dir / "manifest.json").read_bytes()).hexdigest()
    )
    # Origin copies are byte-exact; every published file is read-only.
    for name in ("manifest.json", "batches/001.pdf", "batches/002.response.json"):
        assert (out / "origin" / name).read_bytes() == (parse_dir / name).read_bytes()
    assert all(not os.access(p, os.W_OK) for p in out.rglob("*") if p.is_file())
    assert hashlib.sha256((out / "sidecar.json").read_bytes()).hexdigest() == sha

    loaded = load_sidecar(out, content, expected_sha256=sha, expected_binding=BINDING)
    pages = sorted({b.source.physical_page for batch in loaded.batches for b in batch.blocks})
    assert pages == list(range(1, 11))
    for batch in loaded.batches:
        assert batch.parse_manifest_id == BINDING.parse_manifest_id
        assert batch.source_sha256 == hashlib.sha256(content).hexdigest()
        for block in batch.blocks:
            assert block.row_number is None and block.column_number is None
            if block.kind == "table":
                assert block.table_native_id == block.source.source_native_id
        assert {b.kind for b in batch.blocks} == {"paragraph", "table", "unknown"}
    # Equal to a direct conversion of the same receipt: nothing fabricated or dropped.
    receipt["raw_response"] = json.loads((parse_dir / "batches/001.response.json").read_bytes())
    direct = convert_upstage_parse(
        receipt,
        source=SourceArtifact(
            BINDING.tenant_id,
            BINDING.document_id,
            BINDING.document_version_id,
            hashlib.sha256(content).hexdigest(),
            "v1",
            content,
            True,
        ),
        subset_pdf=(parse_dir / "batches/001.pdf").read_bytes(),
        physical_pages=(1, 2, 3, 4, 5),
        parse_manifest_id=BINDING.parse_manifest_id,
        receipt_sha256=first["receipt_file_sha256"],
    )
    assert loaded.batches[0] == direct.batch


def test_review_csv_is_offline_readable_and_neutralizes_formulas(imported):
    *_, out, _sidecar, _sha = imported
    text = (out / "derived/review.csv").read_bytes().decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("batch,request_id,physical_page")
    assert len(lines) == 31
    assert "'=Scope 1 1 tCO2e" in text and ",=Scope" not in text
    assert "table_level_box_no_cells" in text
    assert all(line.split(",")[-2] == "false" for line in lines[1:] if "'=Scope" in line)


def test_parse_run_is_not_mutated_and_import_is_deterministic(tmp_path):
    content, parse_dir, _ = _run(tmp_path)
    before = _tree_digest(parse_dir)
    one = import_parse_run(parse_dir, tmp_path / "a", content)
    two = import_parse_run(parse_dir, tmp_path / "b", content)
    assert _tree_digest(parse_dir) == before
    assert one == two and one[0]["binding"]["kind"] == "offline_derived"
    assert _tree_digest(tmp_path / "a") == _tree_digest(tmp_path / "b")


def test_rotated_batch_is_excluded_not_fabricated(tmp_path):
    content, parse_dir, _ = _run(tmp_path, rotated=(7,), completed=(1, 2, 3), blocked=())
    out = tmp_path / "sidecar"
    sidecar, sha = import_parse_run(parse_dir, out, content, BINDING, synthetic=True)
    assert [b["state"] for b in sidecar["batches"]] == ["converted", "excluded", "converted"]
    assert sidecar["batches"][1]["code"] == "UPSTAGE_PAGE_GEOMETRY_UNSUPPORTED"
    assert not (out / "derived/002.candidates.json").exists()
    assert (out / "origin/batches/002.response.json").exists()
    assert len(load_sidecar(out, content, expected_sha256=sha).batches) == 2


def test_import_gates(tmp_path):
    content, parse_dir, _ = _run(tmp_path)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_PARSE_RUN_INCOMPLETE"):
        import_parse_run(parse_dir, tmp_path / "x", content, require_complete=True)
    with pytest.raises(UpstageSidecarError, match="OUT_EXISTS_OR_UNSAFE"):
        import_parse_run(parse_dir, parse_dir / "sidecar", content)
    (tmp_path / "taken").mkdir()
    with pytest.raises(UpstageSidecarError, match="OUT_EXISTS_OR_UNSAFE"):
        import_parse_run(parse_dir, tmp_path / "taken", content)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SOURCE_HASH_MISMATCH"):
        import_parse_run(parse_dir, tmp_path / "y", _pdf(12, rotated=(1,)))
    assert not any(p.name.startswith(".") for p in tmp_path.iterdir())
    empty = tmp_path / "empty"
    _, bare, _ = _run(empty, completed=(), blocked=())
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_NO_CONVERTED_BATCHES"):
        import_parse_run(bare, empty / "z", (empty / "src.pdf").read_bytes())


def _json_edit(path: Path, change) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    _rewrite(path, parse_cli._dump(value))


def _set_first(key, value):
    def change(response):
        response["elements"][0][key] = value

    return change


@pytest.mark.parametrize(
    ("name", "change", "code"),
    [
        (
            "batches/001.response.json",
            _set_first("content", {"text": "edited"}),
            "UPSTAGE_RESPONSE_HASH_MISMATCH",
        ),
        (
            "batches/001.receipt.json",
            lambda r: r.update(request_id=str(uuid.uuid4())),
            "UPSTAGE_RECEIPT_BINDING_MISMATCH",
        ),
        (
            "batches/001.receipt.json",
            lambda r: r.update(request_sha256="0" * 64),
            "UPSTAGE_REQUEST_HASH_MISMATCH",
        ),
        (
            "manifest.json",
            lambda m: m["batches"][0]["pages"].reverse(),
            "UPSTAGE_ORIGIN_MANIFEST_INVALID",
        ),
        ("manifest.json", lambda m: m.update(verification="verified"), "MANIFEST_INVALID"),
        ("manifest.json", lambda m: m.update(model="document-parse"), "MODEL_OR_MODE_INVALID"),
    ],
)
def test_import_fails_closed_on_tampered_parse_run(tmp_path, name, change, code):
    content, parse_dir, _ = _run(tmp_path)
    _json_edit(parse_dir / name, change)
    with pytest.raises(UpstageSidecarError, match=code):
        import_parse_run(parse_dir, tmp_path / "sidecar", content)
    assert not (tmp_path / "sidecar").exists()


def test_import_rejects_rehashed_response_edit_and_swapped_split(tmp_path):
    content, parse_dir, manifest = _run(tmp_path)
    # Response edited AND receipt digests recomputed: request/split pins still hold,
    # so the converter's canonical response hash is the only thing left -- it must fail.
    response_path = parse_dir / "batches/001.response.json"
    raw = json.loads(response_path.read_bytes())
    raw["elements"][0]["content"]["text"] = "edited"
    _rewrite(response_path, parse_cli._dump(raw))
    _json_edit(
        parse_dir / "batches/001.receipt.json",
        lambda r: r.update(response_file_sha256=_file_sha(response_path)),
    )
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_RESPONSE_HASH_MISMATCH"):
        import_parse_run(parse_dir, tmp_path / "a", content)
    content, parse_dir, manifest = _run(tmp_path / "second")
    _rewrite(parse_dir / "batches/001.pdf", (parse_dir / "batches/002.pdf").read_bytes())
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SPLIT_HASH_MISMATCH"):
        import_parse_run(parse_dir, tmp_path / "b", content)


def test_duplicate_json_keys_and_nan_are_rejected(tmp_path):
    content, parse_dir, _ = _run(tmp_path)
    path = parse_dir / "batches/001.receipt.json"
    _rewrite(path, path.read_bytes().replace(b'"index": 1,', b'"index": 1, "index": 1,'))
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_RECEIPT_SHAPE_INVALID"):
        import_parse_run(parse_dir, tmp_path / "a", content)
    _rewrite(path, path.read_bytes().replace(b'"index": 1, "index": 1,', b'"index": NaN,'))
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_RECEIPT_SHAPE_INVALID"):
        import_parse_run(parse_dir, tmp_path / "b", content)


def _edit_candidate_text(out: Path) -> None:
    path = out / "derived/001.candidates.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["blocks"][0]["source"]["raw_text"] = "Scope 1 99,999 tCO2e"
    _rewrite(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode())


def _flip_approval(out: Path) -> None:
    path = out / "derived/001.conversion.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["citation_approved"] = True
    _rewrite(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode())


def _edit_origin_response(out: Path) -> None:
    path = out / "origin/batches/001.response.json"
    _rewrite(path, path.read_bytes().replace(b"Scope 1 1", b"Scope 1 7"))


def _edit_sidecar(change):
    def edit(out: Path) -> None:
        path = out / "sidecar.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        _rewrite(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode())

    return edit


@pytest.mark.parametrize(
    ("tamper", "repin", "code"),
    [
        (_edit_candidate_text, False, "UPSTAGE_SIDECAR_FILE_HASH_MISMATCH"),
        (_edit_candidate_text, True, "UPSTAGE_SIDECAR_REPLAY_MISMATCH"),
        (_flip_approval, True, "UPSTAGE_SIDECAR_REPLAY_MISMATCH"),
        (_edit_origin_response, True, "UPSTAGE_RESPONSE_HASH_MISMATCH"),
        (
            lambda out: (out / "derived/extra.json").write_bytes(b"{}"),
            False,
            "UPSTAGE_SIDECAR_FILESET_MISMATCH",
        ),
        (
            lambda out: (out / "derived/review.csv").chmod(0o600)
            or (out / "derived/review.csv").unlink(),
            False,
            "UPSTAGE_SIDECAR_FILESET_MISMATCH",
        ),
        (
            _edit_sidecar(lambda m: m.update(citation_approved=True)),
            False,
            "UPSTAGE_SIDECAR_MANIFEST_INVALID",
        ),
        (
            _edit_sidecar(lambda m: m["totals"].update(table_cell_candidates=5)),
            False,
            "UPSTAGE_SIDECAR_REPLAY_MISMATCH",
        ),
        (
            _edit_sidecar(lambda m: m["binding"].update(parse_manifest_id=str(uuid.uuid4()))),
            False,
            "UPSTAGE_SIDECAR_REPLAY_MISMATCH",
        ),
    ],
)
def test_load_fails_closed_on_tampered_sidecar(imported, tamper, repin, code):
    content, *_, out, _sidecar, _sha = imported
    tamper(out)
    if repin:
        _repin(out)
    with pytest.raises(UpstageSidecarError, match=code):
        load_sidecar(out, content)


def test_load_requires_pinned_sha_source_and_binding(imported):
    content, *_, out, _sidecar, sha = imported
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SIDECAR_PIN_MISMATCH"):
        load_sidecar(out, content, expected_sha256="0" * 64)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SOURCE_HASH_MISMATCH"):
        load_sidecar(out, content + b" ")
    other = SidecarBinding(*[str(uuid.uuid4()) for _ in range(4)], "v1")
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SIDECAR_BINDING_INVALID"):
        load_sidecar(out, content, expected_binding=other)
    assert load_sidecar(out, content, expected_sha256=sha).manifest_sha256 == sha


def _symlink(link: Path, target: Path, directory=False) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        if not (directory and os.name == "nt"):
            pytest.skip("symlinks unavailable on this host")
        import _winapi  # unprivileged Windows: a junction is the reachable directory link

        _winapi.CreateJunction(str(target), str(link))


def test_symlinks_are_never_followed(tmp_path):
    content, parse_dir, _ = _run(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes((parse_dir / "batches/002.response.json").read_bytes())
    target = parse_dir / "batches/002.response.json"
    _writable(target)
    target.unlink()
    _symlink(target, outside)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_ORIGIN_PATH_UNSAFE"):
        import_parse_run(parse_dir, tmp_path / "a", content)


def test_linked_directories_are_never_followed(imported, tmp_path):
    content, parse_dir, _origin, out, _sidecar, _sha = imported
    linked_run = tmp_path / "linked-run"
    _symlink(linked_run, parse_dir, directory=True)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_ORIGIN_PATH_UNSAFE"):
        build_sidecar(linked_run, content)
    linked = tmp_path / "linked-sidecar"
    _symlink(linked, out, directory=True)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SIDECAR_PATH_UNSAFE"):
        load_sidecar(linked, content)
    # A linked subdirectory inside a real sidecar is refused too.
    derived = out / "origin" / "batches"
    moved = tmp_path / "moved-batches"
    os.rename(derived, moved)
    _symlink(derived, moved, directory=True)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SIDECAR_PATH_UNSAFE"):
        load_sidecar(out, content)


def test_symlinked_sidecar_file_is_rejected(imported, tmp_path):
    content, *_, out, _sidecar, _sha = imported
    review = out / "derived/review.csv"
    copy = tmp_path / "review-copy.csv"
    copy.write_bytes(review.read_bytes())
    _writable(review)
    review.unlink()
    _symlink(review, copy)
    with pytest.raises(UpstageSidecarError, match="UPSTAGE_SIDECAR_PATH_UNSAFE"):
        load_sidecar(out, content)


def test_cli_import_then_verify(tmp_path, capsys):
    content, parse_dir, _ = _run(tmp_path)
    pdf = tmp_path / "src.pdf"
    out = tmp_path / "sidecar"
    ids = [
        "--tenant-id", BINDING.tenant_id,
        "--document-id", BINDING.document_id,
        "--document-version-id", BINDING.document_version_id,
        "--parse-manifest-id", BINDING.parse_manifest_id,
    ]  # fmt: skip
    with pytest.raises(SystemExit) as partial:
        importer.main(["import", "--parse-dir", str(parse_dir), "--pdf", str(pdf),
                       "--out-dir", str(out), *ids])  # fmt: skip
    assert partial.value.code == 2 and not out.exists()
    capsys.readouterr()
    assert importer.main(["import", "--parse-dir", str(parse_dir), "--pdf", str(pdf),
                          "--out-dir", str(out), *ids, "--object-version-id", "v1",
                          "--synthetic"]) == 0  # fmt: skip
    result = json.loads(capsys.readouterr().out)
    assert result["imported"] is True and result["labels"]["citation_approved"] is False
    assert result["not_converted"] == [{"index": 3, "state": "blocked", "physical_pages": [11, 12]}]
    sha = result["sidecar_sha256"]
    assert importer.main(["verify", "--sidecar", str(out), "--pdf", str(pdf),
                          "--expect-sha256", sha]) == 0  # fmt: skip
    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert importer.main(["verify", "--sidecar", str(out), "--pdf", str(pdf),
                          "--expect-sha256", "0" * 64]) == 1  # fmt: skip
    assert "UPSTAGE_SIDECAR_PIN_MISMATCH" in capsys.readouterr().err
    assert importer.main(["import", "--parse-dir", str(parse_dir), "--pdf", str(pdf),
                          "--out-dir", str(out)]) == 1  # fmt: skip
    assert "OUT_EXISTS_OR_UNSAFE" in capsys.readouterr().err
