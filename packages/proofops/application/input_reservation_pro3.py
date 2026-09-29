"""Explicit Solar Pro 3 input reservation; no implicit fallback from Pro 4.

The official model page was read on 2026-09-29. This bound is deliberately
larger than both decimal and binary interpretations of its 128K context;
it is neither a tokenizer count nor the actual context length.
"""

from datetime import UTC, datetime

from proofops.domain.errors import DomainValidationError

_CAPTURED = datetime(2026, 9, 29, 8, 55, tzinfo=UTC)
_EXPIRES = datetime(2026, 10, 2, tzinfo=UTC)
_INPUT_CEILING = 2**18


def solar_pro3_capacity_policy() -> dict:
    return {
        "schema": "upstage-pro3-context-capacity-reservation-v1",
        "model_id": "solar-pro3",
        "reservation_kind": "conservative-input-ceiling",
        "reservation_input_tokens": _INPUT_CEILING,
        "source_url": "https://console.upstage.ai/docs/models/solar-pro-3",
        "source_context_label": "128K",
        "source_quote": "128K context length",
        "captured_at": "2026-09-29T08:55:00Z",
        "expires_at": "2026-10-02T00:00:00Z",
        "note": (
            "Conservative reservation above the published context bound, not actual model "
            "capacity or tokenizer parity. Provider usage settles separately; student "
            "entitlement does not replace usage accounting."
        ),
    }


def validate_pro3_capacity_policy(policy: dict, *, model_id: str, checked_at: datetime) -> int:
    if (
        model_id != "solar-pro3"
        or not isinstance(policy, dict)
        or policy != solar_pro3_capacity_policy()
        or type(policy.get("reservation_input_tokens")) is not int
    ):
        raise DomainValidationError("capacity policy does not match pinned Solar Pro 3 policy")
    if (
        not isinstance(checked_at, datetime)
        or checked_at.tzinfo is None
        or checked_at.utcoffset() is None
        or not _CAPTURED <= checked_at < _EXPIRES
    ):
        raise DomainValidationError("capacity policy is not valid at checked_at")
    return _INPUT_CEILING
