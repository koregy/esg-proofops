"""Import parse_report_api batches as an immutable, replay-verifiable candidate sidecar.

Offline only: no key, no network, no ledger, no AWS. Reads one
``scripts/parse_report_api.py --out-dir`` (schema ``upstage-parse-batches-v1``)
plus the ORIGINAL source PDF, converts every completed batch with the pinned
``upstage_candidates`` converter and publishes a NEW directory (atomic rename,
read-only files). The parse run and any ODL graph are never modified.

    import  --parse-dir RUN --pdf SRC --out-dir SIDECAR [binding ids] [--require-complete]
    verify  --sidecar SIDECAR --pdf SRC [--expect-sha256 HEX]

Without binding ids the sidecar gets deterministic ``offline_derived`` ids and
binds to no run. With ``--tenant-id --document-id --document-version-id
--parse-manifest-id --object-version-id`` (all five) it is bound to that run
identity. Output is ``candidate_only``: not source-verified, citation not
approved, table-level boxes only (no cells). ``derived/review.csv`` is the
reviewer's offline table. Errors print one fail-closed code and exit 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from proofops.adapters.local.upstage_candidate_store import (
    SidecarBinding,
    UpstageSidecarError,
    import_parse_run,
    load_sidecar,
)

MAX_SOURCE_BYTES = 512 * 1024 * 1024
BINDING = ("tenant_id", "document_id", "document_version_id", "parse_manifest_id")


def _source(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise UpstageSidecarError("UPSTAGE_SOURCE_UNREADABLE")
    return path.read_bytes()


def _summary(manifest: dict, sha: str, directory: Path) -> dict:
    return {
        "sidecar_dir": str(directory),
        "sidecar_sha256": sha,
        "labels": {
            key: manifest[key]
            for key in ("quality", "source_verification", "citation_approved", "graph_effect")
        },
        "binding": manifest["binding"],
        "source_sha256": manifest["source"]["sha256"],
        "origin_manifest_sha256": manifest["origin"]["manifest_sha256"],
        "totals": manifest["totals"],
        "not_converted": [
            {k: b[k] for k in ("index", "state", "code", "physical_pages") if k in b}
            for b in manifest["batches"]
            if b["state"] != "converted"
        ],
        "review_csv": str(directory / "derived" / "review.csv"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="import_parse_api_candidates", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("import")
    run.add_argument("--parse-dir", type=Path, required=True)
    run.add_argument("--pdf", type=Path, required=True, help="original source PDF")
    run.add_argument("--out-dir", type=Path, required=True, help="new sidecar directory")
    for name in (*BINDING, "object_version_id"):
        run.add_argument("--" + name.replace("_", "-"))
    run.add_argument("--synthetic", action="store_true", help="source is synthetic test data")
    run.add_argument("--require-complete", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("--sidecar", type=Path, required=True)
    check.add_argument("--pdf", type=Path, required=True)
    check.add_argument("--expect-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            sidecar = load_sidecar(
                args.sidecar, _source(args.pdf), expected_sha256=args.expect_sha256
            )
            print(
                json.dumps(
                    {
                        "verified": True,
                        **_summary(sidecar.manifest, sidecar.manifest_sha256, args.sidecar),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        given = [getattr(args, n) for n in (*BINDING, "object_version_id")]
        if any(given) and not all(given):
            parser.error(
                "binding needs all of --tenant-id --document-id "
                "--document-version-id --parse-manifest-id --object-version-id"
            )
        binding = SidecarBinding(*given) if all(given) else None
        manifest, sha = import_parse_run(
            args.parse_dir,
            args.out_dir,
            _source(args.pdf),
            binding,
            synthetic=args.synthetic,
            require_complete=args.require_complete,
        )
        # Re-open what was published: the printed summary is of a replay-verified artifact.
        load_sidecar(args.out_dir, _source(args.pdf), expected_sha256=sha)
        print(
            json.dumps(
                {"imported": True, **_summary(manifest, sha, args.out_dir)},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    except (UpstageSidecarError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
