"""Local producer/store for full-document search-coverage receipts.

Everything a receipt asserts is recomputed here from the run's own pinned
inputs: the frozen run snapshot, the original PDF bytes (page facts are read
from them with pdfplumber, never supplied by a caller), the native-replayed run
graph and the replayed claim. Receipts and delegated reviews are content-
addressed canonical JSON files created with O_EXCL, never rewritten. Every read
recomputes the receipt from current state and compares canonical bytes, so a
tampered or re-hashed receipt, a changed original PDF, a stale run input, a new
claim revision or a receipt copied across tenants/runs is refused.

No OCR, model, network or paid call. Nothing here sets an element state; see
``application.evidence.search_coverage`` for the absence rule.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from proofops.application.evidence import search_coverage as coverage
from proofops.domain.rulepacks import canonical_json

# (tenant_id, run_id, claim_id) -> dict(identity, graph, claim, source, selected_pages,
# registered_page_count). The default reads a real local run; tests may inject.
RunLoader = Callable[[str, str, str], dict]


def _uuid(value: str, code: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise ValueError(code) from None
    return value


def pdf_page_facts(source: bytes) -> list[dict]:
    """Per physical page of the ORIGINAL bytes: every text-layer word (index, box,
    exact NFC text, in text-layer order) and the image count."""
    import pdfplumber

    facts = []
    with pdfplumber.open(io.BytesIO(source)) as document:
        for number, page in enumerate(document.pages, start=1):
            words = page.extract_words()
            facts.append(coverage.page_facts_from_words(number, words, len(page.images)))
    return facts


def run_loader(store, uploads, parser) -> RunLoader:
    """Read-only loader over a real local run (frozen snapshot, original, graph, claim)."""

    def load(tenant_id: str, run_id: str, claim_id: str) -> dict:
        from proofops.adapters.local.claim_store import LocalClaimStore
        from proofops.adapters.local.run_artifacts import load_run_graph, load_run_inputs

        snapshot, source, _ = load_run_inputs(store, uploads, tenant_id=tenant_id, run_id=run_id)
        graph = load_run_graph(store, uploads, parser, tenant_id=tenant_id, run_id=run_id)
        _, discovery, _ = LocalClaimStore(store, uploads, parser).load_evidence(tenant_id, run_id)
        claims = [claim for claim in discovery.claims if claim.claim_id == claim_id]
        if len(claims) != 1:
            raise ValueError("SEARCH_CLAIM_NOT_FOUND")
        return dict(
            identity=dict(
                tenant_id=tenant_id,
                run_id=run_id,
                document_version_id=source.document_version_id,
                object_version_id=source.object_version_id,
                input_hash=snapshot["input_hash"],
                source_sha256=source.sha256,
                parse_manifest_id=graph.parse_manifest_id,
                rulepack_sha256=snapshot["rulepack"]["sha256"],
            ),
            graph=graph,
            claim=claims[0],
            source=source.content,
            selected_pages=list(snapshot["selected_pages"]),
            registered_page_count=snapshot["document"]["page_count"],
        )

    return load


class LocalSearchCoverageStore:
    def __init__(self, root: Path, loader: RunLoader):
        self.root = Path(root)
        self._load = loader

    # --- producer ---------------------------------------------------------

    def _compute(self, tenant_id: str, run_id: str, claim_id: str, element: str, queries):
        _uuid(tenant_id, "SEARCH_TENANT_INVALID")
        _uuid(run_id, "SEARCH_RUN_INVALID")
        state = self._load(tenant_id, run_id, claim_id)
        source = state["source"]
        if (
            not isinstance(source, bytes)
            or sha256(source).hexdigest() != (state["identity"]["source_sha256"])
        ):
            raise ValueError("SEARCH_SOURCE_MISMATCH")
        if state["identity"]["tenant_id"] != tenant_id or state["identity"]["run_id"] != run_id:
            raise ValueError("SEARCH_COVERAGE_IDENTITY_MISMATCH")
        facts = pdf_page_facts(source)
        if len(facts) != state["registered_page_count"]:
            raise ValueError("REGISTERED_PAGE_COUNT_MISMATCH")
        receipt = coverage.build_receipt(
            identity=state["identity"],
            graph=state["graph"],
            claim=state["claim"],
            element=element,
            queries=queries,
            page_facts=facts,
            registered_page_count=len(facts),
            selected_pages=state["selected_pages"],
        )
        return receipt, state

    def produce(self, tenant_id, run_id, claim_id, element, queries, *, write=True) -> dict:
        receipt, _ = self._compute(tenant_id, run_id, claim_id, element, queries)
        if write:
            self._write(self._dir(tenant_id, run_id, "receipts"), receipt)
        return receipt

    # --- replay / consumer API ------------------------------------------------

    def replay(self, tenant_id: str, run_id: str, receipt_sha256: str) -> tuple[dict, dict]:
        """Recompute a stored receipt from current run state; refuse any difference."""
        stored = self._read(self._dir(tenant_id, run_id, "receipts"), receipt_sha256)
        coverage.verify_receipt_hash(stored)
        if (stored["tenant_id"], stored["run_id"]) != (tenant_id, run_id):
            raise ValueError("SEARCH_COVERAGE_RECEIPT_MISMATCH")
        receipt, state = self._compute(
            tenant_id, run_id, stored["claim_id"], stored["element"], stored["queries"]
        )
        if canonical_json(receipt) != canonical_json(stored):
            raise ValueError("SEARCH_COVERAGE_RECEIPT_MISMATCH")
        return receipt, state

    def review_request(self, tenant_id: str, run_id: str, receipt_sha256: str) -> dict:
        receipt, state = self.replay(tenant_id, run_id, receipt_sha256)
        return coverage.review_request(receipt, state["graph"])

    def record_review(self, tenant_id: str, run_id: str, review: dict) -> dict:
        receipt, _ = self.replay(tenant_id, run_id, review.get("receipt_sha256", ""))
        validated = coverage.validate_review(receipt, review)
        body = dict(validated, artifact_sha256=coverage.canonical_hash(validated))
        self._write(self._dir(tenant_id, run_id, "reviews"), body)
        return body

    def absence_prerequisite(
        self,
        tenant_id: str,
        run_id: str,
        claim_id: str,
        element: str,
        receipt_sha256: str,
        review_sha256: str | None = None,
    ) -> dict:
        """The consumer contract: ``element_state_candidate`` is ``absent`` only for
        a replay-valid complete receipt bound to this claim/element plus a stored,
        re-validated ``absent_confirmed`` whole-corpus review; otherwise unknown."""
        receipt, _ = self.replay(tenant_id, run_id, receipt_sha256)
        if (receipt["claim_id"], receipt["element"]) != (claim_id, element):
            raise ValueError("SEARCH_COVERAGE_RECEIPT_MISMATCH")
        review = None
        if review_sha256 is not None:
            stored = self._read(self._dir(tenant_id, run_id, "reviews"), review_sha256)
            review = {key: value for key, value in stored.items() if key != "artifact_sha256"}
            if coverage.canonical_hash(review) != stored.get("artifact_sha256"):
                raise ValueError("SEARCH_REVIEW_INVALID")
        state = coverage.absence_candidate(receipt, review)
        return dict(
            receipt_sha256=receipt["artifact_sha256"],
            review_sha256=review_sha256,
            search_prerequisites_complete=receipt["search_prerequisites_complete"],
            incomplete_pages=receipt["incomplete_pages"],
            element_state_candidate=state,
        )

    # --- immutable files ----------------------------------------------------------

    def _dir(self, tenant_id: str, run_id: str, kind: str) -> Path:
        """``<root>/<sha256(tenant:run)[:32]>/<kind>``: short enough for Windows paths.

        The directory key only partitions files; tenant and run are bound inside
        every receipt and re-checked on each replay."""
        key = f"{_uuid(tenant_id, 'SEARCH_TENANT_INVALID')}:{_uuid(run_id, 'SEARCH_RUN_INVALID')}"
        return self.root / sha256(key.encode()).hexdigest()[:32] / kind

    @staticmethod
    def _write(directory: Path, body: dict) -> None:
        data = canonical_json(body).encode()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{body['artifact_sha256']}.json"
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444), "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("SEARCH_COVERAGE_STORE_CONFLICT") from None

    @staticmethod
    def _read(directory: Path, artifact_sha256: str) -> dict:
        if (
            not isinstance(artifact_sha256, str)
            or len(artifact_sha256) != 64
            or any(c not in "0123456789abcdef" for c in artifact_sha256)
        ):
            raise ValueError("SEARCH_COVERAGE_RECEIPT_NOT_FOUND")
        path = directory / f"{artifact_sha256}.json"
        try:
            body = json.loads(path.read_bytes())
        except (OSError, ValueError):
            raise ValueError("SEARCH_COVERAGE_RECEIPT_NOT_FOUND") from None
        if not isinstance(body, dict) or body.get("artifact_sha256") != artifact_sha256:
            raise ValueError("SEARCH_COVERAGE_RECEIPT_MISMATCH")
        return body
