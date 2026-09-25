# R00 · 정본 대조 결과와 구현 vs 판단 지도

> 2026-09-20. `32_PIPELINE_COMPLETION_PLAN.md` R00 산출물. 범위: 현재 v2.0 구현/계약과
> 사용자가 반영을 요청한 v2.2(`ROOT handoff/team-v3/reference/PROJECT_V2_2.md`,
> `RECONCILIATION_SPEC.md`) 사이의 명시 요구/충돌/진짜 미정을 분리한다. 도메인을 새로
> 기획하지 않으며, `00_MASTER_SPEC.md` §9와 `31_DOMAIN_IMPLEMENTATION_GAPS.md`의
> AI 검토 해석 절을 보충한다. Python/런타임 파일은 수정하지 않았다.

## 1. 이미 명시 요구인 것 (재승인 질문 아님)

`ROOT handoff/team-v3/REQUIREMENT_TRACE.csv`의 G1~G8/P1~P6/M1~M6은 v2.0에서 이미
`config/rubric`+`domain/rules`로 구현 대상이었고 v2.2가 이를 변경하지 않았다. C1~C5는
v2.2 §3.7/4.5/4.10 신설 요구로, `REQUIREMENT_TRACE.csv` C1~C5 행 기준 **B 소유**이며,
A가 접근 가능한 공유 저장소 기준(마지막 확인 commit ff61f41)으로는 계약·예제만 보인다
— 이는 A 쪽에서 본 마지막 공유 상태이고 B의 미공유 진행을 "없음"으로 단정하는 것은
아니다. 이는 GAP이 아니라 소유·최신 상태 확인이 B 쪽에 있는 명시 구현 항목이다.

## 2. 진짜 도메인 미정 (GAP-001~010, 해석 제안은 31장)

31장에 AI 검토 해석(`review_origin=ai_project_interpretation`)을 추가했다. GAP-004/008/009/010은
사실관계·외부확인형이라 이번 해석 대상에서 제외했다. 런타임 `decision_status` enum에
새 값을 넣지 않았다 — 28장 계약은 그대로 `blocked_rule_gap`을 반환한다.

## 3. R02 — 소스 정책 변경의 계약/버전/마이그레이션/롤백

**변경 없음.** 이번 작업은 소스 검증 정책을 바꾸지 않았다. 향후 R02가 정책을 바꿀 때
지켜야 할 경계만 기록한다.

| 항목 | 내용 |
|---|---|
| 대상 계약 | `packages/proofops/adapters/local/claim_source_verification.py`, `source_verification.py`, `table_source_verification.py`; 검증 결과가 28장 `ElementState.present = citation_verified AND binding_accepted`의 입력 |
| 현재 검증기 | `tests/integration/test_frozen_native_replay.py`가 과거 실행을 과거 검증기로 재생하는 계약을 보증 |
| 새 정책 적용 범위 | 새 정책은 새 실행에만 고정. 과거 `claims.py` 전체 파일 hash로 고정된 과거 실행은 과거 검증기로 재생 — 새 정책을 구버전에 강제 통과시키는 방식은 R02 완료 기준 위반(`32_PIPELINE_COMPLETION_PLAN.md` R00 항목 3) |
| Migration | 정책 버전 필드를 검증 결과에 추가하는 것은 additive. 기존 reader가 새 필드를 못 읽으면 명시적 unsupported로 거절(§7.2 원칙), 조용히 무시하지 않음 |
| Rollback | 새 producer(새 검증 로직) 비활성화 + 이전 reader 유지. 기존 검증 결과·revision을 삭제하거나 재작성하지 않음 |
| 확인 명령(정책 변경 시에만) | `tests/integration/test_frozen_native_replay.py` + 변경한 검증 모듈의 기존 unit/integration |

## 4. R04 — candidate-stage 의미와 계약 영향 (정정: 2026-09-20 두 번째 검토)

**정정 사항:** 앞선 초안은 `validate_preliminary`가 이미 미검증 후보를 검색까지 진행시킨다고
잘못 기술했다. 실제 코드를 재확인한 결과는 다음과 같다.

