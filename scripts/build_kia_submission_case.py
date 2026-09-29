"""Prepare the real Kia FY2024 / SR 2025 C1-C4 linkage inputs offline (submission 2026-09-29).

Inputs are only already-collected public files, each pinned by SHA-256 in
``fixtures/submission-kia/pinned-inputs.json``:

* the official Kia Sustainability Report 2025 (Korean) PDF (fresh identity 9ce8...;
  Developer A's older d0d8... copy is not reused),
* Developer B's DART FY2024 bundle (rcept_no 20250313001390, derived texts),
* the 2026-09-29 DART subsidiaries-detail annex (24 consolidated subsidiaries),
* Developer A's submission snapshot ``kia-2025.json`` (read-only reference),
* the expert ``reconciliation.csv`` (annotator expectations, no adjudicator: not gold).

Outputs are *inputs and candidates*, never confirmed product state:

1. DART sources re-verified with B's ``validate_source_bytes`` (hash + ``chars:`` quote);
   real (``synthetic=False``) FinancialContexts for C1 (entity_set of 24 subsidiaries +
   parent), C2 (period) and C3 (PPE cash-flow amount, no approved account mapping).
2. SR review-input candidates: every A claim/element quote checked for byte presence on
   its SR page (pdfplumber literal or a unique whitespace re-anchor; else B's pypdf
   ``page:N:whitespace-v1`` rule). Multiple matches are refused. A byte-present quote is
   a ``present_candidate`` only: binding, geometry and acceptance stay with the run's
   parser/source verifier and ReviewService. ``absent`` without verified search coverage
   becomes ``unknown``.
3. A gate projection per claim x C1-C4 from the linkage contract constants. No
   ConfirmedTags are constructed and ``build_packet`` is not called, because no
   published run head exists for this SR.
4. The expert rows' inputs re-verified and set beside the projected gate.

No model, network, AWS or database write. The output directory must not exist yet.

Usage::

    PYTHONUTF8=1 .venv/Scripts/python.exe scripts/build_kia_submission_case.py \
        --sr-pdf .local/submission-20260929/kia-real/sources/kia-sr-2025-kr.pdf \
        --a-snapshot .local/submission-20260929/kia-real/sources/a-submission-kia-2025.json \
        --dart-financial-dir "<ROOT>/esg-proofops/developer-b-kia-financial-20260922" \
        --dart-annex-dir "<ROOT>/output/developer-b-kia-annex-20260929" \
        --reconciliation-csv "<ROOT>/esg-proofops/reconciliation.csv" \
        --out .local/submission-20260929/kia-real/run
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/submission-kia/pinned-inputs.json"

CASE = "proofops-submission-20260929-kia-real"
MANAGEMENT_FACTS = {
    "M1": "named_means_or_concrete_state",
    "M2": "org_boundary",
    "M3": "external_verification",
    "M4": "concrete_implementation_detail",
    "M5": "responsible_organization",
    "M6": "compensation_link",
}
REVIEW_ORIGIN = "ai_delegated"
REVIEWER = "coordinator(AI-delegated):developer-b-worker task_4f28f50fc72e"
REVIEW_KIND = "ai_delegated_source_prepared_candidates_not_confirmed_not_gold"


def uid(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join((CASE, *parts))))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def canonical_sha(value) -> str:
    return sha256_bytes(canonical(value).encode("utf-8"))


class InputRejected(ValueError):
    """An input file does not match its pinned identity; nothing is built."""


# --------------------------------------------------------------------------- #
# SR page text and quote re-anchoring
# --------------------------------------------------------------------------- #


def sr_page_texts(pdf_bytes: bytes) -> list[str]:
    """pdfplumber page text: the reader ``adapters/local/linkage_reader`` verifies with."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


