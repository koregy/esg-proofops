"""Source preview rendering runs contained, bounded and cleaned up on every platform.

The preview child used to import the POSIX-only ``resource`` module unconditionally, so
every Windows render failed. These tests run the real executor and real containment
(Windows Job Object / POSIX session). Hostile children are injected only by replacing
the child *command*; the work directory, environment, limits and cleanup are the
preview's own. Liveness is asked of the kernel, not inferred.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep

import pytest
from proofops.adapters.parsing import source_preview
from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser
from proofops.adapters.parsing.source_preview import SourcePreviewFailure, render_page_preview
from proofops.domain.documents import PageGeometry
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject

WINDOWS = sys.platform == "win32"
GEOMETRY = PageGeometry(500, 650, 0, (50, 100, 550, 750))


def _cropped_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    page.cropbox = RectangleObject([50, 100, 550, 750])
    content = DecodedStreamObject()
    content.set_data(b"0 0 1 rg 10 10 30 30 re f\n1 0 0 rg 100 200 40 40 re f")
    page[NameObject("/Contents")] = writer._add_object(content)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _alive(pid: int) -> bool:
    if WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
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
def scratch(tmp_path, monkeypatch):
    """Point the preview's scratch parent at tmp_path so leftovers are observable."""
    parent = tmp_path / "scratch"
    parent.mkdir()
    monkeypatch.setattr(source_preview.tempfile, "gettempdir", lambda: str(parent))
    return parent


def _leftovers(parent: Path) -> list[Path]:
    return [p for p in parent.iterdir() if p.name.startswith(".parse-")]


def _inject(monkeypatch, script: str, seen: dict):
    """Keep the preview's real work dir, env, limits and executor; swap only the child."""
    real = OpenDataLoaderParser._execute

    def execute(command, work, env, profile):
        seen.update(original=list(command), env=dict(env), profile=profile, work=work)
        body = f"import os; open('pid.txt', 'w').write(str(os.getpid()))\n{script}"
        return real([sys.executable, "-I", "-c", body], work, env, profile)

    monkeypatch.setattr(OpenDataLoaderParser, "_execute", staticmethod(execute))


def _assert_child_gone_and_clean(seen: dict, parent: Path) -> None:
    pid_file = seen["work"] / "pid.txt"
    # The scratch directory is gone, so the pid was captured before cleanup below.
    assert not _leftovers(parent), "preview scratch directory was not removed"
    pid = seen.get("pid")
    if pid is not None:
        deadline = monotonic() + 15
        while _alive(pid) and monotonic() < deadline:
            sleep(0.1)
        assert not _alive(pid), "preview child outlived its bound"
    assert not pid_file.exists()


def _capture_pid(monkeypatch, seen: dict) -> None:
    """Record the child's pid from inside the executor before the work dir is removed."""
    from proofops.adapters.parsing import opendataloader

    real = opendataloader.process_isolation

    def recording(**kwargs):
        isolation = real(**kwargs)
        spawn = isolation.spawn

        def spawn_and_record(command, *, cwd, env):
            process = spawn(command, cwd=cwd, env=env)
            seen["pid"] = process.pid
            seen["process"] = process
            return process

        isolation.spawn = spawn_and_record
        return isolation

    monkeypatch.setattr(opendataloader, "process_isolation", recording)


def test_real_cropped_render_succeeds_with_contained_limits(scratch, monkeypatch):
    from PIL import Image

    seen: dict = {}
    _capture_pid(monkeypatch, seen)
    png, width, height = render_page_preview(_cropped_pdf(), 1, GEOMETRY)
    image = Image.open(BytesIO(png)).convert("RGB")
    assert (width, height) == (500, 650) and png.startswith(b"\x89PNG\r\n\x1a\n")
    # Red square (inside CropBox) is at canonical (50, 550) from the crop origin; blue is cropped.
    red = image.getpixel((round(70 * image.width / 500), round(530 * image.height / 650)))
    assert red[0] > 180 and red[1] < 80 and red[2] < 80
    corner = image.getpixel((1, image.height - 2))
    assert not (corner[2] > 180 and corner[0] < 80), "content outside CropBox leaked"
    assert seen["process"].returncode == 0
    assert not _leftovers(scratch)


