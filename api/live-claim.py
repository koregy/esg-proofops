"""One-claim Vercel demo. Pasted-text evidence only; no PDF/source attestation."""

from __future__ import annotations

import hmac
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
from proofops.application.tagging.preliminary import SYSTEM_PROMPT as CLASSIFY_PROMPT  # noqa: E402
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
MAX_BODY = 8_192
MAX_CLAIM = 500
MAX_CONTEXT = 2_000
MAX_ESTIMATE_USD = 0.01
INPUT_RATE = 0.15 / 1_000_000
OUTPUT_RATE = 0.60 / 1_000_000
TAG_PROMPT = (ROOT / "prompts/element_tagging.md").read_text(encoding="utf-8")
PACK = RulePackSnapshot(**json.loads((ROOT / "api/rulepack.json").read_text(encoding="utf-8")))


class LiveError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


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
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(65_537)
    except (urllib.error.URLError, TimeoutError) as exc:
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
    if not hmac.compare_digest(access_code, required):
        raise LiveError(403, "ACCESS_DENIED")
    if datetime.now(UTC) >= PRICE_RECHECK_AT:
        raise LiveError(503, "PRICE_RECHECK_REQUIRED")
    call_model = call_model or _provider
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
    if (
        preliminary.get("claim_id") != claim_id
        or track not in (*MAPPINGS, None)
        or category
        not in (None, "forward_looking", "emissions_estimate", "third_party_information")
        or any(key in preliminary for key in ("evidence_grade", "label", "decision_status"))
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
    tag_system = (
        TAG_PROMPT + "\nFor this pasted-text mini demo, replace the full llm_tags JSON "
        "schema above with exactly an elements array containing one item "
        "per requested name: {name,state,quote}. state is present, absent, unknown, "
        "or conflict. For present, quote must be a literal substring of claim text, "
        "not context. No grade or label. No PDF verification is available."
    )
    tag_user = {
        "claim": claim,
        "context_for_interpretation_only": context,
        "track": track,
        "elements": names,
    }
    raw_tags, usage2, ms2, _, actual2 = _step(call_model, tag_system, tag_user, 768, est1)
    if actual1 + actual2 > MAX_ESTIMATE_USD:
        raise LiveError(502, "ACTUAL_COST_CAP_EXCEEDED")
    if set(raw_tags) != {"elements"} or not isinstance(raw_tags["elements"], list):
        raise LiveError(502, "TAGS_INVALID")
    given = raw_tags["elements"]
    if len(given) != len(names) or {
        item.get("name") for item in given if isinstance(item, dict)
    } != set(names):
        raise LiveError(502, "TAGS_INVALID")
    facts, visible = [], []
    for item in given:
        if set(item) != {"name", "state", "quote"} or item["state"] not in (
            "present",
            "absent",
            "unknown",
            "conflict",
        ):
            raise LiveError(502, "TAGS_INVALID")
        name, state, quote = item["name"], item["state"], item["quote"]
        if state == "present":
            if not isinstance(quote, str) or not quote.strip() or claim.count(quote) != 1:
                raise LiveError(502, "TAG_QUOTE_NOT_IN_CLAIM")
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
            if quote is not None:
                raise LiveError(502, "TAGS_INVALID")
            # One pasted sentence cannot prove a document-wide absence.
            facts.append(ConfirmedFact(name, "conflict" if state == "conflict" else "unknown"))
        visible.append(
            {
                "name": name,
                "candidate_state": state,
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
        "status": "draft",
        "draft": "사용자 최종 검토 전",
        "notice": "원문 PDF 검증 없음 — 입력 텍스트 기준",
        "replicas": 1,
        "claim_id": claim_id,
        "steps": steps,
        "source": source,
        "decision": decision.to_api_dict(),
        "cost_estimate_usd": round(actual1 + actual2, 6),
        "duration_ms": ms1 + ms2,
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                raise LiveError(413, "BODY_TOO_LARGE")
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
