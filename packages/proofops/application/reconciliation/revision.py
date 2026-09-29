"""Adopted REC-002 / REC-006 revision (R00 section 12, 2026-09-28).

Contract schema 1.1 and the pure domain engine stay frozen. This revision is an
application-layer verification gate that a caller opts into with
``adopted_revision=REVISION``; without it ``reconcile`` runs the unchanged legacy
path, so stored 1.1 results replay byte-for-byte.

The trusted input is a *revision receipt*: an operator import that carries no
authority until an authenticated reviewer confirms it. Confirmation is injected by
the trusted store (never taken from the import) and binds the canonical hash of
the receipt body, so a receipt edited after review is rejected.

REC-002 — an explanation counts only when the receipt binds that exact verified
quote (document, artifact hash, locator, quote) to this claim, item and package
and to the compared sustainability/financial facts. A verified quote that is not
so bound is excluded, never promoted.

REC-006 — the financial filing must be the latest official filing of the
reviewer-classified report lineage whose OpenDART receipt date is on or before the
cutoff. The lineage is proven from a replayed ``/api/list.json`` search with
``last_reprt_at=N`` whose raw page bytes are re-read and re-parsed here; every
earlier receipt stays in that record. Financial dates are never read from
``report_nm``: the pinned period must equal the packet identity and be literally
present, after deterministic date normalisation, in verified quotes from the
pinned filing. Anything incomplete, ambiguous or unverified is ``blocked``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

REVISION = "rec-002-006-v1"
ENGINE_SUFFIX = f"+app-{REVISION}"
CUTOFF_GRANULARITY = "date_inclusive"
LIST_ENDPOINT = "/api/list.json"
_RCEPT_NO = re.compile(r"^[0-9]{14}$")
_DATE_8 = re.compile(r"^[0-9]{8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROW_FIELDS = ("corp_code", "corp_name", "report_nm", "rcept_no", "flr_nm", "rcept_dt", "rm")
_REQUEST_KEYS = {
    "corp_code",
    "bgn_de",
    "end_de",
    "last_reprt_at",
    "pblntf_ty",
    "pblntf_detail_ty",
    "page_count",
}
_DATE_PATTERNS = (
    re.compile(r"(?<![0-9])([0-9]{4})\s*년\s*([0-9]{1,2})\s*월\s*([0-9]{1,2})\s*일"),
    re.compile(r"(?<![0-9])([0-9]{4})\s*[-./]\s*([0-9]{1,2})\s*[-./]\s*([0-9]{1,2})(?![0-9])"),
    re.compile(r"(?<![0-9])([0-9]{4})([0-9]{2})([0-9]{2})(?![0-9])"),
)


class RevisionBlocked(Exception):
    """A revision check failed; ``code`` becomes the blocked reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sha(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def receipt_body_sha256(receipt: Mapping[str, Any]) -> str:
    """Canonical hash of a receipt without its injected confirmation."""
    return _sha({key: value for key, value in receipt.items() if key != "confirmation"})


def _iso(value: Any, code: str) -> date:
    if not isinstance(value, str):
        raise RevisionBlocked(code)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RevisionBlocked(code) from exc


def receipt_date(rcept_dt: Any) -> date:
    """OpenDART ``rcept_dt`` (YYYYMMDD) is the official receipt date, not a period."""
    if not isinstance(rcept_dt, str) or _DATE_8.fullmatch(rcept_dt) is None:
        raise RevisionBlocked("filing_row_invalid")
    try:
        return date(int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:]))
    except ValueError as exc:
        raise RevisionBlocked("filing_row_invalid") from exc


def normalized_dates(text: str) -> set[str]:
    """Deterministic ISO dates literally written in ``text`` (ISO, dotted, Korean)."""
    found: set[str] = set()
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                found.add(date(*(int(part) for part in match.groups())).isoformat())
            except ValueError:
                continue
    return found


