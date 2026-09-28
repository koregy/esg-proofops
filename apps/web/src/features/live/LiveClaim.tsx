import { FormEvent, useState, type CSSProperties } from "react";

const field: CSSProperties = { display: "block", width: "100%", marginTop: 6, padding: 10, font: "inherit", border: "1px solid #cdd9d0", borderRadius: 6 };

type Result = {
  status: string;
  draft: string;
  notice: string;
  decision: {
    decision_status: string;
    evidence_grade: string | null;
    label: string | null;
    grade_range: { floor: string; ceiling: string; open_elements: string[] } | null;
  } | null;
  steps: Array<Record<string, unknown>>;
  cost_estimate_usd: number;
  duration_ms?: number;
};

export function LiveClaim() {
  const [code, setCode] = useState("");
  const [claim, setClaim] = useState("");
  const [context, setContext] = useState("");
  const [page, setPage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await fetch("/api/live-claim", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Demo-Access-Code": code },
        body: JSON.stringify({ claim, context, page_label: page || null }),
        cache: "no-store",
      });
      const data = await response.json() as Result & { error?: string };
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setResult(data);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "요청을 완료하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return <main className="static-main"><section className="surface" style={{ maxWidth: 760, margin: "32px auto" }}>
    <p className="eyebrow">LIVE MINI PIPELINE</p>
    <h1>한 문장 직접 검토</h1>
    <p>공개 가능한 문장만 입력하세요. Solar가 분류와 요소를 태깅하고 Python 규칙엔진이 판정을 계산합니다. 유료 모델 호출은 최대 2회입니다.</p>
    <form onSubmit={submit} style={{ display: "grid", gap: 16 }}>
      <label>데모 접근 코드<input style={field} type="password" autoComplete="off" value={code} onChange={e => setCode(e.target.value)} required /></label>
      <label>환경 주장 한 문장<textarea style={field} value={claim} onChange={e => setClaim(e.target.value)} maxLength={500} rows={3} required /></label>
      <label>짧은 문맥 (선택)<textarea style={field} value={context} onChange={e => setContext(e.target.value)} maxLength={2000} rows={3} /></label>
      <label>페이지 표기 (선택)<input style={field} value={page} onChange={e => setPage(e.target.value)} maxLength={30} /></label>
      <button type="submit" disabled={busy} style={{ minHeight: 44, padding: "10px 18px", border: 0, borderRadius: 6, color: "white", background: "#195a46", cursor: "pointer" }}>{busy ? "검토 중…" : "문장 검토 실행"}</button>
    </form>
    {error && <p role="alert">요청 오류: {error}</p>}
    {result && <section aria-label="검토 결과" style={{ marginTop: 24 }}>
      <h2>{result.decision?.evidence_grade ?? (result.decision?.grade_range ? `${result.decision.grade_range.floor}–${result.decision.grade_range.ceiling} 가능 범위` : "검토 필요")}</h2>
      <p>{result.decision?.label ?? result.decision?.decision_status ?? result.status} · {result.draft}</p>
      <p>{result.notice}</p>
      <p>모델 비용 추정 ${result.cost_estimate_usd.toFixed(6)} · {result.duration_ms ?? 0}ms · 단일 태깅 회차</p>
      <details><summary>분류 · 태깅 · 규칙 경로 보기</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(result.steps, null, 2)}</pre></details>
    </section>}
  </section></main>;
}
