"""Native Upstage OCR policy, eligibility and comparison (no provider, no network)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from proofops.adapters.local import native_paragraph_typography
from proofops.adapters.local import native_upstage_ocr as nuo
from proofops.application.runs import validate_upstage_ocr_policy
from proofops.domain.provenance import canonical_hash

UNSUPPORTED = dict(
    status="unresolved", reason="rendered_reader_unavailable", error="UnsupportedPlatform"
)


def _native(**record):
    base = dict(
        source_id="s1",
        status="unresolved",
        reason="rendered_text_unresolved",
        words=[{"index": 0, "text": "Scope", "bbox": [0, 0, 1, 1]}],
        rendered=dict(UNSUPPORTED),
    )
    base.update(record)
    return dict(schema="native_paragraph_attestation_v2", records=[base])


def test_policy_is_pinned_valid_and_versioned_apart_from_raster():
    policy = nuo.native_upstage_ocr_policy(max_pages=10, max_calls=20)
    assert validate_upstage_ocr_policy(policy) == policy
    assert policy["schema"] == "native_upstage_ocr_policy_v1"
    assert policy["comparison"] == "normalized_quote_fold_v1"
    assert nuo.live_policy_for(policy) == policy
    from proofops.adapters.local.raster_visibility import raster_ocr_policy

    assert canonical_hash(policy) != canonical_hash(raster_ocr_policy())
    for bad in (dict(mode="fast"), dict(max_pages=11), dict(max_calls=21), dict(max_calls=0)):
        with pytest.raises(ValueError):
            nuo.native_upstage_ocr_policy(**bad)
    for field, value in (("comparison", "fuzzy"), ("eligibility", "any"), ("schema", "v2")):
        with pytest.raises(ValueError):
            validate_upstage_ocr_policy({**policy, field: value})


def test_quote_fold_is_exactly_the_existing_finite_map():
    assert nuo.QUOTE_FOLD == native_paragraph_typography._QUOTE_FOLD
    assert len(nuo.QUOTE_FOLD) == 4


@pytest.mark.parametrize(
    ("native", "provider", "expected"),
    [
        ("‘2019년’ 목표", "'2019년' 목표", True),
        ("“A” and ‘b’", '"A" and \'b\'', True),
        ("Scope 1  emissions\n12,345", "Scope 1 emissions 12,345", True),
        ("12,345 tCO2e", "12.345 tCO2e", False),
        ("12,345 tCO2e", "12345 tCO2e", False),
        ("1 일부터", "1일부터", False),
        ("경제·환경", "경제 환경", False),
        ("5′ height", "5' height", False),
        ("25°C", "25 C", False),
        ("", "", False),
        ("text", "", False),
    ],
)
def test_comparison_is_exact_after_normalization_and_quote_fold(native, provider, expected):
    assert nuo.matches(native, provider) is expected


def test_eligibility_is_exact_unsupported_platform_after_all_gates():
    assert nuo.eligible_upstage_sources(_native()) == {"s1"}
    for change in (
        dict(rendered=dict(status="read", text="Scope")),  # Vision read disagreement
        dict(rendered=dict(status="unresolved", reason="render_limit")),
        dict(rendered={**UNSUPPORTED, "extra": 1}),
        dict(reason="text_mismatch"),  # native text mismatch: raw API text never enough
        dict(reason="clipped_or_rotated_words"),
        dict(reason="glyph_geometry_unresolved"),
        dict(reason="interactive_visibility_requires_review"),
        dict(status="verified", reason=None),
        dict(words=[]),
        dict(rendered_attempts=[UNSUPPORTED, UNSUPPORTED]),
    ):
        assert nuo.eligible_upstage_sources(_native(**change)) == set(), change
    with pytest.raises(ValueError, match="NATIVE_GLYPH_ATTESTATION_REQUIRED"):
        nuo.eligible_upstage_sources({**_native(), "schema": "native_paragraph_attestation_v1"})


def test_native_words_come_from_the_base_receipt_only():
    native = _native(
        words=[{"text": "Scope"}, {"text": "1"}, {"text": "12,345"}],
    )
    assert nuo.native_words(native) == {"s1": "Scope 1 12,345"}
    edited = copy.deepcopy(native)
    edited["records"][0]["words"][2]["text"] = "99,999"
    assert nuo.native_words(edited) != nuo.native_words(native)


TRIAL = Path(__file__).resolve().parents[2] / ".local/submission-20260929/api-raster-trial-v3"


@pytest.mark.skipif(not (TRIAL / "receipt.json").is_file(), reason="master trial not present")
def test_real_trial_v3_p28_matches_and_p2_stays_unresolved():
    """Paid trial by the master (private d0d814 source): comparison only, no call here."""
    plan = json.loads((TRIAL / "plan.json").read_text(encoding="utf-8"))
    receipt = json.loads((TRIAL / "receipt.json").read_text(encoding="utf-8"))
    assert plan["source_sha256"].startswith("d0d814")
    assert receipt["response_sha256"] == canonical_hash(receipt["raw_response"])
    pages: dict[int, list[str]] = {}
    for element in receipt["raw_response"]["elements"]:
        pages.setdefault(element["page"], []).append(element["content"]["text"])
    outcome = {
        row["physical_page"]: nuo.matches(row["expected"], " ".join(pages[index]))
        for index, row in enumerate(plan["rows"], 1)
    }
    # p28 differs only by curly vs straight quotes; p2 has spacing/middle-dot differences.
    assert outcome == {2: False, 28: True}
    # Without the pinned quote fold the same p28 reading (submitted page 2) would not match.
    p28 = plan["rows"][1]
    assert citations_normalized(p28["expected"]) != citations_normalized(" ".join(pages[2]))


def citations_normalized(text):
    from proofops.application.evidence.citations import _normalized

    return _normalized(text)


def test_api_settings_passthrough_is_complete_and_exclusive():
    from proofops_api.local_runtime import _upstage_ocr

    policy = nuo.native_upstage_ocr_policy()
    binding = "11111111-1111-4111-8111-111111111111"
    settings = dict(upstage_ocr_runtime_binding_id=binding, upstage_ocr_policy=policy)
    assert _upstage_ocr({}, "upstage_probe") == {}
    assert _upstage_ocr(settings, "upstage_probe") == settings
    for bad, mode in (
        ({"upstage_ocr_policy": policy}, "upstage_probe"),
        (settings, "local_synthetic"),
        ({**settings, "raster_policy": {}, "raster_runtime_binding_id": binding}, "upstage_probe"),
        ({**settings, "upstage_ocr_policy": {**policy, "comparison": "fuzzy"}}, "upstage_probe"),
    ):
        with pytest.raises(ValueError):
            _upstage_ocr(bad, mode)
