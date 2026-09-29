// 검토→보고서 흐름(실행 범위 · 원문 품질 · 내보내기 · 보고서 미리보기)을 실제 Chrome으로 확인한다.
// 사용법: node test/submission-review-flow.mjs [--dist 빌드_폴더] [--out 결과_폴더]
// --dist가 없으면 전체 앱(VITE_DEMO_STATIC=false)을 임시 폴더로 빌드한다. 기존 dist는 건드리지 않는다.
//
// LOCAL SYNTHETIC ADAPTER: 아래 /v1 응답은 전부 이 파일이 만든 합성 데이터다. 실제 기업·모델·AWS·유료 호출이
// 없으며, 서버 계약(openapi.yaml)의 DTO 모양과 로컬 export 저장소의 멱등 키 규칙(같은 키 = 같은 export,
// 실패한 export는 실패로 남음)만 흉내 낸다. 결과는 UI 동작 증거이지 판정 정확도 증거가 아니다.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize, resolve } from "node:path";
import { crc32, deflateRawSync } from "node:zlib";

const webRoot = normalize(join(import.meta.dirname, ".."));
const arg = name => { const index = process.argv.indexOf(name); return index > 0 ? process.argv[index + 1] : null; };
const outDir = resolve(arg("--out") ?? join(webRoot, "../../.local/submission-20260929/review-flow"));
const chromePath = process.env.CHROME_PATH || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].find(existsSync);
if (!chromePath) throw new Error("Chrome 실행 파일을 찾지 못했습니다 (CHROME_PATH 지정)");
mkdirSync(outDir, { recursive: true });

let root = arg("--dist") ? resolve(arg("--dist")) : null;
const builtDir = root ? null : mkdtempSync(join(tmpdir(), "proofops-review-flow-dist-"));
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
  tenant: "5e5e5e5e-0000-4000-8000-000000000001",
  run: "5e5e5e5e-0000-4000-8000-0000000000a1",
  scopedRun: "5e5e5e5e-0000-4000-8000-0000000000a2",
  document: "5e5e5e5e-0000-4000-8000-0000000000d1",
  parse: "5e5e5e5e-0000-4000-8000-0000000000e1",
  source: "5e5e5e5e-0000-4000-8000-0000000000f1",
  claims: [1, 2, 3, 4, 5].map(n => `5e5e5e5e-0000-4000-8000-0000000000c${n}`),
};
const hex = seed => createHash("sha256").update(`synthetic:${seed}`).digest("hex");
const coverage = (overrides = {}) => ({
  pages_total: 10, pages_processed: 10, pages_unreadable: 0, pages_unprocessed: 0,
  chunks_discovered: 20, chunks_processed: 20, claims_discovered: 5, claims_decided: 2,
  claims_needs_review: 3, full_scope: true, complete: true, ...overrides,
});
const runs = {
  // Backend-valid: 2 decided + 3 needs_review == 5 discovered, so coverage.complete is true.
  [ids.run]: { status: "completed", current_stage: "review", coverage: coverage() },
  [ids.scopedRun]: {
    status: "completed", current_stage: "review",
    coverage: coverage({ pages_processed: 4, pages_unprocessed: 6, chunks_discovered: 8, chunks_processed: 8,
      claims_discovered: 2, claims_decided: 2, claims_needs_review: 0, full_scope: false, complete: false }),
  },
};
const runSnapshot = runId => ({
  run_id: runId, document_version_id: ids.document, revision: 4, mutation_epoch: 9,
  rule_pack_sha256: hex("rule-pack"), created_at: "2026-09-29T00:00:00Z", ...runs[runId],
});

const sourceRef = (page, quote) => ({ document_version_id: ids.document, parse_manifest_id: ids.parse, page_num: page,
  bbox: [72, 100 + page, 500, 140 + page], raw_text_sha256: hex(`raw:${page}`), quote });
const provenance = n => ({ rule_pack_sha256: hex("rule-pack"), model_sha256: hex("model"), prompt_sha256: hex("prompt"),
  replicate_hashes: [1, 2, 3].map(r => hex(`replica:${n}:${r}`)) });
