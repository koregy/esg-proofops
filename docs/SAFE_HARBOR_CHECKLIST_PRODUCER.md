# 세이프하버 체크리스트 항목 producer (GAP-001 · A)

근거: R00 §12 (2026-09-28 사용자 채택) `project_checklist_completeness_v1`. 문서화 완결성만 다루며, 법적 보호·E등급·공식 기준 충족은 판단하지 않는다.

## 구성

- 기존 consumer(변경 없음): `ReviewService.resolve_ai_delegated_review(safe_harbor_review=…)` → `reviews._review_safe_harbor`. 모든 ref를 원본에 대해 재검증하고, 입력 snapshot hash·범주(모든 tag-run header = packet)·고정 item 전체·run pack의 정책을 확인한 뒤 불변 receipt를 남긴다.
- 신규 producer `packages/proofops/application/tagging/checklist_producer.py`(순수 application, 쓰기 없음):
  - `decision_template(inputs)`: 고정 checklist id(모두 `unknown`)와, 선택할 수 있는 packet ref(`local_claim`/`same_table`, canonical hash)를 보여 준다.
  - `build_checklist_review(inputs, decision, source_authority=)`: 정확한 `safe_harbor_review` 요청과 producer receipt(identity·decision/request hash·검증된 ref·preview)를 만든다.
- 신규 CLI `scripts/produce_safe_harbor_checklist.py {template|build}`: 기본은 dry run이다. `--apply`는 `--delegated-reviewer`와 `--idempotency-key`가 필요하고, If-Match는 기본으로 현재 review revision을 쓴다. 이미 resolved된 review는 `--re-review`로 다시 쓴다. 기록 출처는 `ai_delegated`이며 사람·gold·법적 승인이 아니다.

## 거부 규칙 (아무것도 쓰지 않음)

| 조건 | 코드 |
|---|---|
| run pack에 체크리스트 정책 없음(checked-in impl2) | `CHECKLIST_POLICY_PACK_REQUIRED` |
| packet 범주 없음·미설정 범주 | `NOT_SAFE_HARBOR_CLAIM` |
| header·packet·decision 범주 불일치 | `SAFE_HARBOR_CATEGORY_MISMATCH` |
| 입력 snapshot 변경 | `CHECKLIST_STALE_INPUTS` |
| 다른 run·claim | `CHECKLIST_CLAIM_MISMATCH` |
| item 누락·추가 | `CHECKLIST_ITEMS_MISMATCH` |
| `absent` | `CHECKLIST_ABSENCE_NOT_ENABLED` |
| present/conflict에 근거 없음, 또는 unknown에 근거 있음 | `CHECKLIST_EVIDENCE_STATE_MISMATCH` |
| packet 밖·범위 밖·재검증 실패 ref, 변조된 quote | `CHECKLIST_SOURCE_REJECTED` |
| 오래된 If-Match, resolved review를 `--re-review` 없이 다시 쓰기 | 서비스 `STALE_REVIEW_REVISION` (412) |

## 결과 의미

모든 고정 item이 검증된 present일 때만 `reasonable_basis_documented=true`다. unknown이나 conflict가 있으면 `null`이다. `false`는 검증된 부재가 필요한데 이 producer는 부재를 만들지 않는다. evidence grade와 label은 null(세이프하버 경로 `blocked_rule_gap`), `legal_effect=not_determined`, `mapping_status=unresolved`, GAP-001 유지다.

부재(`absent`)는 전체 문서 search-coverage producer와 absence consumer 계약(R00 §12 공통 가드)이 통합될 때까지 거부한다. 참고로 기존 서비스 `_review_safe_harbor`는 `search_coverage_verified=true` boolean만으로 absent를 받는다. 이 우회 경로는 root가 담당 작업자에게 알렸다.

## 적용 대상 pack (dry 파생 프로필, 저장·활성화 안 함)

`scripts/review_rulepack.py --checklist-policy project_checklist_completeness_v1 --derived-version <새 버전>`에 dry run을 먼저 돌리고, 기록된 `--apply`로만 활성화한다. 아래는 2026-09-29에 계산한 예시 입력과 결과다.

- before: `proofops-domain-v2.0-impl2`, `b77304f866a37784bfd426954451da7ec264b1041890a0a07acd26484ec70366`
- 입력: version `proofops-domain-v2.0-impl2-checklist-v1`, reviewer `coordinator(AI-delegated)`, reviewed_at `2026-09-29T00:00:00Z`, source_authority `R00 §12 2026-09-28 user adoption GAP-001·A`, note `dry derived profile only; not stored or activated`
- after: `70837e4f1229f7cb8e151577dd2e720a082101493ee332e96e27e1659a6d2e38` (status validated, approved_by null)
- hash는 위 입력(reviewer·시각·authority·note)에 결정적으로 의존한다. 실제 적용 시 입력이 다르면 hash도 달라진다.

producer는 **이미 이 정책 pack으로 생성된 run**에만 쓸 수 있다. 기존 run의 pack은 바꾸지 않으므로, 기존 run은 새 run 또는 명시적 재채점이 필요하다.
