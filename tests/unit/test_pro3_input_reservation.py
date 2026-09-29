from datetime import UTC, datetime

import pytest
from proofops.application.input_reservation import (
    solar_pro4_capacity_policy,
    validate_capacity_policy,
)
from proofops.application.input_reservation_pro3 import solar_pro3_capacity_policy

NOW = datetime(2026, 9, 29, 9, tzinfo=UTC)


def test_explicit_pro3_policy_and_legacy_pro4_remain_distinct():
    assert (
        validate_capacity_policy(
            solar_pro3_capacity_policy(), model_id="solar-pro3", checked_at=NOW
        )
        == 2**18
    )
    assert (
        validate_capacity_policy(
            solar_pro4_capacity_policy(refreshed=True), model_id="solar-pro4", checked_at=NOW
        )
        == 2**20
    )
    for policy, model in (
        (solar_pro3_capacity_policy(), "solar-pro4"),
        (solar_pro4_capacity_policy(refreshed=True), "solar-pro3"),
    ):
        with pytest.raises(ValueError):
            validate_capacity_policy(policy, model_id=model, checked_at=NOW)


@pytest.mark.parametrize(
    "key,value",
    [
        ("reservation_input_tokens", True),
        ("reservation_input_tokens", 262144.0),
        ("reservation_input_tokens", 131072),
        ("model_id", "solar-pro4"),
        ("expires_at", "2099-01-01T00:00:00Z"),
        ("source_quote", "unlimited"),
        ("extra", "field"),
    ],
)
def test_policy_mutation_rejected(key, value):
    policy = solar_pro3_capacity_policy()
    policy[key] = value
    with pytest.raises(ValueError):
        validate_capacity_policy(policy, model_id="solar-pro3", checked_at=NOW)


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 9, 29, 8, 54, tzinfo=UTC),
        datetime(2026, 10, 2, tzinfo=UTC),
        datetime(2026, 9, 29, 9),
    ],
)
def test_expiry_and_timezone_enforced(instant):
    with pytest.raises(ValueError):
        validate_capacity_policy(
            solar_pro3_capacity_policy(), model_id="solar-pro3", checked_at=instant
        )
