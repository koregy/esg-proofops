"""One-claim Vercel demo. Pasted-text evidence only; no PDF/source attestation."""

from __future__ import annotations

import hmac
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from time import monotonic
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from proofops.adapters.local.upstage import PRICE_RECHECK_AT  # noqa: E402
from proofops.application.tagging.consensus import (  # noqa: E402
    PARTIAL_FACTS_V1,
    reviewable_decision,
)
from proofops.domain.rulepacks import RulePackSnapshot  # noqa: E402
from proofops.domain.rules.engine import (  # noqa: E402
    MAPPINGS,
    ConfirmedFact,
    ConfirmedTags,
    RuleContext,
    evaluate,
)
from proofops.domain.values import SourceRef  # noqa: E402

MODEL = "solar-pro3"
CLASSIFY_PROMPT = (
    "Classify the main asserted predicate of one environmental claim, not its topic. "
    "goal=future company intention or commitment; performance=reported achieved result; "
    "management=existing organization, system, policy, or recurring process. "
    "If the predicate is genuinely unclear use null. Return only JSON with claim_id "
    "copied exactly, track (goal/performance/management/null), and "
    "safe_harbor_category (forward_looking/emissions_estimate/third_party_information/null). "
    "A category is a candidate, never legal protection. Never grade or label."
)
MAX_BODY = 8_192
MAX_CLAIM = 500
MAX_CONTEXT = 2_000
MAX_ESTIMATE_USD = 0.01
INPUT_RATE = 0.15 / 1_000_000
OUTPUT_RATE = 0.60 / 1_000_000
# Vercel maxDuration is 55s; never start a paid call that cannot finish inside it.
PROVIDER_TIMEOUT_S = 20
TIME_BUDGET_S = 50
PACK = RulePackSnapshot(**json.loads((ROOT / "api/rulepack.json").read_text(encoding="utf-8")))


class LiveError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _explanation(decision: dict, definitions: dict) -> str:
    gaps = decision["gap_ids"]
    if gaps:
        names = {
            "GAP-001": "세이프하버 등급 매핑",
            "GAP-003": "추가 요소의 등급 효과",
            "GAP-006": "최상급·세이프하버 우선순위",
            "GAP-007": "사다리 미정 조합",
        }
        return (
            "미정 규칙 "
            + ", ".join(f"{gap} {names.get(gap, '판정 기준')}" for gap in gaps)
            + " 때문에 등급을 확정할 수 없습니다."
        )
    grade_range = decision["grade_range"]
    if grade_range:
        elements = ", ".join(
            f"{key} {definitions[key]['name']}" for key in grade_range["open_elements"]
        )
        return (
            f"{elements} 근거가 보고서에 확인되면 {grade_range['ceiling']}까지 가능합니다. "
            "현재 범위는 확정 등급이 아닙니다."
        )
    if decision["open_elements"]:
        elements = ", ".join(
            f"{key} {definitions[key]['name']}"
            for key in decision["open_elements"]
            if key in definitions
        )
        return f"{elements} 근거를 보고서에서 확인해야 등급 범위를 계산할 수 있습니다."
    return "원문 근거와 미해결 요소를 검토해야 등급을 확정할 수 있습니다."


def _provider(system: str, user: dict, max_tokens: int) -> dict:
    key = os.environ.get("UPSTAGE_API_KEY", "")
    if not key:
        raise LiveError(503, "UPSTAGE_NOT_CONFIGURED")
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.upstage.ai/v1/chat/completions",
        body,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PROVIDER_TIMEOUT_S) as response:
            raw = response.read(65_537)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise LiveError(503, "UPSTAGE_RATE_LIMITED") from None
        raise LiveError(502, "UPSTAGE_UNAVAILABLE") from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        # TimeoutError and connection resets are OSError subclasses.
        raise LiveError(502, "UPSTAGE_UNAVAILABLE") from exc
    if len(raw) > 65_536:
        raise LiveError(502, "UPSTAGE_RESPONSE_TOO_LARGE")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise LiveError(502, "UPSTAGE_RESPONSE_INVALID") from exc


def _step(call_model, system: str, user: dict, max_tokens: int, spent_estimate: float):
    # UTF-8 bytes + chat overhead conservatively bound input tokens for short text.
    estimate = (
        (len(system.encode()) + len(json.dumps(user, ensure_ascii=False).encode()) + 1024)
        * INPUT_RATE
        + max_tokens * OUTPUT_RATE
    ) * 1.1
    if spent_estimate + estimate > MAX_ESTIMATE_USD:
        raise LiveError(400, "REQUEST_COST_CAP")
    started = monotonic()
    data = call_model(system, user, max_tokens)
    try:
        message = json.loads(data["choices"][0]["message"]["content"])
        usage = data["usage"]
        tokens = {"input": usage["prompt_tokens"], "output": usage["completion_tokens"]}
        if any(type(n) is not int or n < 0 for n in tokens.values()):
            raise ValueError
        if tokens["output"] > max_tokens:
            raise ValueError
        if not isinstance(message, dict):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LiveError(502, "UPSTAGE_RESPONSE_INVALID") from exc
    actual = (tokens["input"] * INPUT_RATE + tokens["output"] * OUTPUT_RATE) * 1.1
    if actual > MAX_ESTIMATE_USD:
        raise LiveError(502, "ACTUAL_COST_CAP_EXCEEDED")
    return message, tokens, round((monotonic() - started) * 1000), estimate, actual


