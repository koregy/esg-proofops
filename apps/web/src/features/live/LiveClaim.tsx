import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { getElementLabel } from "../labels";
import "./live-claim.css";

type Step = {
  name: string;
  track?: string | null;
  safe_harbor_category?: string | null;
  elements?: { name: string; element_id: string; candidate_state: string; engine_state: string; quote: string | null }[];
  model?: string;
  duration_ms?: number;
  rule_pack_id?: string;
};
type Result = {
  status: string;
  draft: string;
  notice: string;
  decision: {
    decision_status: string;
    evidence_grade: string | null;
    label: string | null;
    grade_range: { floor: string; ceiling: string; open_elements: string[] } | null;
    open_elements?: string[];
  } | null;
  steps: Step[];
  cost_estimate_usd: number;
  duration_ms?: number;
  explanation?: string;
};

// Verbatim excerpts (bullet marks removed) from the source-verified NAVER demo data.
const examples = [
  { kind: "관리체계형", page: "84", claim: "Operation(환경운영부서)과 Internal Carbon Pricing TF(내부탄소가격제 조직) 운영" },
  { kind: "관리체계형", page: "86", claim: "환경 전담 부서 Green Partnership의 주관으로 온실가스 및 에너지 관리 내용을 포함한 ‘네이버 환경경영 정책’을 수립" },
  { kind: "목표형", page: "186", claim: "향후 TNFD 공시 강화를 위해, 관련 거버넌스 체계 및 내부 정책 기반 마련 등 이행 체계를 고도화할 계획" },
  { kind: "성과형", page: "185", claim: "페트병과 알루미늄 캔은 환경 기술 스타트업 ‘수퍼빈’과 협력을 통해 AI 쓰레기통을 도입하여 약 18만 개(3,121 kg)의 폐기물을 수거하였으며, 자동 분류된 재활용 폐기물은 재사용되며 발생한 적립액은 해피빈을 통해 환경단체에 기부" },
  { kind: "판단 어려움", page: "83", claim: "재생에너지 확보, 환경친화적 서비스 확대 등을 통한 친환경 경영 고도화 방향 검토" },
];
const MAX_CLAIM = 500;
// Server maxDuration is 55s; stop waiting shortly after it.
const CLIENT_TIMEOUT_MS = 60_000;
const stageNames: Record<string, string> = { preliminary_classification: "분류", element_tagging: "요소 태깅", python_rule_engine: "Python 규칙엔진" };
const trackNames: Record<string, string> = { management: "관리체계", performance: "성과", goal: "목표" };
const stateNames: Record<string, string> = { present: "확인", absent: "부재 후보", unknown: "확인 전", conflict: "상충", unreadable: "판독 불가" };
const errors: Record<string, string> = {
  ACCESS_DENIED: "접근 코드가 맞지 않습니다.",
  DEMO_NOT_CONFIGURED: "실시간 체험이 아직 설정되지 않았습니다.",
  INVALID_INPUT: "입력을 확인해 주세요. 주장은 한 문장·500자 이하, 문맥은 2,000자, 페이지 표기는 30자 이하입니다.",
  INVALID_JSON: "요청 형식이 올바르지 않습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.",
  BODY_TOO_LARGE: "입력 길이가 허용 범위를 넘었습니다.",
  UPSTAGE_NOT_CONFIGURED: "실시간 모델 연결이 아직 설정되지 않았습니다.",
  UPSTAGE_UNAVAILABLE: "모델 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  UPSTAGE_RATE_LIMITED: "모델 서비스 요청이 많습니다. 잠시 후 다시 시도해 주세요.",
  UPSTAGE_RESPONSE_INVALID: "모델 응답 형식을 확인할 수 없어 결과를 표시하지 않았습니다. 다시 시도해 주세요.",
  UPSTAGE_RESPONSE_TOO_LARGE: "모델 응답이 허용 크기를 넘어 결과를 표시하지 않았습니다.",
  CLASSIFICATION_INVALID: "모델 분류 응답이 허용 형식이 아니어서 판정을 진행하지 않았습니다. 다시 시도해 주세요.",
  ACTUAL_COST_CAP_EXCEEDED: "실제 사용량이 요청당 비용 한도를 넘어 결과를 표시하지 않았습니다.",
  TIME_BUDGET_EXCEEDED: "첫 모델 응답이 늦어 추가 과금 호출을 생략하고 중단했습니다. 잠시 후 다시 시도해 주세요.",
  INTERNAL_ERROR: "서버에서 요청을 처리하지 못했습니다.",
  PRICE_RECHECK_REQUIRED: "운영자가 모델 가격을 재확인해야 다시 실행할 수 있습니다.",
  REQUEST_COST_CAP: "이 문장의 예상 처리 비용이 체험 한도를 넘었습니다.",
};