class PypdfPages:
    """Lazy pypdf page text: the reader B's ``validate_source_bytes`` uses for ``page:N``."""

    def __init__(self, pdf_bytes: bytes):
        from pypdf import PdfReader

        self.reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
        self.cache: dict[int, str] = {}

    def __getitem__(self, index: int) -> str:
        if index not in self.cache:
            self.cache[index] = self.reader.pages[index].extract_text() or ""
        return self.cache[index]


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def anchor_quote(pages: list[str], pypdf_pages, page: int, quote: str) -> dict:
    """pdfplumber literal/whitespace anchor first, else B's pypdf ``whitespace-v1`` rule.

    The second reader is exactly B's reconcile PDF check: the canonical (single-space)
    quote must occur exactly once in the whitespace-collapsed pypdf page text.
    """
    first = reanchor(pages[page - 1], quote)
    if first["verified"]:
        return first | dict(reader="pdfplumber", page_text_sha256=_text_sha(pages[page - 1]))
    if first["reason"] in {"literal_ambiguous", "whitespace_match_ambiguous"}:
        return first
    text = pypdf_pages[page - 1]
    normalized = " ".join(text.split())
    canonical_quote = " ".join(quote.split())
    start = normalized.find(canonical_quote) if canonical_quote else -1
    if start >= 0 and normalized.find(canonical_quote, start + 1) < 0:
        return dict(
            verified=True,
            method="pypdf_whitespace_v1",
            quote=canonical_quote,
            char_start=start,
            char_end=start + len(canonical_quote),
            occurrences="single",
            reader="pypdf",
            page_text_sha256=_text_sha(text),
            pdfplumber_reason=first["reason"],
        )
    if start >= 0:
        reason = "pypdf_ambiguous"
    elif re.sub(r"\s+", "", quote) in re.sub(r"\s+", "", text):
        # e.g. a reader line break inside "경제·환경"; B's rule keeps such text unverified.
        reason = "pypdf_only_matches_with_whitespace_removed"
    else:
        reason = first["reason"]
    return dict(verified=False, reason=reason, pdfplumber_reason=first["reason"])


def reanchor(page_text: str, quote: str) -> dict:
    """Exact literal span of ``quote`` on one page, or a refusal reason.

    Only two outcomes verify: the quote is already a literal substring, or exactly one
    span of the page equals it after collapsing whitespace. Ambiguity, truncation or a
    missing quote stays unverified; nothing is fuzzy-matched.
    """
    quote = quote.strip()
    if not quote:
        return dict(verified=False, reason="empty_quote")
    start = page_text.find(quote)
    if start >= 0:
        if page_text.find(quote, start + 1) >= 0:
            return dict(verified=False, reason="literal_ambiguous")
        return dict(
            verified=True,
            method="literal",
            quote=quote,
            char_start=start,
            char_end=start + len(quote),
            occurrences="single",
        )
    tokens = quote.split()
    pattern = r"\s+".join(re.escape(token) for token in tokens)
    matches = list(re.finditer(pattern, page_text))
    if len(matches) == 1:
        match = matches[0]
        return dict(
            verified=True,
            method="whitespace_reanchored",
            quote=match.group(0),
            char_start=match.start(),
            char_end=match.end(),
            occurrences="single",
        )
    if len(matches) > 1:
        return dict(verified=False, reason="whitespace_match_ambiguous")
    squeezed = re.sub(r"\s+", "", page_text)
    if re.sub(r"\s+", "", quote) in squeezed:
        return dict(verified=False, reason="only_matches_with_whitespace_removed")
    return dict(verified=False, reason="quote_not_on_page")


# --------------------------------------------------------------------------- #
# DART inputs
# --------------------------------------------------------------------------- #


def verify_text_source(path: Path, source: dict) -> dict:
    from proofops.application.reconciliation.sources import validate_source_bytes

    payload = path.read_bytes()
    actual = sha256_bytes(payload)
    if actual != source["artifact_sha256"]:
        return dict(source_id=source["source_id"], verified=False, reason="source_hash_mismatch")
    try:
        validate_source_bytes(payload, source, format_name="text")
    except ValueError as exc:
        return dict(source_id=source["source_id"], verified=False, reason=str(exc))
    return dict(source_id=source["source_id"], verified=True, path=str(path), sha256=actual)


ENTITY_CODE = re.compile(r"\(([A-Za-z][A-Za-z&]*(?: [A-Za-z&]+)*)\)")


