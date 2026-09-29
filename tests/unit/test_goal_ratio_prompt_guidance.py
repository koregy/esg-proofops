"""RQ-01/RQ-02 guidance in the NEW-run element tagging prompt (prose only, no new flag)."""

from evaluation.local_upstage_pilot import live_tagging_settings


def test_element_prompt_carries_ratio_baseline_and_progress_guidance():
    prompt = live_tagging_settings(12)["tagging_settings"]["system_prompt"]
    # RQ-01: a ratio goal needs the literal baseline period and baseline ratio value.
    assert "literal baseline period and the literal baseline ratio value" in prompt
    assert "target ratio or reduction percentage alone is not a baseline" in prompt
    # RQ-02: only directly stated progress is present; computed progress is derived.
    assert "directly states current progress" in prompt
    assert "derived, never present" in prompt and "do not compute it" in prompt
    # The same prose reaches the compact wire profile.
    compact = live_tagging_settings(12, compact_element_wire=True)["tagging_settings"]
    assert "derived, never present" in compact["system_prompt"]
