"""Build the public NAVER demo from the immutable R85 export (stdlib only)."""

import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = Path(
    "/Users/ss020/Dev/ESG_ProofOps/outputs/agent-results/R85-naver-mgmt-delegated-review/naver-r85-export.zip"
)
OUTPUT = ROOT / "apps/web/public/demo/naver-2025.json"
AUDIT_UNCERTAIN = {"f6bfe84f", "0598227c"}


def short(value, limit=200):
    return " ".join(str(value or "").split())[:limit]


def reference(ref):
    return {"page": ref.get("page_num"), "quote": short(ref.get("quote"))}


def build(export):
    with zipfile.ZipFile(export) as archive:
        report = json.loads(archive.read("report.json"))
        manifest = json.loads(archive.read("manifest.json"))
    claims = []
    for item in report["claims"]:
        elements = item.get("tag_elements") or []
        first_id = elements[0]["element_id"][0] if elements else ""
        track = {"M": "management", "G": "goal", "P": "performance"}.get(first_id)
        refs = item.get("source_refs") or []
        claims.append(
            {
                "id": item["claim_id"],
                "page": refs[0]["page_num"] if refs else None,
                "track": track,
                "quote": short(item["claim_quote"]),
                "source_verified": any(ref.get("verification_state") == "verified" for ref in refs),
                "elements": [
                    {
                        "id": element["element_id"],
                        "state": element["state"],
                        "evidence": [reference(ref) for ref in element.get("evidence_refs", [])][
                            :6
                        ],
                    }
                    for element in elements
                ],
                "decision": {
                    "grade": item.get("evidence_grade"),
                    "label": item.get("label"),
                    "grade_range": item.get("grade_range"),
                    "status": item["decision_status"],
                    "missing": item.get("missing_elements") or [],
                    "unresolved": item.get("unresolved_elements") or [],
                },
                "review": {
                    "status": item["review_status"],
                    "tag_revision": item["tag_revision"],
                    "decision_revision": item["decision_revision"],
                    "audit": "uncertain" if item["claim_id"][:8] in AUDIT_UNCERTAIN else None,
                },
            }
        )
    return {
        "title": "NAVER 2025 통합보고서 · 선택 페이지 검토",
        "generated_at": report["generated_at"],
        "partial": report["partial"],
        "coverage": {
            key: report["coverage"][key]
            for key in (
                "pages_processed",
                "pages_total",
                "pages_unprocessed",
                "pages_unreadable",
                "claims_discovered",
                "claims_decided",
                "claims_needs_review",
            )
        },
        "funnel": [
            {"label": "추출 주장", "count": 331},
            {"label": "원문 검증", "count": 272},
            {"label": "예비 분류 합의", "count": 60},
            {"label": "관계 시도", "count": 60},
            {"label": "요소 태그 발행", "count": 36},
            {"label": "R85 위임 검토", "count": 27},
            {"label": "규칙 판정 기록", "count": 17},
        ],
        "funnel_source": "R72 유료 실행 단계와 R85 복사본 위임 검토 결과; 단계별 시점이 다릅니다.",
        "run": {
            "model_ids": ["solar-pro4"],
            "model_note": "R72 고정 실행 설정: solar-pro4 (추출·예비분류·관계·태깅).",
            "model_binding_hash": manifest.get("model_binding_hash"),
            "rule_pack_id": "e498dfd1-2b6c-4b30-b66d-ec014423ab9b",
            "rule_pack_name": "proofops-domain-v2.0-impl2",
            "rule_pack_hash": (report.get("rule_pack_hashes") or [None])[0],
            "r72_cost_usd": 1.822535,
            "r72_paid_calls": 2038,
            "r72_elapsed_seconds": {
                "parse_extraction": 18556.5,
                "tagging": 4238.3,
                "local_postprocess": 264.3,
            },
            "r85_review_seconds": 412.3,
            "r85_model_calls": 0,
        },
        "audit": {
            "agreed": 15,
            "disagreed": 0,
            "uncertain": 2,
            "scope": "R85 E3 17건 독립 읽기 검토",
        },
        "claims": claims,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, default=EXPORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    data = build(args.export)
    assert len(data["claims"]) == 331
    assert sum(c["decision"]["grade"] == "E3" for c in data["claims"]) == 17
    assert sum(c["decision"]["grade_range"] is not None for c in data["claims"]) == 10
    assert all(
        len(c["quote"]) <= 200
        and all(len(e["quote"]) <= 200 for t in c["elements"] for e in t["evidence"])
        for c in data["claims"]
    )
    assert all("tenant" not in json.dumps(c).lower() for c in data["claims"])
    payload = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    assert len(payload) < 3_000_000
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f'{args.output}: {len(payload)} bytes, {len(data["claims"])} claims')


if __name__ == "__main__":
    main()
