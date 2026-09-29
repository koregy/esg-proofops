"""Bounded selected-page handler with local PDF bytes and fake Upstage transports.

Adapted from submission-A tests/test_live_report_handler.py: provider is Upstage
(Document Parse + solar-pro3), and native text matches are never source verification.
"""

import importlib.util
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

MODULE = Path(__file__).resolve().parents[2] / "api/live-report.py"
spec = importlib.util.spec_from_file_location("live_report", MODULE)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

LINE = "Kia will cut Scope 1 emissions 50% by 2030"


def _pdf(lines: list[str]) -> bytes:
    """Minimal one-page Helvetica PDF with a real native text layer."""
    ops = ["BT", "/F1 12 Tf"]
    for i, text in enumerate(lines):
        ops.append(f"1 0 0 1 72 {720 - 20 * i} Tm ({text}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()


PDF = _pdf([LINE])
# Box around the first text line (top ~60pt, bottom ~84pt on a 792pt page).
BOX = [
    {"x": 0.05, "y": 0.06},
    {"x": 0.95, "y": 0.06},
    {"x": 0.95, "y": 0.12},
    {"x": 0.05, "y": 0.12},
]
PARSED = {
    "model": "document-parse-260128",
    "elements": [
        {"page": 1, "category": "paragraph", "coordinates": BOX, "content": {"text": LINE}},
        {"page": 1, "category": "figure", "coordinates": BOX, "content": {"text": "ignored"}},
    ],
}


def _completion(message: dict, output_tokens: int = 50) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(message)}}],
        "usage": {"prompt_tokens": 800, "completion_tokens": output_tokens},
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "secret")
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)


def _fail(*_):
    raise AssertionError("provider must not be called")


def test_fixture_pdf_is_valid_with_native_text():
    assert len(PdfReader(io.BytesIO(PDF)).pages) == 1
    assert report.MODEL == "solar-pro3"
    assert "openrouter" not in MODULE.read_text(encoding="utf-8").lower()


def test_native_text_match_is_not_glyph_attestation():
    assert report.native_text_match("2040년", "2040년까지 전 사업장", "2040년 까지 전 사업장")
    assert not report.native_text_match("2040년", "2040년까지 전 사업장", "204O년까지 전 사업장")
    # Non-unique block span is refused.
    assert not report.native_text_match("전", "전 사업장 전", "전 사업장 전")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"access_code": "wrong"}, "ACCESS_DENIED"),
        ({"pages": list(range(1, 12))}, "INVALID_PAGES"),
        ({"pages": [1, 1]}, "INVALID_PAGES"),
        ({"pages": [2, 3]}, "INVALID_PDF"),  # page count must match uploaded pages
        ({"pdf": b"not a pdf"}, "INVALID_PDF"),
        ({"pdf": b"%PDF" + b"0" * report.MAX_BODY}, "INVALID_PDF"),
    ],
)
def test_guards_fail_before_any_provider_call(kwargs, code):
    args = {"pdf": PDF, "pages": [26], "access_code": "secret"} | kwargs
    with pytest.raises(report.LiveError) as error:
        report.run_report(args.pop("pdf"), args.pop("pages"), parse=_fail, call_model=_fail, **args)
    assert error.value.code == code


def test_demo_code_and_key_required(monkeypatch):
    monkeypatch.delenv("DEMO_ACCESS_CODE")
    with pytest.raises(report.LiveError) as error:
        report.run_report(PDF, [26], access_code="", parse=_fail, call_model=_fail)
    assert error.value.code == "DEMO_NOT_CONFIGURED"
    monkeypatch.setenv("DEMO_ACCESS_CODE", "secret")
    with pytest.raises(report.LiveError) as error:
        report.run_report(PDF, [26], access_code="secret")
    assert (error.value.status, error.value.code) == (503, "UPSTAGE_NOT_CONFIGURED")


def test_cost_ceiling_covers_ten_pages_and_two_capped_calls():
    worst_prompt = {"x": "가" * report.MAX_INPUT_CHARS}
    worst = sum(
        report._estimate(system, worst_prompt, cap)
        for system, cap in zip(
            (report.EXTRACT_SYSTEM, report.TAG_SYSTEM), report.MAX_MODEL_TOKENS, strict=True
        )
    )
    assert worst <= report.MAX_MODEL_USD
    assert report.MAX_REQUEST_USD == pytest.approx(10 * 0.011 + report.MAX_MODEL_USD)
    assert report.PARSE_TIMEOUT_S + 2 * report.MODEL_TIMEOUT_S <= report.TIME_BUDGET_S < 120