def financial_entities(annex_quote: str, rows: list[list[str]]) -> list[dict]:
    """One ID per subsidiary row: ``KIA-SUB:<code>`` from the row's literal ``(CODE)``.

    The code is read from the row name cell and must also occur literally in the
    verified annex quote. The parent is the literal ``기아 주식회사`` of the verified
    footnote. A row without exactly one code is refused rather than guessed.
    """
    entities = []
    for row in rows:
        name = row[0]
        codes = ENTITY_CODE.findall(name)
        if len(codes) != 1:
            raise InputRejected(f"subsidiary row without exactly one literal code: {name!r}")
        label = f"({codes[0]})"
        if label not in annex_quote:
            raise InputRejected(f"subsidiary code {label} not in the verified annex quote")
        entities.append(
            dict(entity_id=f"KIA-SUB:{codes[0]}", source_label=label, name=name.strip())
        )
    parent = "기아 주식회사"
    if parent not in annex_quote:
        raise InputRejected("parent label not in the verified annex quote")
    entities.append(dict(entity_id="KIA-PARENT", source_label=parent, name=parent))
    ids = [e["entity_id"] for e in entities]
    if len(set(ids)) != len(ids):
        raise InputRejected("duplicate entity ids")
    return entities


def summary_count(summary_quote: str) -> int | None:
    """Year-end consolidated entity count on the verified 합계 row, when literal."""
    cells = [line.split("\t", 1)[-1].strip() for line in summary_quote.splitlines()]
    # 합계 row cells: label, 기초, 증가, 감소, 기말, 주요종속회사수 ("-" = none).
    if len(cells) != 6 or cells[0] != "합계" or not re.fullmatch(r"[0-9]+", cells[4]):
        return None
    return int(cells[4])


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def _load_inputs(args) -> dict:
    pins = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sr_pdf = Path(args.sr_pdf).read_bytes()
    a_bytes = Path(args.a_snapshot).read_bytes()
    fin_dir, annex_dir = Path(args.dart_financial_dir), Path(args.dart_annex_dir)
    csv_bytes = Path(args.reconciliation_csv).read_bytes()
    observed = {
        "sr_pdf": sha256_bytes(sr_pdf),
        "a_snapshot": sha256_bytes(a_bytes),
        "dart_facts": sha256_bytes((fin_dir / "facts.json").read_bytes()),
        "dart_annex_source_reference": sha256_bytes(
            (annex_dir / "source-reference.json").read_bytes()
        ),
        "dart_annex_rows": sha256_bytes((annex_dir / "financial-rows.json").read_bytes()),
        "dart_annex_text": sha256_bytes((annex_dir / "subsidiaries-detail.txt").read_bytes()),
        "dart_filings_receipt": sha256_bytes((fin_dir / "filings-receipt.json").read_bytes()),
        "reconciliation_csv": sha256_bytes(csv_bytes),
    }
    mismatched = sorted(k for k, v in observed.items() if pins["sha256"][k] != v)
    if mismatched:
        raise InputRejected(f"input hash mismatch against pinned fixture: {mismatched}")
    return dict(
        pins=pins,
        observed=observed,
        sr_pdf=sr_pdf,
        a_snapshot=json.loads(a_bytes.decode("utf-8")),
        facts=json.loads((fin_dir / "facts.json").read_text(encoding="utf-8")),
        derived=json.loads((fin_dir / "derived-artifacts.json").read_text(encoding="utf-8")),
        filings=json.loads((fin_dir / "filings-receipt.json").read_text(encoding="utf-8")),
        annex_ref=json.loads((annex_dir / "source-reference.json").read_text(encoding="utf-8")),
        annex_rows=json.loads((annex_dir / "financial-rows.json").read_text(encoding="utf-8")),
        reconciliation=list(
            csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")), strict=True)
        ),
        fin_dir=fin_dir,
        annex_dir=annex_dir,
    )


