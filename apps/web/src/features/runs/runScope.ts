// Pure scope/preflight helpers for RunForm. No imports so node can test this file directly.

export type RunScope = "full" | "declared_subset";

export type LocalSubmission = {
  worker_enabled: boolean;
  candidate_rule_pack_id: string | null;
  selected_pages: number[];
  // Optional local-only hints; an older local server omits them and keeps the prior behavior.
  supported_scopes?: RunScope[];
  scope_reason?: string;
  page_selection?: "explicit_required" | "saved_run_pages";
  required_pages?: number[];
};

export type PreflightCheck = { name: string; status: "pass" | "fail" | "not_run"; reason: string };

const pageList = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every(page => Number.isInteger(page) && page > 0);

export function checkLocalSubmission(value: LocalSubmission): LocalSubmission {
  const scopes = value.supported_scopes;
  if (typeof value.worker_enabled !== "boolean" ||
      !(value.candidate_rule_pack_id === null || typeof value.candidate_rule_pack_id === "string") ||
      !pageList(value.selected_pages) ||
      !(scopes === undefined || (Array.isArray(scopes) && scopes.length > 0 &&
        scopes.every(scope => scope === "full" || scope === "declared_subset"))) ||
      !(value.page_selection === undefined || value.page_selection === "explicit_required" ||
        value.page_selection === "saved_run_pages") ||
      !(value.required_pages === undefined || pageList(value.required_pages))) {
    throw new Error("로컬 실행 설정을 확인할 수 없습니다.");
  }
  return value;
}

/** Full scope is offered unless the local server says it is unsupported. */
export function fullScopeSupported(local: LocalSubmission | null): boolean {
  return !local?.supported_scopes || local.supported_scopes.includes("full");
}

/** Pages to prefill, never the bootstrap seed's CLI default. */
export function initialPages(local: LocalSubmission): number[] {
  return local.page_selection === "explicit_required" ? [] : local.selected_pages;
}

export function missingRequiredPages(local: LocalSubmission | null, pages: number[]): number[] {
  const chosen = new Set(pages);
  return (local?.required_pages ?? []).filter(page => !chosen.has(page));
}

const CHECK_LABELS: Record<string, string> = {
  runtime_approval: "실행 환경 승인",
  consent_approval: "데이터 처리 동의",
  supply_chain: "빌드·라이선스 검증",
};

/** Failed preflight checks as "label: server reason" lines; not_run never blocks readiness. */
export function preflightFailures(checks: PreflightCheck[] | undefined): string[] {
  if (!Array.isArray(checks)) return [];
  return checks
    .filter(check => check && check.status === "fail")
    .map(check => `${CHECK_LABELS[check.name] ?? check.name}${check.reason ? `: ${check.reason}` : ""}`);
}
