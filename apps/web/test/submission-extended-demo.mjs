// 공개 데모 확장 화면(판정 안내 · 저장된 처리 재생 · 검토 시뮬레이터 · 감사 보고서)을 실제 Chrome으로 확인한다.
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
  await waitFor("document.readyState === 'complete' && !document.querySelector('[role=status]') && !!document.querySelector('main')", `load ${path}`);
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
  await check("data provenance: engine table and snapshot share the pinned rule pack; engine version matches guide", async () => {
    assert(table.rule_pack_sha256 === rulepack.sha256, `table ${table.rule_pack_sha256} != api/rulepack.json ${rulepack.sha256}`);
    assert(snapshot.run.rule_pack_hash === rulepack.sha256, "snapshot rule pack hash differs");
    assert(engineSource.includes('ENGINE_VERSION = "explicit-ladders-exceptions-3"'), "engine version changed; update domainContract.ts");
    const bundle = readdirSync(join(root, "assets")).filter(name => name.endsWith(".js")).map(name => readFileSync(join(root, "assets", name), "utf8")).join("\n");
    assert(!bundle.includes("display_grade") && !bundle.includes("display_note"), "bundle still references A display_grade fallback");
    assert(!existsSync(join(root, "demo/kia-2025.json")), "Kia data must not ship in this submission yet");
  });

  await viewport(1440, 900, false);
  await check("extended routes are discoverable from the landing and demo pages", async () => {
    await open("/");
    const links = await evaluate("[...document.querySelectorAll('.xd-strip nav a')].map(a => a.getAttribute('href'))");
    assert(JSON.stringify(links) === JSON.stringify(["/guide", "/replay", "/review", "/report/naver"]), `strip links ${JSON.stringify(links)}`);
    assert(await evaluate("!!document.querySelector('.hero-actions .primary-link')"), "existing landing CTA missing");
    await evaluate("[...document.querySelectorAll('.xd-strip nav a')].find(a => a.getAttribute('href') === '/guide').click()");
    await waitFor("location.pathname === '/guide' && !!document.querySelector('#guide-title')", "guide via nav");
    assert(await evaluate("document.querySelector('.xd-strip a[href=\"/guide\"]').getAttribute('aria-current') === 'page'"), "aria-current");
    await open("/demo");
    assert(await evaluate("document.querySelectorAll('.xd-strip nav a').length === 4 && !!document.querySelector('.claims-layout')"), "demo page lost strip or claim list");
  });

  await check("decision guide quotes only sourced contract text", async () => {
    await open("/guide");
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
    await screenshot("desktop-guide");
  });

  await check("replay is labelled stored replay with simulated timing and shows stored counts", async () => {
    await open("/replay");
    const banner = await text(".xd-banner[data-mode=stored-replay]");
    for (const needle of ["STORED REPLAY", "실제 모델 실행이 아니며", "시뮬레이션된 타이밍", "demo/naver-2025.json"]) assert(banner.includes(needle), `banner missing ${needle}`);
    assert(!(await evaluate("!!document.querySelector('[data-testid=replay-finish]')")), "replay should start before the finish");
    await evaluate("[...document.querySelectorAll('.replay-controls button')].find(b => b.innerText.includes('결과 바로 보기')).click()");
    await waitFor("!!document.querySelector('[data-testid=replay-finish]')", "finish");
    const stages = await evaluate("[...document.querySelectorAll('.replay-step')].map(li => li.querySelector('strong').innerText.replace(/[^0-9]/g, ''))");
    const expected = [snapshot.coverage.pages_processed, ...snapshot.funnel.map(step => step.count)].map(String);
    assert(JSON.stringify(stages) === JSON.stringify(expected), `stage counts ${stages} != ${expected}`);
    const finish = await text("[data-testid=replay-finish]");
    const e3 = snapshot.claims.filter(c => c.decision.grade === "E3").length;
    const notRun = snapshot.claims.filter(c => c.decision.status === "not_run").length;
    const ranges = snapshot.claims.filter(c => !c.decision.grade && c.decision.grade_range).length;
    for (const needle of [`E3 ${e3}건`, `미판정(not_run) ${notRun}건`, `가능 범위만 있는 보류 ${ranges}건`, `등급 미정 ${nullGrade.length}건`]) assert(finish.includes(needle), `finish missing ${needle}`);
    const body = await text();
    assert(body.includes(snapshot.run.r72_paid_calls.toLocaleString("ko-KR")) && body.includes(`$${snapshot.run.r72_cost_usd.toFixed(2)}`), "recorded run values");
    await screenshot("desktop-replay");
  });

  await check("review simulator is local simulation over the Python precomputed table; stored decision stays immutable", async () => {
    await open("/review");
    const tracked = snapshot.claims.filter(c => c.track).length;
    assert((await evaluate("document.querySelectorAll('.review-index tbody tr').length")) === tracked, "index rows");
    await open(`/review/${decidedClaim.id}`);
    await waitFor("!!document.querySelector('[data-testid=simulated-decision]')", "simulator");
    const banner = await text(".xd-banner[data-mode=local-simulation]");
    for (const needle of ["LOCAL SIMULATION", "로컬 시뮬레이션", "Python 규칙엔진", "미리 계산한 조회표", "수락된 태깅·판정 revision이 생기지 않고", "API 업로드", "사람 승인"]) assert(banner.includes(needle), `banner missing ${needle}`);
    assert((await evaluate("document.querySelector('[data-consistency]').dataset.consistency")) === "match", "stored states should reproduce stored decision");
    assert((await text("[data-testid=stored-decision] .review-sim-grade")) === "E3", "stored grade");
    await evaluate("const s = document.querySelector('#sim-M3'); s.value = 'absent'; s.dispatchEvent(new Event('change', { bubbles: true }))");
    await waitFor("document.querySelector('[data-testid=simulated-decision] .review-sim-grade').innerText === 'E2'", "simulated E2");
    const expected = table.tracks.management.rows["ppa00"];
    assert((await text("[data-testid=simulated-decision]")).includes(expected[2]), "simulated label from table");
    assert((await text("[data-testid=stored-decision] .review-sim-grade")) === "E3", "stored decision must not change");
    assert((await evaluate("document.querySelectorAll('.review-sim-history li').length")) === 1, "history entry");
    await screenshot("desktop-review-simulator");

    await open(`/review/${rangeClaim.id}`);
    await waitFor("!!document.querySelector('[data-testid=stored-decision]')", "range simulator");
    const stored = await text("[data-testid=stored-decision]");
    assert(stored.includes(gradeText(rangeClaim)) && stored.includes("null"), `range claim stored ${stored}`);
    await evaluate("const s = document.querySelector('#sim-M3'); s.value = 'present'; s.dispatchEvent(new Event('change', { bubbles: true }))");
    await waitFor("document.querySelector('[data-testid=simulated-decision] .review-sim-grade').innerText === 'E3'", "range claim simulated E3");
    assert((await text("[data-testid=stored-decision] .review-sim-grade")) === gradeText(rangeClaim), "stored range decision must not change");

    await open(`/review/${notRunTracked.id}`);
    await waitFor("!!document.querySelector('[data-consistency]')", "not_run simulator");
    assert((await evaluate("document.querySelector('[data-consistency]').dataset.consistency")) === "not_run", "not_run consistency");
    assert((await text("[data-testid=stored-decision] .review-sim-grade")) === "미판정", "not_run stored grade");
    await open(`/review/${untracked.id}`);
    assert((await text()).includes("트랙이 정해지지 않은 주장입니다"), "untracked claim must not be simulated");
  });

  await check("audit report uses stored immutable claims, keeps null grades unresolved and links sources", async () => {
    await open("/report/naver");
    const banner = await text(".xd-banner[data-mode=stored-snapshot]");
    assert(banner.includes("다시 계산하지 않습니다") && banner.includes("null로 두고 E0이나 근거 부재로 세지 않습니다"), "report banner");
    assert((await text("[data-testid=audit-null-grade] strong")).replace(/[^0-9]/g, "") === String(nullGrade.length), "null-grade count");
    assert((await text("[data-testid=audit-provenance]")).includes(snapshotSha), "snapshot sha256 in provenance");
    assert(await evaluate(`[...document.querySelectorAll('a[href="https://www.navercorp.com/esg/esgReports"]')].length > 0`), "official source link");
    await evaluate("[...document.querySelectorAll('.audit-filter button')].find(b => b.innerText.startsWith('전체')).click()");
    await sleep(300);
    const rows = await evaluate("[...document.querySelectorAll('.audit-table tbody tr')].map(tr => ({ id: tr.dataset.claim, status: tr.dataset.status, grade: tr.querySelector('.audit-grade').innerText.trim(), demo: !!tr.querySelector(`a[href=\"/demo/${tr.dataset.claim}\"]`) }))");
    assert(rows.length === snapshot.claims.length, `rows ${rows.length}`);
    for (const row of rows) {
      const claim = snapshot.claims.find(item => item.id === row.id);
      assert(claim && row.status === claim.decision.status && row.grade === gradeText(claim) && row.demo, `row mismatch ${row.id}`);
      if (claim.decision.grade === null) assert(!/^E[0-3]$/.test(row.grade), `null grade shown as ${row.grade} for ${row.id}`);
    }
    await evaluate("[...document.querySelectorAll('.audit-actions button')].find(b => b.innerText.startsWith('JSON')).click()");
    const exported = JSON.parse(await waitDownload("naver-proofops-audit-report.json"));
    assert(exported.provenance.snapshot_sha256 === snapshotSha && exported.provenance.rule_pack_hash === snapshot.run.rule_pack_hash, "export provenance");
    assert(exported.claims.filter(claim => claim.evidenceGrade === null).length === nullGrade.length, "export null grades");
    for (const claim of exported.claims) {
      const stored = snapshot.claims.find(item => item.id === claim.id);
      assert(claim.evidenceGrade === stored.decision.grade && claim.label === stored.decision.label && claim.decisionRevision === stored.review.decision_revision, `export differs ${claim.id}`);
    }
    await evaluate("[...document.querySelectorAll('.audit-actions button')].find(b => b.innerText.startsWith('CSV')).click()");
    const csv = (await waitDownload("naver-proofops-claims.csv")).replace(/^\uFEFF/, "").split("\r\n");
    assert(csv.length === snapshot.claims.length + 1 && csv[0].startsWith('"claim_id"'), "csv rows");
    const nullRow = csv.find(line => line.startsWith(`"${nullGrade[0].id}"`));
    assert(nullRow && nullRow.includes(`"${nullGrade[0].decision.status}","",""`), `csv null grade row ${nullRow}`);
    await screenshot("desktop-audit-report");
    await open("/report/kia");
    assert((await text()).includes("NAVER 저장 스냅샷의 보고서만"), "Kia must not be integrated yet");
  });

  await viewport(390, 844, true);
  for (const [name, path] of [["guide", "/guide"], ["replay", "/replay"], ["review-simulator", `/review/${decidedClaim.id}`], ["audit-report", "/report/naver"], ["landing", "/"]]) {
    await check(`mobile ${name} has no horizontal overflow and keeps the navigation strip`, async () => {
      await open(path);
      if (name === "replay") await evaluate("[...document.querySelectorAll('.replay-controls button')].find(b => b.innerText.includes('결과 바로 보기'))?.click()");
      await sleep(200);
      assert(await noHorizontalOverflow(), `${path} overflows horizontally`);
      assert(await evaluate("[...document.querySelectorAll('.xd-strip nav a')].every(a => a.getBoundingClientRect().height > 0)"), "strip links hidden");
      if (["replay", "review-simulator", "audit-report"].includes(name)) assert(await evaluate("!!document.querySelector('.xd-banner')"), "stored/simulation banner missing on mobile");
      await screenshot(`mobile-${name}`);
    });
  }

  await check("extended demo made no /api/, /v1/ or write requests and threw no page errors", async () => {
    const api = requests.filter(request => request.url.startsWith(base + "/api/") || request.url.startsWith(base + "/v1/"));
    const writes = requests.filter(request => !["GET", "HEAD"].includes(request.method));
    assert(api.length === 0, `api requests ${api.map(request => request.url).join(", ")}`);
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