const baseClaim = (n, fields) => ({
  claim_id: ids.claims[n], claim_quote: `[합성] 검토 대상 주장 문장 ${n + 1}`, classification_review: null,
  tag_elements: [], tag_revision: 1, decision_revision: 1, evidence_grade: null, label: null, grade_range: null,
  review_status: "needs_review", missing_elements: [], unresolved_elements: [], gap_ids: [],
  source_refs: [sourceRef(3 + n, `[합성] 원문 인용 ${n + 1}`)], source_status: "verified", basis_refs: [],
  assurance: { status: "not_run" }, safe_harbor: { status: "not_run" }, suggestion: null, review_action: null,
  ...provenance(n), ...fields,
});
function reportModel(final) {
  const decided = baseClaim(0, {
    decision_status: "decided", evidence_grade: "E3", label: "SUBSTANTIATED", review_status: "human_confirmed",
    tag_elements: [{ element_id: "P1", state: "present", normalized_value: "[합성] 1,000 tCO2e", evidence_refs: [sourceRef(3, "[합성] 1,000 tCO2e")] }],
    basis_refs: [{ element_id: "P1", source_section: "7.2", clause: "SYNTHETIC-CLAUSE-1", verification_status: "verified", rule_ids: ["R-SYN-1"] }],
    assurance: { status: "covered", level: "limited", provider: "SYNTHETIC-ASSURER", statement_id: null,
      metric_match: "match", period_match: "match", boundary_match: "undetermined", evidence_refs: [] },
    safe_harbor: { status: "not_run" },
  });
  if (final) return { claims: [decided, baseClaim(1, { decision_status: "decided", evidence_grade: "E1", label: "INCOMPLETE",
    review_status: "human_confirmed", missing_elements: ["P2"], suggestion: "[합성] P2 비교기준을 공시하세요." })], cov: coverage({ claims_discovered: 2, claims_decided: 2, claims_needs_review: 0 }) };
  const claims = [
    decided,
    baseClaim(1, { decision_status: "decided", evidence_grade: "E1", label: "INCOMPLETE", missing_elements: ["P2"],
      suggestion: "[합성] P2 비교기준을 공시하세요." }),
    baseClaim(2, { decision_status: "blocked_evidence", unresolved_elements: ["G1"],
      tag_elements: [{ element_id: "G1", state: "unknown", normalized_value: null, evidence_refs: [] }],
      review_action: { claim_id: ids.claims[2], reasons: ["unresolved_evidence", "assurance_not_run"],
        checks: ["미해결 요소의 원문 근거 귀속을 확인(absent 단정 금지): G1", "보증 대조 미실행. 보증서 기관·기간·지표·경계를 대조하세요."],
        unresolved_elements: ["G1"], gap_ids: [], source_pages: [5] } }),
    baseClaim(3, { decision_status: "blocked_rule_gap", gap_ids: ["GAP-SYN-1"],
      basis_refs: [{ element_id: "M1", source_section: "9.1", clause: null, verification_status: "unverified", rule_ids: [] }],
      review_action: { claim_id: ids.claims[3], reasons: ["basis_validation_pending", "domain_gap"],
        checks: ["기준 조항의 대응이 미확인입니다. 승인된 기준 원문과 해당 요소의 대응을 확인하세요.", "아직 확정되지 않은 규칙 항목을 확인하고 해당 판정에 미치는 영향을 검토하세요: GAP-SYN-1"],
        unresolved_elements: [], gap_ids: ["GAP-SYN-1"], source_pages: [6] } }),
    { ...baseClaim(4, { decision_status: "not_run", tag_revision: 0, decision_revision: 0, tag_elements: null, source_refs: [],
      source_status: "not_run", rule_pack_sha256: null, model_sha256: null, prompt_sha256: null, replicate_hashes: [],
      review_action: { claim_id: ids.claims[4], reasons: ["not_processed", "assurance_not_run", "safe_harbor_not_run"],
        checks: ["판정이 아직 실행되지 않았습니다. 주장 상세에서 원문 검증·분류·태깅 상태를 확인하고 미완료 단계를 진행하세요."],
        unresolved_elements: [], gap_ids: [], source_pages: [] } }) },
  ];
  return { claims, cov: coverage() };
}
function reportJson(final) {
  const { claims, cov } = reportModel(final);
  const unfinished = claims.filter(claim => claim.decision_status !== "decided").length;
  const unverified = claims.flatMap(claim => claim.basis_refs).filter(basis => !basis.clause || basis.verification_status !== "verified").length;
  return {
    schema: "report_model_v1", tenant_id: ids.tenant, run_id: ids.run, document_version_id: ids.document,
    parse_manifest_id: ids.parse, source_sha256: hex("source-pdf"), snapshot_epoch: 9,
    generated_at: "2026-09-29T01:02:03Z", execution_profile: "local-synthetic-only", rule_pack_hashes: [hex("rule-pack")],
    coverage: cov, unverified_basis: unverified, partial: Boolean(!cov.complete || unfinished || unverified),
    unfinished_count: unfinished, unverified_clause_count: unverified, claims,
  };
}
function zip(members) {
  const locals = [], centrals = [];
  let offset = 0;
  for (const [name, text] of members) {
    const raw = Buffer.from(text), data = deflateRawSync(raw), fileName = Buffer.from(name), crc = crc32(raw);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(8, 8);
    local.writeUInt16LE(0x21, 12); local.writeUInt32LE(crc, 14); local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(raw.length, 22); local.writeUInt16LE(fileName.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6);
    central.writeUInt16LE(8, 10); central.writeUInt16LE(0x21, 14); central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20); central.writeUInt32LE(raw.length, 24);
    central.writeUInt16LE(fileName.length, 28); central.writeUInt32LE(offset, 42);
    locals.push(local, fileName, data); centrals.push(central, fileName);
    offset += 30 + fileName.length + data.length;
  }
  const directory = Buffer.concat(centrals), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(members.length, 8); end.writeUInt16LE(members.length, 10);
  end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
}

