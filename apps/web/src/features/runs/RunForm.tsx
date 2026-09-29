import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, isSessionError, requestJson } from "../session/api";
import type { RuntimeOptions, UploadSelection } from "../upload/CompanySelector";
import type { ReadyDocumentVersion } from "../upload/UploadForm";
import type { RunSnapshot } from "./RunProgress";

type Preflight = {
  ready: boolean;
  checks: Array<{ name: string; status: "pass" | "fail" | "not_run"; reason: string }>;
  binding_sha256: string | null;
  checked_at: string;
};

type LocalSubmission = {
  worker_enabled: boolean;
  candidate_rule_pack_id: string | null;
  selected_pages: number[];
};

type ScopeProposal = {
  version_id: string;
  source_sha256: string;
  page_count: number;
  status: "candidate_only";
  full_scope_declared: false;
  apply_as: "declared_subset";
  method: string;
  proposed_pages: number[];
  claim_candidate_pages: number[];
  evidence_candidate_pages: number[];
  unknown_pages: number[];
  conflict_pages: number[];
  other_candidate_pages: number[];
  issue_counts: Record<string, number>;
  map_sha256: string;
  policy_sha256: string;
};

const SHA256 = /^[0-9a-f]{64}$/;

const SCOPE_ERRORS: Record<string, string> = {
  SOURCE_SHA_MISMATCH: "선택한 문서 버전의 원본 해시가 요청과 다릅니다. 문서 버전을 다시 불러오세요.",
  UPLOAD_INTEGRITY_MISMATCH: "저장된 원본이 검증된 해시와 일치하지 않아 범위를 제안하지 않았습니다.",
  VERSION_NOT_READY: "검증이 끝난 문서 버전만 범위를 제안할 수 있습니다.",
  PAGE_COUNT_MISMATCH: "원본 페이지 수가 검증 기록과 달라 범위를 제안하지 않았습니다.",
  SECTION_SOURCE_TOO_LARGE: "범위 제안은 100MiB·500페이지 이하 문서만 지원합니다. 페이지를 직접 지정하세요.",
  SECTION_SOURCE_ENCRYPTED: "암호화된 PDF는 범위를 제안할 수 없습니다. 페이지를 직접 지정하세요.",
  SECTION_SOURCE_INVALID: "원본을 읽을 수 없어 범위를 제안하지 않았습니다.",
  SECTION_INSPECTION_FAILED: "PDF 구조를 읽지 못해 범위를 제안하지 않았습니다. 페이지를 직접 지정하세요.",
  SECTION_INSPECTION_TIMEOUT: "문서 구조 확인이 제한 시간을 넘어 중단했습니다. 같은 문서는 다시 시도해도 같을 수 있으니 페이지를 직접 지정하세요.",
  SECTION_INSPECTION_RESOURCE_LIMIT: "문서 구조 확인이 메모리·출력 한도를 넘어 중단했습니다. 페이지를 직접 지정하세요.",
  SECTION_INSPECTION_UNAVAILABLE: "범위 제안 작업자를 시작할 수 없습니다. 잠시 후 다시 시도하거나 페이지를 직접 지정하세요.",
  SCOPE_INSPECTION_BUSY: "다른 범위 제안을 계산하고 있습니다. 잠시 후 다시 시도하세요.",
  RATE_LIMITED: "범위 제안 요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
  SOURCE_UNAVAILABLE: "원본 파일을 읽을 수 없어 범위를 제안하지 않았습니다.",
  RESOURCE_NOT_FOUND: "이 문서 버전의 범위 제안을 사용할 수 없습니다. 페이지를 직접 지정하세요.",
};

function pinnedSha(version: ReadyDocumentVersion): string | null {
  // The ready version is the server's DocumentVersion; sha256 is its verified source identity.
  const value = (version as ReadyDocumentVersion & { sha256?: unknown }).sha256;
  return typeof value === "string" && SHA256.test(value) ? value : null;
}

