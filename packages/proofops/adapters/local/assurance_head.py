"""Fail-closed consumers of P4-bearing tag heads: full re-derivation, then an identity pin.

Every consumer of a head carrying an ``assurance-link-v1`` receipt (claim API and
lists, exports, summaries, comparisons, reconciliation, rescoring, safe-harbor
analysis, review resolve) must see a proof that was fully re-derived from the
original source, not only matching database hashes. Full re-derivation opens SQLite
writer transactions (``BEGIN IMMEDIATE``) and reparses artifacts, so it cannot run
inside a consumer's own transaction. The pattern is therefore:

1. ``assurance_proofs(jobs, tenant, run, claim_ids)`` runs **before** the consumer
   opens its transaction. ``AssuranceProofVerifier.prove`` (attached to the job store
   by composition) re-digests the original PDF, replays the review inputs, re-extracts
   the pinned statement from its graph refs and recomputes the receipt byte-for-byte.
   The result is a per-claim proof bound to the exact head (tag revision + tag hash),
   the statement record hash and the source digest.
2. Inside the transaction, ``check_assurance_tag`` re-checks the receipt's internal
   integrity and pins the head and statement record against that proof (optimistic
   immutable revision). A changed head, statement, source, a failed derivation, no
   verifier or no proof context raises ``AssuranceHeadRejected``.

Heads without an ``assurance_review`` and without a P4 element naming
``assurance-link-v1`` are untouched (no new legacy requirement) and need no verifier.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from typing import Protocol

from proofops.application.tagging.assurance_link import (
    FACT,
    POLICY,
    POLICY_HASH,
    AssuranceLinkRejected,
    replay_assurance_receipt,
)
from proofops.domain.provenance import canonical_hash

_STATEMENT_KIND = "assurance_statement"
_STATEMENT_KEY = "STATEMENT"
_RECEIPT_MARKER = b'"assurance_review":'
_PROOFS: ContextVar[dict | None] = ContextVar("assurance_head_proofs", default=None)


class AssuranceHeadRejected(ValueError):
    """A P4-bearing head failed verification; consumers must not use it."""


class HeadReceiptVerifier(Protocol):
    """Consumer-side contract for any head receipt that needs source re-derivation.

    ``AssuranceProofVerifier`` implements it for ``assurance_review``. The planned
    search-coverage producer (dispatch ``ctx_4ecb9d2e1ee3``) is expected to add a
    verifier for its own receipt key with the same shape: ``prove`` runs outside any
    transaction and returns ``{claim_id: proof | error_code}`` bound to the exact tag
    revision/hash; consumers then re-pin inside their read transaction. Not
    implemented here: no coverage producer exists yet.
    """

    receipt_key: str

    def prove(self, tenant_id: str, run_id: str, claim_ids: Iterable[str] | None) -> dict: ...


def _claims_link(tag: dict) -> bool:
    return any(
        e.get("element_id") == "P4" and e.get("reason_code") == POLICY
        for e in tag.get("elements") or ()
    )


def _statement_record(db, jobs, tenant_id, run_id):
    raw = jobs._raw(db, tenant_id, run_id, _STATEMENT_KIND, _STATEMENT_KEY)
    return raw, (sha256(raw).hexdigest() if raw is not None else None)


def check_integrity(db, jobs, tenant_id, run_id, tag) -> bool:
    """Receipt-internal and same-connection pins. Returns whether a receipt exists."""
    receipt = tag.get("assurance_review") if isinstance(tag, dict) else None
    if receipt is None:
        if isinstance(tag, dict) and _claims_link(tag):
            raise AssuranceHeadRejected("ASSURANCE_PROOF_MISSING")
        return False
    try:
        stored = {k: v for k, v in receipt.items() if k not in ("receipt_sha256", "carried_from")}
        request = receipt["request"]
        pinned_input = tag.get("input_snapshot_sha256") or (
            canonical_hash(tag["inputs"]) if "inputs" in tag else None
        )
        refs = canonical_hash(receipt["evidence_refs"])
        p4 = [e for e in tag.get("elements") or () if e.get("element_id") == "P4"]
        facts = [
            f for f in (tag.get("confirmed_tags") or {}).get("facts") or () if f.get("name") == FACT
        ]
        raw, _ = _statement_record(db, jobs, tenant_id, run_id)
        statement = json.loads(raw) if raw is not None else None
        pinned = receipt["statement"]
        ok = (
            receipt.get("receipt_sha256") == canonical_hash(stored)
            and (receipt.get("policy"), receipt.get("policy_hash")) == (POLICY, POLICY_HASH)
            and (request.get("policy"), request.get("policy_hash")) == (POLICY, POLICY_HASH)
            and receipt.get("request_sha256") == canonical_hash(request)
            and receipt.get("status") == "covered"
            and not receipt.get("reasons")
            and request.get("input_snapshot_sha256") == pinned_input
            and len(p4) == 1
            and p4[0]["state"] == "present"
            and p4[0]["normalized_value"] == "covered"
            and p4[0]["reason_code"] == POLICY
            and canonical_hash(p4[0]["evidence_refs"]) == refs
            and len(facts) == 1
            and facts[0]["state"] == "present"
            and facts[0]["normalized_value"] == "covered"
            and facts[0]["source_scope"] == "global_bound"
            and canonical_hash(facts[0]["evidence_refs"]) == refs
            and statement is not None
            and statement.get("tenant_id") == tenant_id
            and statement.get("statement_id") == request.get("statement_id")
            and statement.get("semantic_hash") == request.get("statement_semantic_hash")
            and statement.get("graph_sha256") == pinned["graph_sha256"]
            and statement.get("source_sha256") == pinned["source_sha256"]
        )
    except (KeyError, TypeError, AttributeError, ValueError):
        ok = False
    if not ok:
        raise AssuranceHeadRejected("ASSURANCE_HEAD_REJECTED")
    return True


class AssuranceProofVerifier:
    """Full, transaction-free re-derivation of receipt-bearing heads for one run.

    ``load_inputs`` is ``LocalTagStore.load_inputs``; ``load_statement`` is
    ``LocalAssuranceStore.load`` (re-extracts the statement from its graph refs);
    ``source_digest(tenant, document_version_id)`` returns the SHA-256 of the actual
    original bytes (``sha256(UploadService.read_original(...))``).
    """

    receipt_key = "assurance_review"

    def __init__(
        self,
        jobs,
        *,
        load_inputs: Callable,
        load_statement: Callable,
        source_digest: Callable[[str, str], str],
    ):
        self.jobs = jobs
        self.load_inputs, self.load_statement = load_inputs, load_statement
        self.source_digest = source_digest

    def _receipt_heads(self, tenant_id, run_id, claim_ids):
        wanted = None if claim_ids is None else set(claim_ids)
        heads = {}
        with self.jobs._transaction() as db:
            rows = db.execute(
                """SELECT record_id, value FROM job_records
                WHERE tenant_id=? AND run_id=? AND kind='claim_head'""",
                (tenant_id, run_id),
            ).fetchall()
            for claim_id, raw in rows:
                if wanted is not None and claim_id not in wanted:
                    continue
                key = f"{claim_id}:{json.loads(raw)['tag_revision']:010}"
                hit = db.execute(
                    """SELECT value FROM job_records WHERE tenant_id=? AND run_id=?
                    AND kind='tag_revision' AND record_id=? AND instr(value, ?) > 0""",
                    (tenant_id, run_id, key, _RECEIPT_MARKER),
                ).fetchone()
                if hit is not None:
                    tag = json.loads(hit[0])
                    if check_integrity(db, self.jobs, tenant_id, run_id, tag):
                        heads[claim_id] = tag
            _, statement_sha = _statement_record(db, self.jobs, tenant_id, run_id)
        return heads, statement_sha

    def _prove_one(self, tenant_id, run_id, claim_id, tag, statement_sha) -> dict:
        receipt = tag["assurance_review"]
        inputs = self.load_inputs(tenant_id, run_id, claim_id)
        original = inputs.original
        digest = self.source_digest(tenant_id, original.document_version_id)
        if digest != original.source_sha256 or digest != receipt["statement"]["source_sha256"]:
            raise AssuranceHeadRejected("ASSURANCE_SOURCE_CHANGED")
        statement = self.load_statement(tenant_id, run_id)
        replay_assurance_receipt(inputs, receipt, statement)
        return dict(
            tag_revision=tag["tag_revision"],
            tag_sha256=canonical_hash(tag),
            statement_record_sha256=statement_sha,
            source_sha256=digest,
            receipt_sha256=receipt["receipt_sha256"],
        )

    def prove(self, tenant_id, run_id, claim_ids: Iterable[str] | None = None) -> dict:
        """Return ``{claim_id: proof | error_code}`` for receipt-bearing heads only."""
        heads, statement_sha = self._receipt_heads(tenant_id, run_id, claim_ids)
        proofs: dict = {}
        for claim_id, tag in heads.items():
            try:
                proofs[claim_id] = self._prove_one(tenant_id, run_id, claim_id, tag, statement_sha)
            except AssuranceHeadRejected as exc:
                proofs[claim_id] = str(exc)
            except AssuranceLinkRejected as exc:
                proofs[claim_id] = f"ASSURANCE_REDERIVATION_FAILED:{exc}"
            except (ValueError, KeyError, TypeError) as exc:
                proofs[claim_id] = f"ASSURANCE_REDERIVATION_FAILED:{type(exc).__name__}"
        return proofs


@contextmanager
def assurance_proofs(
    jobs, tenant_id, run_id, claim_ids: Iterable[str] | None = None
) -> Iterator[None]:
    """Compute proofs before a consumer transaction; must not be entered inside one."""
    current = dict(_PROOFS.get() or {})
    key = (str(getattr(jobs, "path", id(jobs))), tenant_id, run_id)
    wanted = None if claim_ids is None else frozenset(claim_ids)
    prior = current.get(key)
    if prior is not None and (
        prior["claims"] is None or (wanted is not None and wanted <= prior["claims"])
    ):
        yield
        return
    verifier = getattr(jobs, "assurance_verifier", None)
    proofs = verifier.prove(tenant_id, run_id, wanted) if verifier is not None else None
    current[key] = dict(claims=wanted, proofs=proofs)
    token = _PROOFS.set(current)
    try:
        yield
    finally:
        _PROOFS.reset(token)


def check_assurance_tag(db, jobs, tenant_id, run_id, tag) -> None:
    """Consumer check inside the read transaction; fails closed for receipt heads."""
    if not check_integrity(db, jobs, tenant_id, run_id, tag):
        return
    context = (_PROOFS.get() or {}).get((str(getattr(jobs, "path", id(jobs))), tenant_id, run_id))
    if context is None:
        raise AssuranceHeadRejected("ASSURANCE_PROOF_UNAVAILABLE")
    if context["proofs"] is None:
        raise AssuranceHeadRejected("ASSURANCE_LOADER_UNAVAILABLE")
    claim_id = (tag.get("confirmed_tags") or {}).get("claim_id")
    proof = context["proofs"].get(claim_id)
    if proof is None:
        raise AssuranceHeadRejected("ASSURANCE_PROOF_STALE")
    if isinstance(proof, str):
        raise AssuranceHeadRejected(proof)
    _, statement_sha = _statement_record(db, jobs, tenant_id, run_id)
    if (
        proof["tag_revision"] != tag["tag_revision"]
        or proof["tag_sha256"] != canonical_hash(tag)
        or proof["statement_record_sha256"] != statement_sha
    ):
        raise AssuranceHeadRejected("ASSURANCE_PROOF_STALE")


def read_head(jobs, tenant_id, run_id, claim_id):
    """Current head through the claim head pointer with receipt integrity checked.

    Used by the trusted CLI before a full re-derivation (``verify_head_proof``).
    """
    with jobs._transaction() as db:
        head = jobs._get(db, tenant_id, run_id, "claim_head", claim_id)
        tag = jobs._get(
            db, tenant_id, run_id, "tag_revision", f'{claim_id}:{head["tag_revision"]:010}'
        )
        check_integrity(db, jobs, tenant_id, run_id, tag)
    return head, tag


def verify_head_proof(
    jobs, tenant_id, run_id, claim_id, *, load_inputs, load_statement, source_digest=None
) -> dict:
    """Reload a published head and fully re-derive its P4 proof outside any transaction."""
    head, tag = read_head(jobs, tenant_id, run_id, claim_id)
    receipt = tag.get("assurance_review")
    if receipt is None:
        return dict(status="no_assurance_receipt", tag_revision=tag["tag_revision"])
    inputs = load_inputs(tenant_id, run_id, claim_id)
    if source_digest is not None:
        digest = source_digest(tenant_id, inputs.original.document_version_id)
        if (
            digest != inputs.original.source_sha256
            or digest != receipt["statement"]["source_sha256"]
        ):
            raise AssuranceHeadRejected("ASSURANCE_SOURCE_CHANGED")
    fact, replayed = replay_assurance_receipt(inputs, receipt, load_statement(tenant_id, run_id))
    return dict(
        status="verified",
        tag_revision=tag["tag_revision"],
        decision_revision=head["decision_revision"],
        receipt_sha256=replayed["receipt_sha256"],
        statement_id=replayed["statement"]["statement_id"],
        statement_semantic_hash=replayed["statement"]["semantic_hash"],
        fact=dict(name=fact.name, state=fact.state, normalized_value=fact.normalized_value),
    )
