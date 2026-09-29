// 정적 제출 데모(VITE_DEMO_STATIC=true 빌드)의 심사 경로를 실제 Chrome으로 확인한다.
// 사용법: VITE_DEMO_STATIC=true pnpm build 후 `node test/submission-static-demo.mjs [--out 스크린샷_폴더]`
// 추가 의존성 없이 Chrome headless + DevTools Protocol(WebSocket)만 사용하며 /api/ 요청과 외부 출처 요청이 없어야 통과한다.
// 통합 공개 UI(제출 A 화면 + 저장된 NAVER 스냅샷) 기준 선택자다. 저장 등급·원문 대조·부분 범위 안전장치 의미는 유지한다.
import { spawn } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize, resolve } from "node:path";

const root = normalize(join(import.meta.dirname, "../dist"));
const outArg = process.argv.indexOf("--out");
const outDir = resolve(outArg > 0 ? process.argv[outArg + 1] : join(import.meta.dirname, "../../../.local/submission-20260929/screenshots"));
const snapshot = JSON.parse(readFileSync(join(import.meta.dirname, "../public/demo/naver-2025.json"), "utf8"));
const featuredId = "eb706579-f992-5604-b90d-24a0a65a6839";
const chromePath = process.env.CHROME_PATH || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].find(existsSync);
if (!existsSync(join(root, "index.html"))) throw new Error("dist 없음: VITE_DEMO_STATIC=true pnpm build를 먼저 실행하세요");
if (!chromePath) throw new Error("Chrome 실행 파일을 찾지 못했습니다 (CHROME_PATH 지정)");
mkdirSync(outDir, { recursive: true });

