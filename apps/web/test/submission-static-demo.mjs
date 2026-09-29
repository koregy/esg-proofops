// 정적 제출 데모(VITE_DEMO_STATIC=true 빌드)의 심사 경로를 실제 Chrome으로 확인한다.
// 사용법: VITE_DEMO_STATIC=true pnpm build 후 `node test/submission-static-demo.mjs [--out 스크린샷_폴더]`
// 추가 의존성 없이 Chrome headless + DevTools Protocol(WebSocket)만 사용하며 /api/ 요청이 없어야 통과한다.
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
  await viewport(1440, 900, false);
  await check("landing links to the stored NAVER E3 claim and shows scope/cost/audit limits", async () => {
    await open("/");
    const href = await evaluate("document.querySelector('.hero-actions .primary-link')?.getAttribute('href')");
    assert(href === `/demo/${featuredId}`, `primary CTA ${href}`);
    const body = await text();
    const c = snapshot.coverage;
    for (const needle of [`${c.pages_processed} / ${c.pages_total}쪽`, `${c.claims_decided} / ${c.claims_discovered}건`, "사람 정답셋 아님", `$${snapshot.run.r72_cost_usd.toFixed(2)}`, "Operation(환경운영부서)", "SUBSTANTIATED", "사용자 최종 검토 전"]) assert(body.includes(needle), `missing ${needle}`);
    assert(!body.includes("LIVE CASE"), "stored snapshot must not be labelled live");
    const ladder = await evaluate("[...document.querySelectorAll('.mock-ladder li')].map(li => li.innerText.replace(/\\s+/g, ' '))");
    assert(ladder.length === 3 && ladder[2].includes("M3") && ladder[2].includes("p.230, 242"), `ladder ${JSON.stringify(ladder)}`);
    await screenshot("desktop-landing");
  });

  await check("CTA opens claim detail with focus, evidence pages and full provenance", async () => {
    await evaluate("document.querySelector('.hero-actions .primary-link').click()");
    await waitFor("location.pathname.includes('eb706579') && !!document.querySelector('[data-detail-heading]')", "detail");
    await sleep(200);
    assert(await evaluate("document.activeElement?.hasAttribute('data-detail-heading')"), "detail heading not focused");
    const detail = await evaluate("document.querySelector('.claim-detail').innerText");
    for (const needle of ["E3", "SUBSTANTIATED", featuredId, "AA1000AS v3", "다른 페이지", snapshot.run.rule_pack_hash.slice(0, 16), "선택 주장 JSON 내보내기"]) assert(detail.includes(needle), `detail missing ${needle}`);
    const current = await evaluate(`[...document.querySelectorAll('.claim-item')].filter(a => a.getAttribute('href') === '/demo/${featuredId}').map(a => a.getAttribute('aria-current'))`);
    assert(current.every(value => value === "true"), `aria-current ${JSON.stringify(current)}`);
    const lefts = await evaluate("[...new Set([...document.querySelectorAll('.claim-item')].map(a => Math.round(a.getBoundingClientRect().left)))]");
    assert(lefts.length === 1, `claim list items are not stacked: ${JSON.stringify(lefts)}`);
    const notice = await evaluate("document.querySelector('.notice').innerText");
    assert(notice.includes(`${snapshot.audit.agreed}건 동의`) && notice.includes("gold"), `notice ${notice}`);
    await screenshot("desktop-demo-featured-claim");
  });

  await check("export downloads the stored claim unchanged with run provenance", async () => {
    await evaluate("document.querySelector('.export-button').click()");
    const expected = `proofops-naver-2025-claim-${featuredId}.json`;
    const until = Date.now() + 10000;
    while (!readdirSync(downloads).includes(expected) && Date.now() < until) await sleep(100);
    const exported = JSON.parse(readFileSync(join(downloads, expected), "utf8"));
    const stored = snapshot.claims.find(claim => claim.id === featuredId);
    assert(JSON.stringify(exported.claim) === JSON.stringify(stored), "claim differs from snapshot");
    assert(exported.run.rule_pack_hash === snapshot.run.rule_pack_hash && exported.snapshot.partial === true, "provenance");
    assert(exported.export_notice.includes("다시 계산하지 않았습니다"), "notice");
  });

  await check("unknown claim id shows a not-found state instead of a blank panel", async () => {
    await open("/demo/00000000-0000-0000-0000-000000000000");
    assert((await text()).includes("이 스냅샷에 없는 주장입니다"), "no not-found message");
  });

  await check("uncertain audit and range claims keep their caveats", async () => {
    const uncertain = snapshot.claims.find(claim => claim.review.audit === "uncertain");
    await open(`/demo/${uncertain.id}`);
    assert((await evaluate("document.querySelector('.claim-detail').innerText")).includes("확인 필요"), "uncertain caveat");
    const range = snapshot.claims.find(claim => claim.decision.grade_range);
    await open(`/demo/${range.id}`);
    const detail = await evaluate("document.querySelector('.claim-detail').innerText");
    assert(detail.includes("가능") && detail.includes("확정 등급이 아닙니다"), "range caveat");
  });

  await viewport(390, 844, true);
  await check("mobile landing has no horizontal overflow", async () => {
    await open("/");
    assert(await noHorizontalOverflow(), "landing overflows horizontally");
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