def _dart_sources(inputs: dict) -> tuple[dict, list]:
    """Verified DART sources keyed by a short role id, plus per-source receipts."""
    fin_dir = inputs["fin_dir"]
    paths = {k: fin_dir / v["path"] for k, v in inputs["derived"].items()}
    receipts, sources = [], {}
    for fact in inputs["facts"]["facts"]:
        src = {k: fact["source"][k] for k in ("source_id", "artifact_sha256", "locator", "quote")}
        src["document_id"] = fact["source"]["document_id"]
        path = paths[src["document_id"]]
        receipt = verify_text_source(path, src) | dict(
            item=fact["item"], role=fact["role"], fact=fact["fact"], local_path=str(path)
        )
        receipts.append(receipt)
        if receipt["verified"]:
            sources[fact["role"] + ":" + src["source_id"]] = dict(src, local_path=str(path))
    annex = dict(inputs["annex_ref"])
    annex_src = dict(
        source_id="kia-fs-2024-subsidiaries-detail",
        document_id=annex["document_id"],
        artifact_sha256=annex["artifact_sha256"],
        locator=annex["locator"],
        quote=annex["quote"],
    )
    path = inputs["annex_dir"] / "subsidiaries-detail.txt"
    receipt = verify_text_source(path, annex_src) | dict(
        item="C1", role="consolidation_scope_detail", local_path=str(path)
    )
    receipts.append(receipt)
    if receipt["verified"]:
        sources["consolidation_scope_detail"] = dict(annex_src, local_path=str(path))
    return sources, receipts


def _by_role(sources: dict, role: str, contains: str | None = None) -> dict | None:
    for key, value in sources.items():
        if key.split(":", 1)[0] == role and (contains is None or contains in value["quote"]):
            return value
    return None


def _financial_contexts(inputs: dict, sources: dict, entities: list[dict]) -> dict:
    from proofops.application.linkage_exchange import (
        C3Context,
        FinancialContext,
        FinancialFact,
        FinancialSource,
    )

    pins = inputs["pins"]["financial_identity"]
    period_src = _by_role(sources, "financial_period")
    if period_src is None:
        raise InputRejected("DART period source did not verify")
    period_literal = re.findall(r"([0-9]{4})년 ([0-9]{2})월 ([0-9]{2})일", period_src["quote"])
    if len(period_literal) != 2:
        raise InputRejected("DART period quote does not carry exactly two dates")
    start, end = ("-".join(parts) for parts in period_literal)
    filing = next(c for c in inputs["filings"]["candidates"] if c["rcept_no"] == pins["rcept_no"])
    published = f"{filing['rcept_dt'][:4]}-{filing['rcept_dt'][4:6]}-{filing['rcept_dt'][6:]}"

    def fsrc(src):
        return FinancialSource(
            **{k: src[k] for k in ("source_id", "document_id", "artifact_sha256", "locator")},
            quote=src["quote"],
        )

    base = dict(
        synthetic=False,
        company_id=pins["company_id"],
        package_id=pins["package_id"],
        dart_corp_code=inputs["facts"]["corp_code"],
        financial_document_version=inputs["facts"]["document_version_id"],
        financial_fiscal_year=int(inputs["facts"]["fiscal_year"]),
        consolidation=inputs["facts"]["consolidation"],
        financial_period_start=start,
        financial_period_end=end,
        financial_published_at=published,
        rcept_no=inputs["facts"]["rcept_no"],
        as_of_date=pins["as_of_date"],
    )
    contexts, local_paths = {}, {}
    annex = sources.get("consolidation_scope_detail")
    if annex is not None:
        normalized = json.dumps(
            sorted(e["entity_id"] for e in entities), ensure_ascii=False, separators=(",", ":")
        )
        contexts["C1"] = FinancialContext(
            **base,
            financial=FinancialFact(
                raw=annex["quote"],
                normalized=normalized,
                kind="entity_set",
                unit=None,
                source_id=annex["source_id"],
            ),
            financial_sources=(fsrc(annex),),
        )
        local_paths["C1"] = {annex["source_id"]: annex["local_path"]}
    contexts["C2"] = FinancialContext(
        **base,
        financial=FinancialFact(
            raw=period_src["quote"],
            normalized=f"{start}/{end}",
            kind="period",
            unit=None,
            source_id=period_src["source_id"],
        ),
        financial_sources=(fsrc(period_src),),
    )
    local_paths["C2"] = {period_src["source_id"]: period_src["local_path"]}
    capex = _by_role(sources, "capex_cashflow")
    if capex is not None:
        amount = re.search(r'"thstrm_amount":"(-?[0-9]+)"', capex["quote"])
        contexts["C3"] = FinancialContext(
            **base,
            financial=FinancialFact(
                raw=capex["quote"],
                normalized=amount.group(1) if amount else None,
                kind="currency_amount",
                unit="KRW",
                source_id=capex["source_id"],
            ),
            financial_sources=(fsrc(capex),),
            # REC-003 B requires an approved operator CAPEX account mapping; none is
            # approved, so no account id is asserted.
            c3_context=C3Context(
                currency="KRW",
                target_period_start=None,
                target_period_end=None,
                capex_period_start=start,
                capex_period_end=end,
                capex_account_ids=(),
                commitment_source_id=None,
                funding_plan_source_id=None,
            ),
        )
        local_paths["C3"] = {capex["source_id"]: capex["local_path"]}
    # C4: DART body has no eco-friendly-vehicle classification disclosure (B bundle
    # README); no financial fact or c4_context is asserted.
    return dict(contexts=contexts, local_paths=local_paths, published_at=published)


