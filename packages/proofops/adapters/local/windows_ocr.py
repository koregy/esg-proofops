"""Bounded Windows.Media.Ocr reader for one rendered PNG crop (scalar text only).

Runs the sha-pinned ``windows_ocr.ps1`` in a fresh PowerShell inside the parser's
existing Job Object containment (memory and CPU caps, no window, whole-tree kill)
with a wall-clock deadline. The script bytes are verified against ``HELPER_SHA256``
and executed from a private temporary copy, so an edit to the live file between
check and use cannot run. Output is one JSON file whose size, shape, text length and
language are checked. Every failure returns ``status="unresolved"`` with a reason
code; nothing here raises for an unavailable engine and nothing is retried.

No network, no model provider, no key. Reading text never proves anything by
itself: callers compare it against native PDF words that already passed every
geometry/visibility gate.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic

HELPER = Path(__file__).with_name("windows_ocr.ps1")
LANGUAGE = "ko"
TIMEOUT_SECONDS = 30.0
CPU_SECONDS = 30
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 200_000
ENGINE_FIELDS = ("reader", "language", "os_version", "os_build", "os_ubr", "max_image_dimension")
_SYSTEM_ROOT = os.environ.get("SystemRoot", r"C:\Windows")
POWERSHELL = Path(_SYSTEM_ROOT) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def helper_sha256() -> str:
    return sha256(HELPER.read_bytes()).hexdigest()


def limits() -> dict:
    return dict(
        timeout_seconds=TIMEOUT_SECONDS,
        cpu_seconds=CPU_SECONDS,
        memory_bytes=MEMORY_BYTES,
        max_image_bytes=MAX_IMAGE_BYTES,
        max_output_bytes=MAX_OUTPUT_BYTES,
        max_text_chars=MAX_TEXT_CHARS,
    )


def _unresolved(reason: str) -> dict:
    return dict(status="unresolved", reason=reason)


def _parse(data: bytes) -> dict:
    value = json.loads(data.decode("utf-8-sig"))
    if not isinstance(value, dict) or set(value) != {"text", *ENGINE_FIELDS}:
        raise ValueError("shape")
    if not isinstance(value["text"], str) or len(value["text"]) > MAX_TEXT_CHARS:
        raise ValueError("text")
    if value["reader"] != "Windows.Media.Ocr" or not str(value["language"]).startswith(LANGUAGE):
        raise ValueError("engine")
    if any(not isinstance(value[k], str) or not value[k] for k in ENGINE_FIELDS[:5]):
        raise ValueError("engine")
    if type(value["max_image_dimension"]) is not int:
        raise ValueError("engine")
    return dict(text=value["text"], engine={k: value[k] for k in ENGINE_FIELDS})


def read_windows_ocr(png: bytes, *, expected_helper_sha256: str) -> dict:
    """Return ``{status: read, text, engine}`` or ``{status: unresolved, reason}``."""
    if sys.platform != "win32":
        return _unresolved("windows_ocr_unsupported_platform")
    if not isinstance(png, bytes) or not png or len(png) > MAX_IMAGE_BYTES:
        return _unresolved("windows_ocr_image_limit")
    try:
        script = HELPER.read_bytes()
    except OSError:
        return _unresolved("windows_ocr_helper_missing")
    if sha256(script).hexdigest() != expected_helper_sha256:
        return _unresolved("windows_ocr_helper_changed")
    if not POWERSHELL.is_file():
        return _unresolved("windows_ocr_unavailable")
    from proofops.adapters.parsing.windows_isolation import (
        IsolationUnavailable,
        kill_and_reap,
        process_isolation,
    )

    try:
        with tempfile.TemporaryDirectory(
            prefix="proofops-winocr-", ignore_cleanup_errors=True
        ) as folder:
            work = Path(folder)
            (work / "reader.ps1").write_bytes(script)
            (work / "region.png").write_bytes(png)
            out = work / "result.json"
            command = [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(work / "reader.ps1"),
                "-ImagePath",
                str(work / "region.png"),
                "-OutPath",
                str(out),
            ]
            env = {
                key: os.environ[key]
                for key in ("SystemRoot", "SystemDrive", "WINDIR", "TEMP", "TMP", "PATH")
                if key in os.environ
            }
            isolation = process_isolation(memory_bytes=MEMORY_BYTES, cpu_seconds=CPU_SECONDS)
            try:
                deadline = monotonic() + TIMEOUT_SECONDS
                with isolation.spawn(command, cwd=work, env=env) as process:
                    try:
                        while process.poll() is None:
                            if monotonic() >= deadline:
                                return _unresolved("windows_ocr_timeout")
                            if isolation.memory_limit_exceeded(process):
                                return _unresolved("windows_ocr_memory_limit")
                            try:
                                process.wait(timeout=0.05)
                            except subprocess.TimeoutExpired:
                                pass
                        if isolation.memory_limit_exceeded(process):
                            return _unresolved("windows_ocr_memory_limit")
                        if process.returncode:
                            return _unresolved("windows_ocr_failed")
                    finally:
                        try:
                            isolation.terminate_tree(process)
                        finally:
                            kill_and_reap(process)
            finally:
                isolation.close()
            if not out.is_file() or out.is_symlink() or out.stat().st_size > MAX_OUTPUT_BYTES:
                return _unresolved("windows_ocr_output_invalid")
            try:
                parsed = _parse(out.read_bytes())
            except (ValueError, UnicodeDecodeError):
                return _unresolved("windows_ocr_output_invalid")
            return dict(status="read", **parsed)
    except IsolationUnavailable:
        return _unresolved("windows_ocr_isolation_unavailable")
    except OSError:
        return _unresolved("windows_ocr_unavailable")


_engine_lock = Lock()
_engine_cache: dict[str, dict] = {}


def engine_identity(*, expected_helper_sha256: str) -> dict:
    """Probe the installed engine once per process with a blank crop.

    Returns the engine identity (reader, language, OS version/build/UBR, max image
    dimension) or raises ``WindowsOcrUnavailable``; a new run cannot pin a policy
    without a working Korean engine.
    """
    with _engine_lock:
        if expected_helper_sha256 in _engine_cache:
            return dict(_engine_cache[expected_helper_sha256])
    from PIL import Image

    buffer = io.BytesIO()
    with Image.new("RGB", (64, 32), "white") as blank:
        blank.save(buffer, format="PNG")
    result = read_windows_ocr(buffer.getvalue(), expected_helper_sha256=expected_helper_sha256)
    if result.get("status") != "read":
        raise WindowsOcrUnavailable(result.get("reason", "windows_ocr_unavailable"))
    with _engine_lock:
        _engine_cache[expected_helper_sha256] = dict(result["engine"])
    return dict(result["engine"])


class WindowsOcrUnavailable(RuntimeError):
    """The pinned Korean Windows OCR engine cannot be used on this host."""