def run_claim(payload: dict, *, access_code: str, call_model=None) -> dict:
    required = os.environ.get("DEMO_ACCESS_CODE", "")
    if not required:
        raise LiveError(503, "DEMO_NOT_CONFIGURED")
    if not hmac.compare_digest(access_code.encode("utf-8"), required.encode("utf-8")):
        raise LiveError(403, "ACCESS_DENIED")
    if datetime.now(UTC) >= PRICE_RECHECK_AT:
        raise LiveError(503, "PRICE_RECHECK_REQUIRED")
    call_model = call_model or _provider
    request_started = monotonic()
    if not isinstance(payload, dict) or set(payload) - {"claim", "context", "page_label"}:
        raise LiveError(400, "INVALID_INPUT")
    claim, context, page = (
        payload.get("claim"),
        payload.get("context", ""),
        payload.get("page_label"),
    )
    if (
        not isinstance(claim, str)
        or not 1 <= len(claim.strip()) <= MAX_CLAIM
        or "\n" in claim
        or not isinstance(context, str)
        or len(context) > MAX_CONTEXT
        or (page is not None and (not isinstance(page, str) or len(page) > 30))
    ):
        raise LiveError(400, "INVALID_INPUT")
    claim_id, document_id, source_id, parse_id = (str(uuid4()) for _ in range(4))
    tenant_id = PACK.tenant_id
    packet_hash = _hash(
        json.dumps({"claim": claim, "context": context, "page": page}, sort_keys=True)
    )
    classify_user = {
        "claim_id": claim_id,
        "sources": [{"source_index": 0, "text": claim}],
        "context_blocks": [{"text": context}] if context else [],
    }
    preliminary, usage1, ms1, est1, actual1 = _step(
        call_model, CLASSIFY_PROMPT, classify_user, 384, 0
    )
    track = preliminary.get("track")
    category = preliminary.get("safe_harbor_category")
    # Case/whitespace variants only; unknown values still fail closed below.
    if isinstance(track, str):
        track = track.strip().lower()
    if isinstance(category, str):
        category = category.strip().lower()
    if track in ("unknown", "unclear", "null", "none"):
        track = None
    if category in ("null", "none", "unknown"):
        category = None
    if (
        preliminary.get("claim_id") != claim_id
        or track not in (*MAPPINGS, None)
        or category
        not in (None, "forward_looking", "emissions_estimate", "third_party_information")
    ):
        raise LiveError(502, "CLASSIFICATION_INVALID")
    steps = [
        {
            "name": "preliminary_classification",
            "track": track,
            "safe_harbor_category": category,
            "model": MODEL,
            "prompt_sha256": _hash(CLASSIFY_PROMPT),
            "duration_ms": ms1,
            "tokens": usage1,
        }
    ]
    source = {
        "kind": "pasted_text",
        "sha256": _hash(claim),
        "page_label": page,
        "document_version_id": document_id,
    }
    if track is None:
        return {
            "status": "needs_review",
            "notice": "원문 PDF 검증 없음 — 입력 텍스트 기준",
            "draft": "사용자 최종 검토 전",
            "steps": steps,
            "source": source,
            "replicas": 1,
            "decision": None,
            "cost_estimate_usd": round(actual1, 6),
            "duration_ms": ms1,
        }
    names = sorted({name for group in MAPPINGS[track].values() for name in group})
    definitions = {e["id"]: e for e in PACK.file_content("rubric/elements.yaml")["elements"]}
    local_allowed = {
        name
        for element, group in MAPPINGS[track].items()
        if "local_claim" in definitions[element]["source_scopes"]
        for name in group
    }
    tag_system = (
        "Tag only the pasted claim. Return a JSON object with exactly one key, elements. "
        "elements must contain one {name,state,quote} object for EACH requested name, "
        "including names with no evidence. Use state present only when quote is an exact "
        "substring of claim; otherwise use unknown and quote null. Use context only to "
        "interpret the claim, never as quote evidence. Do not output grades or labels."
    )
    tag_user = {
        "claim": claim,
        "context_for_interpretation_only": context,
        "track": track,
        "elements": names,
    }
    if monotonic() - request_started + PROVIDER_TIMEOUT_S > TIME_BUDGET_S:
        raise LiveError(504, "TIME_BUDGET_EXCEEDED")
    raw_tags, usage2, ms2, _, actual2 = _step(call_model, tag_system, tag_user, 768, est1)
    if actual1 + actual2 > MAX_ESTIMATE_USD:
        raise LiveError(502, "ACTUAL_COST_CAP_EXCEEDED")
    given = raw_tags.get("elements", [])
    if isinstance(given, dict):
        given = [
            {"name": name, **value} for name, value in given.items() if isinstance(value, dict)
        ]
    if not isinstance(given, list):
        given = []
    given_by_name = {}
    for item in given:
        if not isinstance(item, dict) or item.get("name") not in names:
            continue
        name = item["name"]
        state = item.get("state")
        if state not in ("present", "absent", "unknown", "conflict"):
            state = "unknown"
        normalized = {
            "name": name,
            "state": state,
            "quote": item.get("quote") if state == "present" else None,
        }
        # Conflicting duplicate candidates cannot establish an element.
        given_by_name[name] = (
            {"name": name, "state": "conflict", "quote": None}
            if name in given_by_name
            else normalized
        )
    facts, visible = [], []
    element_ids = {name: element for element, group in MAPPINGS[track].items() for name in group}
    for name in names:
        item = given_by_name.get(name, {"name": name, "state": "unknown", "quote": None})
        state, quote = item["state"], item["quote"]
        candidate_state = state
        if state == "present":
            if not isinstance(quote, str) or not quote.strip() or claim.count(quote) != 1:
                state, quote = "unknown", None
            else:
                start = claim.index(quote)
                ref = SourceRef(
                    source_id,
                    document_id,
                    parse_id,
                    1,
                    page,
                    None,
                    _hash(claim),
                    quote,
                    start,
                    start + len(quote),
                    "located",
                    "verified",
                )
            if state == "present" and name in local_allowed:
                facts.append(
                    ConfirmedFact(
                        name,
                        "present",
                        (ref,),
                        tenant_id,
                        True,
                        True,
                        source_scope="local_claim",
                        normalized_value=quote,
                    )
                )
            else:
                facts.append(ConfirmedFact(name, "unknown"))
        else:
            # One pasted sentence cannot prove a document-wide absence.
            facts.append(ConfirmedFact(name, "conflict" if state == "conflict" else "unknown"))
        visible.append(
            {
                "name": name,
                "element_id": element_ids[name],
                "candidate_state": candidate_state,
                "engine_state": facts[-1].state,
                "quote": quote,
                "page_label": page if state == "present" else None,
            }
        )
    # The full evaluator DTO requires three hashes; the latter two mark unrun replicas.
    replica_hashes = (
        _hash(json.dumps(raw_tags, sort_keys=True)),
        _hash("not_run_replica_2"),
        _hash("not_run_replica_3"),
    )
    tags = ConfirmedTags(
        tenant_id,
        document_id,
        claim_id,
        track,
        tuple(facts),
        1,
        packet_hash,
        _hash(MODEL),
        _hash(tag_system),
        replica_hashes,
        PACK.ontology_version,
        category,
    )
    rule_started = monotonic()
    decision = evaluate(
        tags, RuleContext(tenant_id, document_id, claim_id, packet_hash, local_synthetic=True), PACK
    )
    reviewable, _ = reviewable_decision(decision, PARTIAL_FACTS_V1, "needs_review")
    decision_view = (reviewable or decision).to_api_dict() | {
        "review_status": "needs_review",
        "evidence_grade": None,
        "label": None,
        "sublabel": None,
        "open_elements": list(decision.unresolved_elements),
    }
    steps.append(
        {
            "name": "element_tagging",
            "elements": visible,
            "model": MODEL,
            "prompt_sha256": _hash(tag_system),
            "duration_ms": ms2,
            "tokens": usage2,
        }
    )
    steps.append(
        {
            "name": "python_rule_engine",
            "rule_pack_id": PACK.rule_pack_id,
            "rule_pack_sha256": PACK.sha256,
            "duration_ms": round((monotonic() - rule_started) * 1000),
        }
    )
    return {
        "status": "needs_review",
        "draft": "사용자 최종 검토 전",
        "notice": "원문 PDF 검증 없음 — 입력 텍스트 기준",
        "replicas": 1,
        "claim_id": claim_id,
        "steps": steps,
        "source": source,
        "fact_assembly": PARTIAL_FACTS_V1,
        "decision": decision_view,
        "explanation": _explanation(decision_view, definitions),
        "cost_estimate_usd": round(actual1 + actual2, 6),
        "duration_ms": ms1 + ms2,
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                raise LiveError(413, "BODY_TOO_LARGE")
            if length < 1:
                raise LiveError(400, "INVALID_INPUT")
            payload = json.loads(self.rfile.read(length))
            result = run_claim(payload, access_code=self.headers.get("X-Demo-Access-Code", ""))
            status = 200
        except LiveError as exc:
            status, result = exc.status, {"error": exc.code}
        except (ValueError, UnicodeError):
            status, result = 400, {"error": "INVALID_JSON"}
        except Exception:
            status, result = 500, {"error": "INTERNAL_ERROR"}
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
