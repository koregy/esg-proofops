// 공개 제출 데모의 저장된 NAVER 스냅샷(public/demo/naver-2025.json) 타입과 읽기 전용 표시 도우미.
// 등급·라벨은 저장된 규칙엔진 산출값만 표시하고, 없는 값을 다른 필드로 채우지 않는다.
export type Evidence = { page: number | null; quote: string };
export type SnapshotElement = { id: string; state: string; evidence: Evidence[] };
export type GradeRange = { floor: string; ceiling: string; open_elements: string[] };
export type SnapshotClaim = {
  id: string; page: number | null; track: string | null; quote: string; source_verified: boolean;
  elements: SnapshotElement[];
  decision: { grade: string | null; label: string | null; grade_range: GradeRange | null; status: string; missing: string[]; unresolved: string[] };
  review: { status: string; tag_revision: number; decision_revision: number; audit: string | null };
};
export type DemoSnapshot = {
  title: string; generated_at: string; partial: boolean;
  coverage: { pages_processed: number; pages_total: number; pages_unprocessed: number; pages_unreadable: number; claims_discovered: number; claims_decided: number; claims_needs_review: number };
  funnel: { label: string; count: number }[]; funnel_source: string;
  run: { model_ids: string[]; model_note: string; model_binding_hash: string | null; rule_pack_id: string; rule_pack_name: string; rule_pack_hash: string; r72_cost_usd: number; r72_paid_calls: number; r72_elapsed_seconds: Record<string, number>; r85_review_seconds: number; r85_model_calls: number };
  audit: { agreed: number; disagreed: number; uncertain: number; scope: string };
  claims: SnapshotClaim[];
};
export type LoadedSnapshot = { data: DemoSnapshot; sha256: string | null };

export const SNAPSHOT_PATH = "demo/naver-2025.json";
export const OFFICIAL_REPORT_URL = "https://www.navercorp.com/esg/esgReports";
export const trackText: Record<string, string> = { management: "관리체계", goal: "목표", performance: "성과" };
export const stateText: Record<string, string> = { present: "근거 확인", absent: "근거 부재", unknown: "확인 전", conflict: "근거 상충", unreadable: "판독 불가", not_applicable: "비적용" };
export const statusText: Record<string, string> = { decided: "규칙 판정", blocked_evidence: "근거 보류", blocked_rule_gap: "규칙 보류", not_run: "미판정" };

/** 저장된 등급 표시. 등급이 null이면 범위(확정 아님) 또는 미판정으로만 표시한다. */
export function storedGradeText(claim: SnapshotClaim): string {
  if (claim.decision.grade) return claim.decision.grade;
  const range = claim.decision.grade_range;
  return range ? `${range.floor}–${range.ceiling} 가능 범위` : "미판정";
}

export async function loadSnapshot(signal: AbortSignal): Promise<LoadedSnapshot> {
  const response = await fetch(`${import.meta.env.BASE_URL}${SNAPSHOT_PATH}`, { signal });
  if (!response.ok) throw new Error("snapshot unavailable");
  const bytes = await response.arrayBuffer();
  const text = new TextDecoder().decode(bytes);
  let sha256: string | null = null;
  try {
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    sha256 = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  } catch { /* 보안 컨텍스트가 아니면 해시를 표시하지 않는다 */ }
  return { data: JSON.parse(text) as DemoSnapshot, sha256 };
}