| 개념 | 실제 코드 근거 | 실제 현재 동작 |
|---|---|---|
| 소스 검증 게이트 | `apps/worker/src/proofops_worker/tag_runner.py` `LocalTagRunner._execute` (per-claim loop) | `claim.source_quality != "verified"`이면 `reason="SOURCE_VALIDATION_REQUIRED"`로 즉시 `status="blocked"` 기록 후 **continue** — preliminary 호출, evidence retrieval, tagging 전부 미실행 |
| `validate_preliminary` | `packages/proofops/application/tagging/preliminary.py:177-219` | source_quality 검증 이후에만 호출됨. 호출자 게이트에 더해 내부 `_sources`도 source_quality=verified, tenant/document/manifest/source hash와 인용 검증을 요구한다. 필드 스키마 검증만 하는 함수가 아니다 |
| 확정 귀속 | `packages/proofops/application/evidence/binding.py` (`accept_binding`) | verified span 요구 유지 (변경 없음) |
| 등급 입력 게이트 | `28_RULE_ENGINE_CONTRACT.md` §1: `present = citation_verified AND binding_accepted` | 유지 (변경 없음) |

**결론:** 현재 런타임은 R04가 목표로 하는 "미검증이어도 원문 위치 추적되는 후보는 저비용
검색·검토로 진행"을 아직 구현하지 않았다. 지금은 `source_quality`가 `verified`가 아니면
해당 claim 전체가 preliminary/검색 단계 진입 전에 차단된다. R04는 **확정 검증을 유지한 채 후보 검색을 앞 단계에서 허용하는 것**이 실제 작업이며, "이미 분리돼 있다"는 이전 서술은 오류였다.
아래 계약 영향은 이 변경이 실제로 이뤄질 때 지킬 경계다.

**계약/버전 영향(향후 R04가 이 차단을 검색 이후로 옮길 때)**

- 새 필드는 confirmed 스키마와 별도 버전으로 정의한다. 기존 `ConfirmedTags`/`accept_binding` 입력 스키마에 후보 필드를 끼워 넣지 않는다.
- 후보 packet hash가 바뀌면 새 태깅 revision을 만든다(기존 등급에 재사용 금지, `00_MASTER_SPEC.md` §5.6과 합치).
- 구버전 reader는 새 후보 필드를 모르면 무시가 아니라 명시적으로 "후보 미확정"으로 표시해야 한다 — 확정으로 오인되면 §5.4의 검증 계약(문자열 존재≠근거) 위반.
- Rollback: 후보 필드 producer만 비활성화. 이미 확정된 `accept_binding` 결과는 영향받지 않는다(후보 계층과 확정 계층이 분리돼 있으므로 rollback 범위가 작다).
- 확인 대상(변경 시): `tests/integration/test_local_tag_runner.py`, `tests/acceptance/test_preliminary.py` — 핵심 단언은 "미검증 source가 있으면 확정 grade/present가 생기지 않는다"(계약 불변, 이번에 재확인만 함).

## 5. 이 문서가 하지 않은 것

법령·기준 원문의 조항 번호를 만들지 않았다. 세이프하버의 법적 면책 효과를 판정하지
않았다. C군 상태를 label/evidence_grade에 연결하지 않았다. 어떤 rulepack도 활성화하지
않았다. `decision_status` 런타임 enum을 변경하지 않았다.

## 6. 활성화 게이트 (정정: D 승인 필수 아님)

사용자가 조정자(코디네이터)의 도메인 판단을 원문·사례 근거 기반으로 명시 위임했다.
따라서 31장의 AI 검토 해석은 "D(도메인 담당자) 승인 없이는 전부 보류"가 아니라,
**조정자 채택 + rulepack 버전 기록**이 기술적 활성화 게이트다. 이는 가짜 인간 승인을
만드는 것이 아니라 실제 결정 경로를 문서화하는 것이다 — 채택 시 31장 승인 이력 표에
`담당자=coordinator(AI-delegated)`, `rulepack hash`, `timestamp`, `boundary test vector`를
그대로 기록하고 `review_origin=ai_project_interpretation`을 유지한다(법·회계 전문가
승인으로 위장하지 않음). D의 원문·사례 제공은 여전히 유효하지만, 없다는 이유로 조정자의
채택 자체가 막히지는 않는다.

