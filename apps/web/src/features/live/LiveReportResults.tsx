import { useRef, useState } from "react";
import { getElementLabel } from "../labels";
import type { Claim, Result } from "./LiveReport";

const tracks: Record<string, string> = { goal: "목표", performance: "성과", management: "관리체계" };
const queues = ["전체", "규칙 판정", "등급 범위·예비", "원문 대조 필요", "미판정·보류"];
function bucket(c: Claim) {
  if (!c.source_verified) return "원문 대조 필요";
  if (c.decision?.decision_status === "blocked_rule_gap") return "미판정·보류";
  if (c.decision?.evidence_grade) return "규칙 판정";
  if (c.decision?.grade_range) return "등급 범위·예비";
  return "미판정·보류";
}
function grade(c: Claim) {
  if (c.decision?.decision_status === "blocked_rule_gap") return "등급 보류 · 규칙 미정";
  if (c.decision?.evidence_grade) return c.source_verified ? `규칙 판정 ${c.decision.evidence_grade}` : `예비 ${c.decision.evidence_grade} · 원문 검증 전`;
  if (c.decision?.grade_range) return `가능 범위 ${c.decision.grade_range.floor}–${c.decision.grade_range.ceiling} · 검토 필요`;
  return "등급 보류";
}
const source = (c: Claim) => c.source_verified ? "원문 확인" : c.text_matched ? "PDF 텍스트 일치 · 잠정 후보 · 원문 검증 전" : "원문 대조 필요";

