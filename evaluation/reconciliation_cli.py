"""Standalone reconciliation CLI with explicit, operator-controlled trust inputs.

The packet is untrusted. Registry files are local configuration supplied by the
operator, never inferred from assertions inside that packet. No live model call.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import sys
from datetime import date
from pathlib import Path
from typing import Any

from proofops.application.reconciliation.presentation import project_result

MAX_JSON_BYTES = 8 * 1024 * 1024


class InputRejected(ValueError):
    """The command input cannot safely be interpreted."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputRejected("duplicate_json_key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise InputRejected("non_finite_json_number")


def _float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InputRejected("non_finite_json_number")
    return parsed


def load_json(path: Path) -> dict[str, Any]:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_BYTES:
            raise InputRejected("input_size_or_type_invalid")
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES or len(raw) != info.st_size:
            raise InputRejected("input_changed_or_too_large")
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputRejected("input_unreadable_or_invalid_json") from exc
    if not isinstance(value, dict):
        raise InputRejected("json_object_required")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Create an immutable result; never replace an existing revision."""
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--packet", required=True, type=Path)
    cli.add_argument("--policy", required=True, type=Path)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--projection", type=Path, help="Optional separate presentation1 companion")
    cli.add_argument("--artifacts", type=Path, help="Operator-controlled original artifact root")
    cli.add_argument("--documents", type=Path, help="Trusted document registry JSON")
    cli.add_argument("--artifact-index", type=Path, help="Trusted document-to-local-artifact map")
    cli.add_argument("--policy-registry", type=Path, help="Trusted policy approval registry JSON")
    cli.add_argument(
        "--coverage-registry", type=Path, help="Trusted coverage receipt registry JSON"
    )
    cli.add_argument("--explanations", type=Path, help="Candidate sources JSON: {sources: [...]}")
    # Opt-in adopted revision (R00 section 12). Without it the output is unchanged.
    cli.add_argument(
        "--adopted-revision",
        choices=("rec-002-006-v1", "rec-002-006-v2"),
        help="v2 writes output schema 1.2 (explicit REC-006 not_applicable reason)",
    )
    cli.add_argument(
        "--revision-receipt", type=Path, help="Trusted, reviewer-confirmed revision receipt JSON"
    )
    cli.add_argument(
        "--filing-pages",
        type=Path,
        help="Directory of raw OpenDART list pages named <sha256>.json",
    )
    cli.add_argument(
        "--evaluation-date",
        type=date.fromisoformat,
        help="v2 only: evaluation date that closes the filing cutoff (default: today)",
    )
    cli.add_argument(
        "--filing-collection",
        type=Path,
        help="v2 only: collector record (opendart-list-history-2) supplied by the local"
        " operator as trusted evidence, like the registry files. Its clock-stamped pages are"
        " the only accepted retrieval times, but this file is operator-supplied evidence,"
        " never cryptographic proof of a server fetch; only the store's own collection is.",
    )
    return cli


def _page_reader(root: Path | None) -> Any:
    """Read a raw filing page by digest; the gate re-hashes every returned byte."""
    if root is None:
        return None

    def read(digest: str) -> bytes:
        if len(digest) != 64 or not all(char in "0123456789abcdef" for char in digest):
            raise InputRejected("filing_page_digest_invalid")
        path = root / f"{digest}.json"
        if path.stat().st_size > MAX_JSON_BYTES:
            raise InputRejected("filing_page_too_large")
        return path.read_bytes()

    return read


def _optional(path: Path | None) -> dict[str, Any]:
    return {} if path is None else load_json(path)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.output.exists():
            raise InputRejected("output_exists_use_new_revision")
        if args.projection is not None and (
            args.projection.exists() or args.projection.resolve() == args.output.resolve()
        ):
            raise InputRejected("projection_output_exists_or_conflicts")
        packet = load_json(args.packet)
        policy = load_json(args.policy)
        documents = _optional(args.documents)
        artifact_index = _optional(args.artifact_index)
        policies = _optional(args.policy_registry)
        coverage = _optional(args.coverage_registry)
        candidates = _optional(args.explanations).get("sources", [])
        if not isinstance(candidates, list) or any(not isinstance(x, dict) for x in candidates):
            raise InputRejected("invalid_explanation_candidates")
        from proofops.application.reconciliation.service import reconcile

        def unavailable_reader(ref: dict[str, Any]) -> bytes:
            raise FileNotFoundError("source_unavailable")

        reader: Any = unavailable_reader
        if args.artifacts is not None:
            from proofops.adapters.reconciliation import FileSourceReader

            reader = FileSourceReader(args.artifacts, artifact_index)
        revision_kwargs: dict[str, Any] = {}
        if args.adopted_revision is not None:
            revision_kwargs = {
                "adopted_revision": args.adopted_revision,
                "revision_receipt": (
                    None if args.revision_receipt is None else load_json(args.revision_receipt)
                ),
                "filing_page_reader": _page_reader(args.filing_pages),
            }
            if args.evaluation_date is not None:
                revision_kwargs["evaluation_date"] = args.evaluation_date
            if args.filing_collection is not None:
                revision_kwargs["filing_collection"] = load_json(args.filing_collection)
        elif any(
            value is not None
            for value in (
                args.revision_receipt,
                args.filing_pages,
                args.evaluation_date,
                args.filing_collection,
            )
        ):
            raise InputRejected("revision_inputs_without_adopted_revision")
        try:
            result = reconcile(
                packet,
                policy,
                source_reader=reader,
                explanation_search=lambda _: candidates,
                policy_registry=policies,
                coverage_registry=coverage,
                document_registry=documents,
                **revision_kwargs,
            )
        except NotImplementedError:
            if packet.get("item") != "C5":
                raise
            result = {
                "dispatch_schema_version": "1.0",
                "item": "C5",
                "execution_state": "not_run",
                "status": None,
                "reason_codes": ["stage_disabled"],
                "synthetic": packet.get("synthetic") is True,
            }
        write_json(args.output, result)
        if args.projection is not None:
            write_json(args.projection, project_result(result, packet, documents, candidates))
        return 0
    except (ValueError, PermissionError, FileExistsError) as exc:
        # Error types/codes only: no packet content, credential-bearing URL or traceback.
        code = str(exc) if isinstance(exc, InputRejected) else "input_or_authorization_rejected"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2
    except Exception:
        print('{"error":"collection_or_internal_error"}', file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
