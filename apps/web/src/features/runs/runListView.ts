// 최근 실행 목록 표시 규칙. GET /v1/runs 응답(Run)만 읽고 판정·등급을 새로 계산하지 않는다.
// 사용량·상태 문구는 서버 coverage 수치를 그대로 옮긴다. 검토 대기를 "완료"로 표시하지 않는다.
import type { RunSnapshot } from "./RunProgress";

export type RunPage = { items: RunSnapshot[]; next_cursor: string | null };

export type Tone = "neutral" | "success" | "warning" | "danger";

export type RecentRunView = {
  runId: string;
  statusLabel: string;
  tone: Tone;
  scopeLabel: string;
  coverageText: string;
  reviewText: string | null;
  /** 주장이 하나 이상 기록돼 주장·검토 화면에서 결과를 볼 수 있다. */
  resultsAvailable: boolean;
  primary: { href: string; label: string };
  links: Array<{ href: string; label: string }>;
};

const statusLabels: Record<RunSnapshot["status"], [string, Tone]> = {
  queued: ["대기 중", "neutral"],
  running: ["처리 중", "neutral"],
  partial: ["부분 완료", "warning"],
  completed: ["처리 완료", "success"],
  failed: ["실패", "danger"],
  cancelled: ["취소됨", "danger"],
};

/** 서버가 오래된 순으로 돌려줘도 최신 실행이 먼저 오게 정렬한다. 원본 배열은 바꾸지 않는다. */
export function sortRecent(items: RunSnapshot[]): RunSnapshot[] {
  return [...items].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.run_id.localeCompare(a.run_id));
}

export function recentRunView(run: RunSnapshot): RecentRunView {
  const c = run.coverage;
  const [statusLabel, tone] = statusLabels[run.status] ?? [run.status, "neutral"];
  const base = `/runs/${run.run_id}`;
  // 추출 중에는 주장 수가 먼저 늘어도 주장 조회가 아직 게시되지 않을 수 있다(409).
  // 결과 링크는 태깅 체크포인트를 지난 종료 상태(부분 완료·완료)에만 약속한다.
  const resultsAvailable = c.claims_discovered > 0 && (run.status === "partial" || run.status === "completed");
  const inFlight = run.status === "queued" || run.status === "running";
  const pending = c.claims_discovered - c.claims_decided;
  const reviewText = c.claims_discovered === 0 ? null
    : pending > 0 ? `판정 미확정 ${pending}건 · 검토 필요 ${c.claims_needs_review}건`
      : `판정 ${c.claims_decided}건 · 검토 필요 ${c.claims_needs_review}건`;
  const unreadable = c.pages_unreadable > 0 ? ` · 판독 불가 ${c.pages_unreadable}쪽` : "";
  const coverageText = `페이지 ${c.pages_processed}/${c.pages_total}쪽 처리${unreadable} · 주장 ${c.claims_discovered}건`;
  const scopeLabel = c.full_scope ? "전체 범위" : "부분 범위 · 문서 전체 결론 아님";
  const primary = resultsAvailable
    ? { href: `${base}/claims`, label: `주장 ${c.claims_discovered}건 결과 보기` }
    : { href: base, label: inFlight ? (c.claims_discovered > 0 ? `진행 상황 보기 · 주장 ${c.claims_discovered}건 처리 중` : "진행 상황 보기") : "실행 기록 보기" };
  const links = resultsAvailable
    ? [{ href: base, label: "진행·범위" }, { href: `${base}/reviews`, label: "검토 큐" }, { href: `${base}/report`, label: "보고서" }]
    : [];
  return { runId: run.run_id, statusLabel, tone, scopeLabel, coverageText, reviewText, resultsAvailable, primary, links };
}

/** 결과를 볼 수 있는 가장 최근 실행. 새 문서 화면 상단 안내에 쓴다. */
export function latestWithResults(items: RunSnapshot[]): RunSnapshot | null {
  return sortRecent(items).find(run => recentRunView(run).resultsAvailable) ?? null;
}
