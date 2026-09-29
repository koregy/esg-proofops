// 최근 실행 목록 표시 규칙 단위 검사. 브라우저·서버·모델 호출 없음.
// 사용법: node --test test/recent-runs.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { latestWithResults, recentRunView, sortRecent } from "../src/features/runs/runListView.ts";

const coverage = (over = {}) => ({
  pages_total: 133, pages_processed: 1, pages_unreadable: 0, pages_unprocessed: 132,
  chunks_discovered: 91, chunks_processed: 90, claims_discovered: 26, claims_decided: 0,
  claims_needs_review: 26, full_scope: false, complete: false, ...over,
});
const run = (id, created_at, status, over) => ({
  run_id: id, document_version_id: "dv", status, current_stage: "tag", revision: 1, mutation_epoch: 0,
  coverage: coverage(over), rule_pack_sha256: "0".repeat(64), created_at,
});
// 실제 서버(8767) GET /v1/runs 응답 순서: 오래된 실행이 먼저 온다.
const serverOrder = [
  run("cancel", "2026-09-29T11:58:14Z", "cancelled", { claims_discovered: 0, claims_needs_review: 0, pages_processed: 0, pages_unprocessed: 133 }),
  run("one", "2026-09-29T12:09:13Z", "partial", { claims_discovered: 1, claims_needs_review: 1, pages_unreadable: 1, pages_processed: 0 }),
  run("posco", "2026-09-29T12:18:56Z", "partial"),
];

test("newest run comes first even when the API lists oldest first", () => {
  assert.deepEqual(sortRecent(serverOrder).map(r => r.run_id), ["posco", "one", "cancel"]);
  assert.equal(serverOrder[0].run_id, "cancel", "input is not mutated");
});

test("partial run with undecided claims links to claims and says review is pending, not done", () => {
  const view = recentRunView(serverOrder[2]);
  assert.equal(view.resultsAvailable, true);
  assert.equal(view.primary.href, "/runs/posco/claims");
  assert.equal(view.primary.label, "주장 26건 결과 보기");
  assert.equal(view.statusLabel, "부분 완료");
  assert.equal(view.reviewText, "판정 미확정 26건 · 검토 필요 26건");
  assert.match(view.scopeLabel, /부분 범위/);
  assert.equal(view.coverageText, "페이지 1/133쪽 처리 · 주장 26건");
  assert.deepEqual(view.links.map(l => l.href), ["/runs/posco", "/runs/posco/reviews", "/runs/posco/report"]);
});

test("unreadable pages are shown, not folded into processed or absent", () => {
  assert.equal(recentRunView(serverOrder[1]).coverageText, "페이지 0/133쪽 처리 · 판독 불가 1쪽 · 주장 1건");
});

test("runs without claims link to the run record, never to an empty result", () => {
  const cancelled = recentRunView(serverOrder[0]);
  assert.equal(cancelled.resultsAvailable, false);
  assert.equal(cancelled.primary.href, "/runs/cancel");
  assert.equal(cancelled.primary.label, "실행 기록 보기");
  assert.equal(cancelled.reviewText, null);
  assert.equal(recentRunView(run("q", "2026-09-29T13:00:00Z", "running", { claims_discovered: 0 })).primary.label, "진행 상황 보기");
});

test("claims counted during extraction do not promise readable results before the run settles", () => {
  const extracting = recentRunView(run("x", "2026-09-29T13:00:00Z", "running", { claims_discovered: 5, claims_needs_review: 0 }));
  assert.equal(extracting.resultsAvailable, false);
  assert.equal(extracting.primary.href, "/runs/x");
  assert.equal(extracting.primary.label, "진행 상황 보기 · 주장 5건 처리 중");
  assert.deepEqual(extracting.links, []);
  const failed = recentRunView(run("f", "2026-09-29T13:00:00Z", "failed", { claims_discovered: 3 }));
  assert.equal(failed.resultsAvailable, false);
  assert.equal(failed.primary.label, "실행 기록 보기");
});

test("completed full-scope run is labelled processed, still reporting remaining review", () => {
  const view = recentRunView(run("done", "2026-09-29T13:00:00Z", "completed", { full_scope: true, claims_decided: 26, claims_needs_review: 3 }));
  assert.equal(view.statusLabel, "처리 완료");
  assert.equal(view.scopeLabel, "전체 범위");
  assert.equal(view.reviewText, "판정 26건 · 검토 필요 3건");
});

test("latest run with results skips a newer run that has no claims yet", () => {
  const newer = run("new", "2026-09-29T14:00:00Z", "running", { claims_discovered: 4, claims_needs_review: 0 });
  assert.equal(latestWithResults([...serverOrder, newer])?.run_id, "posco");
  assert.equal(latestWithResults([serverOrder[0]]), null);
});
