"""Bounded, separately killable section-scope inspection (no PDF parsing in the API process).

These tests spawn real child processes and assert on real OS state: after a timeout or
resource breach the child is gone (asked of the kernel, not inferred) and its private
scratch directory is removed. Nothing is mocked except where a test must observe the
spawn or simulate an unavailable containment primitive.
"""

from __future__ import annotations

import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep

import pytest
from proofops.adapters.parsing import opendataloader
from proofops.adapters.parsing.report_sections import SectionInspectionError, inspect_pdf_bytes
from proofops.adapters.parsing.report_sections_worker import (
    InspectionLimits,
    inspect_pdf_bytes_isolated,
    verify_result,
)
from proofops.adapters.parsing.windows_isolation import IsolationUnavailable
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

WINDOWS = sys.platform == "win32"


def _sectioned_pdf() -> bytes:
    writer = PdfWriter()
    for _ in range(8):
        writer.add_blank_page(width=600, height=800)
    for title, index in [("Environment", 1), ("Social", 4), ("ESG DATA", 5), ("Appendix", 7)]:
        writer.add_named_destination(title, index)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _dense_pdf(pages: int, lines: int = 90) -> bytes:
    """Real text on every page: pdfminer must lay out each page (~0.5 s/page here)."""
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
    text = " ".join(
        f"(Line {i} environmental emissions energy water waste disclosure) Tj 0 -8 Td"
        for i in range(lines)
    )
    body = f"BT /F1 7 Tf 20 780 Td {text} ET".encode()
    for _ in range(pages):
        page = writer.add_blank_page(width=600, height=800)
        content = DecodedStreamObject()
        content.set_data(body)
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _alive(pid: int) -> bool:
    """Ask the kernel directly (same probe as the parser isolation tests)."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture()
def spawned(monkeypatch):
    """Record every real child the executor starts (argv, env, process)."""
    records: list[dict] = []
    real = opendataloader.process_isolation

    def recording(**kwargs):
        isolation = real(**kwargs)
        original = isolation.spawn

        def spawn(command, *, cwd, env):
            process = original(command, cwd=cwd, env=env)
            records.append(dict(command=list(command), env=dict(env), process=process))
            return process

        isolation.spawn = spawn
        return isolation

    monkeypatch.setattr(opendataloader, "process_isolation", recording)
    return records


def _assert_reaped(records: list[dict], scratch_parent: Path) -> None:
    assert records, "no child was spawned"
    for record in records:
        pid = record["process"].pid
        deadline = monotonic() + 15
        while _alive(pid) and monotonic() < deadline:
            sleep(0.1)
        assert not _alive(pid), "inspection child outlived its bound"
        assert record["process"].returncode is not None
    assert not [p for p in scratch_parent.iterdir() if p.name.startswith(".parse-")]


def test_isolated_result_is_identical_to_in_process_result(tmp_path, spawned):
    for data in (_sectioned_pdf(), _dense_pdf(2, lines=10)):
        digest = sha256(data).hexdigest()
        isolated = inspect_pdf_bytes_isolated(data, expected_sha256=digest, work_parent=tmp_path)
        assert isolated == inspect_pdf_bytes(data, expected_sha256=digest)
        assert "source_path" not in isolated
    _assert_reaped(spawned, tmp_path)


def test_argv_is_fixed_without_shell_and_env_is_minimal(tmp_path, spawned, monkeypatch):
    monkeypatch.setenv("PROOFOPS_SECRET_PROBE", "must-not-leak")
    data = _sectioned_pdf()
    inspect_pdf_bytes_isolated(data, expected_sha256=sha256(data).hexdigest(), work_parent=tmp_path)
    (record,) = spawned
    command = record["command"]
    assert command[:2] == [sys.executable, "-I"]
    assert Path(command[2]).name == "report_sections_worker.py" and len(command) == 4
    assert Path(command[3]).parent == tmp_path.resolve() or Path(command[3]).parent == tmp_path
    assert "PROOFOPS_SECRET_PROBE" not in record["env"]
    assert not any(key.upper().startswith("AWS") for key in record["env"])


def test_timeout_kills_the_running_child_and_removes_scratch(tmp_path, spawned):
    data = _dense_pdf(120)
    started = monotonic()
    with pytest.raises(SectionInspectionError) as error:
        inspect_pdf_bytes_isolated(
            data,
            expected_sha256=sha256(data).hexdigest(),
            limits=InspectionLimits(timeout_seconds=3.0),
            work_parent=tmp_path,
        )
    elapsed = monotonic() - started
    assert error.value.code == "SECTION_INSPECTION_TIMEOUT"
    # In-process this document takes about a minute; the bound returns promptly.
    assert elapsed < 20, elapsed
    _assert_reaped(spawned, tmp_path)


def test_output_size_limit_stops_the_child(tmp_path, spawned):
    data = _dense_pdf(3, lines=40)
    with pytest.raises(SectionInspectionError) as error:
        inspect_pdf_bytes_isolated(
            data,
            expected_sha256=sha256(data).hexdigest(),
            limits=InspectionLimits(max_output_bytes=1024),
            work_parent=tmp_path,
        )
    assert error.value.code == "SECTION_INSPECTION_RESOURCE_LIMIT"
    _assert_reaped(spawned, tmp_path)


def test_memory_bound_is_enforced_and_never_yields_a_result(tmp_path, spawned):
    data = _dense_pdf(20)
    with pytest.raises(SectionInspectionError) as error:
        inspect_pdf_bytes_isolated(
            data,
            expected_sha256=sha256(data).hexdigest(),
            limits=InspectionLimits(memory_bytes=64 * 1024 * 1024, timeout_seconds=60),
            work_parent=tmp_path,
        )
    # Windows' job cap refuses the allocation (child dies -> FAILED); the POSIX watchdog
    # samples the breach (RESOURCE_LIMIT). Either way no map is produced.
    assert error.value.code in {"SECTION_INSPECTION_RESOURCE_LIMIT", "SECTION_INSPECTION_FAILED"}
    _assert_reaped(spawned, tmp_path)


def test_pins_are_checked_before_any_child_is_spawned(tmp_path, spawned):
    data = _sectioned_pdf()
    with pytest.raises(SectionInspectionError) as stale:
        inspect_pdf_bytes_isolated(data, expected_sha256="0" * 64, work_parent=tmp_path)
    assert stale.value.code == "SOURCE_SHA_MISMATCH"
    with pytest.raises(SectionInspectionError) as empty:
        inspect_pdf_bytes_isolated(b"", expected_sha256="0" * 64, work_parent=tmp_path)
    assert empty.value.code == "SECTION_SOURCE_INVALID"
    assert spawned == []


def test_child_reported_failures_keep_stable_codes(tmp_path, spawned):
    garbage = b"%PDF-1.7 not really a pdf"
    with pytest.raises(SectionInspectionError) as failed:
        inspect_pdf_bytes_isolated(
            garbage, expected_sha256=sha256(garbage).hexdigest(), work_parent=tmp_path
        )
    assert failed.value.code == "SECTION_INSPECTION_FAILED"

    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.encrypt("secret")
    stream = BytesIO()
    writer.write(stream)
    encrypted = stream.getvalue()
    with pytest.raises(SectionInspectionError) as locked:
        inspect_pdf_bytes_isolated(
            encrypted, expected_sha256=sha256(encrypted).hexdigest(), work_parent=tmp_path
        )
    assert locked.value.code == "SECTION_SOURCE_ENCRYPTED"
    _assert_reaped(spawned, tmp_path)


def test_containment_failure_never_degrades_into_uncontained_parsing(tmp_path, monkeypatch):
    def refuse(**_kwargs):
        raise IsolationUnavailable("test")

    monkeypatch.setattr(opendataloader, "process_isolation", refuse)
    data = _sectioned_pdf()
    with pytest.raises(SectionInspectionError) as error:
        inspect_pdf_bytes_isolated(
            data, expected_sha256=sha256(data).hexdigest(), work_parent=tmp_path
        )
    assert error.value.code == "SECTION_INSPECTION_UNAVAILABLE"


def test_parent_rejects_child_output_whose_pins_or_hash_do_not_hold():
    data = _sectioned_pdf()
    digest = sha256(data).hexdigest()
    good = inspect_pdf_bytes(data, expected_sha256=digest)
    assert verify_result(dict(good), digest) == good
    for tampered in (
        dict(good, claim_candidate_pages=[1, 2, 3, 4]),
        dict(good, source_sha256="0" * 64),
        dict(good, policy_sha256="0" * 64),
        dict(good, source_path="/srv/objects/x.pdf"),
        dict(good, status="approved"),
    ):
        with pytest.raises(SectionInspectionError):
            verify_result(tampered, digest)
    with pytest.raises(SectionInspectionError):
        verify_result(dict(good), "f" * 64)


def test_limits_reject_unbounded_configuration():
    for bad in (
        dict(timeout_seconds=0.1),
        dict(timeout_seconds=10_000),
        dict(memory_bytes=1),
        dict(max_output_bytes=10**12),
    ):
        with pytest.raises(ValueError):
            InspectionLimits(**bad)


def test_api_timeout_is_stable_503_releases_single_flight_and_kills_child(
    tmp_path, spawned, monkeypatch
):
    sys.path.insert(0, str(Path(__file__).parent))
    import proofops_api.routers.documents as documents
    from test_document_scope_proposal import _client, _ready_version

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    real = documents.inspect_pdf_bytes_isolated
    monkeypatch.setattr(
        documents,
        "inspect_pdf_bytes_isolated",
        lambda content, *, expected_sha256, limits: real(
            content, expected_sha256=expected_sha256, limits=limits, work_parent=scratch
        ),
    )
    data = _dense_pdf(120)
    service, version = _ready_version(tmp_path, data)
    url = f"/v1/versions/{version['version_id']}/scope-proposal"
    params = {"expected_sha256": version["sha256"]}
    with _client(service, scope_limits=InspectionLimits(timeout_seconds=3.0)) as client:
        first = client.get(url, params=params)
        assert first.status_code == 503, first.text
        body = first.json()["error"]
        assert body["code"] == "SECTION_INSPECTION_TIMEOUT" and body["retryable"] is False
        assert "retry-after" not in first.headers
        assert "PARSER" not in first.text and "Traceback" not in first.text
        # The single-flight slot was released: the next request runs (and times out) again.
        second = client.get(url, params=params)
        assert second.json()["error"]["code"] == "SECTION_INSPECTION_TIMEOUT"
    assert len(spawned) == 2
    _assert_reaped(spawned, scratch)
    service.close()


def test_router_default_limits_come_from_bounded_environment(monkeypatch):
    from proofops_api.routers.documents import _scope_limits_from_env

    monkeypatch.setenv("SCOPE_PROPOSAL_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("SCOPE_PROPOSAL_MEMORY_MIB", "512")
    limits = _scope_limits_from_env()
    assert limits.timeout_seconds == 45 and limits.memory_bytes == 512 * 1024 * 1024
    monkeypatch.setenv("SCOPE_PROPOSAL_TIMEOUT_SECONDS", "100000")
    with pytest.raises(ValueError):
        _scope_limits_from_env()
