"""Context persistence/carry/CAS seam; source validation has independent real-graph tests."""

from copy import deepcopy
from uuid import uuid4

import pytest
from proofops.application.reviews import ReviewRejected

from tests.acceptance.test_citations import RUN, TENANT
from tests.acceptance.test_reviews import workspace
from tests.integration.test_ai_delegated_review import _actor


def test_context_is_immutable_carried_and_part_of_retry_identity(tmp_path, monkeypatch):
    _, service, inputs, review, body, _, _ = workspace(tmp_path)
    request = {"reviewed": "context"}
    receipt = {"request": request, "source_receipt": {"hash": "source"}, "projection": {}}
    calls = []

    def validate(actual, supplied, callback):
        assert actual is inputs and supplied == request
        calls.append(True)
        return deepcopy(receipt)

    monkeypatch.setattr(
        "proofops.application.claim_context_review.review_facility_context", validate
    )
    before = service.store.history(TENANT, RUN, review["claim_id"])
    key = str(uuid4())
    kwargs = dict(delegated_reviewer="context-test", delegation_authority="explicit test")
    result = service.resolve_ai_delegated_review(
        _actor(), review["review_id"], body, '"1"', key, context_review=request, **kwargs
    )
    after = service.store.history(TENANT, RUN, review["claim_id"])
    assert after["tags"][:-1] == before["tags"]
    assert after["tags"][-1]["claim_context_review"] == receipt
    assert (
        next(e for e in after["tags"][-1]["elements"] if e["element_id"] == "P6")["state"]
        == "unknown"
    )
    assert (
        service.resolve_ai_delegated_review(
            _actor(), review["review_id"], body, '"1"', key, context_review=request, **kwargs
        )
        == result
    )
    with pytest.raises(ReviewRejected):
        service.resolve_ai_delegated_review(
            _actor(),
            review["review_id"],
            body,
            '"1"',
            key,
            context_review={"reviewed": "changed"},
            **kwargs,
        )
    fresh = service.store.get(TENANT, review["review_id"])
    body = dict(body, base_tag_revision=result["new_tag_revision"])
    service.resolve_ai_delegated_review(
        _actor(),
        review["review_id"],
        body,
        f'"{fresh["revision"]}"',
        str(uuid4()),
        reopen=True,
        **kwargs,
    )
    carried = service.store.history(TENANT, RUN, review["claim_id"])["tags"][-1]
    assert carried["claim_context_review"]["request"] == request
    assert carried["claim_context_review"]["carried_from"]
    assert len(calls) == 2
