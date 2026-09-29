import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, isSessionError, requestJson } from "../session/api";
import { ReportPreview, type ReportModel } from "./ReportPreview";

type ExportFormat = "json" | "csv" | "html";
type ExportSnapshot = {
  export_id: string;
  run_id: string;
  state: "queued" | "building" | "ready" | "failed";
  snapshot_epoch: number;
  partial: boolean;
  manifest_sha256: string | null;
  created_at: string;
};
type Download = { url: string; expires_at: string; sha256: string };
type ExportRequest = { formats: ExportFormat[]; allow_partial: boolean };
// The Export DTO does not echo the request, so formats are known only for exports created here.
type TrackedExport = { snapshot: ExportSnapshot; source: "created" | "known"; request?: ExportRequest };
type DownloadLink = Download & { href: string; usableUntil: number; reissues: number };
type Preview =
  | { state: "loading" }
  | { state: "ready"; report: ReportModel }
  | { state: "error"; message: string };

type Props = {
  apiBase?: string;
  csrfToken: string;
  tenantKey: string;
  runId: string;
  onSessionInvalid: () => void;
};

const formats = [
  ["json", "JSON"],
  ["csv", "CSV"],
  ["html", "HTML"],
] as const;
const terminalStates = new Set<ExportSnapshot["state"]>(["ready", "failed"]);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const pollIntervalMs = 500;
const maxPolls = 20;
const privateLinkMs = 5 * 60 * 1000;
const maxReportBytes = 32 * 1024 * 1024;
const formatLabel = Object.fromEntries(formats) as Record<ExportFormat, string>;

// A 4xx answer is the server's decision for this idempotency key; resending the same key replays
// the stored failure. Only transport, rate-limit and 5xx failures leave the outcome unknown.
function isDefinitive(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status >= 400 && reason.status < 500 && ![408, 429].includes(reason.status);
}

function exportFailureText(reason: unknown, fallback: string): string {
  if (!(reason instanceof ApiError)) {
    return reason instanceof TypeError
      ? "네트워크 오류로 결과를 확인하지 못했습니다. 다시 시도하면 같은 요청을 재전송하므로 중복 생성되지 않습니다."
      : errorMessage(reason, fallback);
  }
  switch (reason.code) {
    case "REPORT_NOT_FINALIZABLE":
      return "미완료 항목(미판정·검토 필요·미처리 범위·미확인 조항)이 있어 최종 내보내기를 만들 수 없습니다. 검토를 마친 뒤 다시 시도하거나, 검토용이면 부분 결과 허용을 선택하세요.";
    case "EXPORT_SIZE_LIMIT":
      return "내보내기 파일이 32MiB 한도를 넘었습니다. 근거와 이력은 잘라내지 않으므로, 선택한 형식을 줄여(예: JSON만) 다시 생성하세요. 매니페스트는 항상 포함됩니다.";
    case "EXPORT_INTEGRITY_FAILED":
      return "저장된 검토 기록의 무결성 확인에 실패해 내보내기를 만들지 않았습니다. 다시 시도해도 같은 결과일 수 있으니 실행 ID와 함께 관리자에게 알려 주세요.";
    case "EXPORT_NOT_READY":
      return "아직 준비되지 않은 export입니다. 상태를 다시 확인해 주세요.";
    case "RESOURCE_NOT_FOUND":
      return "현재 작업 공간에서 이 실행 또는 export를 찾을 수 없습니다.";
    case "VALIDATION_ERROR":
      return "내보내기 요청 형식이 올바르지 않습니다. 형식을 다시 선택해 주세요.";
  }
  if (reason.status === 429) return "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.";
  if (reason.status >= 500) {
    return "서버가 일시적으로 응답하지 못했습니다. 다시 시도하면 같은 요청을 재전송하므로 중복 생성되지 않습니다.";
  }
  return errorMessage(reason, fallback);
}

async function sha256Hex(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
}

