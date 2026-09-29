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

from proofops.application.tagging.absence_link import POLICY as ABSENCE_POLICY
from proofops.application.tagging.absence_link import POLICY_HASH as ABSENCE_POLICY_HASH
from proofops.application.tagging.absence_link import (
    AbsenceLinkRejected,
    replay_absence_receipt,
)
from proofops.application.tagging.assurance_link import (
    FACT,
    POLICY,
    POLICY_HASH,
    AssuranceLinkRejected,
    replay_assurance_receipt,
)
from proofops.application.tagging.numeric_link import FACT as NUMERIC_FACT
from proofops.application.tagging.numeric_link import POLICY as NUMERIC_POLICY
from proofops.application.tagging.numeric_link import POLICY_HASH as NUMERIC_POLICY_HASH
from proofops.application.tagging.numeric_link import (
    NumericLinkRejected,
    replay_numeric_receipt,
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


def _marked_heads(jobs, tenant_id, run_id, claim_ids, marker: bytes) -> dict:
    """Current heads whose tag bytes carry ``marker`` (cheap SQL prefilter, one read)."""
    wanted = None if claim_ids is None else set(claim_ids)
    heads = {}
    with jobs._transaction() as db:
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
                (tenant_id, run_id, key, marker),
            ).fetchone()
            if hit is not None:
                heads[claim_id] = json.loads(hit[0])
    return heads


def check_absence_integrity(tag) -> bool:
    """Receipt-internal pins of a ``search-absence-link-v1`` head. Returns receipt presence."""
    receipt = tag.get("absence_review") if isinstance(tag, dict) else None
    if receipt is None:
        if isinstance(tag, dict) and any(
            e.get("reason_code") == ABSENCE_POLICY for e in tag.get("elements") or ()
        ):
            raise AssuranceHeadRejected("ABSENCE_PROOF_MISSING")
        return False
    try:
        stored = {k: v for k, v in receipt.items() if k not in ("receipt_sha256", "carried_from")}
        request = receipt["request"]
        pinned_input = tag.get("input_snapshot_sha256") or (
            canonical_hash(tag["inputs"]) if "inputs" in tag else None
        )
        confirmed = tag.get("confirmed_tags") or {}
        facts = {f["name"]: f for f in confirmed.get("facts") or ()}
        elements = {e["element_id"]: e for e in tag.get("elements") or ()}
        ok = (
            receipt.get("receipt_sha256") == canonical_hash(stored)
            and (receipt.get("policy"), receipt.get("policy_hash"))
            == (ABSENCE_POLICY, ABSENCE_POLICY_HASH)
            and (request.get("policy"), request.get("policy_hash"))
            == (ABSENCE_POLICY, ABSENCE_POLICY_HASH)
            and receipt.get("request_sha256") == canonical_hash(request)
            and request.get("input_snapshot_sha256") == pinned_input
            and request.get("track") == confirmed.get("track")
            and request.get("claim_id") == confirmed.get("claim_id")
            and bool(receipt.get("items"))
        )
        for item in receipt.get("items") or ():
            element = elements.get(item["element_id"])
            ok = ok and (
                element is not None
                and element["state"] == "absent"
                and element["reason_code"] == ABSENCE_POLICY
                and not element["evidence_refs"]
                and item["element_state_candidate"] == "absent"
                and all(
                    name in facts
                    and facts[name]["state"] == "absent"
                    and facts[name]["search_coverage_verified"] is True
                    for name in item["absent_facts"]
                )
            )
    except (KeyError, TypeError, AttributeError, ValueError):
        ok = False
    if not ok:
        raise AssuranceHeadRejected("ABSENCE_HEAD_REJECTED")
    return True


