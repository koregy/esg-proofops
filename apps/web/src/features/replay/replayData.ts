import type { DemoSnapshot } from "../auditreport/snapshot";

// 저장된 스냅샷 수치를 재생 단계로 옮긴다. 재생 시간은 화면 연출용 고정값이며 실제 소요 시간이 아니다.
export const STAGE_PLAYBACK_MS = 1400;

export type ReplayStage = { key: string; title: string; detail: string; count: number; unit: string; durationMs: number };
export type ReplayModel = {
  title: string;
  generatedAt: string;
  stages: ReplayStage[];
  gradeCounts: [string, number][];
  ranges: number;
  notRun: number;
  otherBlocked: number;
  recorded: { label: string; seconds: number }[];
  paidCalls: number;
  costUsd: number;
  reviewModelCalls: number;
  stageNote: string;
};

export function buildReplayModel(data: DemoSnapshot): ReplayModel {
  const c = data.coverage;
  const stages: ReplayStage[] = [
    { key: "pages", title: "선택 페이지 파싱", detail: `전체 ${c.pages_total}쪽 중 선택 페이지 · 판독 불가 ${c.pages_unreadable}쪽 · 미처리 ${c.pages_unprocessed}쪽`, count: c.pages_processed, unit: "쪽", durationMs: STAGE_PLAYBACK_MS },
    ...data.funnel.map((step, index) => ({
      key: `funnel-${index}`, title: step.label, count: step.count, unit: "건", durationMs: STAGE_PLAYBACK_MS,
      detail: index === 0 ? "저장된 단계 기록의 첫 단계" : `${data.funnel[0].label} ${data.funnel[0].count}건 대비 ${Math.round(step.count / Math.max(1, data.funnel[0].count) * 100)}%`,
    })),
  ];
  const grades = new Map<string, number>();
  for (const claim of data.claims) if (claim.decision.grade) grades.set(claim.decision.grade, (grades.get(claim.decision.grade) ?? 0) + 1);
  const recordedLabels: Record<string, string> = { parse_extraction: "파싱·주장 추출", tagging: "관계·요소 태깅", local_postprocess: "로컬 후처리" };
  return {
    title: data.title,
    generatedAt: data.generated_at,
    stages,
    gradeCounts: [...grades.entries()].sort(([a], [b]) => b.localeCompare(a)),
    ranges: data.claims.filter(claim => !claim.decision.grade && claim.decision.grade_range).length,
    notRun: data.claims.filter(claim => claim.decision.status === "not_run").length,
    otherBlocked: data.claims.filter(claim => !claim.decision.grade && !claim.decision.grade_range && claim.decision.status !== "not_run").length,
    recorded: [
      ...Object.entries(data.run.r72_elapsed_seconds).map(([key, seconds]) => ({ label: `R72 ${recordedLabels[key] ?? key}`, seconds })),
      { label: "R85 위임 검토", seconds: data.run.r85_review_seconds },
    ],
    paidCalls: data.run.r72_paid_calls,
    costUsd: data.run.r72_cost_usd,
    reviewModelCalls: data.run.r85_model_calls,
    stageNote: data.funnel_source,
  };
}

export const replayDuration = (stages: ReplayStage[]) => stages.reduce((total, stage) => total + stage.durationMs, 0);

export function stageAt(stages: ReplayStage[], elapsedMs: number) {
  let start = 0;
  for (let index = 0; index < stages.length; index += 1) {
    const end = start + stages[index].durationMs;
    if (elapsedMs < end) return { index, fraction: Math.max(0, (elapsedMs - start) / stages[index].durationMs) };
    start = end;
  }
  return { index: stages.length, fraction: 1 };
}