// ---------------------------------------------------------------- synthetic adapter (same origin as the app)
const fixture = { finalizable: false, sizeLimitAllFormats: false, tamperNextTicket: false };
const exportRequests = new Map(); // idempotency key -> { hash, export_id }
const exportsById = new Map();
const apiLog = [];
const json = (response, status, body) => { response.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" }); response.end(JSON.stringify(body)); };
const apiError = (response, status, code) => json(response, status, { error: { code, message: code, request_id: "5e5e5e5e-0000-4000-8000-00000000ffff", retryable: status >= 500 } });
const readBody = async request => { const chunks = []; for await (const chunk of request) chunks.push(chunk); return Buffer.concat(chunks).toString(); };
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };

const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://fixture");
  const path = url.pathname;
  if (path.startsWith("/v1/") || path.startsWith("/local/")) apiLog.push(`${request.method} ${path}`);
  if (path === "/__fixture") { Object.assign(fixture, Object.fromEntries([...url.searchParams].map(([k, v]) => [k, v === "true"]))); return json(response, 200, fixture); }
  if (path === "/v1/session") return json(response, 200, { user_id: "synthetic-reviewer", tenant_id: ids.tenant, role: "reviewer", csrf_token: "csrf-synthetic", expires_at: "2099-01-01T00:00:00Z" });
  if (path === "/local/submission") return apiError(response, 404, "RESOURCE_NOT_FOUND");
  let match = path.match(/^\/v1\/runs\/([0-9a-f-]{36})(\/[a-z-]+)?$/);
  if (match && runs[match[1]]) {
    const [, runId, tail] = match;
    if (!tail && request.method === "GET") return json(response, 200, runSnapshot(runId));
    if (tail === "/cost") return json(response, 200, { run_id: runId, input_tokens: 0, output_tokens: 0, attempt_count: 0, cache_hit_count: 0, amount: null, currency: "USD", pricing_snapshot_id: null, cost_status: "unknown_cost" });
    if (tail === "/summary") return apiError(response, 409, "SUMMARY_NOT_READY");
    if (tail === "/quality") return json(response, 200, { next_cursor: null, items: [
      { issue_id: "5e5e5e5e-0000-4000-8000-0000000000b1", kind: "table_vision_not_run", page_num: 7, source_ids: [], state: "open", reason: "Vision cross-check not_run: approved runtime/account required." },
      { issue_id: "5e5e5e5e-0000-4000-8000-0000000000b2", kind: "no_extractable_text", page_num: 9, source_ids: [], state: "unreadable", reason: "No text extracted; absence of evidence is not established." },
    ] });
    if (tail === "/exports" && request.method === "POST") {
      if (request.headers["x-csrf-token"] !== "csrf-synthetic") return apiError(response, 403, "CSRF_INVALID");
      const key = request.headers["idempotency-key"];
      if (typeof key !== "string" || key.length < 16) return apiError(response, 400, "IDEMPOTENCY_KEY_INVALID");
      const body = JSON.parse(await readBody(request));
      const hash = JSON.stringify([runId, body]);
      let reserved = exportRequests.get(key);
      if (reserved && reserved.hash !== hash) return apiError(response, 409, "IDEMPOTENCY_CONFLICT");
      if (!reserved) {
        // Mirrors LocalExportStore.reserve + ExportService.get: the outcome is decided once per key.
        const exportId = crypto.randomUUID();
        const final = fixture.finalizable;
        let error = null;
        if (fixture.sizeLimitAllFormats && body.formats.length === 3) error = "EXPORT_SIZE_LIMIT";
        else if (!final && !body.allow_partial) error = "REPORT_NOT_FINALIZABLE";
        exportsById.set(exportId, { error, body, final, polls: 0, response: { export_id: exportId, run_id: runId,
          state: error ? "failed" : "queued", snapshot_epoch: 9, partial: true, manifest_sha256: null, created_at: new Date().toISOString() } });
        reserved = { hash, export_id: exportId };
        exportRequests.set(key, reserved);
      }
      const record = exportsById.get(reserved.export_id);
      if (record.error) return apiError(response, 409, record.error);
      return json(response, 202, record.response);
    }
  }
  match = path.match(/^\/v1\/exports\/([0-9a-f-]{36})(\/download|\/content)?$/);
  if (match && exportsById.has(match[1])) {
    const record = exportsById.get(match[1]);
    if (!match[2]) {
      if (record.response.state === "queued" && ++record.polls >= 1) {
        const report = reportJson(record.final);
        const manifest = JSON.stringify({ synthetic: true, run_id: ids.run });
        record.zip = zip([["manifest.json", manifest], ...record.body.formats.map(format => [`report.${format}`,
          format === "json" ? JSON.stringify(report) : `[synthetic ${format} rendering]`])]);
        Object.assign(record.response, { state: "ready", partial: report.partial, manifest_sha256: createHash("sha256").update(manifest).digest("hex") });
      }
      return json(response, 200, record.response);
    }
    if (match[2] === "/download") {
      if (request.headers["x-csrf-token"] !== "csrf-synthetic") return apiError(response, 403, "CSRF_INVALID");
      if (record.response.state !== "ready") return apiError(response, 409, "EXPORT_NOT_READY");
      const sha = fixture.tamperNextTicket ? "0".repeat(64) : createHash("sha256").update(record.zip).digest("hex");
      fixture.tamperNextTicket = false;
      return json(response, 200, { url: `/v1/exports/${match[1]}/content?ticket=${"t".repeat(43)}`, expires_at: new Date(Date.now() + 300000).toISOString(), sha256: sha });
    }
    response.writeHead(200, { "content-type": "application/zip", "cache-control": "no-store" });
    return response.end(record.zip);
  }
  if (path.startsWith("/v1/") || path.startsWith("/local/") || path.startsWith("/auth/")) return apiError(response, 404, "RESOURCE_NOT_FOUND");
  let file = normalize(join(root, decodeURIComponent(path)));
  if (!file.startsWith(root) || !existsSync(file) || !extname(file)) file = join(root, "index.html");
  response.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" });
  createReadStream(file).pipe(response);
});
await new Promise(done => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;

// ---------------------------------------------------------------- Chrome via DevTools Protocol
const profile = mkdtempSync(join(tmpdir(), "proofops-review-flow-chrome-"));
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
await page("Network.enable"); await page("Page.enable"); await page("Runtime.enable");
await page("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });

const sleep = ms => new Promise(done => setTimeout(done, ms));
async function evaluate(expression) {
  const result = await page("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(`${result.exceptionDetails.text} ${expression}`);
  return result.result.value;
}
const bodyText = () => evaluate("document.body.innerText");
const alertText = () => evaluate("[...document.querySelectorAll('[role=alert]')].map(el => el.textContent).join(' | ')");
async function waitForAlert(text, timeout = 8000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { if ((await alertText()).includes(text)) return true; await sleep(100); }
  return false;
}
async function waitForText(text, timeout = 8000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { if ((await bodyText()).includes(text)) return true; await sleep(100); }
  return false;
}
async function navigate(path) { await page("Page.navigate", { url: base + path }); await sleep(300); }
const click = text => evaluate(`(() => { const el = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(text)} && !b.disabled); if (!el) return false; el.click(); return true; })()`);
const setCheckbox = (selector, checked) => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false; if (el.checked !== ${checked}) el.click(); return true; })()`);
const fixtureSet = query => fetch(`${base}/__fixture?${query}`);
async function screenshot(name) {
  const { cssContentSize } = await page("Page.getLayoutMetrics");
  const shot = await page("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1280, height: Math.min(Math.ceil(cssContentSize.height), 12000), scale: 1 } });
  writeFileSync(join(outDir, `${name}.png`), Buffer.from(shot.data, "base64"));
}

const checks = [];
const check = (id, kind, name, ok, detail = "") => { checks.push({ id, kind, name, ok: Boolean(ok), detail }); console.log(`${ok ? "PASS" : "FAIL"} ${id} [${kind}] ${name}${detail ? ` — ${detail}` : ""}`); };
const excerpt = (text, needle, size = 160) => { const at = text.indexOf(needle); return at < 0 ? "" : text.slice(Math.max(0, at - 20), at + size).replace(/\s+/g, " "); };

try {
  // 1. Run screen: coverage truthfulness + source-quality evidence.
  await navigate(`/runs/${ids.run}`);
  await waitForText("분석 범위");
  let text = await bodyText();
  check("C1", "bug", "완료 counters with 3 needs-review claims must not claim a finished review", !text.includes("전수 검토 완료"), excerpt(text, "분석 범위"));
  check("C2", "bug", "coverage names the remaining needs-review claims as unfinished review work", text.includes("검토 필요 주장 3건"), excerpt(text, "분석 범위", 400));
  check("C3", "evidence", "source-quality issues remain listed separately from absence", text.includes("표 이미지 대조 미실행") && text.includes("텍스트 추출 실패"));
  await screenshot("01-run-coverage");
  await navigate(`/runs/${ids.scopedRun}`);
  await waitForText("분석 범위");
  text = await bodyText();
  check("C4", "improvement", "user-limited scope states that the whole document was not covered", text.includes("지정 범위 처리 완료") && text.includes("문서 전체 결론이 아닙니다"), excerpt(text, "분석 범위", 300));
  await screenshot("02-run-scoped-coverage");

  // 2. Report screen: finalization gate, retry after the run becomes finalizable.
  await navigate(`/runs/${ids.run}/report`);
  await waitForText("내보내기 생성");
  await setCheckbox('input[name="allow-partial"]', false);
  await click("내보내기 생성");
  const gate = await waitForAlert("부분 결과 허용");
  text = await bodyText();
  check("E1", "evidence", "complete-only export of an unfinished run is refused with guidance", gate && !text.includes("준비됨"), await alertText());
  await screenshot("03-export-not-finalizable");
  await fixtureSet("finalizable=true"); // reviewers finished elsewhere; the server would now accept a new request
  await click("다시 시도");
  const finalReady = await waitForText("준비됨", 6000);
  text = await bodyText();
  check("E2", "bug", "retry after a definitive 409 sends a new request instead of replaying the stale failure", finalReady && text.includes("최종 스냅샷"), finalReady ? "" : excerpt(text, "내보낼 형식", 400));
  await fixtureSet("finalizable=false");

  // 3. Size limit error must be specific and actionable.
  await navigate(`/runs/${ids.run}/report`);
  await waitForText("내보내기 생성");
  await fixtureSet("sizeLimitAllFormats=true");
  await click("내보내기 생성");
  await waitForAlert("", 3000); await sleep(500);
  check("E3", "bug", "EXPORT_SIZE_LIMIT explains the 32MiB cap and what to change", (await alertText()).includes("32MiB"), await alertText());
  await screenshot("04-export-size-limit");
  await setCheckbox('input[value="csv"]', false);
  await setCheckbox('input[value="html"]', false);
  await click("다시 시도") || await click("내보내기 생성");
  await waitForText("준비됨", 6000);
  text = await bodyText();
  check("E4", "missing", "created export shows which formats and partial mode were requested", text.includes("요청 형식: JSON · 부분 결과 허용"), excerpt(text, "이 화면에서 생성한", 300));

  // 4. In-app preview of the immutable report.json from the private ZIP.
  await click("다운로드 링크 발급");
  await waitForText("비공개 다운로드 열기");
  const previewButton = await click("보고서 미리보기");
  const previewed = previewButton && await waitForText("부분 리포트인 이유", 6000);
  text = await bodyText();
  check("E5", "missing", "reviewer can read the exported report in-app after SHA-256 verification", previewed && text.includes("SHA-256 확인됨"));
  check("R1", "missing", "report lists distinct unfinished reasons (not_run / blocked evidence / rule gap / clause)",
    ["판정 미실행 1건", "근거 불확실로 미판정 1건", "규칙 공백으로 미판정 1건", "기준 조항 미확인 1건"].every(item => text.includes(item)), excerpt(text, "부분 리포트인 이유", 500));
  check("R2", "contract", "SUBSTANTIATED carries the in-disclosure scope note (08 §1) and no legal/assurance approval", text.includes("이 공시 안의 근거 충족") && text.includes("법적 효력이나 외부 보증 의견이 아닙니다"));
  check("R3", "missing", "provenance header shows run, generated time, execution profile and rule pack", text.includes(ids.run) && text.includes("2026-09-29T01:02:03Z") && text.includes("local-synthetic-only") && text.includes(hex("rule-pack")));
  check("R4", "missing", "review status and element state are readable, raw codes kept", text.includes("사람 확인 (human_confirmed)") && text.includes("미상 (unknown)"));
  await screenshot("05-report-preview");

  // 5. Tampered ticket hash: preview must fail closed.
  await navigate(`/runs/${ids.run}/report`);
  await waitForText("내보내기 생성");
  await setCheckbox('input[value="csv"]', false);
  await setCheckbox('input[value="html"]', false);
  await click("내보내기 생성");
  await waitForText("준비됨", 6000);
  await fixtureSet("tamperNextTicket=true");
  await click("다운로드 링크 발급");
  await waitForText("비공개 다운로드 열기");
  const tamperButton = await click("보고서 미리보기");
  const refused = tamperButton && await waitForText("SHA-256이 다운로드 티켓과 다릅니다", 6000);
  text = await bodyText();
  check("E6", "missing", "preview refuses bytes whose SHA-256 differs from the ticket", refused && !text.includes("부분 리포트인 이유"));
  await screenshot("06-report-preview-integrity-refused");

  // index.html (not owned here) links Google Fonts CSS; font fetches are static assets, not API/model calls.
  const fonts = requests.filter(url => /^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(url));
  const foreign = requests.filter(url => !url.startsWith(base) && !url.startsWith("data:") && !url.startsWith("about:") && !fonts.includes(url));
  check("S1", "safety", "no API/model/AWS request left the local synthetic origin", foreign.length === 0, `${foreign.slice(0, 3).join(", ")}${fonts.length ? ` (font asset requests: ${fonts.length})` : ""}`);
  check("S2", "safety", "no uncaught page exceptions", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
} finally {
  const summary = { generated_at: new Date().toISOString(), adapter: "LOCAL SYNTHETIC (apps/web/test/submission-review-flow.mjs)", base,
    passed: checks.filter(item => item.ok).length, failed: checks.filter(item => !item.ok).length, checks, api_calls: apiLog };
  writeFileSync(join(outDir, "results.json"), JSON.stringify(summary, null, 2));
  console.log(`\n${summary.passed} passed, ${summary.failed} failed · results: ${join(outDir, "results.json")}`);
  socket.close(); chrome.kill(); server.close();
  await sleep(300);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome may hold files briefly on Windows */ }
  if (builtDir) try { rmSync(builtDir, { recursive: true, force: true }); } catch { /* ignore */ }
  process.exitCode = checks.some(item => !item.ok) ? 1 : 0;
}