def _review_candidates(inputs: dict, pages: list[str], pypdf_pages) -> tuple[list, list]:
    """Source-prepared review inputs: A's elements with a byte-presence check only.

    Nothing here is a confirmed fact. A quote that is present in the SR bytes stays a
    ``present_candidate``; binding to a parsed source block, geometry and acceptance
    remain the job of the run's parser, source verifier and ReviewService.
    """
    records, element_log = [], []
    for claim in inputs["a_snapshot"]["claims"]:
        page = int(claim["page"])
        claim_check = anchor_quote(pages, pypdf_pages, page, claim["quote"])
        is_mgmt = claim["track"] == "management"
        elements = []
        for element in claim["elements"]:
            name = MANAGEMENT_FACTS[element["id"]] if is_mgmt else element["id"]
            checks = []
            for ev in element["evidence"]:
                check = anchor_quote(pages, pypdf_pages, int(ev["page"]), ev["quote"])
                element_log.append(
                    dict(
                        claim=claim["id"],
                        element=element["id"],
                        fact=name,
                        page=int(ev["page"]),
                        a_quote=ev["quote"],
                        **check,
                    )
                )
                checks.append(dict(page=int(ev["page"]), a_quote=ev["quote"], **check))
            present = [c for c in checks if c["verified"]]
            if element["state"] == "present":
                candidate = "present_candidate" if present else "unknown"
                reason = None if present else "evidence_quote_bytes_not_present"
            elif element["state"] == "absent":
                candidate, reason = "unknown", "absent_without_verified_search_coverage"
            else:
                candidate, reason = element["state"], None
            elements.append(
                dict(
                    element_id=element["id"],
                    fact=name,
                    a_state=element["state"],
                    candidate_state=candidate,
                    reason=reason,
                    quote_checks=checks,
                )
            )
        records.append(
            dict(
                a_claim_id=claim["id"],
                track=claim["track"],
                page=page,
                statement=claim["statement"],
                claim_quote_check=claim_check,
                elements=elements,
                binding="not_accepted",
                geometry="not_verified",
                acceptance="requires ReviewService on a published run",
            )
        )
    return records, element_log


