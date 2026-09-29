// RunForm 범위·사전 점검 헬퍼 단위 검사. 브라우저·서버·모델 호출 없음.
// 사용법: node --test test/run-scope.test.mjs  (Node 22.18+/23.6+ 타입 제거로 .ts를 직접 읽는다)
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  checkLocalSubmission, fullScopeSupported, initialPages, missingRequiredPages, preflightFailures,
} from "../src/features/runs/runScope.ts";

const legacy = { worker_enabled: true, candidate_rule_pack_id: null, selected_pages: [3, 4] };
const bootstrap = {
  worker_enabled: true, candidate_rule_pack_id: "pack", selected_pages: [],
  supported_scopes: ["declared_subset"], scope_reason: "upstage_probe_declared_subset_only",
  page_selection: "explicit_required", required_pages: [],
};

test("older local servers and non-local API keep full scope and saved pages", () => {
  assert.equal(fullScopeSupported(null), true);
  assert.equal(fullScopeSupported(checkLocalSubmission(legacy)), true);
  assert.deepEqual(initialPages(legacy), [3, 4]);
});

test("upstage_probe local runs disable full scope", () => {
  assert.equal(fullScopeSupported(checkLocalSubmission(bootstrap)), false);
});

test("bootstrap never prefills the seed page even if a stale server sends it", () => {
  assert.deepEqual(initialPages({ ...bootstrap, selected_pages: [1] }), []);
  assert.deepEqual(initialPages({ ...bootstrap, page_selection: "saved_run_pages", selected_pages: [1] }), [1]);
});

test("required claim pages must stay inside the declared pages", () => {
  const pinned = { ...bootstrap, required_pages: [1, 35] };
  assert.deepEqual(missingRequiredPages(pinned, [35]), [1]);
  assert.deepEqual(missingRequiredPages(pinned, [1, 35, 36]), []);
  assert.deepEqual(missingRequiredPages(null, [2]), []);
});

test("malformed local hints are rejected", () => {
  for (const bad of [
    { ...bootstrap, supported_scopes: [] }, { ...bootstrap, supported_scopes: ["everything"] },
    { ...bootstrap, page_selection: "auto" }, { ...bootstrap, required_pages: [0] },
    { ...legacy, selected_pages: ["1"] },
  ]) assert.throws(() => checkLocalSubmission(bad));
});

test("preflight failure details list only failed checks with server reasons", () => {
  const lines = preflightFailures([
    { name: "runtime_approval", status: "fail", reason: "local test authorization invalid" },
    { name: "consent_approval", status: "pass", reason: "local test authorization" },
    { name: "live_model_probe", status: "not_run", reason: "no model call during preflight" },
    { name: "custom_gate", status: "fail", reason: "" },
  ]);
  assert.deepEqual(lines, ["실행 환경 승인: local test authorization invalid", "custom_gate"]);
  assert.deepEqual(preflightFailures(undefined), []);
});
