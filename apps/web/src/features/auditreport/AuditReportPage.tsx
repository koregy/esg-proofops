import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { csvCell, reportRow, statusLabel } from "./auditRows";
import { OFFICIAL_REPORT_URL, SNAPSHOT_PATH, stateText, type LoadedSnapshot } from "./snapshot";
import "./audit-report.css";

// 이 제출본은 NAVER 스냅샷 하나만 싣는다. 다른 기업 데이터는 검증된 데이터셋이 준비될 때 추가한다.
const companies: Record<string, string> = { naver: "NAVER" };

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : `${new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul" }).format(date)} KST`;
}

function download(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function AuditReportPage({ snapshot }: { snapshot: LoadedSnapshot }) {
  const { company = "" } = useParams();
  const [showAll, setShowAll] = useState(false);
  const { data, sha256 } = snapshot;
  const rows = useMemo(() => data.claims.map(reportRow), [data]);
  const companyName = companies[company];
  if (!companyName) return <main className="static-main xd-page"><section className="xd-empty"><h1>보고서를 찾을 수 없습니다</h1><p>이 제출본에는 NAVER 저장 스냅샷의 보고서만 있습니다.</p><Link to="/report/naver">NAVER 감사 보고서 보기</Link></section></main>;

  const graded = rows.filter(row => row.evidenceGrade);
  const gradeCounts = ["E3", "E2", "E1", "E0"].map(grade => [grade, graded.filter(row => row.evidenceGrade === grade).length] as const);
  const ranged = rows.filter(row => !row.evidenceGrade && row.range);
  const statusCounts = Object.entries(rows.reduce<Record<string, number>>((acc, row) => { acc[row.status] = (acc[row.status] ?? 0) + 1; return acc; }, {}));
  const stateTotals = Object.entries(rows.reduce<Record<string, number>>((acc, row) => { for (const [state, n] of Object.entries(row.stateCounts)) acc[state] = (acc[state] ?? 0) + n; return acc; }, {}));
  const unresolvedNull = rows.filter(row => row.evidenceGrade === null).length;
  const decisionRows = rows.filter(row => row.status !== "not_run");
  const visible = showAll ? rows : decisionRows;
  const provenance = {
    snapshot_path: SNAPSHOT_PATH, snapshot_sha256: sha256, generated_at: data.generated_at, partial: data.partial,
    rule_pack_name: data.run.rule_pack_name, rule_pack_id: data.run.rule_pack_id, rule_pack_hash: data.run.rule_pack_hash,
    model_ids: data.run.model_ids, model_binding_hash: data.run.model_binding_hash, official_report_index: OFFICIAL_REPORT_URL,
  };

  function exportJson() {
    download(`${company}-proofops-audit-report.json`, JSON.stringify({
      export_kind: "proofops.static_snapshot_audit_report",
      export_notice: "저장된 스냅샷을 그대로 옮긴 읽기 전용 보고서입니다. 등급·라벨은 저장된 규칙엔진 산출값이며 다시 계산하지 않았습니다. null 등급은 null로 둡니다. AI 위임 검토이며 사용자 최종 검토·외부 감사의견이 아닙니다.",
      company: companyName, title: data.title, provenance, coverage: data.coverage, funnel: data.funnel, funnel_source: data.funnel_source, independent_read: data.audit,
      claims: rows,
    }, null, 2), "application/json;charset=utf-8");
  }
  function exportCsv() {
    const head = ["claim_id", "page", "quote", "track", "status", "evidence_grade", "label", "grade_range", "source_verified", "evidence_pages", "tag_revision", "decision_revision", "review_status"];
    const body = rows.map(row => [row.id, row.page, row.quote, row.track, row.status, row.evidenceGrade, row.label, row.range, row.sourceVerified, row.evidencePages.join("; "), row.tagRevision, row.decisionRevision, row.reviewStatus]);
    download(`${company}-proofops-claims.csv`, `﻿${[head, ...body].map(line => line.map(csvCell).join(",")).join("\r\n")}`, "text/csv;charset=utf-8");
  }

  return <main className="static-main xd-page audit-page">
    <div className="audit-toolbar" aria-label="보고서 작업">
      <div className="breadcrumb"><Link to="/">홈</Link><span>/</span> 감사 보고서 · {companyName}</div>
      <div className="audit-actions"><button type="button" onClick={() => window.print()}>인쇄 · PDF</button><button type="button" onClick={exportJson}>JSON 내보내기</button><button type="button" onClick={exportCsv}>CSV 내보내기</button></div>
    </div>
    <p className="xd-banner" data-mode="stored-snapshot" role="note"><strong>STORED SNAPSHOT</strong><span>저장된 스냅샷의 불변 기록으로 만든 보고서입니다. 등급·라벨은 저장된 규칙엔진 산출값이며 이 화면에서 다시 계산하지 않습니다. 등급이 없는 주장은 null로 두고 E0이나 근거 부재로 세지 않습니다. AI 위임 검토 결과이며 사용자 최종 검토나 외부 감사의견이 아닙니다.</span></p>

    <article className="audit-paper">
      <section className="audit-cover">
        <p className="eyebrow">DISCLOSURE EVIDENCE REVIEW · {data.partial ? "PARTIAL RUN" : "FULL RUN"}</p>
        <h1>{companyName} 공시 근거 검토 보고서</h1>
        <p>{data.title}</p>
        <dl className="audit-cover-facts">
          <div><dt>스냅샷 생성</dt><dd>{formatDate(data.generated_at)}</dd></div>
          <div><dt>처리 범위</dt><dd>{data.coverage.pages_processed} / {data.coverage.pages_total}쪽</dd></div>
          <div><dt>규칙집</dt><dd><code>{data.run.rule_pack_name}</code></dd></div>
          <div><dt>원문 보고서</dt><dd><a href={OFFICIAL_REPORT_URL} target="_blank" rel="noopener noreferrer">NAVER ESG 보고서 목록 ↗</a></dd></div>
        </dl>
      </section>

      <section className="audit-section" aria-labelledby="audit-summary-title">
        <div className="audit-section-head"><span>01 / SUMMARY</span><h2 id="audit-summary-title">저장된 검토 결과</h2></div>
        <div className="audit-stats">
          <div><span>추출 주장</span><strong>{rows.length}<small>건</small></strong></div>
          <div><span>저장된 등급</span><strong>{graded.length}<small>건</small></strong></div>
          <div><span>가능 범위만 있음</span><strong>{ranged.length}<small>건</small></strong></div>
          <div data-testid="audit-null-grade"><span>등급 null (미정)</span><strong>{unresolvedNull}<small>건</small></strong></div>
        </div>
        <div className="audit-summary-grid">
          <div className="audit-chart"><h3>저장된 등급 분포 <small>분모 {graded.length}건 · null 제외</small></h3>
            {gradeCounts.map(([grade, n]) => <div className="audit-chart-row" key={grade}><span>{grade}</span><div><i style={{ width: `${graded.length ? n / graded.length * 100 : 0}%` }} /></div><strong>{n}</strong></div>)}
            <p className="xd-muted">판정 상태: {statusCounts.map(([status, n]) => `${statusLabel(status)} ${n}건`).join(" · ")}</p>
          </div>
          <div className="audit-chart"><h3>저장된 요소 상태 합계 <small>주장별 요소 태그</small></h3>
            <ul className="audit-state-list">{stateTotals.map(([state, n]) => <li key={state}><code>{state}</code> {stateText[state] ?? state}<b>{n}</b></li>)}</ul>
            <p className="xd-muted">미확인(unknown)은 근거 부재(absent)로 바꾸지 않았습니다. absent는 검색 범위가 검증된 경우에만 저장됩니다.</p>
          </div>
        </div>
        <div className="audit-funnel"><h3>저장된 처리 단계</h3><ol>{data.funnel.map(step => <li key={step.label}><span>{step.label}</span><b>{step.count}건</b></li>)}</ol><p className="xd-muted">{data.funnel_source}</p></div>
        <p className="xd-note">독립 읽기 검토({data.audit.scope}): 동의 {data.audit.agreed}건 · 이견 {data.audit.disagreed}건 · 확인 필요 {data.audit.uncertain}건. 사람 정답셋이 아닙니다.</p>
      </section>

      <section className="audit-section" aria-labelledby="audit-register-title">
        <div className="audit-section-head"><span>02 / CLAIM REGISTER</span><h2 id="audit-register-title">주장별 저장 판정과 근거</h2></div>
        <div className="audit-filter" role="group" aria-label="표시 범위">
          <button type="button" aria-pressed={!showAll} onClick={() => setShowAll(false)}>규칙엔진 실행 {decisionRows.length}건</button>
          <button type="button" aria-pressed={showAll} onClick={() => setShowAll(true)}>전체 {rows.length}건</button>
        </div>
        <div className="xd-table-wrap"><table className="xd-table audit-table"><thead><tr><th scope="col">원문</th><th scope="col">주장 (최대 200자)</th><th scope="col">트랙</th><th scope="col">저장된 판정</th><th scope="col">요소 상태</th><th scope="col">근거 쪽</th><th scope="col">기록</th></tr></thead>
          <tbody>{visible.map(row => <tr key={row.id} data-claim={row.id} data-status={row.status}>
            <td>p.{row.page ?? "?"}</td>
            <td className="quote">{row.quote}{row.sourceVerified ? null : <small className="audit-warn">원문 인용 검증 기록 없음</small>}</td>
            <td>{row.track}</td>
            <td><b className="audit-grade">{row.gradeText}</b><small>{row.label ?? (row.range ? "확정 등급 아님" : "label null")} · <code>{row.status}</code></small></td>
            <td>{Object.entries(row.stateCounts).map(([state, n]) => `${stateText[state] ?? state} ${n}`).join(" · ") || "—"}</td>
            <td>{row.evidencePages.length ? row.evidencePages.slice(0, 5).map(page => `p.${page}`).join(", ") + (row.evidencePages.length > 5 ? ` 외 ${row.evidencePages.length - 5}` : "") : "—"}</td>
            <td className="links"><small>rev 태깅 {row.tagRevision} · 판정 {row.decisionRevision}</small><Link to={`/demo/${row.id}`}>원문 근거 ↗</Link>{row.track !== "분류 미합의" ? <Link to={`/review/${row.id}`}>시뮬레이터 ↗</Link> : null}</td>
          </tr>)}</tbody></table></div>
      </section>

      <section className="audit-section" aria-labelledby="audit-provenance-title">
        <div className="audit-section-head"><span>03 / PROVENANCE &amp; LIMITS</span><h2 id="audit-provenance-title">출처와 해석 범위</h2></div>
        <dl className="audit-provenance" data-testid="audit-provenance">
          <div><dt>스냅샷</dt><dd><code>{SNAPSHOT_PATH}</code></dd></div>
          <div><dt>스냅샷 SHA-256</dt><dd><code>{sha256 ?? "브라우저에서 계산 불가"}</code></dd></div>
          <div><dt>규칙집</dt><dd><code>{data.run.rule_pack_name}</code> · <code>{data.run.rule_pack_id}</code></dd></div>
          <div><dt>규칙집 SHA-256</dt><dd><code>{data.run.rule_pack_hash}</code></dd></div>
          <div><dt>모델</dt><dd>{data.run.model_ids.join(", ")} — {data.run.model_note}</dd></div>
          <div><dt>모델 바인딩 해시</dt><dd><code>{data.run.model_binding_hash ?? "기록 없음"}</code></dd></div>
        </dl>
        <p>모델은 추출과 태깅만 맡고 등급·라벨은 순수 Python 규칙엔진이 계산했습니다. 이 보고서는 {data.partial ? "선택 페이지만 처리한 부분 실행" : "문서 전체 실행"} 결과이며 미처리 {data.coverage.pages_unprocessed}쪽 · 판독 불가 {data.coverage.pages_unreadable}쪽은 검토되지 않았습니다. 연결된 근거 쪽수는 공시 안의 입증 연결이며 실제 환경 성과나 외부 인증을 보장하지 않습니다. 등급 기준은 <Link to="/guide">판정 안내</Link>에 원문 그대로 정리했습니다.</p>
      </section>
    </article>
  </main>;
}
