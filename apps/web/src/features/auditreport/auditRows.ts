import { statusText, storedGradeText, trackText, type SnapshotClaim } from "./snapshot";

// 감사 보고서 행. 저장된 판정 필드를 그대로 옮기고, null 등급을 다른 값으로 채우지 않는다.
export type ReportRow = {
  id: string;
  page: number | null;
  quote: string;
  track: string;
  status: string;
  evidenceGrade: string | null;
  label: string | null;
  gradeText: string;
  range: string | null;
  sourceVerified: boolean;
  stateCounts: Record<string, number>;
  evidencePages: number[];
  tagRevision: number;
  decisionRevision: number;
  reviewStatus: string;
  audit: string | null;
};

export function shortQuote(value: string) {
  const chars = Array.from(value.replace(/\s+/g, " ").trim());
  return chars.length > 200 ? `${chars.slice(0, 199).join("")}…` : chars.join("");
}

export function reportRow(claim: SnapshotClaim): ReportRow {
  const stateCounts: Record<string, number> = {};
  for (const element of claim.elements) stateCounts[element.state] = (stateCounts[element.state] ?? 0) + 1;
  const evidencePages = [...new Set(claim.elements.filter(element => element.state === "present")
    .flatMap(element => element.evidence).map(ref => ref.page)
    .filter((page): page is number => typeof page === "number" && Number.isInteger(page) && page > 0))].sort((a, b) => a - b);
  const range = claim.decision.grade_range;
  return {
    id: claim.id,
    page: claim.page,
    quote: shortQuote(claim.quote),
    track: claim.track ? trackText[claim.track] ?? claim.track : "분류 미합의",
    status: claim.decision.status,
    evidenceGrade: claim.decision.grade,
    label: claim.decision.label,
    gradeText: storedGradeText(claim),
    range: range ? `${range.floor}–${range.ceiling}` : null,
    sourceVerified: claim.source_verified,
    stateCounts,
    evidencePages,
    tagRevision: claim.review.tag_revision,
    decisionRevision: claim.review.decision_revision,
    reviewStatus: claim.review.status,
    audit: claim.review.audit,
  };
}

export const statusLabel = (status: string) => statusText[status] ?? status;

export function csvCell(value: string | number | boolean | null) {
  const raw = value === null ? "" : String(value);
  const safe = /^[\s]*[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replace(/"/g, '""')}"`;
}