export function LiveClaim() {
  const [code, setCode] = useState("");
  const [claim, setClaim] = useState("");
  const [context, setContext] = useState("");
  const [page, setPage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  // PDF copies often carry line breaks; the server accepts one line only.
  const normalizedClaim = claim.replace(/\s+/g, " ").trim();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (!normalizedClaim || normalizedClaim.length > MAX_CLAIM) { setError(errors.INVALID_INPUT); return; }
    setBusy(true); setError(""); setResult(null);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);
    try {
      const response = await fetch("/api/live-claim", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Demo-Access-Code": code },
        body: JSON.stringify({ claim: normalizedClaim, context: context.trim(), page_label: page.trim() || null }),
        cache: "no-store",
        signal: controller.signal,
      });
      // Platform timeouts and static previews answer with HTML, not the API's JSON.
      const text = await response.text();
      let data: (Result & { error?: string }) | null = null;
      try { data = JSON.parse(text); } catch { data = null; }
      if (!response.ok) {
        const known = data?.error ? errors[data.error] : undefined;
        throw new Error(known || (response.status === 504 ? "서버 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요." : response.status === 404 || response.status === 405 ? "이 미리보기에는 실시간 API가 연결되지 않았습니다. 배포된 서비스에서 다시 시도해 주세요." : `요청을 처리하지 못했습니다 (${response.status}).`));
      }
      if (!data || !Array.isArray(data.steps) || !data.status) throw new Error("이 미리보기에는 실시간 API가 연결되지 않았습니다. 배포된 서비스에서 다시 시도해 주세요.");
      setResult(data);
    } catch (reason) {
      setError(controller.signal.aborted ? "응답이 60초 안에 오지 않아 기다리기를 중단했습니다. 잠시 후 다시 시도해 주세요." : reason instanceof TypeError ? "실시간 API에 연결할 수 없습니다. 네트워크 또는 배포 설정을 확인해 주세요." : reason instanceof Error ? reason.message : "요청을 완료하지 못했습니다.");
    } finally {
      window.clearTimeout(timer);
      setBusy(false);
    }
  }

  const decision = result?.decision;
  // Unresolved track: the server stops after classification, so later stages never ran.
  const skipped = !!result && !result.steps.some(step => step.name === "element_tagging");
  const resultGrade = decision?.evidence_grade || (decision?.grade_range ? `${decision.grade_range.floor}–${decision.grade_range.ceiling} 가능` : "판정 보류");
  return <main className="static-main live-main">
    <div className="breadcrumb"><Link to="/">홈</Link><span>/</span> 실시간 체험</div>
    <div className="live-heading"><p className="eyebrow">LIVE MINI PIPELINE</p><h1>한 문장, 근거의 경로를 따라가다.</h1><p>환경 관련 주장 한 문장을 입력하면 분류와 요소 태깅을 거쳐 Python 규칙엔진의 초안 판정을 보여줍니다.</p></div>
    <div className="live-notice"><strong>체험 범위</strong><span>입력 텍스트 기준 · 원문 PDF 검증 없음 · 요소 태깅 1회(운영은 3회 합의)</span><small>사전분류 모델 호출 1회가 별도로 발생하며, 요청당 비용 한도를 넘을 호출은 보내지 않습니다. 등급은 모델이 아니라 Python 규칙엔진만 계산합니다. 공개하거나 모델에 전송할 권한이 있는 문장만 입력하세요.</small></div>
    <div className="live-grid"><section className="surface live-form-card"><div className="card-heading"><div><p className="eyebrow">YOUR CLAIM</p><h2>검토할 문장</h2></div><span>{normalizedClaim.length}/{MAX_CLAIM}자</span></div>
      <form onSubmit={submit} className="live-form">
        <label>데모 접근 코드<input type="password" autoComplete="off" value={code} onChange={e => setCode(e.target.value)} required placeholder="제공받은 접근 코드" /></label>
        <label>환경 주장 한 문장<textarea value={claim} onChange={e => setClaim(e.target.value)} maxLength={MAX_CLAIM * 2} rows={5} required placeholder="보고서의 환경 관련 주장 한 문장을 붙여 넣으세요. 줄바꿈은 공백으로 합쳐 전송합니다." /></label>
        <div className="live-examples"><span>예시 · NAVER 2025 보고서 원문 발췌 (선택하면 페이지 표기도 채워집니다)</span>{examples.map((example, i) => <button type="button" key={example.claim} disabled={busy} onClick={() => { setClaim(example.claim); setPage(example.page); setContext(""); setResult(null); setError(""); }}>예시 {i + 1} · {example.kind} · p.{example.page} <small>{example.claim}</small></button>)}</div>
        <details className="live-optional"><summary>문맥·페이지 표기 (선택)</summary><label>해석용 문맥<textarea value={context} onChange={e => setContext(e.target.value)} maxLength={2000} rows={3} /></label><label>페이지 표기<input value={page} onChange={e => setPage(e.target.value)} maxLength={30} placeholder="예: 84" /></label></details>
        <button className="live-submit" type="submit" disabled={busy || !normalizedClaim || normalizedClaim.length > MAX_CLAIM}>{busy ? "응답을 기다리는 중…" : "검토 시작 ↗"}</button>
      </form>
      {error && <p className="live-error" role="alert">{error}</p>}
    </section>
    <section className="surface live-result-card" aria-live="polite"><div className="card-heading"><div><p className="eyebrow">REVIEW PATH</p><h2>분류 → 요소 태깅 → 규칙엔진</h2></div><span>{busy ? "처리 중" : result ? "초안" : "대기 중"}</span></div>
      <ol className="live-timeline">{["preliminary_classification", "element_tagging", "python_rule_engine"].map((name, index) => {
        const step = result?.steps.find(item => item.name === name);
        return <li key={name} className={step ? "done" : skipped ? "skipped" : "waiting"}><span className="live-stage-number">0{index + 1}</span><div><strong>{stageNames[name]}</strong><small>{step ? `${step.duration_ms ?? 0}ms${step.model ? ` · ${step.model}` : ""}` : skipped ? "실행 안 함 · 트랙 미확정으로 태깅·판정 생략" : busy && index === 0 ? "응답 대기 중" : "대기"}</small>{step?.track !== undefined && <p>트랙: {step.track ? trackNames[step.track] || step.track : "분류 미합의 · 사람 검토 필요"}</p>}{step?.elements && <div className="live-tags">{step.elements.map(element => <div key={element.name}><b>{getElementLabel(element.element_id)} · {element.name}</b><span>{stateNames[element.engine_state] || element.engine_state}</span>{element.quote && <q>{element.quote}</q>}{element.candidate_state !== element.engine_state && <small>모델 {stateNames[element.candidate_state] || element.candidate_state} → 규칙 입력 {stateNames[element.engine_state] || element.engine_state}</small>}</div>)}</div>}{step?.rule_pack_id && <p>규칙팩 {step.rule_pack_id.slice(0, 8)}…</p>}</div></li>;
      })}</ol>
      {result && <div className="live-decision"><span>Python 규칙엔진 초안 · {result.draft}</span><strong>{resultGrade}</strong><p>{decision?.label || decision?.decision_status || "트랙 분류 미합의로 판정 보류"}</p>{decision?.grade_range && <small>가능 범위이며 확정 등급이 아닙니다. 미해결 요소: {decision.grade_range.open_elements.map(getElementLabel).join(", ") || "확인 필요"}</small>}{!decision?.grade_range && !!decision?.open_elements?.length && <small>확인이 필요한 요소: {decision.open_elements.map(getElementLabel).join(", ")}</small>}{result.explanation && <small>{result.explanation}</small>}<footer>사용량 기준 추정 모델 비용(보수적 1.1배) ${result.cost_estimate_usd.toFixed(6)} · {result.duration_ms != null ? `${(result.duration_ms / 1000).toFixed(1)}초` : "소요 시간 미제공"}</footer></div>}
      {!result && !busy && <div className="live-placeholder">문장을 입력하고 검토를 시작하면 단계별 기록이 이곳에 나타납니다.</div>}
    </section></div>
    <p className="live-footer-note">이 체험은 제출한 문장만 확인합니다. 문서 전체에서 근거가 없다고 단정하거나 실제 환경성과를 평가하지 않습니다. <Link to="/demo">NAVER 원문 근거가 연결된 실제 결과 보기 ↗</Link></p>
  </main>;
}
