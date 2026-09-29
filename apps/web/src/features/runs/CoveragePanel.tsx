export type Coverage = {
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

type Props = {
  status: "queued" | "running" | "partial" | "completed" | "failed" | "cancelled";
  coverage: Coverage;
};

// `coverage.complete` means every page/chunk/claim was processed, not that review finished:
// claims_needs_review can still be non-zero, so the headline never says "review complete".
function headline(status: Props["status"], coverage: Coverage): string {
  switch (status) {
    case "queued":
      return "대기 중 · 아직 처리한 범위가 없습니다.";
    case "running":
      return "처리 중 · 아래 수치는 현재까지의 중간값입니다.";
    case "failed":
      return "실행 실패 · 실패 시점 이후 영역은 처리되지 않았습니다. 실패 단계를 다시 시도하세요.";
    case "cancelled":
      return "취소됨 · 취소 시점 이후 영역은 처리되지 않았습니다.";
    case "partial":
      return "부분 완료 · 아래 남은 작업을 확인하세요.";
    case "completed":
      if (!coverage.full_scope) return "지정 범위 처리 완료 · 문서 전체 결론이 아닙니다.";
      return coverage.complete
        ? "전체 범위 처리 완료 · 검토가 남아 있어 판정 완료가 아닙니다."
        : "처리 완료로 기록됐지만 남은 영역이 있습니다.";
  }
}

export function CoveragePanel({ status, coverage }: Props) {
  const chunksUnprocessed = coverage.chunks_discovered - coverage.chunks_processed;
  const claimsUnprocessed =
    coverage.claims_discovered - coverage.claims_decided - coverage.claims_needs_review;
  const remaining = [
    !coverage.full_scope
      ? `사용자가 지정한 부분 범위만 분석했습니다. 범위 밖 ${coverage.pages_unprocessed}쪽은 미처리로 남습니다.`
      : null,
    coverage.pages_unreadable > 0
      ? `판독 불가 ${coverage.pages_unreadable}쪽 · 아래 원문 읽기 확인에서 원본을 확인하세요. 근거 부재가 아닙니다.`
      : null,
    coverage.full_scope && coverage.pages_unprocessed > 0
      ? `미처리 ${coverage.pages_unprocessed}쪽 · 실행이 이 페이지에 도달하지 않았습니다.`
      : null,
    chunksUnprocessed > 0 ? `미처리 청크 ${chunksUnprocessed}개 · 주장 추출이 끝나지 않은 구간입니다.` : null,
    coverage.claims_needs_review > 0
      ? `검토 필요 주장 ${coverage.claims_needs_review}건 · 검토 큐에서 근거를 확인해야 판정이 확정됩니다.`
      : null,
    claimsUnprocessed > 0 ? `미처리 주장 ${claimsUnprocessed}건 · 발견됐지만 판정 단계에 도달하지 않았습니다.` : null,
  ].filter((item): item is string => item !== null);
  const allDecided = status === "completed" && coverage.complete && remaining.length === 0;

  return (
    <section aria-labelledby="coverage-heading">
      <h2 id="coverage-heading">분석 범위</h2>
      {allDecided ? (
        <strong role="status">
          전체 범위 처리 완료 · 발견 주장 {coverage.claims_decided}건 모두 판정됨 (사람 검토 완료나 외부 보증을 뜻하지 않음)
        </strong>
      ) : (
        <p role="status">{headline(status, coverage)}</p>
      )}
      {remaining.length ? (
        <>
          <h3>남은 작업</h3>
          <ul aria-label="남은 작업">
            {remaining.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </>
      ) : null}
      <p>{coverage.full_scope ? "문서 전체 범위" : "사용자가 지정한 부분 범위"}</p>
      <p>
        판독 불가에는 일부 구간의 글자나 좌표를 확인하지 못한 페이지도 포함됩니다.
        확인된 다른 구간의 주장과 근거는 계속 검토할 수 있습니다.
      </p>
      <dl>
        <dt>페이지</dt>
        <dd>
          전체 {coverage.pages_total}, 처리(판독 불가 제외) {coverage.pages_processed}, 판독 불가{" "}
          {coverage.pages_unreadable}, 미처리 {coverage.pages_unprocessed}
        </dd>
        <dt>청크</dt>
        <dd>
          발견 {coverage.chunks_discovered}, 처리 {coverage.chunks_processed}, 미처리{" "}
          {chunksUnprocessed}
        </dd>
        <dt>주장</dt>
        <dd>
          발견 {coverage.claims_discovered}, 판정 {coverage.claims_decided}, 검토 필요{" "}
          {coverage.claims_needs_review}, 미처리 {claimsUnprocessed}
        </dd>
      </dl>
    </section>
  );
}
