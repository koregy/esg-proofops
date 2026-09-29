// 공개 데모 확장 화면(판정 안내 · 저장된 처리 재생 · 검토 시뮬레이터 · 감사 보고서)을 실제 Chrome으로 확인한다.
// 통합 공개 UI(제출 A 화면: /analyze/replay · /review/:id · /report/:company · /demo/kia, 유지 화면: /guide/decision · /validation/kia) 기준 선택자다.
// 사용법: VITE_DEMO_STATIC=true pnpm build 후 `node test/submission-extended-demo.mjs [--dist 빌드_폴더] [--out 스크린샷_폴더]`
// submission-static-demo.mjs와 같은 방식(Chrome headless + DevTools Protocol, 추가 의존성 없음)이며 /api/ 요청과 쓰기 요청이 없어야 통과한다.
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize, resolve } from "node:path";

const repo = normalize(join(import.meta.dirname, "../../.."));
const arg = name => { const index = process.argv.indexOf(name); return index > 0 ? process.argv[index + 1] : null; };
const root = resolve(arg("--dist") ?? join(import.meta.dirname, "../dist"));
const outDir = resolve(arg("--out") ?? join(repo, ".local/submission-20260929/extended-demo"));
const snapshotBytes = readFileSync(join(import.meta.dirname, "../public/demo/naver-2025.json"));
const snapshot = JSON.parse(snapshotBytes.toString("utf8"));
const snapshotSha = createHash("sha256").update(snapshotBytes).digest("hex");
const table = JSON.parse(readFileSync(join(import.meta.dirname, "../public/demo/engine-table.json"), "utf8"));
const rulepack = JSON.parse(readFileSync(join(repo, "api/rulepack.json"), "utf8"));
const original = readFileSync(join(repo, "sources/PROJECT_DOMAIN_V2_ORIGINAL.md"), "utf8").replace(/\*\*/g, "");
const gapsContract = JSON.parse(readFileSync(join(repo, "contracts/domain_gaps.json"), "utf8"));
const engineSource = readFileSync(join(repo, "packages/proofops/domain/rules/engine.py"), "utf8");
const chromePath = process.env.CHROME_PATH || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].find(existsSync);
if (!existsSync(join(root, "index.html"))) throw new Error("dist 없음: VITE_DEMO_STATIC=true pnpm build를 먼저 실행하세요");
if (!chromePath) throw new Error("Chrome 실행 파일을 찾지 못했습니다 (CHROME_PATH 지정)");
mkdirSync(outDir, { recursive: true });

const decidedClaim = snapshot.claims.find(claim => claim.track === "management" && claim.decision.grade === "E3");
const rangeClaim = snapshot.claims.find(claim => claim.track === "management" && claim.decision.grade_range);
const notRunTracked = snapshot.claims.find(claim => claim.track && claim.decision.status === "not_run");
const untracked = snapshot.claims.find(claim => !claim.track);
const nullGrade = snapshot.claims.filter(claim => claim.decision.grade === null);
const gradeText = claim => claim.decision.grade ?? (claim.decision.grade_range ? `${claim.decision.grade_range.floor}–${claim.decision.grade_range.ceiling} 가능 범위` : "미판정");

const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml" };
const server = createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, "http://x").pathname);
  if (path.startsWith("/api/") || path.startsWith("/v1/")) { response.writeHead(410); return response.end(); }
  let file = normalize(join(root, path));
  if (!file.startsWith(root) || !existsSync(file) || !extname(file)) file = join(root, "index.html");
  response.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" });
  createReadStream(file).pipe(response);
});
await new Promise(done => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;

const profile = mkdtempSync(join(tmpdir(), "proofops-chrome-"));
const downloads = mkdtempSync(join(tmpdir(), "proofops-download-"));
const chrome = spawn(chromePath, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
const wsUrl = await new Promise((done, fail) => {
  let text = "";
  chrome.stderr.on("data", chunk => { text += chunk; const match = text.match(/DevTools listening on (ws:\/\/\S+)/); if (match) done(match[1]); });
  chrome.on("exit", code => fail(new Error(`chrome exited ${code}`)));
  setTimeout(() => fail(new Error("chrome start timeout")), 20000);
});

const socket = new WebSocket(wsUrl);
await new Promise((done, fail) => { socket.onopen = done; socket.onerror = fail; });
let nextId = 0;
const pending = new Map();
const listeners = [];
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const { done, fail } = pending.get(message.id);
    pending.delete(message.id);
    message.error ? fail(new Error(JSON.stringify(message.error))) : done(message.result);
  } else listeners.forEach(listener => listener(message));
};
const send = (method, params = {}, sessionId) => new Promise((done, fail) => { const id = ++nextId; pending.set(id, { done, fail }); socket.send(JSON.stringify({ id, method, params, sessionId })); });