def test_the_child_applies_the_same_limits_on_every_platform(tmp_path, monkeypatch):
    """The in-child limits are the pre-fix POSIX values (CPU 15 s, FSIZE 16 MiB, no core).

    ``_child_limits`` applies them as hard rlimits on POSIX; on Windows the Job Object
    assigned before resume enforces CPU/memory. Run in-process with a spy so the test
    runner itself never receives rlimits.
    """
    calls = []
    monkeypatch.setattr(source_preview, "_child_limits", calls.append)
    (tmp_path / "source.pdf").write_bytes(_cropped_pdf())
    (tmp_path / "request.json").write_text(
        '{"physical_page": 1, "geometry": '
        + __import__("json").dumps(asdict(GEOMETRY))
        + ', "include_annotations_and_forms": false}'
    )
    source_preview._child(tmp_path)
    assert calls == [{"timeout_seconds": 15, "max_output_bytes": 16 * 1024 * 1024}]
    assert (tmp_path / "output.png").read_bytes().startswith(b"\x89PNG")


def test_posix_environment_is_unchanged_and_windows_one_is_minimal(tmp_path, monkeypatch):
    monkeypatch.setenv("PROOFOPS_SECRET_PROBE", "must-not-leak")
    env = source_preview._child_environment(tmp_path)
    assert "PROOFOPS_SECRET_PROBE" not in env
    assert not any(key.upper().startswith("AWS") for key in env)
    if WINDOWS:
        assert env["TEMP"] == env["TMP"] == str(tmp_path) and env["SystemRoot"]
    else:
        assert env == {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


def test_timeout_kills_the_render_child_and_removes_scratch(scratch, monkeypatch):
    seen: dict = {}
    _capture_pid(monkeypatch, seen)
    monkeypatch.setattr(source_preview, "_TIMEOUT_SECONDS", 2)
    _inject(monkeypatch, "import time; time.sleep(600)", seen)
    started = monotonic()
    with pytest.raises(SourcePreviewFailure, match="SOURCE_PREVIEW_FAILED"):
        render_page_preview(_cropped_pdf(), 1, GEOMETRY)
    assert monotonic() - started < 20
    assert seen["profile"].timeout_seconds == 2
    _assert_child_gone_and_clean(seen, scratch)


@pytest.mark.parametrize(("megabytes", "allocated"), [(16, True), (600, False)])
def test_memory_bound_stops_an_allocating_child(
    scratch, monkeypatch, tmp_path, megabytes, allocated
):
    """Touch resident pages and prove the watchdog stops a sustained cap breach.

    POSIX samples RSS, unlike the Windows Job Object allocation cap. A marker
    immediately after allocation races that sample and can precede the kill.
    """
    seen: dict = {}
    _capture_pid(monkeypatch, seen)
    marker = tmp_path / "allocated.txt"
    monkeypatch.setattr(source_preview, "_MEMORY_BYTES", 128 * 1024 * 1024)
    script = "\n".join(
        [
            f"block = bytearray({megabytes} * 1024 * 1024)",
            "for offset in range(0, len(block), 4096): block[offset] = 1",
            "import time; time.sleep(2)",
            f"open({str(marker)!r}, 'w').write('ok')",
        ]
    )
    _inject(monkeypatch, script, seen)
    with pytest.raises(SourcePreviewFailure, match="SOURCE_PREVIEW_FAILED"):
        render_page_preview(_cropped_pdf(), 1, GEOMETRY)  # no PNG is produced either way
    assert seen["profile"].memory_bytes == 128 * 1024 * 1024
    assert marker.exists() is allocated
    _assert_child_gone_and_clean(seen, scratch)


def test_output_bound_stops_a_child_writing_past_the_cap(scratch, monkeypatch):
    seen: dict = {}
    _capture_pid(monkeypatch, seen)
    _inject(
        monkeypatch,
        "import time\nopen('output.png', 'wb').write(b'x' * (17 * 1024 * 1024))\ntime.sleep(30)",
        seen,
    )
    with pytest.raises(SourcePreviewFailure, match="SOURCE_PREVIEW_FAILED"):
        render_page_preview(_cropped_pdf(), 1, GEOMETRY)
    assert seen["profile"].max_output_bytes == 16 * 1024 * 1024
    _assert_child_gone_and_clean(seen, scratch)


def test_render_error_is_sanitized_and_cleaned_up(scratch, monkeypatch):
    seen: dict = {}
    _capture_pid(monkeypatch, seen)
    wrong = PageGeometry(650, 500, 90, (50, 100, 550, 750))  # valid, but page is unrotated
    with pytest.raises(SourcePreviewFailure) as error:
        render_page_preview(_cropped_pdf(), 1, wrong)
    assert str(error.value) == "SOURCE_PREVIEW_FAILED"
    assert seen["process"].returncode not in (None, 0)
    assert not _leftovers(scratch)