def _gate_projection(record: dict, entities: list[dict]) -> dict:
    """First blocking gate per C item, read from the current linkage contract constants.

    A projection over candidates, not a builder result: no ConfirmedTags are made and
    ``build_packet`` is not called. Every row is also blocked earlier by the run state
    (no published claims or accepted tag head for this SR).
    """
    from proofops.application.linkage_exchange import ITEM_TRIGGERS, TRIGGER_TAG_MAP

    candidates = {
        e["fact"]: e for e in record["elements"] if e["candidate_state"] == "present_candidate"
    }
    triggers = {
        TRIGGER_TAG_MAP[name]: element
        for name, element in candidates.items()
        if name in TRIGGER_TAG_MAP
    }
    out = {}
    for item, allowed in ITEM_TRIGGERS.items():
        found = {t: triggers[t] for t in allowed if t in triggers}
        if not record["claim_quote_check"]["verified"]:
            gate = "claim_quote_bytes_not_present"
        elif not found:
            gate = f"no_{item.lower()}_trigger_candidate"
        elif item == "C1":
            quotes = [
                c["quote"]
                for element in found.values()
                for c in element["quote_checks"]
                if c["verified"]
            ]
            labels = sorted(
                {e["source_label"] for e in entities for q in quotes if e["source_label"] in q}
            )
            gate = (
                "c1_entity_review_possible:" + ",".join(labels)
                if labels
                else "c1_boundary_quote_names_no_financial_entity"
            )
        elif item == "C2":
            gate = "c2_only_report_level_period_available"
        else:
            gate = f"{item.lower()}_trigger_candidate_present"
        out[item] = dict(
            first_gate=gate,
            trigger_candidates=sorted(found),
            run_gate="run_not_published:no_claims_no_accepted_tag_head",
            execution_state="blocked",
        )
    return out


def _evaluate_reconciliation_rows(rows, projections, pages, pypdf_pages, sources):
    """Expert rows: re-verify their inputs, then set the current projected gate beside them."""
    results = []
    for row in rows:
        sr_check = anchor_quote(pages, pypdf_pages, int(row["sr_page"]), row["sr_quote"])
        fin_hit = next(
            (s for s in sources.values() if s["locator"] == row["financial_locator"]), None
        )
        fin_quote = row["financial_quote"]
        got = projections.get(row["claim_id"], {}).get(row["rule_id"])
        results.append(
            dict(
                case_id=row["case_id"],
                claim_id=row["claim_id"],
                item=row["rule_id"],
                scenario="assumed_policy" if row["case_id"].endswith("-ASM") else "current",
                sr_quote_bytes_present=sr_check["verified"],
                sr_quote_reason=sr_check.get("reason"),
                financial_locator_is_verified_source=fin_hit is not None,
                financial_quote_equals_verified_quote=(
                    fin_hit is not None and fin_hit["quote"] == fin_quote
                ),
                expected=dict(
                    execution_state=row["expected_execution_state"],
                    status=row["expected_status"] or None,
                    reason=row["reason_code"] or None,
                ),
                projected=got,
                execution_state_agrees=(got or {}).get("execution_state")
                == row["expected_execution_state"],
            )
        )
    return results


