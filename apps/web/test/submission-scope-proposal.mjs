// 환경(E) 범위 제안 흐름을 실제 Chrome과 전체 앱 빌드로 확인한다.
// 사용법: node test/submission-scope-proposal.mjs [--dist 빌드_폴더] [--out 결과_폴더]
// --dist가 없으면 전체 앱(VITE_DEMO_STATIC=false)을 임시 폴더로 빌드한다. 기존 dist는 건드리지 않는다.
//
// LOCAL SYNTHETIC ADAPTER: /v1 응답은 이 파일이 만든 합성 데이터다. 실제 기업·모델·AWS·유료 호출이 없다.
// 성공 제안의 페이지 목록·정책 hash는 실제 GET /v1/versions/{id}/scope-proposal 응답
// (tests/integration/test_document_scope_proposal.py의 8쪽 named-destination PDF)에서 옮긴 값이며,
// source_sha256은 이 테스트가 올린 파일의 실제 SHA-256으로 매번 계산한다. UI 동작 증거이지 탐지 정확도 증거가 아니다.
//
// 회귀 대상:
//  - 업로드 완료 직후 RunForm이 사라지던 버그(App이 매 렌더 새 콜백을 넘겨 UploadForm 초기화가 재실행됨)
//  - 명시적 요청 전 제안 요청 없음, 원본 SHA 고정, 위조/stale/시간 초과/바쁨/비활성화 응답 처리
//  - 제안 적용은 declared_subset만 채우고 unknown/conflict는 넣지 않음, 검토자 수정 후 그 값으로 실행 요청
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize, resolve } from "node:path";

const webRoot = normalize(join(import.meta.dirname, ".."));
const arg = name => { const index = process.argv.indexOf(name); return index > 0 ? process.argv[index + 1] : null; };
const outDir = resolve(arg("--out") ?? join(webRoot, "../../.local/submission-20260929/scope-proposal-flow"));
const chromePath = process.env.CHROME_PATH || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].find(existsSync);
if (!chromePath) throw new Error("Chrome 실행 파일을 찾지 못했습니다 (CHROME_PATH 지정)");
mkdirSync(outDir, { recursive: true });

let root = arg("--dist") ? resolve(arg("--dist")) : null;
const builtDir = root ? null : mkdtempSync(join(tmpdir(), "proofops-scope-flow-dist-"));
if (!root) {
  root = builtDir;
  const vite = join(webRoot, "node_modules/vite/bin/vite.js");
  const build = spawnSync(process.execPath, [vite, "build", "--outDir", root, "--emptyOutDir", "--logLevel", "warn"], {
    cwd: webRoot, stdio: "inherit",
    env: { ...process.env, VITE_DEMO_STATIC: "false", VITE_API_BASE_URL: "", VITE_LOCAL_SYNTHETIC: "true" },
  });
  if (build.status !== 0) throw new Error("vite build 실패");
}
if (!existsSync(join(root, "index.html"))) throw new Error(`index.html 없음: ${root}`);

