import { getElementLabel } from "../labels";

type BasisRef = {
  element_id?: string | null;
  source_section?: string;
  clause?: string | null;
  verification_status: "verified" | "unverified" | "unlicensed";
  rule_ids?: string[];
};

type SourceRef = {
  document_version_id: string;
  parse_manifest_id: string | null;
  page_num: number;
  bbox?: number[] | null;
  raw_text_sha256: string;
  quote: string;
};

type StatusRecord = {
  status: string;
  level?: string | null;
  provider?: string | null;
  statement_id?: string | null;
  metric_match?: string;
  period_match?: string;
  boundary_match?: string;
  evidence_refs?: SourceRef[];
};

type SafeHarborRecord =
  | { status: "not_run" }
  | {
      claim_id: string;
      applicable: boolean | null;
      category: string | null;
      checklist: unknown[];
      reasonable_basis_documented: boolean | null;
      legal_effect: "not_determined";
      mapping_status: "approved" | "unresolved";
      gap_ids: string[];
    };

type TagElement = {
  element_id: string;
  state: string;
  normalized_value: string | null;
  evidence_refs: SourceRef[];
};

type ReportClaim = {
  claim_id: string;
  claim_quote?: string | null;
  classification_review?: { origin: string; track: string; revision: number } | null;
  tag_elements?: TagElement[] | null;
  tag_revision: number;
  decision_revision: number;
  decision_status: string;
  evidence_grade: string | null;
  label: string | null;
  grade_range?: { floor: string; ceiling: string; open_elements: string[] } | null;
  review_status: string;
  missing_elements: string[];
  unresolved_elements: string[];
  gap_ids: string[];
  rule_pack_sha256: string | null;
  model_sha256: string | null;
  prompt_sha256: string | null;
  replicate_hashes: string[];
  source_refs: SourceRef[];
  source_status: string;
  basis_refs: BasisRef[];
  assurance: StatusRecord;
  safe_harbor: SafeHarborRecord;
  suggestion: string | null;
  review_action?: {
    claim_id: string;
    reasons: string[];
    checks: string[];
    unresolved_elements: string[];
    gap_ids: string[];
    source_pages: number[];
  } | null;
};

export type ReportModel = {
  schema: "report_model_v1";
  tenant_id: string;
  run_id: string;
  document_version_id: string;
  parse_manifest_id: string;
  source_sha256: string;
  snapshot_epoch: number;
  generated_at: string;
  execution_profile: string;
  rule_pack_hashes: string[];
  coverage: {
    pages_total: number;
    pages_processed: number;
    pages_unreadable: number;
    pages_unprocessed: number;
    chunks_discovered: number;
    chunks_processed: number;
    claims_discovered: number;
    claims_decided: number;
    claims_needs_review: number;
    full_scope: boolean;
    complete: boolean;
  };
  unverified_basis: number;
  partial: boolean;
  unfinished_count: number;
  unverified_clause_count: number;
  claims: ReportClaim[];
};

const decisionText: Record<string, string> = {
  blocked_evidence: "근거 불확실로 미판정",
  blocked_rule_gap: "규칙 공백으로 미판정",
  not_applicable: "적용 제외",
  not_run: "미실행",
};

const assuranceText: Record<string, string> = {
  covered: "보증 범위 안",
  not_covered: "보증 범위 밖",
  undetermined: "보증 범위 미확정",
  not_run: "보증 확인 미실행",
};

const reviewStatusText: Record<string, string> = {
  auto_confirmed: "자동 확인",
  needs_review: "검토 필요",
  human_confirmed: "사람 확인",
  ai_delegated_confirmed: "AI 검토(위임·사람 아님)",
};

const elementStateText: Record<string, string> = {
  present: "있음",
  absent: "없음",
  unknown: "미상",
  conflict: "불일치",
  not_applicable: "해당 없음",
};

const basisStatusText: Record<string, string> = {
  verified: "확인됨",
  unverified: "미확인",
  unlicensed: "원문 이용권 미확보",
};

const labelScope: Record<string, string> = {
  SUBSTANTIATED: "이 공시 안의 근거 충족",
  INCOMPLETE: "이 공시 안의 근거 일부 부족",
  UNSUBSTANTIATED: "이 공시 안에서 근거 미확인",
};

// Each non-decided status is a different kind of unfinished work with a different next step.
const unfinishedReasons: [status: string, text: string, action: string][] = [
  ["not_run", "판정 미실행", "태깅·판정 단계가 끝나지 않았습니다. 주장 상세에서 미완료 단계를 진행하세요."],
  ["blocked_evidence", "근거 불확실로 미판정", "원문 근거 귀속을 확인하세요. 결손(absent)으로 단정하지 않습니다."],
  ["blocked_rule_gap", "규칙 공백으로 미판정", "확정되지 않은 규칙 항목 때문입니다. 태깅 수정만으로 해소되지 않습니다."],
  ["not_applicable", "적용 제외", "판정 대상에서 제외된 주장입니다. 제외 사유가 맞는지 확인하세요."],
];