class AbsenceProofVerifier:
    """Full, transaction-free replay of absence receipts on current heads.

    ``evidence`` is the trusted producer port (``LocalSearchCoverageStore``): every
    replay recomputes the receipt from the original PDF, graph, claim and run
    snapshot and re-validates the stored whole-corpus review.
    """

    receipt_key = "absence_review"

    def __init__(self, jobs, *, load_inputs: Callable, evidence):
        self.jobs, self.load_inputs, self.evidence = jobs, load_inputs, evidence

    def prove(self, tenant_id, run_id, claim_ids: Iterable[str] | None = None) -> dict:
        heads = _marked_heads(self.jobs, tenant_id, run_id, claim_ids, _ABSENCE_MARKER)
        proofs: dict = {}
        for claim_id, tag in heads.items():
            try:
                if not check_absence_integrity(tag):
                    continue
                inputs = self.load_inputs(tenant_id, run_id, claim_id)
                replay_absence_receipt(inputs, tag["absence_review"], self.evidence)
                proofs[claim_id] = dict(
                    tag_revision=tag["tag_revision"],
                    tag_sha256=canonical_hash(tag),
                    receipt_sha256=tag["absence_review"]["receipt_sha256"],
                )
            except AssuranceHeadRejected as exc:
                proofs[claim_id] = str(exc)
            except AbsenceLinkRejected as exc:
                proofs[claim_id] = f"ABSENCE_REDERIVATION_FAILED:{exc}"
            except (ValueError, KeyError, TypeError, OSError) as exc:
                proofs[claim_id] = f"ABSENCE_REDERIVATION_FAILED:{type(exc).__name__}"
        return proofs


def check_numeric_integrity(tag) -> bool:
    """Receipt-internal pins of a ``numeric-link-v1`` head. Returns receipt presence.

    The stored receipt, the P6 element and the ``numerical_check`` fact must agree;
    a claimed state or boolean is never trusted without the full source replay that
    ``NumericProofVerifier`` performs outside the consumer transaction.
    """
    receipt = tag.get("numeric_review") if isinstance(tag, dict) else None
    if receipt is None:
        if isinstance(tag, dict) and (
            any(e.get("reason_code") == NUMERIC_POLICY for e in tag.get("elements") or ())
            or any(
                f.get("name") == NUMERIC_FACT and f.get("state") in ("present", "conflict")
                for f in (tag.get("confirmed_tags") or {}).get("facts") or ()
            )
        ):
            raise AssuranceHeadRejected("NUMERIC_PROOF_MISSING")
        return False
    try:
        stored = {k: v for k, v in receipt.items() if k not in ("receipt_sha256", "carried_from")}
        request = receipt["request"]
        pinned_input = tag.get("input_snapshot_sha256") or (
            canonical_hash(tag["inputs"]) if "inputs" in tag else None
        )
        confirmed = tag.get("confirmed_tags") or {}
        facts = [f for f in confirmed.get("facts") or () if f.get("name") == NUMERIC_FACT]
        element = next((e for e in tag.get("elements") or () if e.get("element_id") == "P6"), None)
        state = receipt.get("fact_state")
        ok = (
            receipt.get("receipt_sha256") == canonical_hash(stored)
            and (receipt.get("policy"), receipt.get("policy_hash"))
            == (NUMERIC_POLICY, NUMERIC_POLICY_HASH)
            and (request.get("policy"), request.get("policy_hash"))
            == (NUMERIC_POLICY, NUMERIC_POLICY_HASH)
            and receipt.get("request_sha256") == canonical_hash(request)
            and request.get("input_snapshot_sha256") == pinned_input
            and receipt["identity"]["claim_id"] == confirmed.get("claim_id")
            and state in ("present", "conflict")
            and element is not None
            and element["state"] == state
            and element["reason_code"] == NUMERIC_POLICY
            and element["normalized_value"] == receipt["result"]["status"]
            and element["evidence_refs"] == receipt["evidence_refs"]
            and element.get("credited_from") is None
            and len(facts) == 1
            and facts[0]["state"] == state
            and facts[0]["source_scope"] == "computed_check"
            and facts[0]["normalized_value"] == receipt["result"]["status"]
            and facts[0]["evidence_refs"] == receipt["evidence_refs"]
        )
    except (KeyError, TypeError, AttributeError, ValueError):
        ok = False
    if not ok:
        raise AssuranceHeadRejected("NUMERIC_HEAD_REJECTED")
    return True


