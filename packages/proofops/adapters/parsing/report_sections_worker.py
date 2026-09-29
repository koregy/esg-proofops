"""Section-scope inspection in a separately killable, resource-bounded child process.

The API never parses PDF structure in its own process. The parent validates size and
pinned SHA, writes the immutable bytes into a private scratch directory, and runs this
module as ``python -I <this file> <work>`` (fixed argv, no shell, minimal environment).
Containment reuses the parser adapter's tested executor: a Windows Job Object or a
POSIX session, a wall-clock deadline that kills the whole tree, a memory cap/watchdog,
an output-size watchdog, and bounded reaping. A timeout therefore stops the work; it
does not merely stop waiting for it.

The child returns exactly what ``inspect_pdf_bytes`` returns in-process, so
``map_sha256`` and every field stay hash-compatible; the parent re-verifies the pins.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from proofops.adapters.parsing.opendataloader import (
    OpenDataLoaderParser,
    ParseFailure,
    _child_environment,
    _child_limits,
    _parser_work_directory,
)
from proofops.adapters.parsing.report_sections import (
    MAX_PDF_BYTES,
    POLICY_HASH,
    SectionInspectionError,
    inspect_pdf_bytes,
)
from proofops.domain.provenance import canonical_hash

_RESULT = "result.json"
_ERROR = "error.json"
_REQUEST = "request.json"
# Codes the child may report; anything else is treated as an opaque failure.
_CHILD_CODES = frozenset(
    {
        "SECTION_SOURCE_TOO_LARGE",
        "SECTION_SOURCE_ENCRYPTED",
        "SECTION_SOURCE_INVALID",
        "SECTION_INSPECTION_FAILED",
        "SOURCE_SHA_MISMATCH",
    }
)
_EXECUTOR_CODES = {
    "PARSER_TIMEOUT": "SECTION_INSPECTION_TIMEOUT",
    "PARSER_MEMORY_LIMIT": "SECTION_INSPECTION_RESOURCE_LIMIT",
    "PARSER_OUTPUT_LIMIT": "SECTION_INSPECTION_RESOURCE_LIMIT",
    "PARSER_ISOLATION_UNAVAILABLE": "SECTION_INSPECTION_UNAVAILABLE",
    "PARSER_FAILED": "SECTION_INSPECTION_FAILED",
}


@dataclass(frozen=True, slots=True)
class InspectionLimits:
    """Hard bounds for one inspection child. All are enforced outside the child."""

    timeout_seconds: float = 180.0
    memory_bytes: int = 1024 * 1024 * 1024
    max_output_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        if not (1.0 <= self.timeout_seconds <= 900.0):
            raise ValueError("inspection timeout must be within 1..900 seconds")
        if not (64 * 1024 * 1024 <= self.memory_bytes <= 8 * 1024 * 1024 * 1024):
            raise ValueError("inspection memory bound must be within 64MiB..8GiB")
        if not (1024 <= self.max_output_bytes <= 256 * 1024 * 1024):
            raise ValueError("inspection output bound must be within 1KiB..256MiB")


def inspect_pdf_bytes_isolated(
    content: bytes,
    *,
    expected_sha256: str,
    limits: InspectionLimits = InspectionLimits(),
    work_parent: Path | None = None,
) -> dict:
    """Same result as ``inspect_pdf_bytes`` but computed in a bounded child process.

    Raises ``SectionInspectionError`` with a stable code; executor failures map to
    ``SECTION_INSPECTION_TIMEOUT``, ``SECTION_INSPECTION_RESOURCE_LIMIT``,
    ``SECTION_INSPECTION_UNAVAILABLE`` or ``SECTION_INSPECTION_FAILED``.
    """
    # Cheap pins run in the parent so a stale or oversized request never spawns anything.
    if not isinstance(content, bytes) or not content:
        raise SectionInspectionError("SECTION_SOURCE_INVALID", "empty or non-bytes source")
    if len(content) > MAX_PDF_BYTES:
        raise SectionInspectionError("SECTION_SOURCE_TOO_LARGE", "section study limit: 100 MiB")
    if sha256(content).hexdigest() != expected_sha256:
        raise SectionInspectionError("SOURCE_SHA_MISMATCH", "source bytes do not match pin")
    parent = Path(work_parent) if work_parent is not None else Path(tempfile.gettempdir())
    profile = SimpleNamespace(
        timeout_seconds=limits.timeout_seconds,
        memory_bytes=limits.memory_bytes,
        # The copied source is excluded by the watchdog; only produced bytes count.
        max_output_bytes=limits.max_output_bytes,
    )
    with _parser_work_directory(parent) as work:
        (work / "source.pdf").write_bytes(content)
        (work / _REQUEST).write_text(
            json.dumps(
                {
                    "expected_sha256": expected_sha256,
                    "timeout_seconds": limits.timeout_seconds,
                    "max_output_bytes": limits.max_output_bytes,
                }
            ),
            encoding="utf-8",
        )
        command = [sys.executable, "-I", str(Path(__file__).resolve()), str(work)]
        try:
            OpenDataLoaderParser._execute(
                command, work, _child_environment(sys.executable, work, profile), profile
            )
        except ParseFailure as failure:
            code = _EXECUTOR_CODES.get(str(failure), "SECTION_INSPECTION_FAILED")
            raise SectionInspectionError(code, code) from None
        except OSError:
            raise SectionInspectionError(
                "SECTION_INSPECTION_UNAVAILABLE", "inspection worker could not start"
            ) from None
        error_path, result_path = work / _ERROR, work / _RESULT
        if error_path.exists():
            child_code = _read_json(error_path, 4096).get("code")
            raise SectionInspectionError(
                child_code
                if isinstance(child_code, str) and child_code in _CHILD_CODES
                else "SECTION_INSPECTION_FAILED",
                "child failure",
            )
        if not result_path.exists():
            raise SectionInspectionError("SECTION_INSPECTION_FAILED", "no inspection result")
        result = _read_json(result_path, limits.max_output_bytes)
    return verify_result(result, expected_sha256)


def verify_result(result: object, expected_sha256: str) -> dict:
    """Reject any child output whose pins or self-hash do not hold."""
    if (
        not isinstance(result, dict)
        or result.get("source_sha256") != expected_sha256
        or result.get("policy_sha256") != POLICY_HASH
        or result.get("status") != "candidate_only"
        or "source_path" in result
        or not isinstance(result.get("map_sha256"), str)
    ):
        raise SectionInspectionError("SECTION_INSPECTION_FAILED", "inspection result pins")
    unhashed = {key: value for key, value in result.items() if key != "map_sha256"}
    if canonical_hash(unhashed) != result["map_sha256"]:
        raise SectionInspectionError("SECTION_INSPECTION_FAILED", "inspection result hash")
    return result


def _read_json(path: Path, maximum: int) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise SectionInspectionError("SECTION_INSPECTION_RESOURCE_LIMIT", "result too large")
    try:
        value = json.loads(raw)
    except ValueError:
        raise SectionInspectionError("SECTION_INSPECTION_FAILED", "invalid result") from None
    if not isinstance(value, dict):
        raise SectionInspectionError("SECTION_INSPECTION_FAILED", "invalid result")
    return value


def _child(work: Path) -> None:
    request = json.loads((work / _REQUEST).read_text(encoding="utf-8"))
    _child_limits(
        {
            "timeout_seconds": request["timeout_seconds"],
            "max_output_bytes": request["max_output_bytes"],
        }
    )
    content = (work / "source.pdf").read_bytes()
    try:
        result = inspect_pdf_bytes(content, expected_sha256=request["expected_sha256"])
    except SectionInspectionError as error:
        (work / _ERROR).write_text(json.dumps({"code": error.code}), encoding="utf-8")
        return
    # Write-then-rename so the parent never reads a half-written result.
    partial = work / (_RESULT + ".partial")
    partial.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    partial.replace(work / _RESULT)


if __name__ == "__main__":
    _child(Path(sys.argv[1]))