function checkProposal(value: ScopeProposal, versionId: string, sha: string, total: number): ScopeProposal {
  const pages = (list: unknown) =>
    Array.isArray(list) && list.every((page, index) =>
      Number.isInteger(page) && page >= 1 && page <= total && (index === 0 || list[index - 1] < page));
  if (value.version_id !== versionId || value.source_sha256 !== sha || value.page_count !== total ||
      value.status !== "candidate_only" || value.full_scope_declared !== false || value.apply_as !== "declared_subset" ||
      ![value.proposed_pages, value.claim_candidate_pages, value.evidence_candidate_pages, value.unknown_pages,
        value.conflict_pages, value.other_candidate_pages].every(pages)) {
    throw new Error("범위 제안이 선택한 문서 버전·원본 해시와 일치하지 않아 사용하지 않았습니다.");
  }
  return value;
}

function pageSummary(pages: number[]): string {
  if (!pages.length) return "없음";
  const ranges: string[] = [];
  let start = pages[0];
  for (let index = 1; index <= pages.length; index += 1) {
    if (index === pages.length || pages[index] !== pages[index - 1] + 1) {
      const end = pages[index - 1];
      ranges.push(start === end ? `${start}` : `${start}–${end}`);
      start = pages[index];
    }
  }
  return `${ranges.join(", ")} (${pages.length}쪽)`;
}

type Props = {
  apiBase?: string;
  csrfToken: string;
  tenantKey: string;
  version: ReadyDocumentVersion;
  selection: UploadSelection;
  options: RuntimeOptions;
  canRun: boolean;
  onRunCreated: (run: RunSnapshot) => void;
  onSessionInvalid: () => void;
};

function parsePages(value: string, total: number): number[] {
  const parts = value.split(",").map((part) => part.trim());
  if (!value.trim() || parts.some((part) => !/^[1-9][0-9]*$/.test(part))) {
    throw new Error("분석할 페이지를 쉼표로 구분해 입력해 주세요. 예: 1, 3, 5");
  }
  const pages = parts.map(Number);
  if (pages.some((page) => page > total)) {
    throw new Error(`실제 문서 범위인 1~${total}페이지만 선택할 수 있습니다.`);
  }
  if (new Set(pages).size !== pages.length || pages.some((page, index) => index > 0 && pages[index - 1] > page)) {
    throw new Error("페이지는 중복 없이 오름차순으로 입력해 주세요.");
  }
  return pages;
}