class NumericProofVerifier:
    """Full, transaction-free replay of ``numeric-link-v1`` receipts on current heads.

    Every replay recomputes the table observations and the pure numeric check from
    the review inputs' original graph and pinned loader snapshot.
    """

    receipt_key = "numeric_review"

    def __init__(self, jobs, *, load_inputs: Callable):
        self.jobs, self.load_inputs = jobs, load_inputs

    def prove(self, tenant_id, run_id, claim_ids: Iterable[str] | None = None) -> dict:
        heads = _marked_heads(self.jobs, tenant_id, run_id, claim_ids, _NUMERIC_MARKER)
        proofs: dict = {}
        for claim_id, tag in heads.items():
            try:
                if not check_numeric_integrity(tag):
                    continue
                inputs = self.load_inputs(tenant_id, run_id, claim_id)
                replay_numeric_receipt(inputs, tag["numeric_review"])
                proofs[claim_id] = dict(
                    tag_revision=tag["tag_revision"],
                    tag_sha256=canonical_hash(tag),
                    receipt_sha256=tag["numeric_review"]["receipt_sha256"],
                )
            except AssuranceHeadRejected as exc:
                proofs[claim_id] = str(exc)
            except NumericLinkRejected as exc:
                proofs[claim_id] = f"NUMERIC_REDERIVATION_FAILED:{exc}"
            except (ValueError, KeyError, TypeError, OSError) as exc:
                proofs[claim_id] = f"NUMERIC_REDERIVATION_FAILED:{type(exc).__name__}"
        return proofs


_ABSENCE_MARKER = b'"absence_review":'
_NUMERIC_MARKER = b'"numeric_review":'
_VERIFIER_ATTRIBUTES = {
    "assurance_review": ("assurance_verifier", "ASSURANCE_LOADER_UNAVAILABLE"),
    "absence_review": ("absence_verifier", "ABSENCE_EVIDENCE_UNAVAILABLE"),
    "numeric_review": ("numeric_verifier", "NUMERIC_VERIFIER_UNAVAILABLE"),
}


@contextmanager
def assurance_proofs(
    jobs, tenant_id, run_id, claim_ids: Iterable[str] | None = None
) -> Iterator[None]:
    """Compute head proofs before a consumer transaction; never enter inside one.

    Covers every receipt kind in ``_VERIFIER_ATTRIBUTES`` (P4 assurance, absence).
    """
    current = dict(_PROOFS.get() or {})
    key = (str(getattr(jobs, "path", id(jobs))), tenant_id, run_id)
    wanted = None if claim_ids is None else frozenset(claim_ids)
    prior = current.get(key)
    if prior is not None and (
        prior["claims"] is None or (wanted is not None and wanted <= prior["claims"])
    ):
        yield
        return
    proofs = {}
    for receipt_key, (attribute, _) in _VERIFIER_ATTRIBUTES.items():
        verifier = getattr(jobs, attribute, None)
        proofs[receipt_key] = (
            verifier.prove(tenant_id, run_id, wanted) if verifier is not None else None
        )
    current[key] = dict(claims=wanted, proofs=proofs)
    token = _PROOFS.set(current)
    try:
        yield
    finally:
        _PROOFS.reset(token)


def check_assurance_tag(db, jobs, tenant_id, run_id, tag) -> None:
    """Consumer check inside the read transaction; fails closed for receipt heads.

    Applies to every trusted head receipt: ``assurance_review`` (P4) and
    ``absence_review`` (search-absence-link-v1). Heads without either are untouched.
    """
    present = {
        "assurance_review": check_integrity(db, jobs, tenant_id, run_id, tag),
        "absence_review": check_absence_integrity(tag),
        "numeric_review": check_numeric_integrity(tag),
    }
    if not any(present.values()):
        return
    context = (_PROOFS.get() or {}).get((str(getattr(jobs, "path", id(jobs))), tenant_id, run_id))
    if context is None:
        raise AssuranceHeadRejected("ASSURANCE_PROOF_UNAVAILABLE")
    claim_id = (tag.get("confirmed_tags") or {}).get("claim_id")
    for receipt_key, needed in present.items():
        if not needed:
            continue
        proofs = context["proofs"].get(receipt_key)
        if proofs is None:
            raise AssuranceHeadRejected(_VERIFIER_ATTRIBUTES[receipt_key][1])
        proof = proofs.get(claim_id)
        if proof is None:
            raise AssuranceHeadRejected("ASSURANCE_PROOF_STALE")
        if isinstance(proof, str):
            raise AssuranceHeadRejected(proof)
        if proof["tag_revision"] != tag["tag_revision"] or proof["tag_sha256"] != canonical_hash(
            tag
        ):
            raise AssuranceHeadRejected("ASSURANCE_PROOF_STALE")
        if receipt_key == "assurance_review":
            _, statement_sha = _statement_record(db, jobs, tenant_id, run_id)
            if proof["statement_record_sha256"] != statement_sha:
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