## 7. 사용자 도메인 결정 — 2026-09-25 (규칙집 `proofops-domain-v2.0-impl2`)

사용자(프로젝트 책임자)가 채팅에서 직접 결정했다. AI 위임 해석이 아니라 사용자 결정이다.

- **A-1 승인:** 원문 §4.4 트랙별 등급 사다리를 그대로 채점 규칙으로 쓴다. 범위는 시연·내부 검토용이며
  법적 효력, 조항 번호 검증, 고객 공시 승인이 아니다. GAP-001/002/004~010은 그대로 미해결이다.
- **A-2 (GAP-003 해소, 선택지 가):** 등급은 §4.4 사다리 요소만으로 계산한다. §4.5의 추가 요소
  (G7·G8·P5·P6·M4·M5·M6)는 등급을 바꾸지 않고 `missing_elements`/`unresolved_elements`에 남긴다.
  추가 요소가 미확정이면 `review_status=needs_review`다. 감점 규칙을 만들지 않았다.

구현: 트랙 rubric의 `additional_rubric_gap: GAP-003` → `additional_element_policy:
report_without_grade_effect`, manifest `unresolved_gap_ids`에서 GAP-003 제거, 규칙집 파일 10개
`version`을 impl2·`effective_date`를 2026-09-25로 변경. 엔진은 정책 키가 없는 규칙집(impl1)에서
기존 차단 동작을 그대로 유지하므로 기존 run에 고정된 impl1 판정은 재현성이 유지된다. 알 수 없는
정책 값은 fail-closed다.

호환성·롤백: API/DB 형태 변경 없음, migration 없음. 기존 태깅·판정 revision과 보고서는 불변이다.
새 규칙집은 새 run 또는 명시적 재채점에만 적용된다. 롤백은 config를 impl1로 되돌리면 되고,
이미 impl2로 만든 판정 revision은 보존된다.

실제 효과(읽기 전용 측정): 원문 검토 태그가 있는 NAVER 29·KB 4 주장 head를 impl1/impl2로 다시
평가했다. 저장된 판정과 impl1 재평가는 33/33 일치했고, impl2에서도 등급은 0건 그대로다. 막는 원인은
추가 요소가 아니라 사다리 요소의 unknown이다(관리체계 22건은 M2·M3 unknown으로 E1~E3 가능).
absent는 검색 범위 검증이 있어야만 인정되므로, 등급 산출에는 다음 단계(가능 등급 범위 표시 또는
문서 전역 근거 탐색의 부재 확인)가 필요하다.

## 8. 사용자 결정 B — 가능 등급 범위 (2026-09-25, 엔진 `explicit-ladders-exceptions-3`)

사다리 요소가 unknown이라 `blocked_evidence`인 주장에, 엔진이 이미 열거하던 도달 가능 등급의
최소·최대를 `grade_range = {floor, ceiling, open_elements}`로 노출한다. 등급·라벨이 아니며
`evidence_grade`/`label`은 계속 null이다. unknown을 absent로 바꾸지 않는다.

- 노출 조건: 상태가 `blocked_evidence`이고 가능 등급이 둘 이상이며 모든 조합이 사다리 분기와
  일치할 때만. 세이프하버·제품 변형 등 별도 경로(GAP-001/006/007), 사다리 분기가 없는 조합,
  impl1의 GAP-003 차단에서는 null(후보 비공개 유지). `decided`이면 null.