const reviewActionText: Record<string, string> = {
  not_processed: "판정 미실행",
  unresolved_evidence: "미해결 근거 확인",
  source_location_missing: "원문 위치 확보",
  basis_validation_pending: "기준 조항 대응 확인",
  assurance_not_run: "보증 대조 미실행",
  safe_harbor_not_run: "세이프하버 점검 미실행",
  domain_gap: "미확정 규칙 항목 검토",
};

const withCode = (labels: Record<string, string>, code: string) =>
  labels[code] ? `${labels[code]} (${code})` : code;

function partialReasons(report: ReportModel): string[] {
  const { coverage } = report;
  const claimsUnprocessed = coverage.claims_discovered - coverage.claims_decided - coverage.claims_needs_review;
  const counts = new Map<string, number>();
  for (const claim of report.claims) {
    if (claim.decision_status !== "decided") counts.set(claim.decision_status, (counts.get(claim.decision_status) ?? 0) + 1);
  }
  const known = new Set(unfinishedReasons.map(([status]) => status));
  const reasons = [
    !coverage.full_scope ? "사용자가 지정한 부분 범위만 분석했습니다. 문서 전체 결론이 아닙니다." : null,
    coverage.pages_unreadable > 0
      ? `판독 불가 페이지 ${coverage.pages_unreadable}쪽 · 원문을 확인하세요. 근거 부재가 아닙니다.`
      : null,
    coverage.full_scope && coverage.pages_unprocessed > 0 ? `미처리 페이지 ${coverage.pages_unprocessed}쪽` : null,
    coverage.chunks_discovered > coverage.chunks_processed
      ? `미처리 청크 ${coverage.chunks_discovered - coverage.chunks_processed}개`
      : null,
    claimsUnprocessed > 0 ? `판정 단계에 도달하지 않은 주장 ${claimsUnprocessed}건` : null,
    ...unfinishedReasons.map(([status, text, action]) =>
      counts.get(status) ? `${text} ${counts.get(status)}건 · ${action}` : null,
    ),
    ...[...counts].filter(([status]) => !known.has(status)).map(([status, count]) => `${status} ${count}건`),
    report.unverified_clause_count > 0
      ? `기준 조항 미확인 ${report.unverified_clause_count}건 · 승인된 기준 원문과의 대응을 확인해야 최종본이 됩니다.`
      : null,
  ].filter((item): item is string => item !== null);
  if (!reasons.length && !coverage.complete) reasons.push("실행 범위가 완료 상태로 기록되지 않았습니다.");
  return reasons;
}

function safeHarborText(record: SafeHarborRecord) {
  if ("status" in record) return "세이프하버 미실행";
  if (record.applicable === false) return "세이프하버 비대상 · 법적 효력 미판정";
  return record.mapping_status === "unresolved"
    ? "세이프하버 근거 기록 · 법적 효력 미판정"
    : "세이프하버 근거 기록";
}