// ---------------------------------------------------------------- synthetic fixture data
const ids = {
  tenant: "5c5c5c5c-0000-4000-8000-000000000001", rights: "5c5c5c5c-0000-4000-8000-000000000002",
  consent: "5c5c5c5c-0000-4000-8000-000000000003", runtime: "5c5c5c5c-0000-4000-8000-000000000004",
  company: "5c5c5c5c-0000-4000-8000-000000000005", document: "5c5c5c5c-0000-4000-8000-000000000006",
  upload: "5c5c5c5c-0000-4000-8000-000000000007", version: "5c5c5c5c-0000-4000-8000-000000000008",
  job: "5c5c5c5c-0000-4000-8000-000000000009", pack: "5c5c5c5c-0000-4000-8000-00000000000a",
  run: "5c5c5c5c-0000-4000-8000-00000000000b",
};
const scratch = mkdtempSync(join(tmpdir(), "proofops-scope-flow-pdf-"));
const pdfPath = join(scratch, "sectioned-report.pdf");
const pdfBytes = Buffer.from("%PDF-1.4\n% synthetic upload bytes for the scope proposal UI regression\n%%EOF\n");
writeFileSync(pdfPath, pdfBytes);
const sourceSha = createHash("sha256").update(pdfBytes).digest("hex");
const hex = seed => createHash("sha256").update(`synthetic:${seed}`).digest("hex");
const proposal = {
  version_id: ids.version, document_id: ids.document, source_sha256: sourceSha, page_count: 8,
  status: "candidate_only", full_scope_declared: false, apply_as: "declared_subset", method: "pdf_navigation",
  proposed_pages: [2, 3, 4, 6, 7, 8], claim_candidate_pages: [2, 3, 4], evidence_candidate_pages: [2, 3, 4, 6, 7, 8],
  unknown_pages: [1], conflict_pages: [], other_candidate_pages: [5],
  sections: [
    { start_page: 1, end_page: 1, role: "unknown", anchors: [] },
    { start_page: 2, end_page: 4, role: "e_narrative", anchors: [{ page: 2, title: "Environment", method: "named_destination" }] },
    { start_page: 5, end_page: 5, role: "other", anchors: [{ page: 5, title: "Social", method: "named_destination" }] },
    { start_page: 6, end_page: 7, role: "esg_data", anchors: [{ page: 6, title: "ESG DATA", method: "named_destination" }] },
    { start_page: 8, end_page: 8, role: "appendix", anchors: [{ page: 8, title: "Appendix", method: "named_destination" }] },
  ],
  issue_counts: {}, policy_sha256: "d757a32350d49e0bf3edd88184a0d66431aa3cdd6b887c2e709c1c92e94f405e",
  map_sha256: hex("map"), parser_versions: { pypdf: "6.18.0", pdfplumber: "0.11.10" },
  coverage: "unvalidated", live_model: "not_run", table_figure_validation: "not_run",
  limitations: ["Unknown/conflict pages need scope review; they are never evidence absence."],
};
const coverage = { pages_total: 8, pages_processed: 0, pages_unreadable: 0, pages_unprocessed: 7, chunks_discovered: 0, chunks_processed: 0,
  claims_discovered: 0, claims_decided: 0, claims_needs_review: 0, full_scope: false, complete: false };
const runSnapshot = { run_id: ids.run, document_version_id: ids.version, status: "queued", current_stage: "queued", revision: 1,
  mutation_epoch: 0, coverage, rule_pack_sha256: hex("pack"), created_at: "2026-09-29T00:00:00Z" };

let proposalMode = "ok";
const apiLog = [], proposalQueries = [], runBodies = [];
const errorBody = (code, message, retryable = false) => ({ error: { code, message, request_id: "5c5c5c5c-0000-4000-8000-0000000000ff", retryable } });
const scopeFailures = {
  stale: [409, "SOURCE_SHA_MISMATCH"], timeout: [503, "SECTION_INSPECTION_TIMEOUT"], busy: [429, "SCOPE_INSPECTION_BUSY"],
  limit: [422, "SECTION_INSPECTION_RESOURCE_LIMIT"], disabled: [404, "RESOURCE_NOT_FOUND"],
};

