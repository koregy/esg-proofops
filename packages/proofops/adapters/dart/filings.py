"""Bounded OpenDART filing-history search for REC-006 (adopted 2026-09-28).

REC-006 pins the latest official corrected filing available at the evaluation
cutoff and preserves every earlier receipt. Our existing DartClient defaults to
``last_reprt_at=Y``; the official API defaults to ``N``. The official guide
DS001/2019001 defines ``N`` as including corrections and prior filings and ``Y``
as latest only. This collector explicitly requests ``N`` and walks every page
up to an explicit bound.

The output is a *search record* plus the exact raw page bytes, not a decision.
The record's ``search`` section is what a ``rec-002-006-v1`` revision receipt
carries; evaluation re-reads the pages by SHA-256 and re-parses them with the
same :func:`assemble_filing_pages`, so the listing below is operator convenience
only. ``report_nm`` is preserved verbatim and never parsed for a period; lineage
classification and the pinned period/consolidation are reviewer inputs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from proofops.adapters.dart.client import DartClient, DartError
from proofops.application.reconciliation.revision import (
    FILING_SEARCH_VERSION_V2,
    LIST_ENDPOINT,
    assemble_filing_pages,
)

FILING_SEARCH_VERSION = "opendart-list-history-1"
DEFAULT_MAX_PAGES = 20


def collect_filing_history(
    client: DartClient,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    *,
    pblntf_ty: str | None = None,
    pblntf_detail_ty: str | None = None,
    page_count: int = 100,
    max_pages: int = DEFAULT_MAX_PAGES,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return ``(record, pages_by_sha256)`` for a ``last_reprt_at=N`` search.

    ``client`` is constructed by the caller (tests inject a fake transport).
    Transport/API failures, a page bound, drift or a count mismatch make the
    record ``incomplete`` (errors by class name only, so no URL or key leaks);
    an incomplete record can never justify a pin.

    With ``clock`` (a trusted, timezone-aware clock of the collecting process),
    each page records ``retrieved_at``, the UTC instant read just before that
    request, and the record becomes ``opendart-list-history-2``. The fetch instant
    is not in the page bytes, so only the collector can state it. Without a clock
    the record is the unchanged ``opendart-list-history-1`` shape.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be an integer between 1 and 100")
    request = {
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "last_reprt_at": "N",
        "pblntf_ty": pblntf_ty,
        "pblntf_detail_ty": pblntf_detail_ty,
        "page_count": page_count,
    }
    pages: list[dict[str, Any]] = []
    payloads: list[bytes] = []
    reason: str | None = None
    page_no = 1
    while True:
        fetched_at = _instant(clock) if clock is not None else None
        try:
            response = client.list_filings(
                corp_code,
                bgn_de,
                end_de,
                pblntf_ty=pblntf_ty,
                pblntf_detail_ty=pblntf_detail_ty,
                last_reprt_at="N",
                page_no=page_no,
                page_count=page_count,
            )
        except DartError as error:
            reason = f"request_failed:{type(error).__name__}"
            break
        payload = response.raw_bytes
        payloads.append(payload)
        page = {"page_no": page_no, "sha256": hashlib.sha256(payload).hexdigest()}
        if fetched_at is not None:
            page["retrieved_at"] = fetched_at
        pages.append(page)
        if response.get("status") == "013":
            break
        total_page = response.get("total_page")
        if not isinstance(total_page, int) or isinstance(total_page, bool):
            reason = "pagination_metadata_invalid"
            break
        if page_no >= total_page:
            break
        if page_no >= max_pages:
            reason = "page_bound_exceeded"
            break
        page_no += 1
    filings, assembled = assemble_filing_pages(request, payloads)
    reason = reason or assembled
    record = {
        "search_version": FILING_SEARCH_VERSION if clock is None else FILING_SEARCH_VERSION_V2,
        "endpoint": LIST_ENDPOINT,
        "request": request,
        "pages": pages,
        "max_pages": max_pages,
        "state": "incomplete" if reason else "complete",
        "incomplete_reason": reason,
        "filings": filings,
    }
    return record, {page["sha256"]: data for page, data in zip(pages, payloads, strict=True)}


def _instant(clock: Callable[[], datetime]) -> str:
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("collector clock must return a timezone-aware datetime")
    return now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
