"""Select the live tagging probe from the run's frozen snapshot, never from ambient config.

A run created with the explicit Solar Pro 3 tagging option carries ``solar-pro3``
in every pinned role; every earlier run carries ``solar-pro4``. Resuming a run
therefore always reuses the model it was created with. All roles must name one
model (one pinned input-reservation policy covers them), and there is no
fallback: an unknown or mixed model stops before any probe is built.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

LIVE_TAGGING_MODELS = frozenset({"solar-pro3", "solar-pro4"})
_ROLE_KEYS = ("preliminary_settings", "tagging_settings", "relation_settings")


def snapshot_tagging_model(snapshot: Mapping[str, Any]) -> str:
    models = set()
    for key in _ROLE_KEYS:
        if key in snapshot:
            pinned = snapshot[key]
            models.add(pinned.get("model_id") if isinstance(pinned, Mapping) else None)
    if len(models) != 1:
        raise ValueError("LIVE_TAGGING_MODEL_MISMATCH")
    model = models.pop()
    if model not in LIVE_TAGGING_MODELS:
        raise ValueError("LIVE_TAGGING_MODEL_UNSUPPORTED")
    return model


def select_tagging_probe(
    snapshot: Mapping[str, Any],
    probes: MutableMapping[str, Any],
    make_probe: Callable[[str], Any],
) -> Any:
    """Return the probe for the snapshot's model, building it once per process."""
    model = snapshot_tagging_model(snapshot)
    if model not in probes:
        probes[model] = make_probe(model)
    probe = probes[model]
    if getattr(probe, "model", None) != model:
        raise ValueError("LIVE_TAGGING_MODEL_MISMATCH")
    return probe