const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml" };
const server = createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, "http://x").pathname);
  if (path.startsWith("/api/")) { response.writeHead(410); return response.end(); }
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
listeners.push(message => { if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url); });
await page("Network.enable");
await page("Page.enable");
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
async function screenshot(name) {
  const { data } = await page("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  const file = join(outDir, `${name}.png`);
  writeFileSync(file, Buffer.from(data, "base64"));
  shots.push(file);
}
const text = () => evaluate("document.body.innerText");
const noHorizontalOverflow = () => evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1");

const results = [];
const shots = [];
async function check(name, run) {
  try { await run(); results.push({ name, ok: true }); console.log(`ok   ${name}`); }
  catch (error) { results.push({ name, ok: false, error: String(error.message || error) }); console.log(`FAIL ${name}: ${error.message || error}`); }
}
function assert(condition, message) { if (!condition) throw new Error(message); }

try {
  const featured = snapshot.claims.find(claim => claim.id === featuredId);
  const c = snapshot.coverage;
  await viewport(1440, 900, false);
  await check("home stays honest and the guide trace and funnel match the stored NAVER run", async () => {
    await open("/");
    const home = await text();
    assert((await evaluate("document.querySelector('main h1')?.innerText.trim()")) === "ProofOps", "home title");
    assert(home.includes("제3자 보증"), "home must keep the service-scope disclaimer");
    assert(!home.includes("LIVE CASE") && !home.includes("등급 표시") && !home.includes(`${c.claims_discovered}건 판정`), "home must not claim live or full grading");
    // 활용 사례(판정 경로·처리 과정·보고서 미리보기)는 서비스 가이드라인으로 옮겨졌다.
    await open("/guide");
    await sleep(600);
    const body = await text();
    for (const needle of ["Operation(환경운영부서)", "SUBSTANTIATED", "제3자 보증"]) assert(body.includes(needle), `missing ${needle}`);
    const funnel = await evaluate("[...document.querySelectorAll('.mini-card:not(.report-mini) .mini-bar')].map(row => [row.querySelector('span').innerText, Number(row.querySelector('strong').innerText)])");
    assert(JSON.stringify(funnel) === JSON.stringify([["추출 주장", c.claims_discovered], ["원문 대조", snapshot.funnel[1].count], ["검토 필요", c.claims_needs_review], ["규칙 판정", c.claims_decided]]), `funnel preview ${JSON.stringify(funnel)}`);
    // 판정 경로 예시는 저장된 실제 주장의 요소 근거 쪽수와 일치해야 한다.
    const trace = await evaluate("[...document.querySelectorAll('.trace-rows li')].map(li => li.innerText.replace(/\\s+/g, ' '))");
    assert(featured.decision.grade === "E3" && trace.length === 3, `trace ${JSON.stringify(trace)}`);
    for (const [index, id] of ["M1", "M2", "M3"].entries()) {
      const pages = [...new Set(featured.elements.find(element => element.id === id).evidence.map(ref => String(ref.page)))];
      assert(trace[index].includes(id) && pages.every(page => trace[index].includes(page)), `trace ${id} pages ${pages} vs ${trace[index]}`);
    }
    await open("/");
    await screenshot("desktop-landing");
  });

  await check("header and home navigation reach analyze, case and guide; report and replay stay one step away", async () => {
    const nav = await evaluate("[...document.querySelectorAll('#site-menu a')].map(a => a.getAttribute('href'))");
    assert(JSON.stringify(nav) === JSON.stringify(["/", "/analyze", "/demo", "/guide"]), `header nav ${JSON.stringify(nav)}`);
    const links = await evaluate("[...document.querySelectorAll('main a')].map(a => a.getAttribute('href'))");
    for (const href of ["/analyze", "/demo", "/guide"]) assert(links.includes(href), `home link missing ${href}`);
    assert(!(await evaluate("!!document.querySelector('a[href=\"/documents/new\"]')")), "static build must not offer the session workspace");
    const footer = await evaluate("[...document.querySelectorAll('.site-footer a')].map(a => a.getAttribute('href'))");
    assert(footer.includes("https://github.com/koregy/esg-proofops/tree/submission/deadline-20260929"), `footer code link ${JSON.stringify(footer)}`);
    await evaluate("document.querySelector('#site-menu a[href=\"/guide\"]').click()");
    await waitFor("location.pathname === '/guide' && !!document.querySelector('.guide-main a[href=\"/report/naver\"]') && !!document.querySelector('.guide-main a[href=\"/analyze/replay\"]')", "guide links to report and replay");
    await evaluate("document.querySelector('#site-menu a[href=\"/demo\"]').click()");
    await waitFor("location.pathname === '/demo' && !!document.querySelector('.claims-layout') && !!document.querySelector('.demo-heading a[href=\"/report/naver\"]')", "demo via header links to report");
  });

  await check("claim detail shows the stored E3 with evidence pages and review status before user final review", async () => {
    await open(`/demo/${featuredId}`);
    await waitFor("!!document.querySelector('.claim-detail .decision-panel')", "detail");
    const detail = await evaluate("document.querySelector('.claim-detail').innerText");
    for (const needle of ["E3", "SUBSTANTIATED", "AA1000AS v3", "p.230", "p.2 ", "원문 인용 검증 기록 있음", "사용자 최종 검토 전"]) assert(detail.includes(needle), `detail missing ${needle}`);
    assert(await evaluate(`[...document.querySelectorAll('.claim-item.selected')].length === 1 && document.querySelector('.claim-item.selected').getAttribute('href').startsWith('/demo/${featuredId}')`), "selected claim item");
    const lefts = await evaluate("[...new Set([...document.querySelectorAll('.claim-item')].map(a => Math.round(a.getBoundingClientRect().left)))]");
    assert(lefts.length === 1, `claim list items are not stacked: ${JSON.stringify(lefts)}`);
    const heading = await evaluate("document.querySelector('.demo-heading').innerText");
    assert(heading.includes(`${c.pages_processed}/${c.pages_total}쪽`) && heading.includes("사용자 최종 검토 전") && !heading.includes("검토 완료"), `heading ${heading}`);
    assert(await evaluate(`!!document.querySelector('a[href="https://www.navercorp.com/esg/esgReports"]')`), "official source link");
    await screenshot("desktop-demo-featured-claim");
  });

  await check("audit report export copies stored grades unchanged with run provenance", async () => {
    await page("Page.navigate", { url: base + "/report/naver" });
    await waitFor("!!document.querySelector('.audit-toolbar')", "report");
    await evaluate("[...document.querySelectorAll('.audit-toolbar button')].find(b => b.innerText.startsWith('JSON')).click()");
    const expected = "naver-proofops-audit.json";
    const until = Date.now() + 10000;
    while (!readdirSync(downloads).includes(expected) && Date.now() < until) await sleep(100);
    await sleep(200);
    const exported = JSON.parse(readFileSync(join(downloads, expected), "utf8"));
    assert(exported.rule_pack_hash === snapshot.run.rule_pack_hash && JSON.stringify(exported.coverage) === JSON.stringify(snapshot.coverage), "provenance");
    assert(exported.claims.length === snapshot.claims.length, "claim count");
    for (const row of exported.claims) {
      const stored = snapshot.claims.find(claim => claim.id === row.id);
      const want = stored.decision.grade || (stored.decision.grade_range ? `${stored.decision.grade_range.floor}–${stored.decision.grade_range.ceiling} 범위` : "미판정");
      assert(row.grade === want && row.estimated === false, `export grade differs ${row.id}: ${row.grade} vs ${want}`);
    }
  });

  await check("unknown claim id shows a not-found state instead of another claim", async () => {
    await open("/demo/00000000-0000-0000-0000-000000000000");
    const body = await text();
    assert(body.includes("없는 주장") || body.includes("찾을 수 없"), "no not-found message");
    assert(!(await evaluate("!!document.querySelector('.claim-detail .decision-panel')")), "another claim is shown as if it were the requested one");
  });

  await check("uncertain audit, range and source-unverified claims keep their caveats", async () => {
    const uncertain = snapshot.claims.find(claim => claim.review.audit === "uncertain");
    await open(`/demo/${uncertain.id}`);
    assert((await evaluate("document.querySelector('.claim-detail').innerText")).includes("확인 필요"), "uncertain caveat");
    const range = snapshot.claims.find(claim => claim.decision.grade_range);
    await open(`/demo/${range.id}`);
    const detail = await evaluate("document.querySelector('.claim-detail').innerText");
    assert(detail.includes("가능") && detail.includes("확정 등급은 아닙니다"), "range caveat");
    const unverified = snapshot.claims.find(claim => !claim.source_verified);
    await open(`/demo/${unverified.id}`);
    const shown = await evaluate("document.querySelector('.claim-detail .decision-panel strong').innerText.trim()");
    assert(shown.startsWith("원문 대조 필요") && !/E[0-3]/.test(shown), `source-unverified claim shown as ${shown}`);
  });

  await viewport(390, 844, true);
  await check("mobile landing has no horizontal overflow and the menu opens", async () => {
    await open("/");
    assert(await noHorizontalOverflow(), "landing overflows horizontally");
    await evaluate("document.querySelector('.menu-toggle').click()");
    await waitFor("document.querySelector('.menu-toggle').getAttribute('aria-expanded') === 'true'", "menu open");
    await screenshot("mobile-landing");
  });

  await check("mobile deep link scrolls the claim detail into view without overflow", async () => {
    await open(`/demo/${featuredId}`);
    await sleep(300);
    const top = await evaluate("document.querySelector('.claim-detail').getBoundingClientRect().top");
    assert(top >= -2 && top < 200, `detail top ${top}`);
    assert(await noHorizontalOverflow(), "demo overflows horizontally");
    await screenshot("mobile-demo-featured-claim");
  });

  await check("mobile list selection moves to the detail", async () => {
    await open("/demo");
    await evaluate("window.scrollTo(0, 0); document.querySelectorAll('.claim-item')[1].click()");
    await sleep(400);
    const top = await evaluate("document.querySelector('.claim-detail').getBoundingClientRect().top");
    assert(top >= -2 && top < 200, `detail top ${top}`);
  });

  await check("static demo made no /api/ or model requests", async () => {
    const api = requests.filter(url => url.startsWith(base + "/api/"));
    assert(api.length === 0, `api requests ${api.join(", ")}`);
    const outside = requests.filter(url => /^https?:/.test(url) && !url.startsWith(base));
    assert(outside.length === 0, `outside requests ${outside.join(", ")}`);
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
