"""Bounded, per-request PDF page preview rendering for private source reads."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from proofops.adapters.parsing.opendataloader import (
    OpenDataLoaderParser,
    ParseFailure,
    _child_limits,
    _parser_work_directory,
)
from proofops.domain.documents import PageGeometry

_MAX_SOURCE_BYTES = 100 * 1024 * 1024
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
_TIMEOUT_SECONDS = 15
_MEMORY_BYTES = 512 * 1024 * 1024
_MAX_DIMENSION = 2000
_MAX_PIXELS = 4_000_000


class SourcePreviewFailure(ValueError):
    """Sanitized preview error; PDF internals never reach the client."""


def render_page_preview(
    source: bytes,
    physical_page: int,
    geometry: PageGeometry,
    *,
    include_annotations_and_forms: bool = False,
) -> tuple[bytes, float, float]:
    """Render one graph-selected page in an isolated PDFium process."""
    if not isinstance(source, bytes) or len(source) > _MAX_SOURCE_BYTES:
        raise SourcePreviewFailure("SOURCE_PREVIEW_INPUT_LIMIT")
    if type(physical_page) is not int or physical_page < 1:
        raise SourcePreviewFailure("SOURCE_PREVIEW_PAGE_INVALID")
    if type(include_annotations_and_forms) is not bool:
        raise SourcePreviewFailure("SOURCE_PREVIEW_MODE_INVALID")
    profile = SimpleNamespace(
        timeout_seconds=_TIMEOUT_SECONDS,
        memory_bytes=_MEMORY_BYTES,
        max_output_bytes=_MAX_OUTPUT_BYTES,
    )
    try:
        # Owned scratch; cleanup retries briefly while a killed Windows child releases handles.
        with _parser_work_directory(Path(tempfile.gettempdir())) as work:
            (work / "source.pdf").write_bytes(source)
            (work / "request.json").write_text(
                json.dumps(
                    {
                        "physical_page": physical_page,
                        "geometry": asdict(geometry),
                        "include_annotations_and_forms": include_annotations_and_forms,
                    }
                )
            )
            # Containment (Job Object on Windows, new session on POSIX), wall-clock
            # timeout, memory and output watchdogs all come from the shared executor.
            OpenDataLoaderParser._execute(
                [sys.executable, "-I", str(Path(__file__).resolve()), str(work)],
                work,
                _child_environment(work),
                profile,
            )
            result = json.loads((work / "result.json").read_bytes())
            png = (work / "output.png").read_bytes()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ParseFailure):
        raise SourcePreviewFailure("SOURCE_PREVIEW_FAILED") from None
    if (
        not isinstance(result, dict)
        or set(result) != {"width_pt", "height_pt"}
        or any(
            isinstance(result[name], bool)
            or not isinstance(result[name], int | float)
            or not math.isfinite(result[name])
            or result[name] <= 0
            for name in result
        )
        or len(png) > _MAX_OUTPUT_BYTES
        or not png.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise SourcePreviewFailure("SOURCE_PREVIEW_OUTPUT_INVALID")
    return png, float(result["width_pt"]), float(result["height_pt"])


def _child_environment(work: Path) -> dict[str, str]:
    """Minimal, never-inherited child environment for this platform.

    POSIX is unchanged. Windows needs ``SystemRoot`` to start Python/PDFium, and pins
    TEMP inside the watched work directory so scratch output stays under the limit.
    """
    if sys.platform != "win32":
        return {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return {
        "PATH": os.pathsep.join([str(Path(system_root) / "System32"), system_root]),
        "SystemRoot": system_root,
        "TEMP": str(work),
        "TMP": str(work),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }


def _child(work: Path) -> None:
    import pypdfium2 as pdfium

    request = json.loads((work / "request.json").read_bytes())
    page_number = request["physical_page"]
    expected = PageGeometry(**request["geometry"])
    if type(page_number) is not int or page_number < 1:
        raise ValueError("invalid page")
    # POSIX: the same hard RLIMIT_CPU(15)/RLIMIT_FSIZE(output cap)/RLIMIT_CORE(0) as before.
    # Windows: CPU and memory are enforced by the Job Object the parent assigned before
    # this process was resumed, bytes by the parent's output watchdog; this only
    # suppresses crash dialogs that would otherwise hold a killed render open.
    _child_limits({"timeout_seconds": _TIMEOUT_SECONDS, "max_output_bytes": _MAX_OUTPUT_BYTES})
    document = pdfium.PdfDocument(work / "source.pdf")
    page = None
    bitmap = None
    image = None
    try:
        include_appearance = request.get("include_annotations_and_forms", False)
        if type(include_appearance) is not bool:
            raise ValueError("invalid appearance mode")
        if include_appearance:
            if document.get_formtype() not in (
                pdfium.raw.FORMTYPE_NONE,
                pdfium.raw.FORMTYPE_ACRO_FORM,
            ):
                raise ValueError("unsupported XFA form appearance")
            document.init_forms()
        page = document.get_page(page_number - 1)  # fixed 0-based PDFium index
        width_pt, height_pt = page.get_size()
        crop_box = tuple(float(value) for value in page.get_bbox())
        rotation = int(page.get_rotation()) % 360
        actual = PageGeometry(width_pt, height_pt, rotation, crop_box)
        # PDFium converts PDF decimal coordinates to float32; allow subpixel rounding only.
        if actual.rotation != expected.rotation or not all(
            math.isclose(left, right, rel_tol=0, abs_tol=0.0001)
            for left, right in zip(
                (actual.width_pt, actual.height_pt, *actual.crop_box),
                (expected.width_pt, expected.height_pt, *expected.crop_box),
                strict=True,
            )
        ):
            raise ValueError("graph geometry mismatch")
        # Leave one pixel of headroom against renderer rounding at every ceiling.
        scale = min(
            2.0,
            1999.0 / width_pt,
            1999.0 / height_pt,
            math.sqrt(3_996_001 / (width_pt * height_pt)),
        )
        bitmap = page.render(
            scale=scale,
            may_draw_forms=include_appearance,
            draw_annots=include_appearance,
            limit_image_cache=True,
        )
        image = bitmap.to_pil()
        if image.width > _MAX_DIMENSION or image.height > _MAX_DIMENSION:
            raise ValueError("render dimensions")
        if image.width * image.height > _MAX_PIXELS:
            raise ValueError("render pixels")
        image.save(work / "output.png", format="PNG")
        (work / "result.json").write_text(
            json.dumps({"width_pt": width_pt, "height_pt": height_pt})
        )
    finally:
        if image is not None:
            image.close()
        if bitmap is not None:
            bitmap.close()
        if page is not None:
            page.close()
        document.close()


if __name__ == "__main__":
    _child(Path(sys.argv[1]))
