import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { StatusBadge } from "../../components/StatusBadge";
import { ApiError, errorMessage, isSessionError, requestJson } from "../session/api";
import type { RunSnapshot } from "./RunProgress";
import { latestWithResults, recentRunView, sortRecent, type RunPage } from "./runListView";

type Props = {
  apiBase?: string;
  tenantKey: string;
  onSessionInvalid: () => void;
  limit?: number;
};

// 서버는 한 쪽에 최대 100건을 준다. 목록이 매우 길 때 무한히 따라가지 않는다.
const PAGE_SIZE = 100;
const MAX_PAGES = 5;

type State =
  | { kind: "loading" }
  | { kind: "ready"; items: RunSnapshot[]; truncated: boolean }
  | { kind: "unavailable" }
  | { kind: "error"; message: string };

/** 현재 테넌트의 최근 실행. 새로고침 뒤에도 이전 분석 결과로 돌아갈 수 있게 한다. */
export function RecentRuns({ apiBase = "", tenantKey, onSessionInvalid, limit = 8 }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [epoch, setEpoch] = useState(0);
  const reload = useCallback(() => setEpoch(current => current + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    (async () => {
      const items: RunSnapshot[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < MAX_PAGES; page++) {
        const target = new URL(`${apiBase}/v1/runs`, window.location.origin);
        target.searchParams.set("limit", String(PAGE_SIZE));
        if (cursor) target.searchParams.set("cursor", cursor);
        const result: RunPage = await requestJson<RunPage>(target.toString(), { signal: controller.signal });
        items.push(...(result.items ?? []));
        cursor = result.next_cursor;
        if (!cursor) break;
      }
      if (!controller.signal.aborted) setState({ kind: "ready", items: sortRecent(items), truncated: cursor !== null });
    })().catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      if (isSessionError(reason)) return onSessionInvalid();
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 405)) return setState({ kind: "unavailable" });
      setState({ kind: "error", message: errorMessage(reason, "최근 분석 목록을 불러오지 못했습니다.") });
    });
    return () => controller.abort();
  }, [apiBase, epoch, onSessionInvalid, tenantKey]);

  if (state.kind === "unavailable") return null;
  return <section aria-labelledby="recent-runs-heading" className="recent-runs">
    <h2 id="recent-runs-heading">최근 분석</h2>
    {state.kind === "loading" ? <p role="status">최근 분석 목록을 불러오는 중입니다.</p> : null}
    {state.kind === "error" ? <div><p role="alert">{state.message}</p>
      <button type="button" onClick={reload} style={{ minHeight: 44 }}>다시 불러오기</button></div> : null}
    {state.kind === "ready" && state.items.length === 0 ? <p>
      이 작업 공간에는 아직 시작한 분석이 없습니다. 아래에서 기업과 공시 PDF를 등록하면 분석을 시작할 수 있고, 시작한 분석은 새로고침 뒤에도 여기에 남습니다.
    </p> : null}
    {state.kind === "ready" && state.items.length > 0 ? <RecentList items={state.items} truncated={state.truncated} limit={limit} onReload={reload} /> : null}
  </section>;
}

function RecentList({ items, truncated, limit, onReload }: { items: RunSnapshot[]; truncated: boolean; limit: number; onReload: () => void }) {
  const latest = latestWithResults(items);
  const shown = items.slice(0, limit);
  return <>
    {latest ? <RunResultCallout run={latest} /> : null}
    <ul aria-label="최근 분석 목록" style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
      {shown.map(run => {
        const view = recentRunView(run);
        return <li key={run.run_id} style={{ border: "1px solid #d0d7de", borderRadius: 6, padding: "10px 12px" }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
            <StatusBadge label={view.statusLabel} tone={view.tone} />
            <span>{formatTime(run.created_at)} 시작</span>
            <small style={{ color: "#555" }}>실행 <code>{run.run_id.slice(0, 8)}</code> · 문서 버전 <code>{run.document_version_id.slice(0, 8)}</code></small>
          </div>
          <p style={{ margin: "4px 0" }}>{view.coverageText}{view.reviewText ? ` · ${view.reviewText}` : ""} · <small>{view.scopeLabel}</small></p>
          <p style={{ margin: 0, display: "flex", gap: 16, flexWrap: "wrap" }}>
            <Link to={view.primary.href} style={{ fontWeight: view.resultsAvailable ? 700 : 400 }}>{view.primary.label}</Link>
            {view.links.map(link => <Link key={link.href} to={link.href}>{link.label}</Link>)}
          </p>
        </li>;
      })}
    </ul>
    <p style={{ fontSize: "0.9em", color: "#555" }}>
      {items.length > shown.length ? `전체 ${items.length}건 중 최근 ${shown.length}건을 표시합니다. ` : ""}
      {truncated ? `목록이 길어 앞선 ${items.length}건만 확인했습니다. ` : ""}
      <button type="button" onClick={onReload} style={{ minHeight: 32 }}>목록 새로고침</button>
    </p>
  </>;
}

// 주장이 기록된 실행의 결과 화면으로 가는 길. 새 문서 화면과 실행 진행 화면 맨 위에 둔다.
// 판정 미확정·검토 필요는 근거 부재가 아니므로 "완료"나 "없음"으로 줄이지 않는다.
export function RunResultCallout({ run }: { run: RunSnapshot }) {
  const view = recentRunView(run);
  if (!view.resultsAvailable) return null;
  return <aside aria-label="분석 결과 바로가기" className="run-result-callout"
    style={{ border: "2px solid #0d6efd", background: "#eef5ff", borderRadius: 8, padding: "12px 16px", margin: "8px 0 16px" }}>
    <p style={{ margin: "0 0 6px" }}><strong>분석 결과를 볼 수 있습니다.</strong> {view.statusLabel} · {view.coverageText}{view.reviewText ? ` · ${view.reviewText}` : ""}</p>
    <p style={{ margin: "0 0 8px", fontSize: "0.9em", color: "#444" }}>{view.scopeLabel}. 판정 미확정·검토 필요 주장은 근거 부재가 아니라 원문 확인이 남은 상태입니다.</p>
    <p style={{ margin: 0, display: "flex", gap: 16, flexWrap: "wrap" }}>
      <Link to={view.primary.href} style={{ fontWeight: 700 }}>{view.primary.label}</Link>
      {view.links.map(link => <Link key={link.href} to={link.href}>{link.label}</Link>)}
    </p>
  </aside>;
}

function formatTime(value: string): string {
  const time = Date.parse(value);
  return Number.isNaN(time) ? value : new Date(time).toLocaleString("ko-KR");
}