const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
const page = (method, params) => send(method, params, sessionId);
const requests = [];
const consoleErrors = [];
listeners.push(message => {
  if (message.method === "Network.requestWillBeSent") requests.push({ url: message.params.request.url, method: message.params.request.method });
  if (message.method === "Runtime.exceptionThrown") consoleErrors.push(message.params.exceptionDetails.text);
});
await page("Network.enable");
await page("Page.enable");
await page("Runtime.enable");
await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads });

const sleep = ms => new Promise(done => setTimeout(done, ms));
async function evaluate(expression) {
  const result = await page("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text + " " + expression);
  return result.result.value;
}
async function waitFor(expression, label, timeout = 10000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { if (await evaluate(expression)) return; await sleep(100); }
  throw new Error(`timeout: ${label}`);
}
async function viewport(width, height, mobile) {
  await page("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
}
async function open(path) {
  await page("Page.navigate", { url: base + path });
  await waitFor("document.readyState === 'complete' && !document.querySelector('[role=status]') && !!document.querySelector('main, .audit-page')", `load ${path}`);
  await sleep(200);
}
const shots = [];
async function screenshot(name) {
  const { data } = await page("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  const file = join(outDir, `${name}.png`);
  writeFileSync(file, Buffer.from(data, "base64"));
  shots.push(file);
}
const text = (selector = "body") => evaluate(`document.querySelector(${JSON.stringify(selector)})?.innerText ?? ""`);
const noHorizontalOverflow = () => evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1");
async function waitDownload(name) {
  const until = Date.now() + 10000;
  while (!readdirSync(downloads).includes(name) && Date.now() < until) await sleep(100);
  await sleep(200);
  return readFileSync(join(downloads, name), "utf8");
}

const results = [];
async function check(name, run) {
  try { await run(); results.push({ name, ok: true }); console.log(`ok   ${name}`); }
  catch (error) { results.push({ name, ok: false, error: String(error.message || error) }); console.log(`FAIL ${name}: ${error.message || error}`); }
}
function assert(condition, message) { if (!condition) throw new Error(message); }

try {
  await check("data provenance: engine table and snapshot share the pinned rule pack; shipped snapshot has no display-grade fallback", async () => {
    assert(table.rule_pack_sha256 === rulepack.sha256, `table ${table.rule_pack_sha256} != api/rulepack.json ${rulepack.sha256}`);
    assert(snapshot.run.rule_pack_hash === rulepack.sha256, "snapshot rule pack hash differs");
    assert(engineSource.includes('ENGINE_VERSION = "explicit-ladders-exceptions-3"'), "engine version changed; update domainContract.ts");
    // 통합 UI 코드에는 A의 display_grade 분기가 남아 있으므로, 배포되는 NAVER 스냅샷 자체에 표시용 등급이 없어야 한다.
    const shipped = readFileSync(join(root, "demo/naver-2025.json"));
    assert(shipped.equals(snapshotBytes), "shipped NAVER snapshot differs from public/demo/naver-2025.json");
    const raw = snapshotBytes.toString("utf8");
    for (const field of ["display_grade", "display_note", "display_label", "\"estimated\"", "review_bucket", "demo_mode"]) assert(!raw.includes(field), `NAVER snapshot contains ${field}`);
    assert(snapshot.claims.filter(claim => claim.decision.grade).length === snapshot.coverage.claims_decided, "stored grade count differs from coverage");
  });

  await viewport(1440, 900, false);
  await check("extended routes are discoverable from home, guide, footer and demo; direct routes and aliases still resolve", async () => {
    await open("/");
    const home = await evaluate("[...document.querySelectorAll('a')].map(a => a.getAttribute('href'))");
    for (const href of ["/analyze", "/demo", "/guide", "/guide/decision"]) assert(home.includes(href), `home/footer link missing ${href}`);
    await open("/guide");
    const guide = await evaluate("[...document.querySelectorAll('main a')].map(a => a.getAttribute('href'))");
    for (const href of ["/demo", "/analyze/replay", "/report/naver", "/analyze"]) assert(guide.includes(href), `guide link missing ${href}`);
    await evaluate("document.querySelector('.site-footer a[href=\"/guide/decision\"]').click()");
    await waitFor("location.pathname === '/guide/decision' && !!document.querySelector('#guide-title')", "decision guide via footer");
    // 메뉴에서 뺀 화면(문장 분석·기아 사례·기아 원문 검증)도 직접 주소로는 계속 열린다.
    for (const [path, selector] of [["/live", ".live-main"], ["/demo/kia", ".kia-case"], ["/validation/kia", ".kia-validation"]]) {
      await open(path);
      assert(await evaluate(`!!document.querySelector(${JSON.stringify(selector)})`), `${path} no longer resolves`);
    }
    for (const [legacy, target] of [["/replay", "/analyze/replay"], ["/report", "/report/naver"], ["/validation", "/validation/kia"]]) {
      await open(legacy);
      assert((await evaluate("location.pathname")) === target, `${legacy} does not redirect to ${target}`);
    }
    await open(`/demo/${decidedClaim.id}`);
    await waitFor("!!document.querySelector('.claim-detail .detail-actions button')", "review mode entry");
    assert(await evaluate("!!document.querySelector('.claims-layout') && !!document.querySelector('.demo-heading a[href=\"/report/naver\"]')"), "demo page lost claim list or report link");
  });

  await check("Kia metadata keeps unresolved blocks and claims separate from grades", async () => {
    const recorded = JSON.parse(readFileSync(join(repo, "evidence/kia-native-api-validation-20260929.json"), "utf8"));
    const published = JSON.parse(readFileSync(join(root, "demo/kia-validation-20260929.json"), "utf8"));
    assert(JSON.stringify(published) === JSON.stringify(recorded), "Kia public metadata differs from recorded evidence");
    assert(published.source_body_included === false && published.whole_report_complete === false && published.accuracy_evaluated === false, "Kia trial scope changed");
    await open("/validation/kia");
    await waitFor("document.querySelectorAll('.kia-trials section').length === 2", "Kia metadata loaded");
    const counts = await evaluate("[...document.querySelectorAll('.kia-counts')].map(dl => [...dl.querySelectorAll('dd')].map(dd => Number(dd.innerText)))");
    const expected = recorded.runs.map(run => [run.ocr.corroborated, run.ocr.requested - run.ocr.corroborated, run.ocr.skipped]);
    assert(JSON.stringify(counts) === JSON.stringify(expected), "Kia unresolved/skipped counts changed");
    const body = await text();
    for (const needle of ["부분 검증", "확정 등급 없음", "태깅 보류", "비공개 원문을 제외한"]) assert(body.includes(needle), `missing scope ${needle}`);
    assert(await evaluate("!!document.querySelector('a[download=\"kia-validation-20260929.json\"]')"), "Kia metadata download missing");
    await screenshot("kia-validation");
  });

  await check("A-supplied Kia case and report are labelled as provided demonstration data with provisional grades", async () => {
    for (const path of ["/demo/kia", "/report/kia"]) {
      await open(path);
      await waitFor("!!document.querySelector('.kia-provenance')", `Kia provenance ${path}`);
      const note = await text(".kia-provenance");
      assert(note.includes("제공된 시연 데이터") && note.includes("독립 검증하지 않았습니다") && note.includes("예비 등급은 확정 판정이 아닙니다"), `Kia provenance ${path}: ${note}`);
    }
    assert((await text()).includes("예비 등급"), "Kia report must separate provisional grades");
  });

  await check("decision guide quotes only sourced contract text", async () => {
    await open("/guide/decision");
    const grades = await evaluate("[...document.querySelectorAll('#guide-grades tbody tr')].map(tr => [...tr.children].map(td => td.innerText.trim()))");
    assert(grades.length === 4, "grade rows");
    for (const [grade, label, meaning] of grades) assert(original.includes(`| ${grade} | ${label} | ${meaning} |`), `grade row not in original §4.3: ${grade} ${meaning}`);
    const ladder = await evaluate("[...document.querySelectorAll('.guide-ladder li span')].map(span => span.innerText.trim())");
    assert(ladder.length === 12, `ladder rows ${ladder.length}`);
    for (const rule of ladder) assert(original.includes(`| ${rule} |`), `ladder rule not in original §4.4: ${rule}`);
    const gaps = await evaluate("[...document.querySelectorAll('.guide-gap-table tbody tr')].map(tr => ({ id: tr.dataset.gap, issue: tr.children[2].innerText.trim(), state: tr.children[3].innerText.trim() }))");
    assert(gaps.length === gapsContract.length, "gap rows");
    for (const gap of gaps) {
      const source = gapsContract.find(item => item.id === gap.id);
      assert(source && source.issue === gap.issue, `gap text differs ${gap.id}`);
      assert(gap.state === (rulepack.unresolved_gap_ids.includes(gap.id) ? "미해결" : "해소 (R00 §7 A-2)"), `gap state ${gap.id} ${gap.state}`);
    }
    const status = await text("[data-testid=guide-snapshot-status]");
    assert(status.includes(`미판정 ${snapshot.claims.filter(c => c.decision.status === "not_run").length}건`) && status.includes(`규칙 판정 ${snapshot.coverage.claims_decided}건`), `status line ${status}`);
    assert((await text()).includes(snapshot.run.rule_pack_hash.slice(0, 16)), "rule pack hash");
    await open("/guide");
    assert((await text()).includes("확인하지 못한 근거를 “근거 없음”으로 처리하지 않습니다"), "service guide must keep unknown ≠ absent");
    await screenshot("desktop-guide");
  });

  await check("replay is labelled as a stored replay with simulated timing, not a live run", async () => {
    await open("/analyze/replay");
    assert(!(await evaluate("!!document.querySelector('.replay-finish')")), "replay should start before the finish");
    const body0 = await text();
    assert(body0.includes("저장") && (body0.includes("재생") || body0.includes("시뮬레이션")) && !body0.includes("실시간"), "replay must say it replays a stored run, not a live model run");
  });

  await check("replay shows the stored funnel, stored decision count, partial scope and recorded run values", async () => {
    await open("/analyze/replay");
    await evaluate("[...document.querySelectorAll('.replay-hero-bottom button')].find(b => b.innerText.includes('결과 바로 보기')).click()");
    await waitFor("!!document.querySelector('.replay-finish')", "finish");
    const funnel = await evaluate("[...document.querySelectorAll('.replay-funnel-row')].map(row => [row.querySelector('span').innerText, row.querySelector('strong').innerText.replace(/[^0-9]/g, '')])");
    const expected = snapshot.funnel.map(step => [step.label, String(step.count)]);
    assert(JSON.stringify(funnel) === JSON.stringify(expected), `stage counts ${JSON.stringify(funnel)} != ${JSON.stringify(expected)}`);
    const finish = await text(".replay-finish");
    assert(finish.includes(`${snapshot.coverage.claims_decided}건`) && !finish.includes(`${snapshot.claims.length}건`), `finish must count stored decisions only: ${finish}`);
    const body = await text();
    assert(body.includes(`${snapshot.coverage.pages_processed} / ${snapshot.coverage.pages_total}쪽`), "partial page scope");
    assert(body.includes(snapshot.run.r72_paid_calls.toLocaleString("ko-KR")) && body.includes(`$${snapshot.run.r72_cost_usd.toFixed(2)}`), "recorded run values");
    await screenshot("desktop-replay");
  });

  await check("review simulator is local-only over the Python precomputed table; stored decision stays immutable", async () => {
    await open("/review");
    assert((await evaluate("location.pathname")).startsWith("/review/"), "/review should open a stored E3 claim");
    await open(`/review/${decidedClaim.id}`);
    await waitFor("document.querySelector('.review-sim-grade strong')?.innerText === 'E3'", "stored states reproduce stored E3");
    assert((await text(".review-sim")).includes("현재 화면에만 적용"), "simulation must say changes stay on this screen");
    const m3 = `#review-${decidedClaim.id}-M3`;
    await evaluate(`const s = document.querySelector('${m3}'); s.value = 'absent'; s.dispatchEvent(new Event('change', { bubbles: true }))`);
    await waitFor("document.querySelector('.review-sim-grade strong').innerText === 'E2'", "simulated E2");
    const expectedRow = table.tracks.management.rows["ppa00"];
    assert((await text(".review-sim-grade")).includes(expectedRow[2]), "simulated label from table");
    assert((await evaluate("document.querySelectorAll('.review-sim-history li').length")) === 1, "history entry");
    await screenshot("desktop-review-simulator");
    await open(`/demo/${decidedClaim.id}`);
    await waitFor("!!document.querySelector('.claim-detail .decision-panel')", "stored detail");
    assert((await text(".claim-detail .decision-panel strong")).startsWith("E3"), "stored decision must not change after simulation");

    await open(`/review/${rangeClaim.id}`);
    await waitFor(`document.querySelector('.review-sim-grade strong')?.innerText === '${rangeClaim.decision.grade_range.floor}–${rangeClaim.decision.grade_range.ceiling}'`, "range claim starts as a range");
    await evaluate(`const s = document.querySelector('#review-${rangeClaim.id}-M3'); s.value = 'present'; s.dispatchEvent(new Event('change', { bubbles: true }))`);
    await waitFor("document.querySelector('.review-sim-grade strong').innerText === 'E3'", "range claim simulated E3");
    await open(`/demo/${rangeClaim.id}`);
    await waitFor("!!document.querySelector('.claim-detail .decision-panel')", "range stored detail");
    assert(!(await text(".claim-detail .decision-panel strong")).startsWith("E3"), "stored range decision must not change");

    await open(`/demo/${notRunTracked.id}`);
    await waitFor("!!document.querySelector('.claim-detail .decision-panel')", "not_run detail");
    assert(!/E[0-3]/.test(await text(".claim-detail .decision-panel strong")), "not_run claim must not show a stored grade");
    await open(`/review/${untracked.id}`);
    assert((await text()).includes("주장 유형을 확인하면") && !(await evaluate("!!document.querySelector('.review-sim select')")), "untracked claim must not be simulated");
  });

  await check("audit report uses stored immutable claims, keeps null grades unresolved and exports provenance", async () => {
    await open("/report/naver");
    const body = await text();
    assert(body.includes("근거 부재로 바꾸지 않습니다") && body.includes(`${snapshot.coverage.pages_processed}/${snapshot.coverage.pages_total}쪽`), "report scope and unknown ≠ absent");
    const ranges = snapshot.claims.filter(c => !c.decision.grade && c.decision.grade_range).length;
    assert(body.includes(`범위 보류 ${ranges}건 · 미판정 ${nullGrade.length - ranges}건`), "null-grade counts");
    assert(!body.includes("예비 등급"), "NAVER report must not show provisional grades");
    const rows = await evaluate("[...document.querySelectorAll('.audit-claims tbody tr')].map(tr => tr.children[3].querySelector('strong').innerText.trim())");
    assert(rows.length === snapshot.claims.length, `rows ${rows.length}`);
    const rowGrade = claim => claim.decision.grade ?? (claim.decision.grade_range ? `${claim.decision.grade_range.floor}–${claim.decision.grade_range.ceiling} 범위` : "미판정");
    const sorted = [...rows].sort().join("|");
    assert(sorted === snapshot.claims.map(rowGrade).sort().join("|"), "row grades differ from stored grades");
    assert(rows.filter(grade => /^E[0-3]$/.test(grade)).length === snapshot.coverage.claims_decided, "null grade shown as an E grade");
    await evaluate("[...document.querySelectorAll('.audit-toolbar button')].find(b => b.innerText.startsWith('JSON')).click()");
    const exported = JSON.parse(await waitDownload("naver-proofops-audit.json"));
    assert(exported.rule_pack_hash === snapshot.run.rule_pack_hash && exported.pending_count === nullGrade.length - ranges && exported.range_count === ranges, "export provenance and null counts");
    for (const claim of exported.claims) {
      const stored = snapshot.claims.find(item => item.id === claim.id);
      assert(claim.grade === rowGrade(stored), `export differs ${claim.id}`);
    }
    await evaluate("[...document.querySelectorAll('.audit-toolbar button')].find(b => b.innerText.startsWith('CSV')).click()");
    const csv = (await waitDownload("naver-proofops-claims.csv")).replace(/^﻿/, "").split("\r\n");
    assert(csv.length === snapshot.claims.length + 1 && csv[0].startsWith('"주장 ID"'), "csv rows");
    const nullRow = csv.find(line => line.startsWith(`"${nullGrade[0].id}"`));
    assert(nullRow && !/"E[0-3]"/.test(nullRow), `csv null grade row ${nullRow}`);
    await screenshot("desktop-audit-report");
  });

  await viewport(390, 844, true);
  for (const [name, path, phrase] of [["kia-validation", "/validation/kia", "부분 검증"], ["guide", "/guide/decision", "미확인은 부재가 아닙니다"], ["replay", "/analyze/replay", `${snapshot.coverage.pages_processed} / ${snapshot.coverage.pages_total}쪽`], ["review-simulator", `/review/${decidedClaim.id}`, "현재 화면에만 적용"], ["audit-report", "/report/naver", "근거 부재로 바꾸지 않습니다"], ["landing", "/", "제3자 보증"]]) {
    await check(`mobile ${name} has no horizontal overflow, keeps navigation and its scope caveat`, async () => {
      await open(path);
      if (name === "replay") await evaluate("[...document.querySelectorAll('.replay-hero-bottom button')].find(b => b.innerText.includes('결과 바로 보기'))?.click()");
      await sleep(200);
      assert(await noHorizontalOverflow(), `${path} overflows horizontally`);
      if (name !== "audit-report") assert(await evaluate("document.querySelector('.menu-toggle')?.getBoundingClientRect().height > 0"), "mobile menu toggle hidden");
      else assert(await evaluate("document.querySelector('.audit-toolbar a[href=\"/demo\"]')?.getBoundingClientRect().height > 0"), "report back link hidden");
      assert((await text()).includes(phrase), `missing caveat ${phrase}`);
      await screenshot(`mobile-${name}`);
    });
  }

  await check("extended demo made no /api/, /v1/, outside or write requests and threw no page errors", async () => {
    const api = requests.filter(request => request.url.startsWith(base + "/api/") || request.url.startsWith(base + "/v1/"));
    const outside = requests.filter(request => /^https?:/.test(request.url) && !request.url.startsWith(base));
    const writes = requests.filter(request => !["GET", "HEAD"].includes(request.method));
    assert(api.length === 0, `api requests ${api.map(request => request.url).join(", ")}`);
    assert(outside.length === 0, `outside requests ${outside.map(request => request.url).join(", ")}`);
    assert(writes.length === 0, `write requests ${writes.map(request => `${request.method} ${request.url}`).join(", ")}`);
    assert(consoleErrors.length === 0, `page errors ${consoleErrors.join(" | ")}`);
  });
} finally {
  socket.close();
  chrome.kill();
  server.close();
  await sleep(300);
  for (const dir of [profile, downloads]) try { rmSync(dir, { recursive: true, force: true }); } catch { /* Windows가 프로필 잠금을 늦게 푼다 */ }
}

const failed = results.filter(result => !result.ok);
console.log(JSON.stringify({ passed: results.length - failed.length, failed: failed.length, screenshots: shots }, null, 2));
process.exit(failed.length ? 1 : 0);
