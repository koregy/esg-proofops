import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { statusText, storedGradeText, trackText, type DemoSnapshot } from "../auditreport/snapshot";
import { isTrack, loadEngineTable, TABLE_PATH, type EngineTable } from "./engineTable";
import ReviewSimulator from "./ReviewSimulator";
import "./reviewsim.css";

function SimulationBanner({ table }: { table: EngineTable | null }) {
  const scenarios = table ? Object.values(table.tracks).reduce((sum, group) => sum + Object.keys(group.rows).length, 0) : null;
  return <p className="xd-banner" data-mode="local-simulation" role="note"><strong>LOCAL SIMULATION</strong><span>브라우저 안에서만 동작하는 <b>로컬 시뮬레이션</b>입니다. 등급은 Python 규칙엔진이 고정 규칙집으로 미리 계산한 조회표(<code>{TABLE_PATH}</code>{scenarios ? ` · ${scenarios.toLocaleString("ko-KR")}개 시나리오` : ""})에서 찾습니다. 실제로 수락된 태깅·판정 revision이 생기지 않고, API 업로드나 사람 승인도 없습니다. 원문 예외 경로(최상급 특칙 등)는 조회표에 포함되지 않습니다.</span></p>;
}

export default function ReviewPage({ data }: { data: DemoSnapshot }) {
  const { claimId } = useParams();
  const [table, setTable] = useState<EngineTable | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    loadEngineTable().then(value => { if (active) setTable(value); }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  const tracked = data.claims.filter(claim => isTrack(claim.track));
  const claim = claimId ? data.claims.find(item => item.id === claimId) : undefined;

  return <main className="static-main xd-page review-page">
    <div className="breadcrumb"><Link to="/">홈</Link><span>/</span>{claimId ? <><Link to="/review">검토 시뮬레이터</Link><span>/</span> 주장</> : " 검토 시뮬레이터"}</div>
    <SimulationBanner table={table} />
    {error ? <section className="xd-empty"><h1>조회표를 불러오지 못했습니다</h1><p>저장된 판정은 <Link to="/demo">결과 화면</Link>에서 볼 수 있습니다.</p></section>
      : claimId ? (!claim ? <section className="xd-empty"><h1>이 스냅샷에 없는 주장입니다</h1><Link to="/review">시뮬레이터 목록으로</Link></section>
        : !isTrack(claim.track) ? <section className="xd-empty"><h1>트랙이 정해지지 않은 주장입니다</h1><p>예비 분류가 합의되지 않아(track=null) 사다리를 고를 수 없습니다. 임의 트랙으로 시뮬레이션하지 않습니다.</p><Link to="/review">트랙이 있는 주장 보기</Link></section>
        : !table ? <p role="status">규칙엔진 조회표를 불러오는 중입니다…</p>
        : <ReviewSimulator key={claim.id} claim={claim} track={claim.track} table={table} rulePackHash={data.run.rule_pack_hash} />)
      : <section className="xd-card" aria-labelledby="review-index-title">
        <h1 id="review-index-title" className="review-index-title">시뮬레이션할 주장 고르기</h1>
        <p>트랙이 정해진 {tracked.length}건만 사다리를 적용할 수 있습니다. 나머지 {data.claims.length - tracked.length}건은 트랙 미합의로 시뮬레이션 대상이 아닙니다.</p>
        <div className="xd-table-wrap"><table className="xd-table review-index"><thead><tr><th scope="col">원문</th><th scope="col">트랙</th><th scope="col">주장</th><th scope="col">저장된 판정</th><th scope="col"><span className="sr-only">열기</span></th></tr></thead>
          <tbody>{tracked.map(item => <tr key={item.id}><td>p.{item.page ?? "?"}</td><td>{trackText[item.track!]}</td><td className="quote">{item.quote}</td><td><b>{storedGradeText(item)}</b><br /><small>{statusText[item.decision.status] ?? item.decision.status}</small></td><td><Link to={`/review/${item.id}`}>시뮬레이터 ↗</Link></td></tr>)}</tbody></table></div>
      </section>}
  </main>;
}