export function ReportPreview({ report }: { report: ReportModel }) {
  const { coverage } = report;
  const reasons = report.partial ? partialReasons(report) : [];
  const followUps = new Map<string, number>();
  for (const code of report.claims.flatMap((claim) => claim.review_action?.reasons ?? [])) {
    followUps.set(code, (followUps.get(code) ?? 0) + 1);
  }
  return (
    <article aria-labelledby="report-heading">
      <h1 id="report-heading">감사 리포트</h1>
      <p role="status">{report.partial ? "검토용 부분 리포트" : "완료 리포트"}</p>
      <p>
        규칙엔진 판정과 검토 기록의 불변 스냅샷입니다. 법적 효력이나 외부 보증 의견이 아닙니다.
      </p>
      <dl>
        <dt>실행</dt>
        <dd>
          run {report.run_id} · 생성 {report.generated_at} · 실행 프로필 {report.execution_profile}
        </dd>
        <dt>규칙 팩</dt>
        <dd>{report.rule_pack_hashes.length ? report.rule_pack_hashes.join(", ") : "기록 없음"}</dd>
        <dt>스냅샷</dt>
        <dd>
          epoch {report.snapshot_epoch} · document {report.document_version_id} · parse{" "}
          {report.parse_manifest_id ?? "미발행"}
          {" · "}source {report.source_sha256}
        </dd>
        <dt>미완료</dt>
        <dd>미완료 {report.unfinished_count}건</dd>
        <dt>처리 범위</dt>
        <dd>
          {coverage.full_scope ? "문서 전체 범위" : "지정 부분 범위"} · 판독 불가 {coverage.pages_unreadable}쪽 · 미처리{" "}
          {coverage.pages_unprocessed}쪽 · 주장 발견 {coverage.claims_discovered}, 판정 {coverage.claims_decided}, 검토
          필요 {coverage.claims_needs_review}
        </dd>
        <dt>미확인 기준 근거</dt>
        <dd>{report.unverified_clause_count}건</dd>
      </dl>
      {reasons.length ? (
        <section aria-labelledby="partial-reasons-heading">
          <h2 id="partial-reasons-heading">부분 리포트인 이유</h2>
          <ul>{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </section>
      ) : null}
      {followUps.size ? (
        <p>
          후속 검토 안내:{" "}
          {[...followUps].map(([code, count]) => `${reviewActionText[code] ?? code} ${count}건`).join(" · ")}
        </p>
      ) : null}

      {report.claims.map((claim) => (
        <section key={claim.claim_id} aria-labelledby={`claim-${claim.claim_id}`}>
          <h2 id={`claim-${claim.claim_id}`}>주장 {claim.claim_id}</h2>
          <p>검토 대상 주장: {claim.claim_quote ?? "이전 스냅샷에 주장 문장이 저장되지 않았습니다"}</p>
          <p>
            판정: {claim.decision_status === "decided"
              ? `${claim.evidence_grade} / ${claim.label}${claim.label && labelScope[claim.label] ? ` · ${labelScope[claim.label]}` : ""}`
              : decisionText[claim.decision_status] ?? claim.decision_status}
          </p>
          {claim.grade_range ? (
            <p>
              가능 등급 범위: {claim.grade_range.floor} ~ {claim.grade_range.ceiling} (확정 등급 아님) · 확인하면
              범위가 좁혀지는 요소: {claim.grade_range.open_elements.map(getElementLabel).join(", ")}
            </p>
          ) : null}
          <p>
            revision: tag {claim.tag_revision} / decision {claim.decision_revision} · 검토:{" "}
            {withCode(reviewStatusText, claim.review_status)}
          </p>
          {claim.classification_review ? <p>선행분류 기록: {claim.classification_review.origin === "ai_delegated_classification" ? "AI 위임 분류(사람 검토 아님)" : "사람 분류 검토"} · {claim.classification_review.track} · revision {claim.classification_review.revision} (등급 승인 아님)</p> : null}
          <p>규칙 팩: {claim.rule_pack_sha256 ?? "미실행"}</p>
          <p>
            모델: {claim.model_sha256 ?? "미실행"} · 프롬프트: {claim.prompt_sha256 ?? "미실행"} · replicas:{" "}
            {claim.replicate_hashes.length ? claim.replicate_hashes.join(", ") : "미실행"}
          </p>
          {claim.source_refs.length ? (
            <ul aria-label="원문 근거 위치">
              {claim.source_refs.map((source, index) => (
                <li key={`${source.page_num}-${index}`}>
                  p.{source.page_num}
                  {source.bbox ? ` [${source.bbox.join(", ")}]` : " 위치 좌표 미확인"}: {source.quote}
                  {" · "}source {source.raw_text_sha256}
                </li>
              ))}
            </ul>
          ) : (
            <p>원문 근거 위치 미실행</p>
          )}
          {claim.suggestion ? <p>수정 제안: {claim.suggestion}</p> : <p>확정된 수정 제안 없음</p>}
          {claim.review_action?.checks.length ? (
            <section aria-label="다음 검토 작업">
              <h3>다음 검토 작업</h3>
              <ul>{claim.review_action.checks.map((check, index) => <li key={index}>{check}</li>)}</ul>
            </section>
          ) : null}
          {claim.unresolved_elements.length ? (
            <p>미해결 요소: {claim.unresolved_elements.map(getElementLabel).join(", ")}</p>
          ) : null}
          {claim.tag_elements === undefined || claim.tag_elements === null ? (
            <p>태그 요소 미포함(이전 스냅샷)</p>
          ) : claim.tag_elements.length === 0 ? (
            <p>태그된 요소 없음(미태깅)</p>
          ) : (
            <section aria-label="태그 요소">
              <h3>태그 요소</h3>
              <ul>
                {claim.tag_elements.map((element) => (
                  <li key={element.element_id}>
                    {getElementLabel(element.element_id)}: {withCode(elementStateText, element.state)} · 값: {element.normalized_value ?? "기록 없음"}
                    {element.evidence_refs.length ? (
                      <ul>
                        {element.evidence_refs.map((source, index) => (
                          <li key={index}>
                            p.{source.page_num}: {source.quote}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>인용 없음</p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {claim.basis_refs.length ? (
            <ul aria-label="기준 근거">
              {claim.basis_refs.map((basis, index) => (
                <li key={`${basis.source_section ?? "basis"}-${index}`}>
                  {basis.clause && basis.verification_status === "verified"
                    ? basis.clause
                    : "조항 미확인"}
                  {basis.source_section ? ` · 원문 §${basis.source_section}` : ""} ·{" "}
                  {withCode(basisStatusText, basis.verification_status)}
                </li>
              ))}
            </ul>
          ) : (
            <p>기준 근거 미실행</p>
          )}
          <p>
            {assuranceText[claim.assurance.status] ?? claim.assurance.status}
            {claim.assurance.provider ? ` · 기관 ${claim.assurance.provider}` : ""}
            {claim.assurance.level ? ` · 수준 ${claim.assurance.level}` : ""}
            {claim.assurance.metric_match
              ? ` · 지표 ${claim.assurance.metric_match} / 기간 ${claim.assurance.period_match ?? "기록 없음"} / 경계 ${claim.assurance.boundary_match ?? "기록 없음"}`
              : ""}
          </p>
          <p>{safeHarborText(claim.safe_harbor)}</p>
        </section>
      ))}
    </article>
  );
}