# --------------------------------------------------------------------------- #
# OpenDART list pages (shared with adapters.dart.filings)
# --------------------------------------------------------------------------- #


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def assemble_filing_pages(
    request: Mapping[str, Any], payloads: list[bytes]
) -> tuple[list[dict[str, Any]], str | None]:
    """Parse ordered raw ``list.json`` pages into one filing listing.

    Returns ``(filings, None)`` only for a complete, internally consistent search;
    otherwise ``(partial, reason)``. Rows are copied verbatim, titles included.
    """
    filings: list[dict[str, Any]] = []
    page_count = request.get("page_count")
    if type(page_count) is not int or not 1 <= page_count <= 100:
        return filings, "filing_request_invalid"
    if not payloads:
        return filings, "filing_pages_missing"
    totals: tuple[int, int] | None = None
    seen: set[str] = set()
    for index, payload in enumerate(payloads, start=1):
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return filings, "filing_page_invalid"
        if not isinstance(data, dict):
            return filings, "filing_page_invalid"
        if data.get("status") == "013":
            if index != 1 or len(payloads) != 1:
                return filings, "filing_page_invalid"
            return filings, None
        if data.get("status") != "000":
            return filings, "filing_page_status"
        total_count, total_page = _int(data.get("total_count")), _int(data.get("total_page"))
        if total_count is None or total_page is None or _int(data.get("page_no")) != index:
            return filings, "pagination_metadata_invalid"
        if totals is None:
            totals = (total_count, total_page)
        elif totals != (total_count, total_page):
            return filings, "pagination_drift"
        rows = data.get("list")
        if not isinstance(rows, list) or len(rows) > page_count:
            return filings, "filing_page_invalid"
        for row in rows:
            if not isinstance(row, dict):
                return filings, "filing_row_invalid"
            rcept_no = row.get("rcept_no")
            if not isinstance(rcept_no, str) or _RCEPT_NO.fullmatch(rcept_no) is None:
                return filings, "filing_row_invalid"
            try:
                receipt_date(row.get("rcept_dt"))
            except RevisionBlocked:
                return filings, "filing_row_invalid"
            if row.get("corp_code") != request.get("corp_code"):
                return filings, "filing_row_invalid"
            if rcept_no in seen:
                return filings, "duplicate_rcept_no"
            seen.add(rcept_no)
            filings.append({key: row.get(key) for key in _ROW_FIELDS})
    assert totals is not None
    if totals[1] != len(payloads):
        return filings, "filing_pages_missing"
    if totals[0] != len(filings):
        return filings, "row_count_mismatch"
    return filings, None


# --------------------------------------------------------------------------- #
# receipt checks
# --------------------------------------------------------------------------- #


def require_confirmed(receipt: Any) -> Mapping[str, Any]:
    if not isinstance(receipt, Mapping) or receipt.get("revision") != REVISION:
        raise RevisionBlocked("revision_receipt_invalid")
    confirmation = receipt.get("confirmation")
    if not isinstance(confirmation, Mapping):
        raise RevisionBlocked("revision_receipt_unconfirmed")
    actor = confirmation.get("actor")
    if not isinstance(actor, str) or not actor:
        raise RevisionBlocked("revision_receipt_unconfirmed")
    _iso(confirmation.get("confirmed_on"), "revision_receipt_unconfirmed")
    if confirmation.get("receipt_sha256") != receipt_body_sha256(receipt):
        raise RevisionBlocked("revision_receipt_tampered")
    return receipt


def explanation_bound(
    receipt: Mapping[str, Any], candidate: Mapping[str, Any], packet: Mapping[str, Any]
) -> bool:
    """REC-002: exact binding of one verified quote to the compared difference."""
    bindings = receipt.get("explanation_bindings")
    binding = bindings.get(candidate["source_id"]) if isinstance(bindings, Mapping) else None
    if not isinstance(binding, Mapping):
        return False
    identity = packet["identity"]
    expected = {
        "claim_id": identity["claim_id"],
        "item": packet["item"],
        "package_id": identity["package_id"],
        "document_id": candidate["document_id"],
        "artifact_sha256": candidate["artifact_sha256"],
        "locator": candidate["locator"],
        "quote": candidate["quote"],
        "compared": {
            "sustainability_source_id": packet["sustainability"]["source_id"],
            "sustainability_normalized": packet["sustainability"]["normalized"],
            "financial_source_id": packet["financial"]["source_id"],
            "financial_normalized": packet["financial"]["normalized"],
        },
    }
    return dict(binding) == expected