// ---------------------------------------------------------------- synthetic adapter (same origin as the app)
const json = (response, status, body, headers = {}) => { response.writeHead(status, { "content-type": "application/json", "cache-control": "no-store", ...headers }); response.end(JSON.stringify(body)); };
const readBody = async request => { const chunks = []; for await (const chunk of request) chunks.push(chunk); return Buffer.concat(chunks).toString(); };
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://fixture");
  const path = url.pathname;
  if (path.startsWith("/v1/") || path.startsWith("/local/")) apiLog.push(`${request.method} ${path}`);
  if (path === "/v1/session") return json(response, 200, { user_id: "scope-flow-user", tenant_id: ids.tenant, role: "admin", csrf_token: "scope-flow-csrf", expires_at: "2027-01-01T00:00:00Z" });
  if (path === "/v1/companies" && request.method === "GET") return json(response, 200, { items: [{ company_id: ids.company, legal_name: "합성 기업", registration_identifier: null, aliases: [], created_at: "2026-09-09T00:00:00Z" }], next_cursor: null, snapshot_epoch: 1 });
  if (path === "/v1/runtime-options") return json(response, 200, {
    rights_profiles: [{ id: ids.rights, name: "합성 권리", status: "approved", reason: null }],
    consent_profiles: [{ id: ids.consent, name: "합성 동의", status: "approved", reason: null }, { id: hex("consent-2").slice(0, 8) + "-0000-4000-8000-000000000013", name: "다른 합성 동의", status: "approved", reason: null }],
    runtime_bindings: [{ id: ids.runtime, name: "합성 실행", status: "approved", reason: null }],
    rule_packs: [{ rule_pack_id: ids.pack, version: "synthetic-v1", sha256: hex("pack"), status: "active", mode: "disclosure", effective_date: "2026-01-01", unresolved_gap_ids: [] }],
    enabled_modes: ["disclosure"],
  });
  if (path === "/local/submission") return json(response, 200, { worker_enabled: true, candidate_rule_pack_id: null, selected_pages: [] });
  if (path === "/v1/documents" && request.method === "POST") { await readBody(request); return json(response, 201, { document_id: ids.document }); }
  if (path === `/v1/documents/${ids.document}/versions` && request.method === "POST") { await readBody(request); return json(response, 201, { upload_id: ids.upload, document_id: ids.document, post_url: `/local/uploads/${ids.upload}/content`, post_fields: { ticket: "synthetic-ticket" }, expires_at: "2027-01-01T00:00:00Z" }); }
  if (path === `/local/uploads/${ids.upload}/content`) { await readBody(request); response.writeHead(204); return response.end(); }
  if (path === `/v1/uploads/${ids.upload}/complete`) { await readBody(request); return json(response, 202, { job_id: ids.job, resource_id: ids.version, status: "ready", status_url: `/v1/versions/${ids.version}` }); }
  if (path === `/v1/versions/${ids.version}`) return json(response, 200, { version_id: ids.version, document_id: ids.document, sha256: sourceSha, report_year: 2025, page_count: 8, status: "ready", created_at: "2026-09-09T00:00:00Z" });
  if (path === `/v1/versions/${ids.version}/scope-proposal`) {
    proposalQueries.push(url.search);
    if (proposalMode === "forged") return json(response, 200, { ...proposal, source_sha256: hex("other-source") });
    if (scopeFailures[proposalMode]) {
      const [status, code] = scopeFailures[proposalMode];
      return json(response, status, errorBody(code, code, status === 429), status === 429 ? { "retry-after": "30" } : {});
    }
    return json(response, 200, proposal);
  }
  if (path === "/v1/preflight") { await readBody(request); return json(response, 200, { ready: true, checks: [], binding_sha256: null, checked_at: "2026-09-29T00:00:00Z" }); }
  if (path === "/v1/runs" && request.method === "POST") { runBodies.push(JSON.parse(await readBody(request))); return json(response, 201, runSnapshot); }
  if (path === `/v1/runs/${ids.run}`) return json(response, 200, runSnapshot);
  if (path.startsWith("/v1/")) return json(response, 404, errorBody("RESOURCE_NOT_FOUND", "not in synthetic adapter"));
  let file = normalize(join(root, decodeURIComponent(path)));
  if (!file.startsWith(root) || !existsSync(file) || !extname(file)) file = join(root, "index.html");
  response.writeHead(200, { "content-type": { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream" });
  createReadStream(file).pipe(response);
});
await new Promise(done => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;

// ---------------------------------------------------------------- Chrome via DevTools Protocol
const profile = mkdtempSync(join(tmpdir(), "proofops-scope-flow-chrome-"));
const chrome = spawn(chromePath, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
const wsUrl = await new Promise((done, fail) => {
  let text = "";
  chrome.stderr.on("data", chunk => { text += chunk; const found = text.match(/DevTools listening on (ws:\/\/\S+)/); if (found) done(found[1]); });
  chrome.on("exit", code => fail(new Error(`chrome exited ${code}`)));
  setTimeout(() => fail(new Error("chrome start timeout")), 20000);
});
const socket = new WebSocket(wsUrl);
await new Promise((done, fail) => { socket.onopen = done; socket.onerror = fail; });
let nextId = 0;
const pending = new Map(), listeners = [];
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const { done, fail } = pending.get(message.id); pending.delete(message.id);
    message.error ? fail(new Error(JSON.stringify(message.error))) : done(message.result);
  } else listeners.forEach(listener => listener(message));
};
const send = (method, params = {}, sessionId) => new Promise((done, fail) => { const id = ++nextId; pending.set(id, { done, fail }); socket.send(JSON.stringify({ id, method, params, sessionId })); });
const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
const page = (method, params) => send(method, params, sessionId);
const requests = [], consoleErrors = [];
listeners.push(message => {
  if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url);
  if (message.method === "Runtime.exceptionThrown") consoleErrors.push(message.params.exceptionDetails.text);
});
await page("Network.enable"); await page("Page.enable"); await page("Runtime.enable"); await page("DOM.enable");
await page("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });

const sleep = ms => new Promise(done => setTimeout(done, ms));
async function evaluate(expression) {
  const result = await page("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(`${result.exceptionDetails.text} ${expression}`);
  return result.result.value;
}
async function waitFor(expression, timeout = 10000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { if (await evaluate(expression)) return true; await sleep(100); }
  return false;
}
const bodyText = () => evaluate("document.body.innerText");
const scopeText = () => evaluate("document.querySelector('[aria-label=\"환경 범위 제안\"]')?.innerText ?? ''");
const waitForScope = (text, timeout = 8000) => waitFor(`(document.querySelector('[aria-label="환경 범위 제안"]')?.innerText ?? '').includes(${JSON.stringify(text)})`, timeout);
const value = selector => evaluate(`document.querySelector(${JSON.stringify(selector)})?.value ?? null`);
const setValue = (selector, next) => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false;
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, ${JSON.stringify(next)});
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true })); return true; })()`);
const click = text => evaluate(`(() => { const el = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(text)} && !b.disabled); if (!el) return false; el.click(); return true; })()`);
const inspectButton = "환경(E) 범위 후보 찾기";
async function screenshot(name) {
  const { cssContentSize } = await page("Page.getLayoutMetrics");
  const shot = await page("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1280, height: Math.min(Math.ceil(cssContentSize.height), 12000), scale: 1 } });
  writeFileSync(join(outDir, `${name}.png`), Buffer.from(shot.data, "base64"));
}

const checks = [];
const check = (id, kind, name, ok, detail = "") => { checks.push({ id, kind, name, ok: Boolean(ok), detail }); console.log(`${ok ? "PASS" : "FAIL"} ${id} [${kind}] ${name}${detail ? ` — ${detail}` : ""}`); };

try {
  // ---- existing routes: "/" is the public landing (no session call); /documents/new is the app flow
  await page("Page.navigate", { url: base + "/" });
  await sleep(1500);
  const landing = (await evaluate("location.pathname")) === "/" && !(await evaluate("!!document.querySelector('#pdf-file')"));
  check("R1", "route", "/ still renders the public landing without calling the session API", landing && !apiLog.includes("GET /v1/session"), apiLog.join(", "));
  await page("Page.navigate", { url: base + "/documents/new" });
  const loaded = await waitFor(`!!document.querySelector('#company option[value="${ids.company}"]') && !!document.querySelector('#pdf-file')`, 15000);
  check("R2", "route", "/documents/new loads the session, tenant options and upload form", loaded, await evaluate("location.pathname"));

  // ---- upload-callback regression: an App re-render must not reset typed fields
  await setValue("#company", ids.company); await sleep(150);
  await setValue("#rights-profile", ids.rights); await sleep(150);
  await setValue("#runtime-binding", ids.runtime); await sleep(150);
  await setValue("#document-title", "합성 섹션 보고서");
  await setValue("#period-start", "2025-01-01");
  await setValue("#period-end", "2025-12-31");
  await setValue("#consent-profile", ids.consent); await sleep(300); // App selection state changes after typing
  check("U1", "regression", "typed upload fields survive an App re-render (selection change)",
    (await value("#document-title")) === "합성 섹션 보고서" && (await value("#period-start")) === "2025-01-01", `title=${await value("#document-title")}`);

  const { root: documentNode } = await page("DOM.getDocument", {});
  const { nodeId } = await page("DOM.querySelector", { nodeId: documentNode.nodeId, selector: "#pdf-file" });
  await page("DOM.setFileInputFiles", { nodeId, files: [pdfPath] });
  await sleep(200);
  await evaluate(`window.__runFormSeen = false; new MutationObserver(() => { if (document.querySelector('[aria-label="환경 범위 제안"]')) window.__runFormSeen = true; }).observe(document.body, { subtree: true, childList: true }); true`);
  await evaluate("document.querySelector('#pdf-file').closest('form').requestSubmit(), true");
  const mounted = await waitFor(`!!document.querySelector('[aria-label="환경 범위 제안"]')`, 15000);
  await sleep(1500); // the old bug unmounted RunForm right after the ready-version render
  const stillMounted = await evaluate(`!!document.querySelector('[aria-label="환경 범위 제안"]')`);
  check("U2", "regression", "upload completion keeps RunForm mounted (ready version not cleared)", mounted && stillMounted,
    `mounted=${mounted} still=${stillMounted} seen=${await evaluate("window.__runFormSeen")}`);

  // ---- explicit, SHA-pinned proposal
  await sleep(500);
  check("P1", "scope", "no scope proposal is requested before the reviewer asks", proposalQueries.length === 0 && (await value("#run-scope")) === "full", `requests=${proposalQueries.length}`);

  proposalMode = "forged";
  await click(inspectButton);
  check("P2", "integrity", "a proposal for another source SHA is rejected client-side",
    (await waitForScope("원본 해시와 일치하지 않아")) && !(await scopeText()).includes("제안 페이지를 지정 범위로 적용"));

  proposalMode = "stale";
  await click(inspectButton);
  check("P3", "integrity", "stale-SHA 409 shows reload guidance", await waitForScope("문서 버전을 다시 불러오세요"));

  proposalMode = "timeout";
  await click(inspectButton);
  const timeoutShown = await waitForScope("제한 시간");
  check("P4", "bounded", "worker timeout 503 shows a stable manual-pages guidance", timeoutShown && (await scopeText()).includes("페이지를 직접 지정"), (await scopeText()).slice(0, 240).replace(/\s+/g, " "));
  await screenshot("01-timeout-guidance");

  proposalMode = "limit";
  await click(inspectButton);
  check("P5", "bounded", "resource-limit 422 is explained with manual fallback", await waitForScope("메모리·출력 한도"));

  proposalMode = "busy";
  await click(inspectButton);
  check("P6", "bounded", "single-flight 429 asks to retry later", await waitForScope("잠시 후 다시 시도"));

  proposalMode = "disabled";
  await click(inspectButton);
  const disabledShown = await waitForScope("범위 제안을 사용할 수 없습니다");
  await setValue("#run-scope", "declared_subset"); await sleep(150);
  const manualStill = (await evaluate("!!document.querySelector('#selected-pages')"));
  await setValue("#run-scope", "full"); await sleep(150);
  check("P7", "rollback", "route disabled (404) keeps manual page entry usable", disabledShown && manualStill);

  proposalMode = "ok";
  await click(inspectButton);
  const shown = await waitFor(`!!document.querySelector('[aria-label="범위 제안 결과"]')`);
  const region = shown ? await evaluate(`document.querySelector('[aria-label="범위 제안 결과"]').innerText`) : "";
  const needles = ["후보일 뿐입니다", "근거 부재를 뜻하지 않습니다", "제안 페이지: 2–4, 6–8 (6쪽)", "미확정 페이지: 1 (1쪽)", "자동으로 넣지 않았습니다", sourceSha.slice(0, 12)];
  const missing = needles.filter(needle => !region.includes(needle));
  check("P8", "scope", "proposal shows candidates, unknown pages, no-absence warning and source SHA", shown && missing.length === 0, missing.join(", "));
  check("P9", "integrity", "every proposal request was pinned to the uploaded file's SHA-256",
    proposalQueries.length > 0 && proposalQueries.every(query => query === `?expected_sha256=${sourceSha}`), `${proposalQueries.length} requests`);
  check("P10", "scope", "inspecting alone never changes the run scope", (await value("#run-scope")) === "full");
  await screenshot("02-proposal-shown");

  await click("제안 페이지를 지정 범위로 적용"); await sleep(200);
  check("A1", "apply", "apply fills declared_subset with proposed pages only (unknown page 1 excluded)",
    (await value("#run-scope")) === "declared_subset" && (await value("#selected-pages")) === "2, 3, 4, 6, 7, 8", `${await value("#run-scope")} / ${await value("#selected-pages")}`);
  await setValue("#selected-pages", "1, 2, 3, 4, 6, 7, 8"); await sleep(200);
  check("A2", "apply", "reviewer edit of the applied pages is flagged", (await bodyText()).includes("검토자가 자동 제안을 수정했습니다"));
  await screenshot("03-applied-and-edited");

  await evaluate(`document.querySelector('form[aria-label="문서 분석 실행"]').requestSubmit(), true`);
  const posted = await waitFor(`location.pathname === "/runs/${ids.run}"`, 10000);
  const runBody = runBodies[0] ?? {};
  check("A3", "apply", "run request carries the reviewer-edited declared subset and no extra fields",
    runBodies.length === 1 && runBody.scope === "declared_subset" && JSON.stringify(runBody.selected_pages) === "[1,2,3,4,6,7,8]" &&
    Object.keys(runBody).sort().join(",") === "consent_profile_id,document_version_id,mode,rule_pack_id,runtime_binding_id,scope,selected_pages", JSON.stringify(runBody));
  check("R4", "route", "run creation still navigates to the existing /runs/:runId route", posted && !(await bodyText()).includes("화면을 찾을 수 없습니다"), await evaluate("location.pathname"));

  await page("Page.navigate", { url: base + "/no-such-screen" });
  check("R3", "route", "unknown routes still render the existing not-found screen", await waitFor(`document.body.innerText.includes("화면을 찾을 수 없습니다")`));

  const fonts = requests.filter(url => /^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(url));
  const foreign = requests.filter(url => !url.startsWith(base) && !url.startsWith("data:") && !url.startsWith("about:") && !url.startsWith("blob:") && !fonts.includes(url));
  check("S1", "safety", "no API/model/AWS request left the local synthetic origin", foreign.length === 0, foreign.slice(0, 3).join(", "));
  check("S2", "safety", "no uncaught page exceptions", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
} finally {
  const summary = { generated_at: new Date().toISOString(), adapter: "LOCAL SYNTHETIC (apps/web/test/submission-scope-proposal.mjs)", base, source_sha256: sourceSha,
    passed: checks.filter(item => item.ok).length, failed: checks.filter(item => !item.ok).length, checks, proposal_queries: proposalQueries, run_bodies: runBodies, api_calls: apiLog };
  writeFileSync(join(outDir, "results.json"), JSON.stringify(summary, null, 2));
  console.log(`\n${summary.passed} passed, ${summary.failed} failed · results: ${join(outDir, "results.json")}`);
  socket.close(); chrome.kill(); server.close();
  await sleep(300);
  for (const dir of [profile, scratch, builtDir]) if (dir) try { rmSync(dir, { recursive: true, force: true }); } catch { /* Chrome may hold files briefly on Windows */ }
  process.exitCode = checks.length && checks.every(item => item.ok) ? 0 : 1;
}
