import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { SNAPSHOT_PATH, type DemoSnapshot } from "../auditreport/snapshot";
import { buildReplayModel, replayDuration, STAGE_PLAYBACK_MS, stageAt } from "./replayData";
import "./replay.css";

const count = (value: number) => value.toLocaleString("ko-KR");
const clock = (ms: number) => { const seconds = Math.floor(ms / 1000); return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`; };
const duration = (seconds: number) => { const minutes = Math.round(seconds / 60); return minutes >= 60 ? `${Math.floor(minutes / 60)}시간 ${minutes % 60}분` : `${minutes}분`; };

export default function ReplayPage({ data }: { data: DemoSnapshot }) {
  const model = useMemo(() => buildReplayModel(data), [data]);
  const total = replayDuration(model.stages);
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(true);
  const finished = elapsed >= total;

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setElapsed(total); setPlaying(false); }
  }, [total]);
  useEffect(() => {
    if (!playing || finished) return;
    const started = performance.now() - elapsed;
    const timer = window.setInterval(() => setElapsed(Math.min(total, performance.now() - started)), 80);
    return () => window.clearInterval(timer);
  // elapsed는 재생을 다시 시작할 때의 기준점으로만 읽는다.
  }, [playing, finished, total]);

  const current = stageAt(model.stages, elapsed);
  const progress = Math.min(100, elapsed / total * 100);
  const shown = (index: number) => finished || current.index > index ? model.stages[index].count : current.index < index ? 0 : Math.round(model.stages[index].count * current.fraction);
  const graded = model.gradeCounts.reduce((sum, [, n]) => sum + n, 0);

  return <main className="static-main xd-page replay-page">
    <div className="breadcrumb"><Link to="/">홈</Link><span>/</span> 저장된 처리 재생</div>
    <p className="xd-banner" data-mode="stored-replay" role="note"><strong>STORED REPLAY</strong><span>저장된 실행 기록을 다시 보여 주는 화면입니다. <b>실제 모델 실행이 아니며</b>, 이 화면은 모델이나 API를 호출하지 않습니다. 단계별 재생 시간은 <b>시뮬레이션된 타이밍</b>(단계당 {STAGE_PLAYBACK_MS / 1000}초)이고, 실제 소요 시간은 아래 ‘기록된 실행 값’에 따로 표시합니다. 모든 수치는 <code>{SNAPSHOT_PATH}</code>(생성 {new Date(model.generatedAt).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} KST)에서 읽습니다.</span></p>

    <section className="replay-hero" aria-labelledby="replay-title">
      <div className="replay-hero-body">
        <div><p className="eyebrow">STORED RUN · {data.partial ? "부분 실행" : "실행 완료"}</p><h1 id="replay-title">보고서 한 권이 <em>검토 기록</em>이 되기까지</h1><p>{model.title}</p></div>
        <div className="replay-hero-number">{finished ? <><strong>{count(graded)}</strong><span>건 규칙 판정 기록 (저장값)</span></> : <><strong>{count(shown(current.index))}</strong><span>{model.stages[current.index].unit} · {model.stages[current.index].title}</span></>}</div>
      </div>
      <div className="replay-progress-heading"><span>{finished ? "재생 완료" : `${model.stages[current.index]?.title} 재생 중`}</span><strong>{Math.round(progress)}%</strong></div>
      <div className="replay-progress-track" role="progressbar" aria-label="저장된 기록 재생 진행률" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${progress}%` }} /></div>
      <div className="replay-controls">
        <span>재생 {clock(elapsed)} / {clock(total)} <small>(시뮬레이션 타이밍)</small></span>
        <div>
          {!finished ? <button type="button" onClick={() => setPlaying(!playing)}>{playing ? "일시정지" : "계속 재생"}</button> : null}
          <button type="button" onClick={() => { setElapsed(0); setPlaying(true); }}>처음부터 ↺</button>
          {!finished ? <button type="button" className="strong" onClick={() => setElapsed(total)}>결과 바로 보기 ↗</button> : null}
        </div>
      </div>
    </section>

    <div className="replay-grid">
      <section className="xd-card replay-timeline" aria-labelledby="replay-steps-title">
        <h2 id="replay-steps-title">저장된 단계 기록</h2>
        <p className="xd-muted">{model.stageNote}</p>
        <ol>{model.stages.map((stage, index) => {
          const state = finished || index < current.index ? "done" : index === current.index ? "active" : "waiting";
          return <li key={stage.key} className={`replay-step ${state}`} data-stage={stage.key}>
            <span className="replay-dot" aria-hidden="true">{state === "done" ? "✓" : String(index + 1).padStart(2, "0")}</span>
            <div><h3>{stage.title}</h3><p>{stage.detail}</p></div>
            <strong>{state === "waiting" ? "—" : count(shown(index))}<small>{stage.unit}</small></strong>
          </li>;
        })}</ol>
      </section>

      <aside className="replay-side">
        <section className="xd-card" aria-labelledby="replay-recorded-title">
          <h2 id="replay-recorded-title">기록된 실행 값</h2>
          <p className="xd-muted">재생 속도와 무관한, 스냅샷 <code>run</code>에 저장된 값입니다.</p>
          <dl className="replay-recorded">
            {model.recorded.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{duration(item.seconds)}<small>{count(item.seconds)}초</small></dd></div>)}
            <div><dt>R72 유료 모델 호출</dt><dd>{count(model.paidCalls)}<small>회</small></dd></div>
            <div><dt>R72 모델 비용</dt><dd>${model.costUsd.toFixed(2)}</dd></div>
            <div><dt>R85 모델 호출</dt><dd>{count(model.reviewModelCalls)}<small>회</small></dd></div>
            <div><dt>모델</dt><dd className="small">{data.run.model_ids.join(", ")}</dd></div>
          </dl>
        </section>
      </aside>
    </div>

    {finished ? <section className="replay-finish" aria-live="polite" data-testid="replay-finish">
      <div>
        <p className="eyebrow">STORED RESULT</p>
        <h2>규칙 판정 기록 {count(graded)}건 · 등급 미정 {count(data.claims.length - graded)}건</h2>
        <p>{model.gradeCounts.map(([grade, n]) => `${grade} ${n}건`).join(" · ")} (저장된 규칙엔진 등급) · 가능 범위만 있는 보류 {model.ranges}건 · 미판정(not_run) {model.notRun}건{model.otherBlocked ? ` · 기타 보류 ${model.otherBlocked}건` : ""}. 미판정과 보류는 등급을 채우지 않고 null로 둡니다.</p>
      </div>
      <div className="replay-finish-links"><Link to="/demo">주장별 결과 보기 ↗</Link><Link to="/report/naver">감사 보고서 ↗</Link></div>
    </section> : null}
  </main>;
}
