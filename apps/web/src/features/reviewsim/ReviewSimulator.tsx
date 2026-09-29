import { useMemo, useState } from "react";
import { Link } from "react-router";
import { getElementLabel } from "../labels";
import { stateText, statusText, storedGradeText, trackText, type SnapshotClaim } from "../auditreport/snapshot";
import { lookup, rowGradeText, SIM_STATES, TABLE_PATH, toSimState, type EngineTable, type SimState, type Track } from "./engineTable";

type Change = { seq: number; change: string; before: string; after: string };

function initialStates(claim: SnapshotClaim, table: EngineTable, track: Track): Record<string, SimState> {
  const group = table.tracks[track];
  const stored = Object.fromEntries(claim.elements.map(element => [element.id, element.state]));
  // 저장된 상태가 없거나 조회표가 다루지 않는 상태는 미확인으로 둔다. 부재로 바꾸지 않는다.
  return Object.fromEntries([...group.ladder, ...group.other].map(id => [id, toSimState(stored[id] ?? "unknown") ?? "unknown"]));
}

export default function ReviewSimulator({ claim, track, table, rulePackHash }: { claim: SnapshotClaim; track: Track; table: EngineTable; rulePackHash: string }) {
  const group = table.tracks[track];
  const hashMatches = table.rule_pack_sha256 === rulePackHash;
  const stored = useMemo(() => initialStates(claim, table, track), [claim, table, track]);
  const [states, setStates] = useState(stored);
  const [willingnessOnly, setWillingnessOnly] = useState(false);
  const [history, setHistory] = useState<Change[]>([]);
  const storedRow = lookup(table, track, stored, false);
  const row = lookup(table, track, states, willingnessOnly);
  const unsupported = claim.elements.filter(element => !toSimState(element.state));
  const decision = claim.decision;
  const storedRange = decision.grade_range ? [decision.grade_range.floor, decision.grade_range.ceiling, decision.grade_range.open_elements] : null;
  const consistency = decision.status === "not_run" ? "not_run"
    : storedRow && storedRow[0] === decision.status && storedRow[1] === decision.grade && storedRow[2] === decision.label && JSON.stringify(storedRow[3]) === JSON.stringify(storedRange) ? "match" : "mismatch";

  function record(change: string, nextStates: Record<string, SimState>, nextWillingness: boolean) {
    const after = lookup(table, track, nextStates, nextWillingness);
    setHistory(current => [{ seq: current.length + 1, change, before: rowGradeText(row), after: rowGradeText(after) }, ...current]);
  }
  function changeState(id: string, next: SimState) {
    if (states[id] === next) return;
    const nextStates = { ...states, [id]: next };
    record(`${id} ${stateText[states[id]]} → ${stateText[next]}`, nextStates, willingnessOnly);
    setStates(nextStates);
  }
  function changeWillingness(next: boolean) {
    record(`의지 표현만: ${next ? "예" : "아니오"}`, states, next);
    setWillingnessOnly(next);
  }

  const ladderOnly = (ids: string[]) => ids.filter(id => group.ladder.includes(id));
  const additionalOpen = group.other.filter(id => states[id] === "unknown" || states[id] === "conflict");
  const additionalMissing = group.other.filter(id => states[id] === "absent");
  const changed = history.length > 0;

  return <section className="review-sim" aria-labelledby="review-sim-title">
    <div className="review-sim-head">
      <div><p className="eyebrow">DECISION REVIEW · LOCAL SIMULATION</p><h1 id="review-sim-title">판정 검토 시뮬레이터</h1><p>{trackText[track]} 주장 · 원문 p.{claim.page ?? "?"} · <Link to={`/demo/${claim.id}`}>저장된 주장 상세 ↗</Link></p></div>
    </div>
    <blockquote className="review-sim-quote">“{claim.quote}”</blockquote>
    {!hashMatches ? <p role="alert" className="xd-note">조회표 규칙집 해시({table.rule_pack_sha256.slice(0, 12)}…)가 스냅샷 규칙집({rulePackHash.slice(0, 12)}…)과 다릅니다. 시뮬레이션 결과를 표시하지 않습니다.</p> : null}

    <div className="review-sim-compare">
      <article className="review-sim-panel stored" aria-labelledby="stored-title" data-testid="stored-decision">
        <h2 id="stored-title">저장된 판정 <span className="xd-pill muted">불변 기록</span></h2>
        <strong className="review-sim-grade">{storedGradeText(claim)}</strong>
        <dl>
          <div><dt>상태</dt><dd><code>{decision.status}</code> · {statusText[decision.status] ?? decision.status}</dd></div>
          <div><dt>evidence_grade</dt><dd><code>{decision.grade ?? "null"}</code></dd></div>
          <div><dt>label</dt><dd><code>{decision.label ?? "null"}</code></dd></div>
          <div><dt>revision</dt><dd>태깅 {claim.review.tag_revision} · 판정 {claim.review.decision_revision}</dd></div>
          <div><dt>검토 상태</dt><dd><code>{claim.review.status}</code>{claim.review.audit ? ` · 독립 읽기 ${claim.review.audit}` : ""}</dd></div>
        </dl>
      </article>
      <article className="review-sim-panel sim" aria-labelledby="sim-title" aria-live="polite" data-testid="simulated-decision">
        <h2 id="sim-title">시뮬레이션 결과 <span className="xd-pill info">저장되지 않음</span></h2>
        <strong className="review-sim-grade">{hashMatches ? rowGradeText(row) : "—"}</strong>
        {hashMatches && row ? <dl>
          <div><dt>상태</dt><dd><code>{row[0]}</code> · {statusText[row[0]] ?? row[0]}</dd></div>
          <div><dt>evidence_grade</dt><dd><code>{row[1] ?? "null"}</code></dd></div>
          <div><dt>label</dt><dd><code>{row[2] ?? "null"}</code></dd></div>
          {row[3] ? <div><dt>가능 범위</dt><dd>{row[3][0]}–{row[3][1]} (확정 등급 아님) · 좁힐 요소 {row[3][2].join(", ")}</dd></div> : null}
          {ladderOnly(row[6]).length ? <div><dt>사다리 결손</dt><dd>{ladderOnly(row[6]).map(getElementLabel).join(", ")}</dd></div> : null}
          {ladderOnly(row[7]).length ? <div><dt>사다리 미확정</dt><dd>{ladderOnly(row[7]).map(getElementLabel).join(", ")}</dd></div> : null}
          {row[4].length ? <div><dt>적용 분기</dt><dd><code>{row[4].join(", ")}</code></dd></div> : null}
          {row[5].length ? <div><dt>미정 계약</dt><dd>{row[5].join(", ")}</dd></div> : null}
        </dl> : null}
      </article>
    </div>
    <p className="review-sim-consistency" data-consistency={consistency}>
      {consistency === "match" ? "저장된 요소 상태를 그대로 조회하면 저장된 판정과 같은 결과가 나옵니다." :
        consistency === "not_run" ? "이 주장은 스냅샷에서 규칙엔진이 실행되지 않았습니다(not_run). 시뮬레이션 결과는 저장된 판정이 아닙니다." :
        "저장된 요소 상태로 조회한 결과가 저장된 판정과 다릅니다. 조회표에 없는 원문 예외 경로가 적용됐을 수 있으므로 저장된 판정을 기준으로 보세요."}
      {changed ? " 현재 화면은 요소 상태를 바꾼 가정 결과입니다." : ""}
    </p>

    <div className="review-sim-grid">
      <div className="xd-card">
        <h2>요소 상태 가정</h2>
        <p className="xd-muted">사다리 요소만 등급을 바꿉니다. 추가 요소는 등급에 영향이 없습니다(R00 §7 A-2). ‘근거 부재’를 고르면 검색 범위 검증이 끝났다고 가정한 것입니다.</p>
        {[...group.ladder, ...group.other].map(id => <div className={`review-sim-row ${group.ladder.includes(id) ? "ladder" : "additional"}`} key={id}>
          <label htmlFor={`sim-${id}`}>{getElementLabel(id)}<small>{group.ladder.includes(id) ? "사다리 요소" : "추가 요소 · 등급 영향 없음"}{stored[id] !== states[id] ? ` · 저장값 ${stateText[stored[id]]}` : ""}</small></label>
          <select id={`sim-${id}`} value={states[id]} disabled={!hashMatches} onChange={event => changeState(id, event.target.value as SimState)}>
            {SIM_STATES.map(state => <option key={state} value={state}>{stateText[state]}</option>)}
          </select>
        </div>)}
        {track === "management" ? <label className="review-sim-check"><input type="checkbox" checked={willingnessOnly} disabled={!hashMatches} onChange={event => changeWillingness(event.target.checked)} /> 의지 표현만 서술한 주장으로 가정 (원문 §4.4 관리체계 E0)</label> : null}
        {unsupported.length ? <p className="xd-muted">조회표 밖 저장 상태({unsupported.map(element => `${element.id}:${element.state}`).join(", ")})는 미확인으로 시작합니다.</p> : null}
        {(additionalOpen.length || additionalMissing.length) ? <p className="xd-muted">추가 요소 현황: {additionalOpen.length ? `미확정 ${additionalOpen.join(", ")}` : ""}{additionalOpen.length && additionalMissing.length ? " · " : ""}{additionalMissing.length ? `결손 ${additionalMissing.join(", ")}` : ""} → 검토 필요로 남습니다.</p> : null}
        <button type="button" className="review-sim-reset" disabled={!changed} onClick={() => { setStates(stored); setWillingnessOnly(false); setHistory([]); }}>저장된 상태로 되돌리기</button>
      </div>
      <aside className="xd-card review-sim-history">
        <h2>시뮬레이션 변경 기록</h2>
        <p className="xd-muted">이 브라우저 화면에만 남습니다. 태깅·판정 revision을 만들지 않고 서버로 보내지 않습니다.</p>
        {history.length ? <ol>{history.map(item => <li key={item.seq}><b>#{item.seq}</b> {item.change}<span>{item.before} → {item.after}</span></li>)}</ol> : <p>요소 상태를 바꾸면 여기에 기록됩니다.</p>}
        <p className="xd-muted">조회표: <code>{TABLE_PATH}</code> · 규칙집 SHA-256 <code>{table.rule_pack_sha256.slice(0, 16)}…</code>{hashMatches ? " (스냅샷과 일치)" : ""}</p>
      </aside>
    </div>
  </section>;
}