- `open_elements`: 범위를 좁히는 unresolved 사다리 요소만. 추가 요소(M4 등)는 포함하지 않는다.
- API 계약: `Decision.grade_range`를 선택(optional) nullable 필드로 추가(openapi.yaml, api_models
  schema 동일). required 목록 변경 없음, `decided`이면 null 강제. 엔진 v3 이전에 저장된 판정은
  저장된 API 응답 그대로 필드가 없다(재작성 없음). 웹 타입도 optional.
- 저장: 판정 revision JSON에 `grade_floor`/`grade_ceiling`/`grade_open_elements`가 추가된다.
  migration 없음. 기존 레코드는 기본값으로 로드된다(rescore의 `Decision(**stored)` 포함).
- 보고서: JSON `claims[].grade_range`, CSV 마지막 열 `grade_range`(기존 열 위치 불변), HTML·React
  미리보기에 "가능 등급 범위: E1 ~ E3 (확정 등급 아님)". 이전 스냅샷은 null.
- 롤백: 엔진 v2 코드로 되돌리면 새 판정에 범위가 생기지 않는다. 이미 저장된 v3 판정 revision과
  보고서는 보존한다. 스키마의 optional 필드는 남겨도 이전 응답과 호환된다.

실측(읽기 전용, 저장된 검토 태그): NAVER 29·KB 4 = 33 주장 중 27건에 범위가 생긴다.
E1~E3 23건, E2~E3 3건, E0~E3 1건. 나머지 6건은 별도 세이프하버 경로 2건과 사다리 분기 공백 조합
4건으로 null이다. 실제 저장 경로(재채점)는 기존 run이 비합성(real) run이고, 기존 rescore가
`local_synthetic` run만 허용하므로 아직 쓰지 않았다(별도 결정 필요).

## 9. 사용자 결정 — 실제 run 재채점 허용 (2026-09-25)

`RescoreService`는 기존에 `local_synthetic` run만 재채점했다. 이제 비합성(real) run도 **대상 규칙집이
active이고 approved_by/approved_at이 기록된 경우에만** 재채점한다. 아니면 `409 RULEPACK_APPROVAL_REQUIRED`
이며 아무것도 쓰지 않는다. 원문 인용 재검증, 입력 snapshot/tag 핀, 테넌트·문서 identity, CAS, 불변
revision 검사는 그대로다. 트랙 비교는 검토 revision(origin human/ai_delegated)이면 그 revision에
기록된 트랙을 쓰고, 미검토 태그는 여전히 원래 packet 트랙과 같아야 한다(§4.2 재분류 경로).
API/DB 형태 변경·migration 없음. 롤백은 조건을 이전처럼 `local_synthetic` 전용으로 되돌리면 되고,
이미 기록된 재채점 판정과 receipt는 보존한다.

실제 적용 결과(NAVER 검토 DB 사본 `.local/r37-naver-grade-range`, 원본 불변): impl2를 사용자 승인으로
등록·활성화한 뒤 실제 HTTP 재채점을 호출했다. 입력 검사는 통과했으나 29건 모두 `RETAG_REQUIRED`로
거절됐고 쓰기는 없었다. 원래 태깅이 규칙이 참조하는 사실을 수집하지 않았기 때문이다(관리체계 23건:
governance_claim·compensation_link_claim·willingness_only, 목표 5건: offset_or_carbon_neutral_claim·
science_based_claim, 성과 1건: reduction_or_improvement_claim). 수집되지 않은 사실을 unknown으로 간주하지
않는 기존 가드(`test_uncollected_fact_is_not_manufactured_as_unknown_or_absent`)는 유지했다. 특히
`willingness_only`는 규칙 파일에만 있고 이를 생산하는 태깅 프롬프트·스키마·검토 경로가 없다.

후속(같은 날, 사용자 결정 1번): 적용성 검토가 관리체계 사다리의 `willingness_only`도 원자 주장 단위로
받도록 확장했다(해당 사다리 분기가 있는 track만, track 변경 시 제거). NAVER 29건을 위임 AI 재검토로
기록한 뒤 실제 run 재채점이 성공했다. 결과는 ROOT `outputs/agent-results/R37-grade-range/REPORT.md`.
