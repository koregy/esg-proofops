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
from datetime import UTC, date, datetime
from typing import Any

REVISION = "rec-002-006-v1"
ENGINE_SUFFIX = f"+app-{REVISION}"
# Opt-in successor: the same gates plus the REC-006 "complete official lookup, no
# timely same-period filing -> completed not_applicable with an explicit reason"
# outcome, which needs output schema 1.2. ``REVISION`` results stay schema 1.1.
REVISION_V2 = "rec-002-006-v2"
ENGINE_SUFFIX_V2 = f"+app-{REVISION_V2}"
REVISIONS = frozenset({REVISION, REVISION_V2})
NO_TIMELY_FILING = "financial_filing_not_available_as_of"
# Collector record whose pages carry the collector clock's fetch instant.
FILING_SEARCH_VERSION_V2 = "opendart-list-history-2"
_COLLECTED_SEARCH_KEYS = ("search_version", "endpoint", "request", "pages")
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

    def __init__(self, code: str, *also: str) -> None:
        super().__init__(code)
        self.code = code
        self.codes = (code, *also)


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


def require_confirmed(receipt: Any, revision: str = REVISION) -> Mapping[str, Any]:
    if not isinstance(receipt, Mapping) or receipt.get("revision") != revision:
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
    replay = _replay_history(receipt, packet, page_reader)
    if not replay["family"]:
        # REC-006 not_applicable needs an explicit schema outcome that 1.1 lacks.
        raise RevisionBlocked(NO_TIMELY_FILING)
    return _pin_latest(replay, packet, sources, documents)


def verify_filing_lookup(
    receipt: Mapping[str, Any],
    packet: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Any],
    page_reader: Callable[[str], bytes] | None,
    *,
    evaluation_date: date,
    collection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """REC-006 for ``REVISION_V2``: a pinned filing or a proven absence.

    Returns the ``filing_lookup`` record of output schema 1.2 with every replayed
    row (``rcept_no``, receipt date, verbatim title, timeliness, reviewer lineage).
    ``collection`` is the *trusted* collector record (from the store that ran the
    collector with its own clock, or an operator trust input for the CLI); retrieval
    instants are read only from it, never from the receipt. ``no_timely_filing`` is
    returned only when that collection is complete, fetched after the cutoff day,
    unfiltered, confirmed after fetch and evaluated after confirmation, every timely
    row is reviewer-classified, and the reviewer-declared family/period/
    consolidation is written in verified package quotes. Everything else raises the
    v1 blocked codes or a specific one.
    """
    history = receipt.get("filing_history")
    cutoff = history.get("cutoff") if isinstance(history, Mapping) else None
    if isinstance(cutoff, Mapping) and isinstance(cutoff.get("date"), str):
        # A cutoff that has not passed can still receive a timely filing.
        if _iso(cutoff["date"], "filing_cutoff_invalid") > evaluation_date:
            raise RevisionBlocked("filing_cutoff_future")
    replay = _replay_history(receipt, packet, page_reader)
    named = isinstance(history, Mapping) and "collection_sha256" in history
    if replay["family"]:
        times = _collection_times(receipt, collection) if named else None
        pinned = _pin_latest(replay, packet, sources, documents)
        return _lookup_record(replay, "pinned", pinned, None, times)
    try:
        times = _collection_times(receipt, collection)
        family_basis = _no_timely_filing(
            replay, packet, sources, documents, evaluation_date, receipt, times
        )
    except RevisionBlocked as error:
        # Same headline as v1 for an unproven absence, plus the specific cause.
        raise RevisionBlocked(NO_TIMELY_FILING, error.code) from error
    return _lookup_record(replay, "no_timely_filing", None, family_basis, times)


def collection_sha256(record: Mapping[str, Any]) -> str:
    """Canonical hash that a receipt uses to name one collector record."""
    return _sha(dict(record))


