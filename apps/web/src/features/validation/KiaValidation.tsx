import { useEffect, useState } from "react";
import { Link } from "react-router";
import "./validation.css";

type Trial = {
  run_id: string; selected_pages: number[];
  ocr: { eligible: number; requested: number; skipped: number; corroborated: number };
  claims_discovered: number; claims_source_verified?: number; claims_needing_review?: number;
  tag_stage_status: string; tag_block_reasons?: Record<string, number>;
  offline_replay_with_network_blocked: { matches_recorded_result: boolean };
};
type Validation = {
  schema: string; recorded_date: string; source_sha256: string; source_pages: number;
  whole_report_complete: boolean; accuracy_evaluated: boolean; runs: Trial[];
};
const file = `${import.meta.env.BASE_URL}demo/kia-validation-20260929.json`;

export default function KiaValidation() {
  const [data, setData] = useState<Validation | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch(file, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error("unavailable");
      const result = await response.json() as Validation;
      if (result.schema !== "proofops-real-validation-summary-v1" || !Array.isArray(result.runs)) throw new Error("invalid snapshot");
      return result;
    }).then(setData).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, []);
  return <main className="static-main xd-page kia-validation">
    <div className="breadcrumb"><Link to="/">홈</Link><span>/</span> 기아 원문 검증</div>
    <p className="eyebrow">KIA · RECORDED VALIDATION</p>
    <h1>읽은 결과와 입증한 결과를 구분합니다</h1>
    <p className="xd-muted">기아 보고서로 실행한 Document Parse 이미지 대조와 Solar Pro 3 처리 기록입니다. 비공개 원문을 제외한 검증 수치만 보여 줍니다.</p>
    {failed ? <p role="alert">검증 기록을 불러오지 못했습니다. 새로고침해 주세요.</p> : !data ? <p role="status">검증 기록을 불러오는 중입니다…</p> : <>
      <p className="xd-banner" role="note"><strong>부분 검증</strong><span>선택한 쪽의 시험 결과입니다. 전체 {data.source_pages}쪽의 검증 완료나 기업의 입증 등급을 뜻하지 않습니다. 모델 정확도는 별도 평가 전입니다.</span></p>
      <div className="kia-trials">{data.runs.map(run => <section className="xd-card" key={run.run_id}>
        <p className="eyebrow">보고서 {run.selected_pages.join(" · ")}쪽</p>
        <h2>{run.selected_pages.length === 1 ? "목표 관련 페이지" : "보증 관련 페이지"}</h2>
        <p>대조 대상 {run.ocr.eligible}개 원문 블록 중 {run.ocr.requested}개를 이미지로 대조했습니다.</p>
        <div className="kia-bars" role="img" aria-label={`원문 일치 ${run.ocr.corroborated}개, 대조 후 보류 ${run.ocr.requested - run.ocr.corroborated}개, 미처리 ${run.ocr.skipped}개`}>
          <span className="matched" style={{ flex: run.ocr.corroborated }} />
          <span className="unresolved" style={{ flex: run.ocr.requested - run.ocr.corroborated }} />
          {run.ocr.skipped > 0 ? <span className="skipped" style={{ flex: run.ocr.skipped }} /> : null}
        </div>
        <dl className="kia-counts"><div><dt>원문 일치</dt><dd>{run.ocr.corroborated}</dd></div><div><dt>대조 후 보류</dt><dd>{run.ocr.requested - run.ocr.corroborated}</dd></div><div><dt>미처리</dt><dd>{run.ocr.skipped}</dd></div></dl>
        <p>문자·공백·읽기 순서가 맞지 않는 블록은 근거로 승인하지 않았습니다. 일치한 블록에도 짧은 표제가 포함되어 있습니다.</p>
        <h3>주장 처리</h3>
        <p>주장 {run.claims_discovered}개 발견{run.claims_source_verified !== undefined ? ` · 원문 검증 ${run.claims_source_verified}개` : ""}</p>
        <p>{run.tag_stage_status === "blocked" ? "태깅 보류: 원문 검증 또는 사전 분류가 필요합니다." : `검토 대기 ${run.claims_needing_review ?? run.claims_discovered}개 · 확정 등급 없음`}</p>
        <p className="xd-muted">{run.offline_replay_with_network_blocked.matches_recorded_result ? "네트워크를 차단한 재생에서도 저장된 원문 검증 결과가 일치했습니다." : "오프라인 재생 확인 전입니다."}</p>
        <details><summary>실행 식별자</summary><code>{run.run_id}</code></details>
      </section>)}</div>
      <section className="xd-card kia-source"><h2>어디까지 확인했나요?</h2><p>이 기록의 두 실행은 처리 경로를 확인하기 위한 시험입니다. 사업자 식별정보를 결속한 실제 기업 대사나 보증 범위 확정을 완료한 결과는 아닙니다.</p><p>원문 일치는 인용 가능성을 확인하는 단계입니다. 지표·기간·조직경계를 대조하고 검토를 마쳐야 보증 근거와 연결할 수 있습니다.</p><p>기록일 {data.recorded_date} · 원문 {data.source_pages}쪽</p><details><summary>원문 파일 해시</summary><code>{data.source_sha256}</code></details><p><a href={file} download="kia-validation-20260929.json">검증 수치 JSON 내려받기</a> · <Link to="/demo">NAVER 주장별 검토 결과 보기 →</Link></p></section>
    </>}
  </main>;
}
