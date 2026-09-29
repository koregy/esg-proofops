import { Link } from "react-router";
import { elementLabels } from "../labels";
import { stateText, statusText, type DemoSnapshot } from "../auditreport/snapshot";
import { CONDITIONAL, DECISIONS, ELEMENT_STATES, ENGINE_VERSION, GAPS, GRADES, LADDER_ELEMENTS, LADDERS, SOURCES, SPLIT, SUPERLATIVE, UNRESOLVED_GAP_IDS } from "./domainContract";
import "./extended-shell.css";
import "./decision-guide.css";

const trackPrefix = { goal: "G", performance: "P", management: "M" } as const;

export function DecisionGuide({ data }: { data: DemoSnapshot }) {
  const statusCounts = Object.entries(data.claims.reduce<Record<string, number>>((counts, claim) => {
    counts[claim.decision.status] = (counts[claim.decision.status] ?? 0) + 1;
    return counts;
  }, {}));
  const ranges = data.claims.filter(claim => claim.decision.grade_range).length;
  return <main className="static-main xd-page guide-page">
    <div className="breadcrumb"><Link to="/">홈</Link><span>/</span> 판정 안내</div>
    <section className="xd-heading" aria-labelledby="guide-title">
      <p className="eyebrow">READING THE RESULT</p>
      <h1 id="guide-title">등급과 라벨은 이렇게 계산됩니다</h1>
      <p>이 화면은 새 기준을 만들지 않습니다. 원문 도메인 문서, 사용자 도메인 결정, 미정 계약 목록에 적힌 문구만 옮겨 보여 줍니다. 등급은 공시 문장을 뒷받침하는 원문 근거의 수준이며 기업 성과의 진위나 법 위반 여부를 판정하지 않습니다.</p>
      <p className="xd-source">출처: <code>{SOURCES.original}</code> §4 · <code>{SOURCES.decisions}</code> §7–8 · <code>{SOURCES.gaps}</code></p>
    </section>

    <nav className="guide-toc" aria-label="판정 안내 목차">
      <a href="#guide-split">역할 분리</a><a href="#guide-grades">등급·라벨</a><a href="#guide-ladders">트랙별 사다리</a><a href="#guide-states">요소 상태</a><a href="#guide-status">판정 상태</a><a href="#guide-gaps">미정 경계</a>
    </nav>

    <section className="xd-card" id="guide-split" aria-labelledby="guide-split-title">
      <h2 id="guide-split-title">LLM은 읽고, 규칙엔진이 판정한다 <small>원문 §4.1</small></h2>
      <div className="xd-table-wrap"><table className="xd-table"><thead><tr><th scope="col">담당</th><th scope="col">하는 일</th><th scope="col">하지 않는 일</th></tr></thead>
        <tbody>{SPLIT.map(row => <tr key={row.owner}><th scope="row">{row.owner}</th><td>{row.does}</td><td>{row.doesNot}</td></tr>)}</tbody></table></div>
      <p className="xd-note">이 제출본의 규칙엔진: 순수 Python 평가기 <code>{ENGINE_VERSION}</code> · 규칙집 <code>{data.run.rule_pack_name}</code> · SHA-256 <code>{data.run.rule_pack_hash.slice(0, 16)}…</code></p>
    </section>

    <section className="xd-card" id="guide-grades" aria-labelledby="guide-grades-title">
      <h2 id="guide-grades-title">입증 등급과 라벨 <small>원문 §4.3</small></h2>
      <p>등급은 E0~E3 연속 사다리이고, 라벨은 등급의 함수입니다. 검토 상태(review_status)는 라벨이 아니라 별도 필드입니다.</p>
      <div className="xd-table-wrap"><table className="xd-table"><thead><tr><th scope="col">등급</th><th scope="col">라벨</th><th scope="col">의미</th></tr></thead>
        <tbody>{GRADES.map(row => <tr key={row.grade}><th scope="row">{row.grade}</th><td><code>{row.label}</code></td><td>{row.meaning}</td></tr>)}</tbody></table></div>
      <p className="xd-note">INCOMPLETE의 PERF/IMPL 세분 매핑은 모든 트랙에 정의되지 않았습니다(GAP-005). 정의되지 않은 조합은 세부 라벨을 비워 둡니다.</p>
    </section>

    <section className="xd-card" id="guide-ladders" aria-labelledby="guide-ladders-title">
      <h2 id="guide-ladders-title">트랙별 등급 사다리 <small>원문 §4.4 · §4.5</small></h2>
      <div className="guide-ladders">{LADDERS.map(ladder => <article key={ladder.track} aria-labelledby={`ladder-${ladder.track}`}>
        <h3 id={`ladder-${ladder.track}`}>{ladder.title}</h3>{ladder.basis ? <p className="xd-muted">근거: {ladder.basis}</p> : null}
        <ol className="guide-ladder">{ladder.rows.map(([grade, rule]) => <li key={grade}><b>{grade}</b><span>{rule}</span></li>)}</ol>
        <h4>요소</h4>
        <ul className="guide-elements">{Object.entries(elementLabels).filter(([id]) => id.startsWith(trackPrefix[ladder.track])).map(([id, name]) => {
          const inLadder = LADDER_ELEMENTS[ladder.track].includes(id);
          return <li key={id} className={inLadder ? "ladder" : "additional"}><code>{id}</code> {name}<small>{inLadder ? "사다리 요소" : CONDITIONAL[id] ? `추가 요소 · 조건부: ${CONDITIONAL[id]}` : "추가 요소 · 필수"}</small></li>;
        })}</ul>
      </article>)}</div>
      <p className="xd-note"><b>추가 요소 (사용자 결정 R00 §7 A-2):</b> {DECISIONS.additional}</p>
      <p className="xd-note"><b>최상급 주장 특칙:</b> {SUPERLATIVE}</p>
    </section>

    <section className="xd-card" id="guide-states" aria-labelledby="guide-states-title">
      <h2 id="guide-states-title">요소 상태 — 미확인은 부재가 아닙니다</h2>
      <div className="xd-table-wrap"><table className="xd-table"><thead><tr><th scope="col">상태</th><th scope="col">화면 표기</th><th scope="col">규칙엔진 입력 조건</th></tr></thead>
        <tbody>{ELEMENT_STATES.map(row => <tr key={row.state}><th scope="row"><code>{row.state}</code></th><td>{stateText[row.state]}</td><td>{row.rule}</td></tr>)}</tbody></table></div>
      <p className="xd-muted">출처: <code>{SOURCES.values}</code> ELEMENT_STATES · <code>{SOURCES.engine}</code> ConfirmedFact 검증</p>
    </section>

    <section className="xd-card" id="guide-status" aria-labelledby="guide-status-title">
      <h2 id="guide-status-title">판정 상태와 가능 등급 범위 <small>R00 §8</small></h2>
      <dl className="guide-status">
        <div><dt><code>decided</code> · {statusText.decided}</dt><dd>명시 사다리 분기로 등급과 라벨이 하나로 정해졌습니다.</dd></div>
        <div><dt><code>blocked_evidence</code> · {statusText.blocked_evidence}</dt><dd>사다리 요소가 미확인이라 가능한 등급이 둘 이상입니다. 가능 등급 범위: {DECISIONS.range}</dd></div>
        <div><dt><code>blocked_rule_gap</code> · {statusText.blocked_rule_gap}</dt><dd>명시된 분기가 없는 조합입니다(GAP-007 등). 다른 채점기의 임계값으로 채우지 않습니다.</dd></div>
        <div><dt><code>not_run</code> · {statusText.not_run}</dt><dd>이 스냅샷에서 규칙엔진이 실행되지 않은 주장입니다. 등급·라벨은 null이며 E0으로 세지 않습니다.</dd></div>
      </dl>
      <p className="xd-note" data-testid="guide-snapshot-status">이 스냅샷({data.claims.length}건): {statusCounts.map(([status, count]) => `${statusText[status] ?? status} ${count}건`).join(" · ")} · 가능 범위 표시 {ranges}건</p>
    </section>

    <section className="xd-card" id="guide-gaps" aria-labelledby="guide-gaps-title">
      <h2 id="guide-gaps-title">미정 계약 — 자동 판정을 멈추는 경계</h2>
      <p>원문에 값이 없는 곳은 임의 기준을 만들지 않고 해당 자동판정만 차단합니다. 해제 여부는 규칙집 매니페스트의 <code>unresolved_gap_ids</code>를 따릅니다.</p>
      <div className="xd-table-wrap"><table className="xd-table guide-gap-table"><thead><tr><th scope="col">ID</th><th scope="col">원문</th><th scope="col">빈 계약</th><th scope="col">상태</th></tr></thead>
        <tbody>{GAPS.map(gap => { const open = UNRESOLVED_GAP_IDS.includes(gap.id); return <tr key={gap.id} data-gap={gap.id}><th scope="row">{gap.id}</th><td>{gap.source_section}</td><td>{gap.issue}</td><td><span className={`xd-pill ${open ? "warn" : "ok"}`}>{open ? "미해결" : "해소 (R00 §7 A-2)"}</span></td></tr>; })}</tbody></table></div>
      <p className="xd-muted">출처: <code>{SOURCES.gaps}</code> · <code>{SOURCES.manifest}</code></p>
    </section>
    <p className="xd-next"><Link className="primary-link" to="/review">검토 시뮬레이터로 규칙 확인하기 <span aria-hidden="true">↗</span></Link></p>
  </main>;
}