def test_selected_page_two_calls_text_match_is_provisional_only():
    calls = []

    def model(system, user, cap):
        calls.append(cap)
        if cap == report.MAX_MODEL_TOKENS[0]:
            assert user["blocks"] == [{"index": 0, "page": 26, "text": LINE}]
            return _completion(
                {
                    "claims": [
                        {
                            "block": 0,
                            "quote": LINE,
                            "track": "goal",
                            "safe_harbor_category": "forward_looking",
                        }
                    ]
                }
            )
        names = user["claims"][0]["names"]
        return _completion(
            {
                "claims": [
                    {
                        "index": 0,
                        "elements": [
                            {"name": names[0], "state": "present", "quote": "by 2030"},
                            {"name": names[1], "state": "present", "quote": "not in the page"},
                        ],
                    }
                ]
            }
        )

    result = report.run_report(
        PDF, [26], access_code="secret", parse=lambda _: PARSED, call_model=model
    )
    assert calls == list(report.MAX_MODEL_TOKENS)  # exactly two calls, no retry
    claim = result["claims"][0]
    assert claim["page"] == 26 and claim["track"] == "goal"
    assert claim["text_matched"] is True and claim["native_text_match"] is True
    assert claim["verification_level"] == "native_text_match"
    assert claim["source_verified"] is False
    assert claim["decision"] is None
    assert claim["blocked_reason"] == report.BLOCKED_WEAK_SOURCE
    states = {e["name"]: e for e in claim["elements"]}
    assert all(e["state"] in ("candidate", "unknown") for e in states.values())
    assert all(e["source_verified"] is False for e in states.values())
    matched = [e for e in states.values() if e["state"] == "candidate"]
    assert [e["quote"] for e in matched] == ["by 2030"]
    body = json.dumps(result)
    assert '"present"' not in body and '"evidence_grade"' not in body
    assert result["status"] == "provisional_candidates"
    assert result["ledger"] == "none_serverless_per_request_cap_only"
    assert 0.011 < result["cost_usd"] <= result["cost_cap_usd"]


def test_unmatched_quote_skips_tagging_call():
    calls = []

    def hallucinated(system, user, cap):
        calls.append(cap)
        return _completion(
            {"claims": [{"block": 0, "quote": "보고서에 없는 환경 주장", "track": "management"}]}
        )

    claim = report.run_report(
        PDF, [26], access_code="secret", parse=lambda _: PARSED, call_model=hallucinated
    )["claims"][0]
    assert calls == [report.MAX_MODEL_TOKENS[0]]
    assert claim["text_matched"] is False and claim["verification_level"] == "unmatched"
    assert claim["source_verified"] is False and claim["decision"] is None
    assert claim["blocked_reason"] == "원문 대조 필요"


def test_invalid_model_output_and_limits_fail_closed():
    malformed = _completion({"claims": None})
    with pytest.raises(report.LiveError) as error:
        report.run_report(
            PDF, [26], access_code="secret", parse=lambda _: PARSED, call_model=lambda *_: malformed
        )
    assert error.value.code == "EXTRACTION_INVALID"
    over = _completion({"claims": []}, output_tokens=report.MAX_MODEL_TOKENS[0] + 1)
    with pytest.raises(report.LiveError) as error:
        report.run_report(
            PDF, [26], access_code="secret", parse=lambda _: PARSED, call_model=lambda *_: over
        )
    assert error.value.code == "SOLAR_RESPONSE_INVALID"
    with pytest.raises(report.LiveError) as error:
        report.run_report(
            PDF, [26], access_code="secret", parse=lambda _: {"x": 1}, call_model=_fail
        )
    assert error.value.code == "PARSE_INVALID"


def test_excess_candidates_are_bounded_and_omission_is_reported_without_retry():
    calls = []

    def model(*args):
        calls.append(args)
        return _completion({"claims": [{"block": 0, "quote": LINE}] * 7})

    result = report.run_report(
        PDF, [26], access_code="secret", parse=lambda _: PARSED, call_model=model
    )
    assert len(calls) == 1
    assert len(result["claims"]) == report.MAX_CLAIMS == 5
    assert result["claims_returned_by_model"] == 7 and result["claims_omitted"] == 2
    assert all(c["decision"] is None and not c["source_verified"] for c in result["claims"])


def test_truncated_tagging_preserves_extraction_with_explicit_warning_and_no_retry():
    calls = []

    def model(system, user, cap):
        calls.append(cap)
        if len(calls) == 1:
            return _completion({"claims": [{"block": 0, "quote": LINE, "track": "goal"}]})
        response = _completion({})
        response["choices"][0]["message"]["content"] = '{"claims":['
        return response

    result = report.run_report(
        PDF, [26], access_code="secret", parse=lambda _: PARSED, call_model=model
    )
    assert calls == list(report.MAX_MODEL_TOKENS)
    assert len(result["claims"]) == 1 and result["claims"][0]["elements"] == []
    assert result["claims"][0]["decision"] is None
    assert not result["claims"][0]["source_verified"]
    assert result["tagging_error"] == "SOLAR_RESPONSE_INVALID"
    assert result["tagging_passes"] == 0
    assert result["cost_basis"] == "reserved_upper_bound_after_tagging_error"
    assert "태깅 미완료" in result["notice"]


def test_no_text_blocks_returns_without_model_call():
    result = report.run_report(
        PDF, [26], access_code="secret", parse=lambda _: {"elements": []}, call_model=_fail
    )
    assert result["claims"] == [] and result["cost_usd"] == pytest.approx(0.011)