def build(args) -> dict:
    out = Path(args.out)
    if out.exists():
        raise InputRejected(f"output directory already exists, refusing to overwrite: {out}")
    inputs = _load_inputs(args)
    sr_sha = inputs["observed"]["sr_pdf"]
    pages = sr_page_texts(inputs["sr_pdf"])
    if len(pages) != inputs["pins"]["sr_page_count"]:
        raise InputRejected("SR page count differs from the pinned count")
    pypdf_pages = PypdfPages(inputs["sr_pdf"])

    sources, dart_receipts = _dart_sources(inputs)
    annex = sources.get("consolidation_scope_detail")
    entities = financial_entities(annex["quote"], inputs["annex_rows"]["rows"]) if annex else []
    total = _by_role(sources, "consolidation_scope_count", "합계")
    year_end = summary_count(total["quote"]) if total else None
    contexts = _financial_contexts(inputs, sources, entities)

    from proofops.application.linkage_exchange import period_from_literal

    period_check = reanchor(pages[1], inputs["pins"]["sr_period_quote"])
    if not period_check["verified"]:
        raise InputRejected("SR reporting period literal not on page 2")
    period = period_from_literal(period_check["quote"])

    records, element_log = _review_candidates(inputs, pages, pypdf_pages)
    projections = {r["a_claim_id"]: _gate_projection(r, entities) for r in records}
    evaluation = _evaluate_reconciliation_rows(
        inputs["reconciliation"], projections, pages, pypdf_pages, sources
    )
    gate_counts: dict[str, int] = {}
    for items in projections.values():
        for value in items.values():
            key = value["first_gate"].split(":", 1)[0]
            gate_counts[key] = gate_counts.get(key, 0) + 1
    present = [e for e in element_log if e["verified"]]
    refused = [e for e in element_log if not e["verified"]]
    summary = dict(
        case=CASE,
        sr_sha256=sr_sha,
        sr_pages=len(pages),
        sr_reporting_period=period,
        prior_developer_a_sr_sha256=inputs["pins"]["sr_source"]["prior_developer_a_sha256"],
        company_id=inputs["pins"]["financial_identity"]["company_id"],
        dart=dict(
            corp_code=inputs["facts"]["corp_code"],
            rcept_no=inputs["facts"]["rcept_no"],
            financial_published_at=contexts["published_at"],
            sources_verified=sum(1 for r in dart_receipts if r["verified"]),
            sources_total=len(dart_receipts),
            consolidated_subsidiaries_listed=len(inputs["annex_rows"]["rows"]),
            summary_year_end_count=year_end,
            count_consistent=year_end == len(inputs["annex_rows"]["rows"]),
            financial_entity_ids=len(entities),
            financial_contexts=sorted(contexts["contexts"]),
        ),
        review_candidates=dict(
            claims=len(records),
            claim_quote_bytes_present=sum(1 for r in records if r["claim_quote_check"]["verified"]),
            evidence_quotes=len(element_log),
            evidence_bytes_present=len(present),
            by_method={
                m: sum(1 for e in present if e["method"] == m)
                for m in sorted({e["method"] for e in present})
            },
            evidence_refused=len(refused),
            refused_reasons={
                r: sum(1 for e in refused if e["reason"] == r)
                for r in sorted({e["reason"] for e in refused})
            },
            present_candidates=sum(
                1
                for r in records
                for e in r["elements"]
                if e["candidate_state"] == "present_candidate"
            ),
        ),
        gate_projection=dict(
            claim_items=sum(len(v) for v in projections.values()),
            packets_built=0,
            by_first_gate=dict(sorted(gate_counts.items())),
        ),
        reconciliation_rows=dict(
            total=len(evaluation),
            sr_quote_bytes_present=sum(1 for e in evaluation if e["sr_quote_bytes_present"]),
            financial_quote_verified=sum(
                1 for e in evaluation if e["financial_quote_equals_verified_quote"]
            ),
            execution_state_agrees=sum(1 for e in evaluation if e["execution_state_agrees"]),
        ),
        review=dict(origin=REVIEW_ORIGIN, reviewer=REVIEWER, kind=REVIEW_KIND),
        not_claimed=[
            "no confirmed tag, accepted binding, native geometry or accepted revision",
            "no linkage packet: build_packet is not called without a published run head",
            "no model call, AWS call, human approval, legal/accounting approval or gold label",
            "absent is never asserted without verified full-document search coverage",
        ],
    )

    out.mkdir(parents=True)
    files = {
        "summary.json": summary,
        "inputs.json": dict(
            pinned=inputs["pins"],
            observed_sha256=inputs["observed"],
            args={k: str(v) for k, v in vars(args).items()},
        ),
        "dart-source-verification.json": dart_receipts,
        "financial-entities.json": entities,
        "sr-period.json": dict(check=period_check, normalized=period),
        "review-candidates.json": dict(
            review_origin=REVIEW_ORIGIN,
            prepared_by=REVIEWER,
            kind=REVIEW_KIND,
            sr_sha256=sr_sha,
            records=records,
        ),
        "sr-evidence-quote-checks.json": element_log,
        "gate-projection.json": projections,
        "reconciliation-evaluation.json": evaluation,
    }
    for item, context in contexts["contexts"].items():
        files[f"financial-context-{item}.json"] = dict(
            asdict(context), local_paths=contexts["local_paths"][item]
        )
    manifest = {}
    for name, value in files.items():
        raw = json.dumps(value, ensure_ascii=False, indent=2, default=list).encode("utf-8")
        (out / name).write_bytes(raw)
        manifest[name] = sha256_bytes(raw)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sr-pdf", required=True)
    parser.add_argument("--a-snapshot", required=True)
    parser.add_argument("--dart-financial-dir", required=True)
    parser.add_argument("--dart-annex-dir", required=True)
    parser.add_argument("--reconciliation-csv", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        summary = build(args)
    except InputRejected as exc:
        print(json.dumps(dict(execution_state="blocked", reason=str(exc)), ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