def _source_list(value: Any, sources: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not value or len(set(map(str, value))) != len(value):
        raise RevisionBlocked("financial_evidence_missing")
    if any(not isinstance(item, str) or item not in sources for item in value):
        raise RevisionBlocked("financial_evidence_missing")
    return [sources[item] for item in value]


def verify_filing_history(
    receipt: Mapping[str, Any],
    packet: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Any],
    page_reader: Callable[[str], bytes] | None,
) -> str:
    """REC-006: return the pinned ``rcept_no`` or raise ``RevisionBlocked``.

    ``sources`` must already be byte-verified against the document registry.
    """
    identity = packet["identity"]
    history = receipt.get("filing_history")
    if not isinstance(history, Mapping):
        raise RevisionBlocked("filing_history_missing")
    cutoff = history.get("cutoff")
    if not isinstance(cutoff, Mapping) or cutoff.get("granularity") != CUTOFF_GRANULARITY:
        raise RevisionBlocked("filing_cutoff_invalid")
    if cutoff.get("date") != identity["as_of_date"]:
        raise RevisionBlocked("filing_cutoff_mismatch")
    as_of = _iso(identity["as_of_date"], "filing_cutoff_invalid")

    period_start, period_end = identity["financial_period_start"], identity["financial_period_end"]
    if period_start is None or period_end is None:
        raise RevisionBlocked("financial_period_unresolved")
    if identity["consolidation"] not in {"consolidated", "separate"}:
        raise RevisionBlocked("financial_consolidation_unverified")

    search = history.get("search")
    request = search.get("request") if isinstance(search, Mapping) else None
    pages = search.get("pages") if isinstance(search, Mapping) else None
    if (
        not isinstance(search, Mapping)
        or search.get("endpoint") != LIST_ENDPOINT
        or not isinstance(request, Mapping)
        or set(request) != _REQUEST_KEYS
        or not isinstance(pages, list)
    ):
        raise RevisionBlocked("filing_search_invalid")
    if request["last_reprt_at"] != "N":
        # "Y" returns only the final version and hides the receipt history.
        raise RevisionBlocked("filing_search_latest_only")
    if request["corp_code"] != identity["dart_corp_code"]:
        raise RevisionBlocked("filing_search_identity_mismatch")
    for key in ("bgn_de", "end_de"):
        if not isinstance(request[key], str) or _DATE_8.fullmatch(request[key]) is None:
            raise RevisionBlocked("filing_search_invalid")
    begin, end = receipt_date(request["bgn_de"]), receipt_date(request["end_de"])
    if end < as_of:
        raise RevisionBlocked("filing_search_before_cutoff")
    if begin > _iso(period_start, "financial_period_unresolved"):
        raise RevisionBlocked("filing_search_window_incomplete")
    if page_reader is None:
        raise RevisionBlocked("filing_pages_unreadable")
    payloads: list[bytes] = []
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, Mapping) or page.get("page_no") != index:
            raise RevisionBlocked("filing_search_invalid")
        digest = page.get("sha256")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise RevisionBlocked("filing_search_invalid")
        try:
            payload = page_reader(digest)
        except Exception as exc:
            raise RevisionBlocked("filing_pages_unreadable") from exc
        if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != digest:
            raise RevisionBlocked("filing_page_tampered")
        payloads.append(payload)
    filings, reason = assemble_filing_pages(request, payloads)
    if reason is not None:
        raise RevisionBlocked("filing_search_incomplete")

    # Rows received after the cutoff are preserved in the record but never used.
    timely = {row["rcept_no"]: row for row in filings if receipt_date(row["rcept_dt"]) <= as_of}
    classification = history.get("classification")
    if not isinstance(classification, Mapping) or set(classification) - {
        row["rcept_no"] for row in filings
    }:
        raise RevisionBlocked("filing_classification_invalid")
    family: list[dict[str, Any]] = []
    for rcept_no, row in timely.items():
        entry = classification.get(rcept_no)
        basis = entry.get("basis") if isinstance(entry, Mapping) else None
        lineage = entry.get("lineage") if isinstance(entry, Mapping) else None
        if lineage not in {"family", "unrelated"} or not isinstance(basis, str) or not basis:
            raise RevisionBlocked("filing_classification_incomplete")
        if lineage == "family":
            family.append(row)
    if not family:
        # REC-006 not_applicable needs an explicit schema outcome that 1.1 lacks.
        raise RevisionBlocked("financial_filing_not_available_as_of")
    latest_day = max(receipt_date(row["rcept_dt"]) for row in family)
    latest = [row for row in family if receipt_date(row["rcept_dt"]) == latest_day]
    if len(latest) != 1:
        # Official data gives only a date; never order same-day filings by number.
        raise RevisionBlocked("filing_order_ambiguous")
    pinned_row = latest[0]

    pinned = history.get("pinned")
    if not isinstance(pinned, Mapping) or pinned.get("rcept_no") != pinned_row["rcept_no"]:
        raise RevisionBlocked("financial_filing_not_latest")
    if identity["rcept_no"] != pinned_row["rcept_no"]:
        raise RevisionBlocked("financial_filing_not_latest")
    if identity["financial_published_at"] != latest_day.isoformat():
        raise RevisionBlocked("financial_filing_date_mismatch")
    if (pinned.get("period_start"), pinned.get("period_end")) != (period_start, period_end):
        raise RevisionBlocked("financial_period_unverified")
    if pinned.get("consolidation") != identity["consolidation"]:
        raise RevisionBlocked("financial_consolidation_unverified")

    def from_pinned(items: list[Mapping[str, Any]]) -> None:
        for source in items:
            entry = documents.get(source["document_id"])
            if (
                not isinstance(entry, Mapping)
                or entry.get("document_role") != "financial"
                or entry.get("rcept_no") != pinned_row["rcept_no"]
            ):
                raise RevisionBlocked("financial_evidence_not_pinned")

    period_sources = _source_list(pinned.get("period_evidence_source_ids"), sources)
    consolidation_sources = _source_list(pinned.get("consolidation_evidence_source_ids"), sources)
    from_pinned(period_sources)
    from_pinned(consolidation_sources)
    written: set[str] = set()
    for source in period_sources:
        written |= normalized_dates(source["quote"])
    if not {period_start, period_end} <= written:
        raise RevisionBlocked("financial_period_unverified")
    return pinned_row["rcept_no"]
