"""R10 source-bound evidence Recall@K; missing/unreadable cases stay in the denominator.

These are comparisons with an explicitly supplied review set, not automatic
source verification or proof that the review set exhausts an entire document.
AI-delegated labels remain AI agreement, never independent human accuracy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from proofops.domain.provenance import canonical_hash
from proofops.domain.values import _require_sha256


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    claim_id: str
    company_id: str
    document_version_id: str
    source_sha256: str
    required_source_ids: tuple[str, ...]
    readable: bool

    def __post_init__(self) -> None:
        for field in ("claim_id", "company_id", "document_version_id"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError(f"{field} is required")
        _require_sha256("source_sha256", self.source_sha256)
        if not isinstance(self.required_source_ids, tuple | list):
            raise ValueError("required evidence IDs must be an array")
        ids = tuple(self.required_source_ids)
        if (
            not ids
            or len(ids) > 1000
            or any(not isinstance(i, str) or not i.strip() for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("nonempty unique required evidence IDs are required")
        if type(self.readable) is not bool:
            raise ValueError("readable must be an explicit boolean")
        object.__setattr__(self, "required_source_ids", tuple(sorted(ids)))


def score_retrieval(
    cases: tuple[RetrievalCase, ...],
    rankings: dict[str, dict],
    *,
    reviewer_kind: str,
    reviewer_id: str,
    ks: tuple[int, ...] = (10, 20),
) -> dict:
    """Score exact IDs within the same version/hash; no fuzzy or cross-document hit.

    Each ranking supplies document_version_id, source_sha256, source_ids in rank
    order. A missing ranking is a miss. An unreadable case is an end-to-end miss
    even if a caller supplied matching IDs; a separate readable-only metric is
    explicitly conditional. Empty reference sets are not scored as perfect hits.
    """
    if (
        not cases
        or len(cases) > 10_000
        or any(not isinstance(c, RetrievalCase) for c in cases)
        or len({c.claim_id for c in cases}) != len(cases)
    ):
        raise ValueError("bounded unique retrieval cases required")
    if (
        reviewer_kind not in ("human", "ai_delegated")
        or not isinstance(reviewer_id, str)
        or not reviewer_id.strip()
    ):
        raise ValueError("explicit reviewer identity required")
    if (
        not ks
        or any(type(k) is not int or not 1 <= k <= 1000 for k in ks)
        or len(set(ks)) != len(ks)
    ):
        raise ValueError("unique bounded positive K values required")
    by_id = {c.claim_id: c for c in cases}
    if not isinstance(rankings, dict) or set(rankings) - set(by_id):
        raise ValueError("rankings must belong to the fixed review set")
    for claim_id, ranking in rankings.items():
        case = by_id[claim_id]
        if not isinstance(ranking, dict) or set(ranking) != {
            "document_version_id",
            "source_sha256",
            "source_ids",
        }:
            raise ValueError("version-pinned ranking required")
        if (
            ranking["document_version_id"] != case.document_version_id
            or ranking["source_sha256"] != case.source_sha256
        ):
            raise ValueError("ranking document identity mismatch")
        ids = ranking["source_ids"]
        if (
            not isinstance(ids, list)
            or len(ids) > 1000
            or any(not isinstance(i, str) or not i.strip() for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("ranked evidence IDs must be unique and bounded")

    def metrics(selected: tuple[RetrievalCase, ...], k: int) -> dict:
        complete = partial = required = hits = 0
        for case in selected:
            expected = set(case.required_source_ids)
            found = set(rankings.get(case.claim_id, {}).get("source_ids", [])[:k])
            hit = len(expected & found) if case.readable else 0
            required += len(expected)
            hits += hit
            complete += hit == len(expected)
            partial += 0 < hit < len(expected)
        return dict(
            claims=len(selected),
            all_required_hit=complete,
            partial_required_hit=partial,
            no_required_hit=len(selected) - complete - partial,
            missing_rankings=sum(c.claim_id not in rankings for c in selected),
            unreadable_claims=sum(not c.readable for c in selected),
            evidence_hits=hits,
            evidence_required=required,
            complete_recall=complete / len(selected) if selected else None,
            evidence_recall=hits / required if required else None,
        )

    def group(selected: tuple[RetrievalCase, ...]) -> dict:
        readable = tuple(c for c in selected if c.readable)
        return {
            str(k): {"end_to_end": metrics(selected, k), "readable_only": metrics(readable, k)}
            for k in sorted(ks)
        }

    fixed = [asdict(c) for c in sorted(cases, key=lambda c: c.claim_id)]
    return dict(
        schema="source-bound-retrieval-evaluation-v1",
        reference_sha256=canonical_hash(fixed),
        prediction_sha256=canonical_hash(rankings),
        reviewer_kind=reviewer_kind,
        reviewer_id=reviewer_id,
        metric_scope="supplied_source_bound_cases_only",
        independent_accuracy_claim=False,
        overall=group(cases),
        by_company={
            company: group(tuple(c for c in cases if c.company_id == company))
            for company in sorted({c.company_id for c in cases})
        },
    )