// Reads one member of the export ZIP (stored or deflated) through its central directory.
async function readZipMember(bytes: Uint8Array<ArrayBuffer>, name: string): Promise<Uint8Array | null> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let end = -1;
  for (let at = bytes.length - 22; at >= Math.max(0, bytes.length - 22 - 0xffff); at -= 1) {
    if (view.getUint32(at, true) === 0x06054b50) { end = at; break; }
  }
  if (end < 0) throw new Error("ZIP 구조를 확인하지 못했습니다.");
  const decoder = new TextDecoder();
  let offset = view.getUint32(end + 16, true);
  for (let entry = 0; entry < view.getUint16(end + 10, true); entry += 1) {
    if (view.getUint32(offset, true) !== 0x02014b50) throw new Error("ZIP 목록이 손상되었습니다.");
    const method = view.getUint16(offset + 10, true);
    const compressedSize = view.getUint32(offset + 20, true);
    const size = view.getUint32(offset + 24, true);
    const nameLength = view.getUint16(offset + 28, true);
    const local = view.getUint32(offset + 42, true);
    const entryName = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLength));
    offset += 46 + nameLength + view.getUint16(offset + 30, true) + view.getUint16(offset + 32, true);
    if (entryName !== name) continue;
    if (size > maxReportBytes) throw new Error("report.json이 미리보기 한도를 넘습니다. ZIP을 내려받아 확인하세요.");
    if (view.getUint32(local, true) !== 0x04034b50) throw new Error("ZIP 항목이 손상되었습니다.");
    const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    const data = bytes.subarray(start, start + compressedSize);
    if (method === 0) return data;
    if (method !== 8) throw new Error("지원하지 않는 ZIP 압축 방식입니다.");
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  return null;
}

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(done, pollIntervalMs);
    function done() {
      signal.removeEventListener("abort", abort);
      resolve();
    }
    function abort() {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }
    signal.addEventListener("abort", abort, { once: true });
  });
}

function apiOrigin(apiBase: string): string {
  return new URL(apiBase || window.location.origin, window.location.origin).origin;
}

function trustedDownload(apiBase: string, value: string): string {
  const origin = apiOrigin(apiBase);
  const target = new URL(value, origin);
  if (target.origin !== origin || !["http:", "https:"].includes(target.protocol)) {
    throw new Error("다운로드 주소가 신뢰된 API origin과 다릅니다.");
  }
  return target.toString();
}

function clearExportQuery() {
  const page = new URL(window.location.href);
  page.searchParams.delete("export_id");
  window.history.replaceState(window.history.state, "", `${page.pathname}${page.search}${page.hash}`);
}

function stateText(state: ExportSnapshot["state"]): string {
  return { queued: "대기 중", building: "생성 중", ready: "준비됨", failed: "실패" }[state];
}

