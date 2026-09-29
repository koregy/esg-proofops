import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { buildReplayModel, replayDuration, stageAt } from "./replayData.ts";

test("saved N사 run becomes a complete replay without inventing counts", () => {
  const snapshot = JSON.parse(readFileSync(new URL("../../../public/demo/naver-2025.json", import.meta.url), "utf8"));
  const funnel = label => snapshot.funnel.find(step => step.label === label).count;
  const model = buildReplayModel(snapshot);
  assert.equal(model.stages.length, 9);
  assert.equal(model.stages[0].count, null);
  assert.equal(model.stages[1].count, snapshot.coverage.pages_processed);
  assert.equal(model.stages[1].count, 54);
  assert.equal(model.stages[2].count, snapshot.coverage.claims_discovered);
  assert.equal(model.stages[3].count, funnel("원문 검증"));
  assert.equal(model.stages[4].count, funnel("예비 분류 합의"));
  assert.equal(model.stages[4].count, 60);
  assert.equal(model.stages[5].count, funnel("요소 태그 발행"));
  assert.equal(model.stages[5].count, 36);
  // 저장 스냅샷의 규칙 판정은 17건뿐이며 나머지 314건은 보류·미판정으로 남는다.
  assert.equal(model.stages[6].count, snapshot.coverage.claims_decided);
  assert.equal(model.stages[6].count, 17);
  assert.equal(snapshot.coverage.claims_needs_review, 314);
  assert.equal(model.stages[7].count, null);
  assert.equal(model.stages[8].count, null);
  assert.equal(model.claimsDecided, 17);
  assert.equal(model.claimsDisplayGraded, 17);
  assert.equal(model.displayCounts, null);
  assert.equal(model.demoPass, null);
  assert.equal(model.demoPassStats, null);
  assert.equal(model.paidCalls, snapshot.run.r72_paid_calls);
  assert.equal(model.costUsd, snapshot.run.r72_cost_usd);
  assert.equal(model.parseSeconds, snapshot.run.r72_elapsed_seconds.parse_extraction);
  assert.equal(model.taggingSeconds, snapshot.run.r72_elapsed_seconds.tagging);
  assert.equal(replayDuration(model.stages), 37700);
  assert.equal(stageAt(model.stages, replayDuration(model.stages)).index, 9);
});

test("missing optional data stays unknown and added demo pass data is used", () => {
  const model = buildReplayModel({ coverage: { claims_decided: 12 }, demo_pass: { claims_decided: 12 } });
  assert.equal(model.pagesProcessed, null);
  assert.equal(model.paidCalls, null);
  assert.deepEqual(model.demoPass, { label: "추가 분석", count: 12 });
  assert.equal(model.stages[6].count, 12);
  assert.equal(model.displayCounts, null);
  assert.equal(model.demoPassStats, null);
});

test("older funnel labels remain readable", () => {
  const model = buildReplayModel({
    funnel: [
      { label: "예비 분류 합의", count: 8 },
      { label: "관계 시도", count: 7 },
      { label: "요소 태그 발행", count: 6 },
      { label: "규칙 판정 기록", count: 5 },
    ],
  });
  assert.equal(model.stages[4].count, 8);
  assert.equal(model.stages[5].count, 6);
  assert.equal(model.stages[6].count, 5);
});