export function RunForm({
  apiBase = "",
  csrfToken,
  tenantKey,
  version,
  selection,
  options,
  canRun,
  onRunCreated,
  onSessionInvalid,
}: Props) {
  const [mode, setMode] = useState<"disclosure" | "advertising">("disclosure");
  const [scope, setScope] = useState<"full" | "declared_subset">("full");
  const [pageText, setPageText] = useState("");
  const [rulePackId, setRulePackId] = useState("");
  const [local, setLocal] = useState<LocalSubmission | null>(null);
  const [localLoading, setLocalLoading] = useState(true);
  const [localError, setLocalError] = useState(false);
  const [phase, setPhase] = useState("준비된 문서 버전의 분석 범위를 확인해 주세요.");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [proposal, setProposal] = useState<ScopeProposal | null>(null);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [appliedPages, setAppliedPages] = useState<string | null>(null);
  const proposalController = useRef<AbortController | null>(null);
  const sourceSha = pinnedSha(version);
  const controller = useRef<AbortController | null>(null);
  const request = useRef<{ signature: string; preflightKey: string; runKey: string } | null>(null);
  const inFlight = useRef(false);
  const activePacks = options.rule_packs.filter(
    (pack) => pack.status === "active" && pack.mode === mode,
  );
  const advertisingReady = options.enabled_modes.includes("advertising") &&
    options.rule_packs.some((pack) => pack.status === "active" && pack.mode === "advertising");

  useEffect(() => {
    const request = new AbortController();
    setLocal(null); setLocalLoading(true); setLocalError(false);
    requestJson<LocalSubmission>(`${apiBase}/local/submission`, { signal: request.signal })
      .then(value => {
        if (request.signal.aborted) return;
        if (typeof value.worker_enabled !== "boolean" ||
            !(value.candidate_rule_pack_id === null || typeof value.candidate_rule_pack_id === "string") ||
            !Array.isArray(value.selected_pages) || !value.selected_pages.every(p => Number.isInteger(p) && p > 0)) {
          throw new Error("로컬 실행 설정을 확인할 수 없습니다.");
        }
        setLocal(value);
        if (value.selected_pages.length) {
          setScope("declared_subset"); setPageText(value.selected_pages.join(","));
        }
      })
      .catch(reason => {
        if (request.signal.aborted) return;
        if (isSessionError(reason)) onSessionInvalid();
        if (!(reason instanceof ApiError && reason.status === 404)) setLocalError(true);
      })
      .finally(() => { if (!request.signal.aborted) setLocalLoading(false); });
    return () => request.abort();
  }, [apiBase, tenantKey, version.version_id, onSessionInvalid]);

  const candidatePackId = mode === "disclosure" ? local?.candidate_rule_pack_id : null;
  const localBlocked = localLoading || localError || local?.worker_enabled === false;
  useEffect(() => {
    if (rulePackId === candidatePackId && candidatePackId) return;
    if (!activePacks.some((pack) => pack.rule_pack_id === rulePackId)) {
      setRulePackId(activePacks[0]?.rule_pack_id ?? candidatePackId ?? "");
    }
  }, [activePacks, candidatePackId, rulePackId]);

  useEffect(() => {
    controller.current?.abort();
    inFlight.current = false;
    request.current = null;
    setMode("disclosure");
    setScope("full");
    setPageText("");
    setError(null);
    setBusy(false);
    setPhase("준비된 문서 버전의 분석 범위를 확인해 주세요.");
    proposalController.current?.abort();
    setProposal(null);
    setProposalBusy(false);
    setProposalError(null);
    setAppliedPages(null);
    return () => {
      controller.current?.abort();
      proposalController.current?.abort();
    };
  }, [tenantKey, version.version_id]);

  const inspectScope = async () => {
    if (proposalBusy || busy) return;
    if (!sourceSha || version.page_count === null) {
      setProposalError("원본 해시와 페이지 수를 확인한 문서 버전만 범위를 제안할 수 있습니다.");
      return;
    }
    const abortController = new AbortController();
    proposalController.current?.abort();
    proposalController.current = abortController;
    setProposalBusy(true);
    setProposalError(null);
    setProposal(null);
    setAppliedPages(null);
    try {
      const params = new URLSearchParams({ expected_sha256: sourceSha });
      const value = await requestJson<ScopeProposal>(
        `${apiBase}/v1/versions/${encodeURIComponent(version.version_id)}/scope-proposal?${params}`,
        { signal: abortController.signal },
      );
      if (abortController.signal.aborted) return;
      setProposal(checkProposal(value, version.version_id, sourceSha, version.page_count));
    } catch (reason: unknown) {
      if (abortController.signal.aborted) return;
      if (isSessionError(reason)) {
        onSessionInvalid();
        return;
      }
      setProposalError(
        (reason instanceof ApiError ? SCOPE_ERRORS[reason.code] : undefined) ??
          errorMessage(reason, "범위 제안을 계산하지 못했습니다. 페이지를 직접 지정할 수 있습니다."),
      );
    } finally {
      if (!abortController.signal.aborted) setProposalBusy(false);
    }
  };

  const applyProposal = () => {
    if (!proposal || !proposal.proposed_pages.length) return;
    const text = proposal.proposed_pages.join(", ");
    setScope("declared_subset");
    setPageText(text);
    setAppliedPages(text);
    setError(null);
    setPhase("제안 페이지를 지정 범위로 넣었습니다. 검토 후 필요한 페이지를 추가·삭제하세요.");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (inFlight.current || localBlocked) return;
    if (!canRun) {
      setError("분석 전 사전 점검은 관리자 권한이 필요합니다.");
      return;
    }
    if (version.page_count === null) {
      setError("서버가 문서 페이지 수를 확인한 뒤 분석을 시작할 수 있습니다.");
      return;
    }
    if (!selection.consentProfileId || !selection.runtimeBindingId || !rulePackId) {
      setError("승인된 동의·실행 환경과 사용할 규칙집을 선택해 주세요.");
      return;
    }
    let pages: number[] | undefined;
    try {
      pages = scope === "declared_subset" ? parsePages(pageText, version.page_count) : undefined;
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "분석 페이지를 확인해 주세요.");
      return;
    }

    const signature = JSON.stringify({
      version: version.version_id,
      mode,
      scope,
      pages,
      rulePackId,
      consentProfileId: selection.consentProfileId,
      runtimeBindingId: selection.runtimeBindingId,
    });
    if (request.current?.signature !== signature) {
      request.current = {
        signature,
        preflightKey: crypto.randomUUID(),
        runKey: crypto.randomUUID(),
      };
    }
    const abortController = new AbortController();
    controller.current?.abort();
    controller.current = abortController;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      setPhase("승인된 실행 환경과 데이터 처리 동의를 사전 점검하는 중입니다.");
      const preflight = await requestJson<Preflight>(`${apiBase}/v1/preflight`, {
        method: "POST",
        signal: abortController.signal,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": request.current.preflightKey,
        },
        body: JSON.stringify({
          runtime_binding_id: selection.runtimeBindingId,
          consent_profile_id: selection.consentProfileId,
          include_live_model_probe: false,
        }),
      });
      if (!preflight.ready) {
        setError("사전 점검을 통과하지 못했습니다. 관리자에게 승인 구성을 확인해 달라고 요청하세요.");
        setPhase("분석 실행을 시작하지 않았습니다.");
        return;
      }
      setPhase("사전 점검을 통과했습니다. 분석 실행을 등록하는 중입니다.");
      const run = await requestJson<RunSnapshot>(`${apiBase}/v1/runs`, {
        method: "POST",
        signal: abortController.signal,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": request.current.runKey,
        },
        body: JSON.stringify({
          document_version_id: version.version_id,
          mode,
          scope,
          ...(pages ? { selected_pages: pages } : {}),
          rule_pack_id: rulePackId,
          consent_profile_id: selection.consentProfileId,
          runtime_binding_id: selection.runtimeBindingId,
        }),
      });
      request.current = null;
      setPhase("분석 실행이 등록되었습니다.");
      onRunCreated(run);
    } catch (reason: unknown) {
      if (abortController.signal.aborted) return;
      if (isSessionError(reason)) {
        onSessionInvalid();
        return;
      }
      setError(errorMessage(reason, "분석 실행을 시작하지 못했습니다. 같은 설정으로 다시 시도해 주세요."));
      setPhase("분석 실행을 시작하지 않았습니다.");
    } finally {
      if (!abortController.signal.aborted) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  };

  return (
    <form onSubmit={submit} aria-label="문서 분석 실행" style={{ marginTop: 32 }}>
      <fieldset disabled={busy} aria-busy={busy} style={{ display: "grid", gap: 8 }}>
        <legend>분석 실행</legend>
        <p>준비된 문서: {version.page_count === null ? "페이지 수 확인 중" : `${version.page_count}페이지`}</p>
        <label htmlFor="run-mode">검토 모드</label>
        <select id="run-mode" value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
          <option value="disclosure">공시 검토</option>
          <option value="advertising" disabled={!advertisingReady}>광고 문구 검토</option>
        </select>
        {!advertisingReady ? <p role="status">광고 문구 검토는 승인된 전용 규칙집이 없어 시작할 수 없습니다.</p> : null}

        {localLoading ? <p role="status">실행 가능 상태를 확인하고 있습니다.</p> : null}
        {localError ? <p role="alert">로컬 실행 상태를 확인하지 못했습니다. 화면을 새로고침해 주세요.</p> : null}
        {local?.worker_enabled === false ? <p role="status">현재는 저장 결과 열람 모드입니다. 분석 워커를 켠 뒤 새 분석을 시작할 수 있습니다.</p> : null}
        <label htmlFor="rule-pack">규칙집</label>
        <select id="rule-pack" value={rulePackId} onChange={(event) => setRulePackId(event.target.value)} disabled={activePacks.length === 0 && !candidatePackId} required>
          <option value="">현재 모드의 활성 규칙집을 선택하세요</option>
          {candidatePackId && !activePacks.some(pack => pack.rule_pack_id === candidatePackId) ? <option value={candidatePackId}>초안 기준 · 태깅만 수행 / 등급 보류</option> : null}
          {activePacks.map((pack) => <option key={pack.rule_pack_id} value={pack.rule_pack_id}>{pack.version}</option>)}
        </select>
        {activePacks.length === 0 && !candidatePackId ? <p role="status">현재 모드에 사용할 활성 규칙집이 없습니다.</p> : null}
        {rulePackId === candidatePackId && candidatePackId ? <p role="status">초안은 추출·태깅 참고용입니다. 승인 전에는 등급과 검토 수정 확정을 보류합니다.</p> : null}

        <label htmlFor="run-scope">분석 범위</label>
        <select id="run-scope" value={scope} onChange={(event) => setScope(event.target.value as typeof scope)}>
          <option value="full">문서 전체</option>
          <option value="declared_subset">지정한 페이지만</option>
        </select>
        <section aria-label="환경 범위 제안" aria-busy={proposalBusy} style={{ display: "grid", gap: 6, padding: 8, border: "1px solid #ccc", borderRadius: 6 }}>
          <button type="button" onClick={inspectScope} disabled={proposalBusy || !sourceSha || version.page_count === null} aria-describedby="scope-proposal-help" style={{ minHeight: 44 }}>
            {proposalBusy ? "문서 구조 확인 중…" : "환경(E) 범위 후보 찾기"}
          </button>
          <p id="scope-proposal-help">서버에 저장된 이 버전의 원본 목차·제목만 읽어 후보를 제안합니다. 모델을 호출하지 않으며, 큰 문서는 몇 분 걸릴 수 있습니다.</p>
          {!sourceSha ? <p role="status">원본 해시가 확인되지 않아 범위를 제안할 수 없습니다. 페이지를 직접 지정하세요.</p> : null}
          {proposalError ? <p role="alert">{proposalError}</p> : null}
          {proposal ? (
            <div role="region" aria-label="범위 제안 결과">
              <p><strong>후보일 뿐입니다.</strong> 전체 범위 완료나 누락 페이지의 근거 부재를 뜻하지 않습니다.</p>
              <ul>
                <li>제안 페이지: {pageSummary(proposal.proposed_pages)}</li>
                <li>환경 본문 후보: {pageSummary(proposal.claim_candidate_pages)}</li>
                <li>근거(데이터·부록) 후보: {pageSummary(proposal.evidence_candidate_pages)}</li>
                <li>미확정 페이지: {pageSummary(proposal.unknown_pages)}</li>
                <li>분류 충돌 페이지: {pageSummary(proposal.conflict_pages)}</li>
              </ul>
              <p>탐지 방법 {proposal.method} · 원본 SHA-256 {proposal.source_sha256.slice(0, 12)}… · 지도 {proposal.map_sha256.slice(0, 12)}…</p>
              {proposal.unknown_pages.length || proposal.conflict_pages.length ? (
                <p role="status">미확정·충돌 페이지는 자동으로 넣지 않았습니다. 필요하면 검토 후 직접 추가하세요.</p>
              ) : null}
              {proposal.proposed_pages.length ? (
                <button type="button" onClick={applyProposal} style={{ minHeight: 44 }}>제안 페이지를 지정 범위로 적용</button>
              ) : (
                <p role="status">환경 후보 페이지를 찾지 못했습니다. 환경 내용이 없다는 뜻이 아니므로 페이지를 직접 지정하세요.</p>
              )}
            </div>
          ) : null}
        </section>
        {scope === "declared_subset" ? (
          <>
            <label htmlFor="selected-pages">분석할 실제 PDF 페이지</label>
            <input
              id="selected-pages"
              value={pageText}
              onChange={(event) => setPageText(event.target.value)}
              inputMode="numeric"
              pattern="[0-9, ]+"
              placeholder="예: 1, 3, 5"
              aria-describedby="selected-pages-help"
              required
            />
            <p id="selected-pages-help">PDF 파일의 1부터 {version.page_count ?? "확인된 마지막"} 페이지까지, 중복 없이 오름차순으로 입력하세요.</p>
            {appliedPages !== null ? (
              <p role="status">{pageText === appliedPages ? "자동 제안을 그대로 사용 중입니다. 지정 범위 분석이며 문서 전체 분석이 아닙니다." : "검토자가 자동 제안을 수정했습니다. 입력한 페이지만 분석합니다."}</p>
            ) : null}
          </>
        ) : null}
        <button type="submit" disabled={localBlocked || !canRun || version.page_count === null || !rulePackId || !selection.consentProfileId || !selection.runtimeBindingId} style={{ minHeight: 44 }}>
          {busy ? "확인 중…" : "사전 점검 후 분석 시작"}
        </button>
      </fieldset>
      {!canRun ? <p role="status">분석 전 사전 점검과 실행 시작은 관리자 권한이 필요합니다.</p> : null}
      <p role="status" aria-live="polite">{phase}</p>
      {error ? <p role="alert">{error}</p> : null}
    </form>
  );
}