def _collection_times(
    receipt: Mapping[str, Any], collection: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Bind the replayed search to the trusted collector record and its clock."""
    history = receipt["filing_history"]
    if not isinstance(collection, Mapping):
        raise RevisionBlocked("filing_lookup_collection_missing")
    digest = collection_sha256(collection)
    if history.get("collection_sha256") != digest:
        raise RevisionBlocked("filing_lookup_collection_mismatch")
    if collection.get("search_version") != FILING_SEARCH_VERSION_V2 or (
        collection.get("state") != "complete" or collection.get("incomplete_reason") is not None
    ):
        raise RevisionBlocked("filing_lookup_collection_incomplete")
    search = history.get("search")
    # The receipt restates the collector's search verbatim, fetch instants included.
    if not isinstance(search, Mapping) or dict(search) != {
        key: collection.get(key) for key in _COLLECTED_SEARCH_KEYS
    }:
        raise RevisionBlocked("filing_lookup_collection_mismatch")
    instants: list[datetime] = []
    for page in collection.get("pages") or []:
        value = page.get("retrieved_at") if isinstance(page, Mapping) else None
        try:
            instant = datetime.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            instant = None
        if instant is None or instant.tzinfo is None:
            raise RevisionBlocked("filing_lookup_retrieval_unrecorded")
        instants.append(instant.astimezone(UTC))
    if not instants:
        raise RevisionBlocked("filing_lookup_retrieval_unrecorded")
    return {
        "collection_sha256": digest,
        "first": min(instants),
        "last": max(instants),
    }


def _replay_history(
    receipt: Mapping[str, Any],
    packet: Mapping[str, Any],
    page_reader: Callable[[str], bytes] | None,
) -> dict[str, Any]:
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
    return {
        "history": history,
        "search": search,
        "request": request,
        "as_of": as_of,
        "filings": filings,
        "classification": classification,
        "family": family,
    }


def _pin_latest(
    replay: Mapping[str, Any],
    packet: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Any],
) -> str:
    identity = packet["identity"]
    history, family = replay["history"], replay["family"]
    period_start, period_end = identity["financial_period_start"], identity["financial_period_end"]
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
    return str(pinned_row["rcept_no"])


def _no_timely_filing(
    replay: Mapping[str, Any],
    packet: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Any],
    evaluation_date: date,
    receipt: Mapping[str, Any],
    times: Mapping[str, Any],
) -> str:
    """REC-006 A: a *genuine* absence, as opposed to an unknown source.

    Returns the reviewer's family basis. Every failure is blocked with a specific
    code, so an unproven absence can never read as not_applicable.
    """
    identity, history = packet["identity"], replay["history"]
    request, as_of = replay["request"], replay["as_of"]
    # ``rcept_dt`` is an OpenDART calendar date (a Korean service). A fetch whose
    # UTC date is after the cutoff began after that day ended in every zone at or
    # east of UTC; a fetch on the cutoff day cannot exclude a later same-day receipt.
    first_day, last_day = times["first"].date(), times["last"].date()
    if first_day == as_of:
        raise RevisionBlocked("filing_lookup_cutoff_day_open")
    if first_day < as_of:
        raise RevisionBlocked("filing_lookup_before_cutoff")
    if last_day > evaluation_date:
        raise RevisionBlocked("filing_lookup_retrieval_future")
    confirmed_on = _iso(receipt["confirmation"]["confirmed_on"], "revision_receipt_unconfirmed")
    if confirmed_on < last_day:
        # A reviewer cannot have confirmed a lookup that had not been fetched yet.
        raise RevisionBlocked("filing_lookup_confirmed_before_retrieval")
    if evaluation_date < confirmed_on:
        raise RevisionBlocked("filing_lookup_evaluated_before_confirmation")
    if request["pblntf_ty"] not in (None, "") or request["pblntf_detail_ty"] not in (None, ""):
        # A type-filtered listing cannot show that no family filing exists.
        raise RevisionBlocked("filing_lookup_filtered")

    financial = packet["financial"]
    financial_sources = [
        source
        for source in sources.values()
        if isinstance(documents.get(source["document_id"]), Mapping)
        and documents[source["document_id"]].get("document_role") == "financial"
    ]
    if (
        identity["rcept_no"] is not None
        or identity["financial_published_at"] is not None
        or any(financial[key] is not None for key in ("raw", "normalized", "source_id"))
        or history.get("pinned") is not None
        or financial_sources
        # Only input 1.2 can say "no financial document": a 1.1 version string
        # names a document, and a placeholder never stands in for a filing.
        or identity["financial_document_version"] is not None
    ):
        raise RevisionBlocked("financial_filing_identity_conflict")

    declared = history.get("no_timely_filing")
    basis = declared.get("family_basis") if isinstance(declared, Mapping) else None
    if not isinstance(declared, Mapping) or not isinstance(basis, str) or not basis.strip():
        raise RevisionBlocked("no_timely_filing_unreviewed")
    period = (identity["financial_period_start"], identity["financial_period_end"])
    if (declared.get("period_start"), declared.get("period_end")) != period:
        raise RevisionBlocked("financial_period_unverified")
    if declared.get("consolidation") != identity["consolidation"]:
        raise RevisionBlocked("financial_consolidation_unverified")
    period_sources = _source_list(declared.get("period_evidence_source_ids"), sources)
    consolidation_sources = _source_list(declared.get("consolidation_evidence_source_ids"), sources)
    for source in (*period_sources, *consolidation_sources):
        entry = documents.get(source["document_id"])
        if not isinstance(entry, Mapping) or entry.get("document_role") != "sustainability":
            raise RevisionBlocked("financial_evidence_missing")
    written: set[str] = set()
    for source in period_sources:
        written |= normalized_dates(source["quote"])
    if not set(period) <= written:
        raise RevisionBlocked("financial_period_unverified")
    return basis


def _lookup_record(
    replay: Mapping[str, Any],
    outcome: str,
    pinned_rcept_no: str | None,
    family_basis: str | None,
    times: Mapping[str, Any] | None,
) -> dict[str, Any]:
    history, search, as_of = replay["history"], replay["search"], replay["as_of"]
    classification = replay["classification"]
    filings = []
    for row in sorted(replay["filings"], key=lambda value: (value["rcept_dt"], value["rcept_no"])):
        entry = classification.get(row["rcept_no"])
        lineage = entry.get("lineage") if isinstance(entry, Mapping) else None
        filed_on = receipt_date(row["rcept_dt"])
        filings.append(
            {
                "rcept_no": row["rcept_no"],
                "rcept_dt": filed_on.isoformat(),
                # Preserved verbatim for audit; never parsed for period or lineage.
                "report_nm": row["report_nm"],
                "timely": filed_on <= as_of,
                "lineage": lineage if lineage in {"family", "unrelated"} else None,
            }
        )

    def instant(key: str) -> str | None:
        if times is None:
            return None
        return str(times[key].isoformat(timespec="seconds").replace("+00:00", "Z"))

    return {
        "outcome": outcome,
        "cutoff": {"date": history["cutoff"]["date"], "granularity": CUTOFF_GRANULARITY},
        "collection_sha256": None if times is None else times["collection_sha256"],
        "retrieved_from": instant("first"),
        "retrieved_until": instant("last"),
        "search_request": dict(replay["request"]),
        "page_sha256": [page["sha256"] for page in search["pages"]],
        "pinned_rcept_no": pinned_rcept_no,
        "family_basis": family_basis,
        "filings": filings,
    }