export function LiveReportResults({ result, fileName, busy }: { result: Result; fileName: string; busy: boolean }) {
  const [queue, setQueue] = useState("전체");
  const [track, setTrack] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(0);
  const detail = useRef<HTMLElement>(null);
  const claims = result.claims;
  const verified = claims.filter(c => c.source_verified).length;
  const decided = claims.filter(c => bucket(c) === "규칙 판정").length;
  const ranged = claims.filter(c => bucket(c) === "등급 범위·예비").length;
  const filtered = claims.map((claim, index) => ({ claim, index })).filter(({ claim }) =>
    (queue === "전체" || bucket(claim) === queue) && (track === "all" || (claim.track || "unknown") === track) &&
    `${claim.quote} ${claim.page}`.toLowerCase().includes(search.toLowerCase()));
  const active = filtered.find(item => item.index === selected) || filtered[0];
  const claim = active?.claim;
  const counts = [["추출 주장", claims.length], ["원문 검증", verified], ["확정 판정", decided], ["검토 필요", claims.length - decided]] as const;
  const flow = [["추출 주장", claims.length], ["요소 후보 추출", claims.filter(c => c.elements.length).length], ["원문 검증", verified], ["규칙 판정", decided]] as const;
  return <div className="live-analysis-dashboard">
    <div className="demo-heading"><div><p className="eyebrow">REPORT ANALYSIS</p><h2>분석 결과</h2><p className="live-result-filename">{fileName}</p><div className="badges"><span className="badge amber">선택 범위 {result.pages.length}쪽</span><span className="badge blue">사용자 최종 검토 전</span></div></div><div className="heading-side"><span>REVIEW STATUS</span><strong>{busy ? "분석 진행 중 · 부분 결과" : "선택 범위 처리 종료"}</strong><small>{(result.duration_ms / 1000).toFixed(1)}초 · 처리 비용 ${result.cost_usd.toFixed(4)}</small></div></div>
    <p className="notice">{result.notice}</p><p className="caption">선택한 쪽: {result.pages.join(", ")} · 묶음당 환경 관련 문단 최대 16개에서 주장 최대 5건을 추출합니다. 전체 보고서 검증 결과가 아닙니다.</p>
    <section className="metrics" aria-label="분석 요약">{counts.map(([label, count]) => <div key={label}><span>{label}</span><strong>{count}<small>건</small></strong></div>)}</section>
    <section className="overview-grid"><div className="surface funnel-card"><p className="eyebrow">PROCESS FUNNEL</p><h2>처리 흐름</h2><div className="funnel-list">{flow.map(([label, count], i) => <div className="funnel-row" key={label}><span className="step-name"><b>{String(i + 1).padStart(2, "0")}</b>{label}</span><div className="bar-track"><div style={{ width: `${count / (claims.length || 1) * 100}%` }} /></div><strong>{count}</strong></div>)}</div></div>
      <div className="surface grade-card"><p className="eyebrow">DECISION DISTRIBUTION</p><h2>등급과 보류</h2><div className="grade-bars">{[["확정 · 규칙 판정", decided, "e3"], ["등급 범위", ranged, "range"], ["확인 대기 · 보류", claims.length - decided - ranged, "pending"]].map(([label, count, style]) => <div key={label}><div className="live-grade-row"><span>{label}</span><strong>{count}</strong></div><div className={`grade-line ${style}`}><i style={{ width: `${Number(count) / (claims.length || 1) * 100}%` }} /></div></div>)}</div><p className="caption">텍스트 일치는 원문 검증 완료가 아닙니다. 미검증 근거는 후보로 남기며 확정 등급으로 집계하지 않습니다.</p></div></section>
    <section className="claims-section"><div className="card-heading"><div><p className="eyebrow">CLAIM REVIEW</p><h2>문장별 결과</h2><p>문장을 선택하면 원문 인용과 요소별 근거를 확인할 수 있습니다.</p></div><span>{filtered.length} / {claims.length}건</span></div>
      <div className="queue-tabs" role="tablist" aria-label="검토 상태">{queues.map(name => <button key={name} type="button" role="tab" aria-selected={queue === name} onClick={() => setQueue(name)}>{name} <span>{name === "전체" ? claims.length : claims.filter(c => bucket(c) === name).length}</span></button>)}</div>
      <div className="filters"><label>검색<input value={search} onChange={e => setSearch(e.target.value)} placeholder="문장, 페이지 검색" /></label><label>트랙<select value={track} onChange={e => setTrack(e.target.value)}><option value="all">전체 트랙</option>{Object.entries(tracks).map(([key, label]) => <option key={key} value={key}>{label}</option>)}<option value="unknown">분류 검토 필요</option></select></label></div>
      <div className="claims-layout"><div className="claim-list" aria-label="주장 목록">{filtered.map(({ claim: c, index }) => <button type="button" key={`${c.page}-${index}`} className={`claim-item ${active?.index === index ? "selected" : ""}`} aria-pressed={active?.index === index} onClick={() => { setSelected(index); if (window.matchMedia("(max-width: 1024px)").matches) requestAnimationFrame(() => detail.current?.scrollIntoView({ behavior: "smooth", block: "start" })); }}><div className="claim-item-top"><span>p.{c.page} · {tracks[c.track || ""] || "분류 검토 필요"}</span><span className={`mini-status ${bucket(c) === "규칙 판정" ? "good" : "warn"}`}>{grade(c)}</span></div><p>{c.quote}</p><small>{source(c)}</small></button>)}{!filtered.length && <p className="empty">{claims.length ? "조건에 맞는 주장이 없습니다." : "검토한 문단에서 환경 주장 후보를 찾지 못했습니다. 다른 쪽을 선택해 주세요."}</p>}</div>
        <aside ref={detail} className="claim-detail">{claim ? <><div className="detail-top"><div><p className="eyebrow">CLAIM DETAIL · p.{claim.page}</p><h3>주장과 판정 근거</h3></div></div><div className="source-quote"><span>추출 문장 · p.{claim.page}</span><blockquote>“{claim.quote}”</blockquote><small>{source(claim)}</small></div><div className="why-panel"><h4>판정과 보류 사유</h4><p>{claim.blocked_reason || (claim.source_verified ? "아래 요소별 근거와 규칙 판정 결과를 검토해 주세요." : "원문 검증과 검토 전에는 등급을 확정하지 않습니다.")}</p></div><div className="decision-panel"><span>사용자 최종 검토 전</span><strong>{grade(claim)}</strong><p>{source(claim)}</p></div><div className="detail-block"><h4>요소별 근거</h4>{claim.elements.length ? <div className="element-table-wrap"><table className="element-table"><thead><tr><th>요소</th><th>상태</th><th>근거 문구</th></tr></thead><tbody>{claim.elements.map((el, i) => <tr key={`${el.element_id}-${i}`}><th scope="row"><b>{el.element_id}</b><span>{getElementLabel(el.element_id)}</span></th><td>{el.state === "present" && el.source_verified && claim.source_verified ? "근거 확인" : el.state === "present" || el.state === "candidate" ? "근거 후보 · 원문 검증 전" : ({ unknown: "미확인", conflict: "근거 충돌", unreadable: "판독 불가", absent: "근거 없음", not_applicable: "비적용" }[el.state] || "미확인")}</td><td>{el.quote ? <p>“{el.quote}”</p> : <span className="no-evidence">연결된 근거 문구 없음</span>}</td></tr>)}</tbody></table></div> : <p className="caption">요소 태깅 전입니다. 빈 요소를 근거 없음으로 보지 않습니다.</p>}</div></> : <div className="detail-empty"><h3>표시할 주장이 없습니다</h3><p>검색 조건을 바꾸거나 다른 쪽을 분석해 주세요.</p></div>}</aside></div>
    </section>
  </div>;
}