export function ExportWorkspace({ apiBase = "", csrfToken, tenantKey, runId, onSessionInvalid }: Props) {
  // Review drafts are explicit partial requests; API defaults and final-result checks are unchanged.
  const [selected, setSelected] = useState<ExportFormat[]>(["json", "csv", "html"]);
  const [allowPartial, setAllowPartial] = useState(true);
  const [tracked, setTracked] = useState<TrackedExport[]>([]);
  const [links, setLinks] = useState<Record<string, DownloadLink>>({});
  const [busy, setBusy] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [retryMode, setRetryMode] = useState<"resend" | "new" | null>(null);
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [clock, setClock] = useState(Date.now());
  const createRequest = useRef<{ signature: string; key: string } | null>(null);
  const workflow = useRef<AbortController | null>(null);
  const download = useRef<AbortController | null>(null);
  const preview = useRef<AbortController | null>(null);
  const previousScope = useRef<string | null>(null);

  const clearPrivateState = () => {
    workflow.current?.abort();
    download.current?.abort();
    preview.current?.abort();
    setTracked([]);
    setLinks({});
    setPreviews({});
    setRetryMode(null);
    setBusy(false);
    setDownloadBusy(null);
    createRequest.current = null;
  };

  const invalidateSession = () => {
    clearPrivateState();
    clearExportQuery();
    setMessage("세션이 만료되었습니다. 다시 로그인해 주세요.");
    onSessionInvalid();
  };

  const remember = (snapshot: ExportSnapshot, source: TrackedExport["source"], request?: ExportRequest) => {
    if (snapshot.run_id !== runId) throw new Error("현재 실행과 다른 export 응답은 표시할 수 없습니다.");
    setTracked(current => {
      const existing = current.find(item => item.snapshot.export_id === snapshot.export_id);
      const next: TrackedExport = {
        snapshot,
        source: existing?.source === "created" ? "created" : source,
        request: request ?? existing?.request,
      };
      return [next, ...current.filter(item => item.snapshot.export_id !== snapshot.export_id)];
    });
  };

  const poll = async (initial: ExportSnapshot, controller: AbortController, source: TrackedExport["source"]) => {
    let current = initial;
    for (let attempt = 0; attempt < maxPolls && !terminalStates.has(current.state); attempt += 1) {
      await pause(controller.signal);
      current = await requestJson<ExportSnapshot>(`${apiBase}/v1/exports/${current.export_id}`, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      remember(current, source);
    }
    if (!controller.signal.aborted && !terminalStates.has(current.state)) {
      setMessage("자동 상태 확인 시간이 끝났습니다. 알려진 export를 다시 확인해 주세요.");
    }
  };

  const trackUntilTerminal = async (
    initial: ExportSnapshot,
    controller: AbortController,
    source: TrackedExport["source"],
    request?: ExportRequest,
  ) => {
    if (controller.signal.aborted) return;
    remember(initial, source, request);
    await poll(initial, controller, source);
  };

  useEffect(() => () => {
    workflow.current?.abort();
    download.current?.abort();
    preview.current?.abort();
  }, []);

  useEffect(() => {
    const scope = `${tenantKey}:${runId}`;
    const scopeChanged = previousScope.current !== null && previousScope.current !== scope;
    previousScope.current = scope;
    clearPrivateState();
    setSelected(["json", "csv", "html"]);
    setAllowPartial(true);
    setMessage(null);

    const page = new URL(window.location.href);
    if (scopeChanged) {
      clearExportQuery();
      return;
    }
    const exportId = page.searchParams.get("export_id");
    if (!exportId || !uuid.test(exportId)) return;

    const controller = new AbortController();
    workflow.current = controller;
    setBusy(true);
    void requestJson<ExportSnapshot>(`${apiBase}/v1/exports/${exportId}`, { signal: controller.signal })
      .then(value => trackUntilTerminal(value, controller, "known"))
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        if (isSessionError(reason)) return invalidateSession();
        setMessage(errorMessage(reason, "알려진 export 상태를 불러오지 못했습니다."));
      })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
    // Props identify a new tenant/run workspace; callbacks intentionally use that snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, runId, tenantKey]);

  useEffect(() => {
    const nextExpiry = Object.values(links)
      .map(link => link.usableUntil)
      .filter(value => value > clock)
      .sort((left, right) => left - right)[0];
    if (!nextExpiry) return;
    const timer = window.setTimeout(() => setClock(Date.now()), Math.min(nextExpiry - clock + 25, privateLinkMs));
    return () => window.clearTimeout(timer);
  }, [clock, links]);

  const createExport = async (event?: FormEvent) => {
    event?.preventDefault();
    if (busy || selected.length === 0) return;
    const body: ExportRequest = { formats: formats.map(([value]) => value).filter(value => selected.includes(value)), allow_partial: allowPartial };
    const signature = JSON.stringify(body);
    if (createRequest.current?.signature !== signature) {
      createRequest.current = { signature, key: crypto.randomUUID() };
    }
    const controller = new AbortController();
    workflow.current?.abort();
    workflow.current = controller;
    setBusy(true);
    setMessage(null);
    setRetryMode(null);
    try {
      const value = await requestJson<ExportSnapshot>(`${apiBase}/v1/runs/${runId}/exports`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": createRequest.current.key,
        },
        body: signature,
      });
      if (controller.signal.aborted) return;
      if (value.run_id !== runId) throw new Error("현재 실행과 다른 export 응답은 표시할 수 없습니다.");
      createRequest.current = null;
      const page = new URL(window.location.href);
      page.searchParams.set("export_id", value.export_id);
      window.history.replaceState(window.history.state, "", `${page.pathname}${page.search}${page.hash}`);
      await trackUntilTerminal(value, controller, "created", body);
    } catch (reason: unknown) {
      if (controller.signal.aborted) return;
      if (isSessionError(reason)) return invalidateSession();
      // A definitive refusal is final for this key; the next attempt must be a new request so
      // it is evaluated against the run's current state instead of replaying the stored failure.
      const definitive = isDefinitive(reason);
      if (definitive) createRequest.current = null;
      setRetryMode(definitive ? "new" : "resend");
      setMessage(exportFailureText(reason, "내보내기를 생성하지 못했습니다."));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const refresh = async (snapshot: ExportSnapshot, source: TrackedExport["source"]) => {
    if (busy) return;
    const controller = new AbortController();
    workflow.current?.abort();
    workflow.current = controller;
    setBusy(true);
    setMessage(null);
    try {
      const value = await requestJson<ExportSnapshot>(`${apiBase}/v1/exports/${snapshot.export_id}`, {
        signal: controller.signal,
      });
      if (!controller.signal.aborted) await trackUntilTerminal(value, controller, source);
    } catch (reason: unknown) {
      if (controller.signal.aborted) return;
      if (isSessionError(reason)) return invalidateSession();
      setMessage(exportFailureText(reason, "export 상태를 다시 확인하지 못했습니다."));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const issueDownload = async (snapshot: ExportSnapshot, reissue: boolean) => {
    const existing = links[snapshot.export_id];
    if (snapshot.state !== "ready" || downloadBusy || (reissue && (!existing || existing.reissues >= 1 || existing.usableUntil > Date.now()))) return;
    const controller = new AbortController();
    download.current?.abort();
    download.current = controller;
    setDownloadBusy(snapshot.export_id);
    setMessage(null);
    try {
      const ticket = await requestJson<Download>(`${apiBase}/v1/exports/${snapshot.export_id}/download`, {
        method: "POST",
        signal: controller.signal,
        headers: { "X-CSRF-Token": csrfToken },
      });
      if (controller.signal.aborted) return;
      const expiresAt = Date.parse(ticket.expires_at);
      if (Number.isNaN(expiresAt)) throw new Error("다운로드 만료 시각이 올바르지 않습니다.");
      const now = Date.now();
      const href = trustedDownload(apiBase, ticket.url);
      setLinks(current => ({
        ...current,
        [snapshot.export_id]: {
          ...ticket,
          href,
          usableUntil: Math.min(expiresAt, now + privateLinkMs),
          reissues: reissue ? existing.reissues + 1 : 0,
        },
      }));
      setClock(now);
    } catch (reason: unknown) {
      if (controller.signal.aborted) return;
      if (isSessionError(reason)) return invalidateSession();
      setMessage(exportFailureText(reason, "비공개 다운로드 링크를 발급하지 못했습니다."));
    } finally {
      if (!controller.signal.aborted) setDownloadBusy(null);
    }
  };

  const loadPreview = async (snapshot: ExportSnapshot, link: DownloadLink) => {
    const exportId = snapshot.export_id;
    const controller = new AbortController();
    preview.current?.abort();
    preview.current = controller;
    setPreviews(current => ({ ...current, [exportId]: { state: "loading" } }));
    try {
      if (link.usableUntil <= Date.now()) throw new Error("다운로드 링크가 만료되었습니다. 링크를 다시 발급해 주세요.");
      const response = await fetch(link.href, { credentials: "include", signal: controller.signal });
      if (!response.ok) {
        if (response.status === 401) throw new ApiError(401, "SESSION_EXPIRED", "session");
        throw new Error(response.status === 403
          ? "다운로드 링크가 만료되었거나 유효하지 않습니다. 링크를 다시 발급해 주세요."
          : "export 파일을 불러오지 못했습니다. ZIP을 내려받아 확인해 주세요.");
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (!crypto.subtle) throw new Error("이 브라우저 환경에서는 SHA-256을 확인할 수 없어 미리보기를 표시하지 않습니다.");
      if (await sha256Hex(bytes) !== link.sha256) {
        throw new Error("받은 파일의 SHA-256이 다운로드 티켓과 다릅니다. 미리보기를 표시하지 않습니다.");
      }
      const member = await readZipMember(bytes, "report.json");
      if (!member) throw new Error("이 export에는 JSON 형식이 없어 미리보기를 할 수 없습니다. 내려받은 CSV·HTML을 확인하세요.");
      const report = JSON.parse(new TextDecoder().decode(member)) as ReportModel;
      if (report?.schema !== "report_model_v1" || report.run_id !== runId || !Array.isArray(report.claims)) {
        throw new Error("report.json이 현재 실행의 report_model_v1이 아니어서 표시하지 않습니다.");
      }
      if (!controller.signal.aborted) setPreviews(current => ({ ...current, [exportId]: { state: "ready", report } }));
    } catch (reason: unknown) {
      if (controller.signal.aborted) return;
      if (isSessionError(reason)) return invalidateSession();
      const text = reason instanceof SyntaxError
        ? "report.json을 읽지 못했습니다."
        : reason instanceof Error ? reason.message : "보고서 미리보기를 불러오지 못했습니다.";
      setPreviews(current => ({ ...current, [exportId]: { state: "error", message: text } }));
    }
  };

  const renderExport = ({ snapshot, source, request }: TrackedExport) => {
    const link = links[snapshot.export_id];
    const expired = Boolean(link && link.usableUntil <= clock);
    const view = previews[snapshot.export_id];
    return (
      <li key={snapshot.export_id}>
        <h3><code>{snapshot.export_id}</code></h3>
        <p>
          {snapshot.state !== "ready"
            ? "부분 여부는 생성이 끝나면 확정됩니다"
            : snapshot.partial ? "검토용 부분 스냅샷" : "최종 스냅샷(법적 효력·외부 보증 아님)"} · {stateText(snapshot.state)}
        </p>
        <p>
          {request
            ? `요청 형식: ${request.formats.map(format => formatLabel[format]).join(" · ")} · ${request.allow_partial ? "부분 결과 허용" : "완료 결과만"}`
            : "요청 형식: 이 화면에서 만든 요청이 아니어서 알 수 없음(ZIP 안 파일로 확인)"}
        </p>
        <p>epoch {snapshot.snapshot_epoch}</p>
        <p>manifest hash: <code>{snapshot.manifest_sha256 ?? "생성 전"}</code></p>
        {!terminalStates.has(snapshot.state) ? (
          <button type="button" disabled={busy} onClick={() => void refresh(snapshot, source)} style={{ minHeight: 44 }}>상태 다시 확인</button>
        ) : null}
        {snapshot.state === "failed" ? (
          <p role="alert">
            export 생성에 실패했습니다. 저장된 결과는 다운로드할 수 없습니다. 이 export는 다시 생성되지 않으므로 위에서 새
            내보내기를 생성하세요.
          </p>
        ) : null}
        {snapshot.state === "ready" && !link ? (
          <button type="button" disabled={downloadBusy === snapshot.export_id} onClick={() => void issueDownload(snapshot, false)} style={{ minHeight: 44 }}>
            {downloadBusy === snapshot.export_id ? "링크 발급 중…" : "다운로드 링크 발급"}
          </button>
        ) : null}
        {snapshot.state === "ready" && link && !expired ? (
          <>
            <p><a href={link.href} target="_blank" rel="noreferrer">비공개 다운로드 열기</a> · 최대 5분 유효 · SHA-256 <code>{link.sha256}</code></p>
            {!request || request.formats.includes("json") ? (
              <button type="button" disabled={view?.state === "loading"} onClick={() => void loadPreview(snapshot, link)} style={{ minHeight: 44 }}>
                {view?.state === "loading" ? "미리보기 확인 중…" : "보고서 미리보기"}
              </button>
            ) : <p>JSON 형식을 포함한 export만 화면에서 미리볼 수 있습니다.</p>}
          </>
        ) : null}
        {view?.state === "error" ? <p role="alert">{view.message}</p> : null}
        {view?.state === "ready" ? (
          <section aria-label="보고서 미리보기" style={{ borderTop: "1px solid #cbd5e1", marginTop: 16, paddingTop: 8 }}>
            <p role="status">export 파일 SHA-256 확인됨 · report.json을 그대로 표시합니다(편집 불가).</p>
            <ReportPreview report={view.report} />
          </section>
        ) : null}
        {snapshot.state === "ready" && link && expired ? (
          link.reissues < 1
            ? <button type="button" disabled={downloadBusy === snapshot.export_id} onClick={() => void issueDownload(snapshot, true)} style={{ minHeight: 44 }}>만료된 링크 1회 재발급</button>
            : <p role="alert">재발급한 링크도 만료되었습니다. 새 export를 생성해 주세요.</p>
        ) : null}
      </li>
    );
  };

  const created = tracked.filter(item => item.source === "created");
  const known = tracked.filter(item => item.source === "known");
  return (
    <section aria-labelledby="export-heading">
      <h1 id="export-heading">리포트 내보내기</h1>
      <p>현재 실행의 불변 스냅샷을 JSON, CSV 또는 HTML 감사 꾸러미로 생성합니다.</p>
      <form onSubmit={createExport}>
        <fieldset>
          <legend>내보낼 형식</legend>
          {formats.map(([value, label]) => (
            <label key={value} style={{ marginRight: 16 }}>
              <input
                type="checkbox"
                name="formats"
                value={value}
                checked={selected.includes(value)}
                onChange={event => setSelected(current => event.target.checked
                  ? [...current, value]
                  : current.filter(item => item !== value))}
              /> {label}
            </label>
          ))}
        </fieldset>
        <label>
          <input name="allow-partial" type="checkbox" checked={allowPartial} onChange={event => setAllowPartial(event.target.checked)} /> 부분 결과 허용
        </label>
        <p>미완료 항목을 포함한 검토용 결과입니다. 최종본이 아닙니다. 체크를 해제하면 완료된 결과만 내보냅니다.</p>
        {selected.length === 0 ? <p role="alert">형식을 하나 이상 선택하세요.</p> : null}
        <button type="submit" disabled={busy || selected.length === 0} style={{ minHeight: 44 }}>
          {busy ? "처리 중…" : createRequest.current || retryMode ? "다시 시도" : created.length ? "새 내보내기 생성" : "내보내기 생성"}
        </button>
      </form>
      {message ? <p role="alert">{message}</p> : null}
      {message && retryMode === "new" ? <p>다시 시도하면 최신 실행 상태로 새 요청을 보냅니다.</p> : null}

      <section aria-labelledby="created-export-heading">
        <h2 id="created-export-heading">이 화면에서 생성한 내보내기</h2>
        {created.length ? <ol>{created.map(renderExport)}</ol> : <p>아직 이 화면에서 생성한 내보내기가 없습니다.</p>}
      </section>
      {known.length ? (
        <section aria-labelledby="known-export-heading">
          <h2 id="known-export-heading">URL로 다시 연 내보내기</h2>
          <ul>{known.map(renderExport)}</ul>
        </section>
      ) : null}
    </section>
  );
}
