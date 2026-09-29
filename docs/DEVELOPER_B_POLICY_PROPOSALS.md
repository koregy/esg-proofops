# B-F02 정책·해석 결정안 (제안 원문 · REC-001~008 채택·적용 현황 2026-09-29)

작성: 개발자 B (Claude Opus) · 2026-09-22 (KST) · 기준 커밋 `ca75df592345fb412cd1593c29a0f5c506f81fe3`

이 문서는 **제안**이며 승인 기록이 아니다. 여기의 어떤 항목도 실행 정책으로 적용되지 않았고,
`policy.approved`는 모든 실자료 실행에서 `false`로 남아 있다. 채택하려면 실제 승인자·역할·일시·
정책 해시·버전·적용 범위를 별도로 기록해야 한다. 외부 기준·조항은 인용하지 않았으며, 아래
근거는 저장소 안의 원문·코드·이번 실자료 관찰로만 구성했다.

## 2026-09-29 갱신 — 채택(adopted)과 실행 적용(execution-applied)의 구분

사용자는 2026-09-28 R84 추천안 REC-001~008을 일괄 채택했다(`docs/R00_DOMAIN_DECISIONS.md` 12장).
아래 DEC-* 표는 그 이전의 **제안 원문**으로 보존한다. "채택"은 프로젝트 정책 결정이고, "실행 적용"은
엔진·서비스 코드가 그 규칙을 실제로 강제하는지를 뜻한다. 채택은 실정책 승인 레지스트리 등록,
`policy.approved=true`, 임계값·CAPEX 계정 매핑 값을 만들지 않는다. 실자료 C3는 여전히
승인된 매핑이 없어서 `blocked`이다.

적용 버전: 엔진 `reconciliation-engine-1.2.0`(이전 `1.1.0`). 입력·정책·출력 스키마 1.1은 바꾸지 않았다.
`1.1.0`으로 저장된 결과 revision은 불변이며, 재평가하면 새 revision이 `1.2.0`을 기록한다.

| 항목 | 채택 규칙 요지 | 실행 적용 상태 | 코드·테스트 | 남은 것 / 계약 긴장 |
|---|---|---|---|---|
| REC-001 A | 검증된 집합을 같은 기간·조직 기준에서만 비교, 사업장↔법인 매핑은 검증된 관계가 있을 때만, 미해결이면 blocked | **적용(이번 변경)**: C1은 SR 기간≠재무 기간이면 `c1_period_mismatch`, 기간이 null이면 `c1_period_unresolved`, 연결 기준 `unknown`이면 `c1_consolidation_unknown`으로 차단한다. 사업장·법인 집합 혼합은 기존 `kind_mismatch` 차단을 유지한다 | `c1.py`; `test_c1.py::test_rec001_*` | 1.1에는 통제·경계 관계 매핑 필드가 없어서 사업장→법인 매핑은 실행 불가(항상 blocked). 필요하면 계약 확장이 필요하며 이번에 만들지 않았다 |
| REC-002 A | 설명은 등록 패키지의 검증 인용 + 경계 차이에 명시 연결일 때만 present | **기존 적용**: 설명 후보는 해시·원문·`explanation` 역할·검토 영수증 포함을 모두 통과해야 하고, 완전 검색 + 무인용이면 `needs_explanation`, 미완료면 blocked | `service.py` `reconcile`; `test_integration.py`, `test_service.py` | 설명이 "해당 경계 차이"에 연결됐는지는 문서 레지스트리의 `explanation` 역할 부여(운영자·A 입력)에 의존한다 |
| REC-003 B | 승인된 재무 계정→CAPEX 매핑, 같은 기간·통화, 다년도는 정확한 일정표가 있을 때만 | **부분 적용**: 매핑 승인·허용 목록·통화 가드는 기존 적용. **이번 변경**: CAPEX 기간이 고정된 재무 기간과 정확히 같지 않으면 `c3_capex_period_mismatch`, 재무 기간이 null이면 `c3_period_unresolved` | `c3.py`; `test_c3.py::test_rec003_*` | 승인된 계정 매핑 **값**이 없어서 실행은 blocked(외부 입력). 1.1에는 다년도 일정표 필드가 없어서 다년도 CAPEX는 항상 blocked |
| REC-004 B | 임계값 null 유지, 임계값 기반 결과 blocked, 매칭되는 공시 투자 약정의 직접 존재만 matched | **적용(이번 변경)**: 기존에는 `_policy_resolved`가 임계값 null에서 약정 경로보다 먼저 차단했다. 이제 등록·검증된 `c3_commitment` 역할 약정 원문이 있으면 임계값 없이 `matched/commitment_disclosed`가 된다. 임계값 경로는 null이면 설명 근거가 있어도 `c3_policy_unapproved`로 남는다. 5.0은 기본값이 아니다 | `c3.py`; `test_c3.py::test_rec004_*`, `test_integration.py::test_rec004_*` | 약정 경로도 REC-003 매핑·통화·기간·양수 금액 가드와 역할·원문 검증을 그대로 거친다. 매핑이 승인되지 않은 실자료에서는 여전히 blocked |
| REC-005 A | 등록된 SR/FS 전 범위 + 판독 가능 + 검색 manifest 완결일 때만 needs_explanation | **적용(이번 변경 포함)**: 영수증 레지스트리·실패 문서 없음·필수 문서 등록 확인은 기존 적용. **이번 변경**: `complete` 영수증의 `required_document_ids`가 패킷의 SR·FS 문서 버전을 모두 포함하지 않으면 `coverage_unverified` | `service.py` `_apply_coverage`; `test_integration.py::test_rec005_*` | 문서 **안의** 쪽·주석 단위 전수 여부는 영수증 작성자(검색 manifest)가 보증한다. 엔진은 문서 단위까지만 검사한다 |
| REC-006 A | 실제 시작·종료일과 연결 범위, 평가 시점의 최신 정정 공시 고정, 불가·비교불가면 blocked, 연환산 없음 | **기존 적용**: C2는 달력 날짜로 비교한다. `rcept_no`·기간·공시일·`as_of`는 문서 레지스트리와 일치해야 하고, 연환산은 없다. `comparability=not_comparable`이면 `completed/not_applicable` | `c2.py`, `service.py` `_document_reason` | "최신 정정본 선택"은 수집·운영 단계 책임이다. 엔진은 고정된 `rcept_no`만 검증한다 |
| REC-007 A | 같은 claim/기간 공시에서 정의 + 포함·제외 기준 또는 산식 분모가 있을 때만 C4 matched | **기존 적용**: 정의와 산식 근거가 둘 다 있어야 matched, 완전 검색에서 누락이면 `needs_explanation`, 미완료면 blocked. 역할 `c4_definition`/`c4_calculation` 검증을 거치고 회계 적정성은 판정하지 않는다 | `c4.py`, `service.py` | 없음 |
| REC-008 A | C5는 stage>CURRENT_STAGE면 실행 차단, 별도 `not_run` 봉투, 1.1 스키마에 C5를 추가하지 않음 | **기존 적용**: 엔진·서비스는 `NotImplementedError("stage_disabled")`를 내고, CLI는 별도 dispatch 봉투(`not_run`, `stage_disabled`)를 기록한다. 제품 저장소·HTTP는 `C5_DISABLED` 422로 거부한다 | `engine.py`, `service.py`, `evaluation/reconciliation_cli.py`, `reconciliation_store.py` | 봉투 생성은 CLI(`evaluation/`, master 소유)에만 있다. HTTP는 봉투 대신 422 오류를 준다. 둘 다 판정을 만들지 않으므로 규칙 위반은 아니다 |

**이 변경이 하지 않은 것:** 임계값·계정 매핑 값 승인, 실정책 레지스트리 등록, `reconciliation.csv`
기대값 변경, 실제 claim·Kia SR 입력 생성. 등록된 claim과 Kia SR 원문, 승인된 CAPEX 매핑은 정당한
외부 입력으로 남는다.

## 근거로 사용한 실제 자료

| 식별자 | 값 |
|---|---|
| 삼성전자 FY2024 DART | corp_code `00126380`, rcept_no `20250311001085`, 연결, 접수일 2025-03-11 |
| 삼성전자 SR | `Samsung_Electronics_Sustainability_Report_2025_KOR.pdf`, sha256 `342a99a1...38c8c5`, 87쪽, 발행 2025-06-27 |
| 오뚜기 FY2024 DART | corp_code `00141529`, rcept_no `20250318000979`, 연결 |
| 동서 FY2024 DART | corp_code `00144395`, 최초 `20250313000532`(부분 실패), 후속 `20251210000195`(전체 수집) |

오뚜기·동서는 이전 턴(`060c69b`)에서 master/Sol이 이미 열람한 자료이므로 **독립 holdout이 아니다**.
세 회사 모두 등록된 A 검증 claim이 없어 의미 판정(matched/needs_explanation)은 산출하지 않았다.
삼성 패킷의 `tenant_id`/`company_id`/`claim_id`는 과거 오프라인 탐색용 placeholder UUID이며
실제 등록된 claim이 아니다.

---

## DEC-C1 — 연결범위 동일성 판정

| 항목 | 내용 |
|---|---|
| 결정 ID | DEC-C1 |
| 원문 위치·문구 | `handoff/2026-09-18/reference/RECONCILIATION_SPEC.md` 3장 C1: "IF 지속가능성 조직경계 == 연결 기준 AND 종속기업 **수** 일치: status = matched", 허용 차이 유형 4종(운영통제 vs 지배력, 해외 자회사 제외, 지분법 제외, 신규 취득·처분 안분) |
| 현재 코드 | `packages/proofops/domain/reconciliation/c1.py`: `exact_verified_entity_set`. 검증된 식별자 **집합**이 같을 때만 `matched`. 개수는 대리값으로 쓰지 않는다. 빈 집합은 `blocked("entity_set_empty")`. `policy.allowed_difference_types`는 의도적으로 읽지 않으며 자동 통과 목록으로 쓰지 않는다. |
| 실제 사례 | 이번 실자료에서 C1 양성/음성/경계 사례를 **확보하지 못했다**. 삼성 SR 조직경계 문구와 사업보고서 종속기업 목록 주석을 연결하려면 등록된 A claim과 대상 검색이 필요한데 둘 다 없다. 후보 준비 단계의 검색 범위도 문서 전체의 약 2.6%(DEC-COVERAGE)에 그쳐 종속기업 주석이 후보에 포함되지 않았다. |
| 제안 | 원문의 "개수 일치"를 그대로 채택하지 않고 현재 코드의 집합 동일성을 유지한다. "개수 일치"는 집합 동일성의 **필요조건**일 뿐이며, 같은 개수·다른 집합을 matched로 만들면 원문의 취지(연결범위 일치 확인)를 위반한다. 차이는 `resolve_difference` 경로에서 **검증된 설명 근거**로만 해소한다. |
| 반대 사례 | 종속기업 12개사 중 1개가 기중 처분되어 SR은 11개, 사업보고서 주석은 12개를 열거하는 경우 개수·집합이 모두 다르지만 실제로는 정상 차이다. 설명 근거가 없으면 현재 코드는 `search_complete`에 따라 `blocked(search_incomplete)` 또는 `needs_explanation`을 낸다. 이 동작을 "오류"로 집계하지 않도록 기대값 작성 시 구분해야 한다. |
| 영향 범위 | C1 판정, `allowed_difference_types`의 의미(분류 태그이지 통과 목록이 아님), 기대값 작성 지침. |
| 미확정점 | (1) 법인 단위와 사업장 단위를 같은 집합에서 비교할 수 있는지, (2) 지분법 대상 표기 방법, (3) 기중 취득·처분의 기간 안분을 집합 동일성으로 표현할지 설명 경로로 보낼지. **셋 다 미확정이며 이 문서로 승인되지 않는다.** |

---

## DEC-C3 — 임계값·계정 매핑

| 항목 | 내용 |
|---|---|
| 결정 ID | DEC-C3 |
| 원문 위치·문구 | 같은 명세 3장 C3 및 설정 예시 `flag_if_multiple_of_annual_capex: 5.0   # 연간 CAPEX의 5배 초과 시에만 검토`. 2-3절: "임계값은 설정 파일에 외부화하고 코드에 하드코딩하지 않는다." 3장 C3 주의: "약정 주석에 없다는 것만으로 문제로 띄우지 말 것." |
| 현재 코드 | `c3.py`: `c3_threshold`가 `None`이거나 `c3_account_mapping_approved`가 거짓이거나 허용 계정 집합이 비었거나 요청 계정이 허용 집합의 부분집합이 아니면 `blocked("c3_policy_unapproved")`. 임계값 비교는 나눗셈 없이 `commitment <= threshold * capex`. 약정 주석이 있으면 임계값과 무관하게 `matched("commitment_disclosed")`. 트리거(목표형 + 통화 금액)가 없으면 `not_applicable("c3_trigger_absent")`. |
| 실제 사례 | 실제 C3 사례 없음. 삼성 SR에서 금액 명시 투자 약속을 후보로 확보하지 못했고, CAPEX 매핑 대상인 현금흐름표 투자활동·자본적지출 약정 주석도 잘린 후보 집합에 들어오지 않았다. 합성 회귀 `cli_c3-policy-unresolved`는 이번 커밋에서 통과했고, 실자료 삼성 C2 패킷도 미승인 정책 때문에 `blocked/policy_unapproved`로 나왔다. |
| 제안 | 5.0을 기본값으로 채택하지 않는다. 원문에서 5.0은 **설정 예시**이며 채택 근거가 제시되어 있지 않다. 승인 시 (a) 임계값 숫자와 근거, (b) 기간 정의(약속 목표기간 vs 비교 대상 CAPEX 회계기간), (c) 통화·단위, (d) 허용 계정 ID 목록과 출처(XBRL 계정 또는 주석 항목)를 한 건의 정책 버전으로 함께 승인한다. 넷 중 하나라도 비면 계속 차단한다. |
| 반대 사례 | 다년도 약속(예: 2030년까지 누적 투자)을 단년도 CAPEX와 비교하면 거의 모든 사례가 임계값을 초과한다. 이때 `needs_explanation`은 회계적 문제 신호가 아니라 기간 정의 오류다. 또한 약정 주석 부재가 정상인 미래 투자 약속을 문제로 집계하면 안 된다. |
| 영향 범위 | C3 판정, 정책 버전·해시, 승인 화면의 필수 입력, 실기업 C3 재판정 시점. |
| 미확정점 | 임계값 수치, 허용 계정 목록, 다년도 약속의 기간 정규화 규칙. **전부 미확정이며 승인 주체는 기준·데이터 담당자다.** |

---

## DEC-STATUS — 비적용과 미확인의 구별

| 항목 | 내용 |
|---|---|
| 결정 ID | DEC-STATUS |
| 원문 위치·문구 | `AGENTS.md`: "unknown, conflict, unreadable을 근거 부재로 바꾸지 않는다." 계약 1.1: `execution_state`는 completed/blocked/not_run, status는 completed에서만 matched/needs_explanation/not_applicable, blocked/not_run의 status는 null. |
| 현재 코드 | `service.reconcile`이 정책 미승인·원문 검증 실패·검색 미완료·값 미해결을 모두 `execution_state=blocked`, `status=null`, 각기 다른 `reason_codes`로 낸다. `not_applicable`은 C2 기간 외 활동(`period_out_of_scope`)과 C3 트리거 부재(`c3_trigger_absent`)처럼 **검증된 비적용**에만 쓰인다. |
| 실제 사례 | **정상 차단 확인:** 동서 FY2024 최초 접수분 `20250313000532`은 statements 아티팩트가 `collection_or_identity_failed`로 남아 있고, 이 manifest로 후보 준비를 실행하면 `{"error": "collection_identity_or_status_mismatch"}`와 종료코드 2로 거부되며 출력 폴더를 만들지 않는다. 수집 실패가 `not_applicable`이나 빈 결과로 바뀌지 않는다. **확인:** 삼성 실자료 C2는 `blocked` / `policy_unapproved`, status는 null이었다. |
| 제안 | 사용자 문구를 네 갈래로 분리한다. (1) 확인된 비적용(`not_applicable` + 근거), (2) 정책 미승인(`blocked/policy_unapproved`, 해제 조건: 승인 주체·정책 버전), (3) 입력 미확보(`blocked/value_unresolved`·`source_*`, 해제 조건: 어떤 입력인지), (4) 검색 미완료(`blocked/search_incomplete`, 해제 조건: 어떤 문서·영역). 화면과 보고서에서 (1)과 (2)~(4)를 같은 칸에 넣지 않는다. |
| 반대 사례 | "해당 없음" 한 줄로 (1)과 (3)을 합치면 수집 실패가 정상 판정처럼 보인다. 동서 사례가 정확히 그 경로였다. |
| 영향 범위 | 화면 문구, 결과 JSON 소비자, 사례표의 `결함/외부입력/정상보류` 분류. |
| 미확정점 | 사용자 대면 한국어 문구의 최종 표현. 코드 계약은 변경 불필요. |

---

## DEC-PERIOD — 측정기간·발간연도·정정공시

| 항목 | 내용 |
|---|---|
| 결정 ID | DEC-PERIOD |
| 원문 위치·문구 | 같은 명세 3장 C2: "대조: 지속가능성 정보의 기준일 ↔ 재무제표 결산일", "불일치 시 `needs_explanation`. 단 데이터 수집 시차를 명시한 경우 `matched`." 후속 업무 문서 5절: "2025 보고서를 FY2025라고 추정하지 않습니다." |
| 현재 코드 | `c2.py`가 연도 라벨이 아니라 실제 달력 날짜로 비교한다. `identity.period_start/end` 밖의 활동은 `not_applicable("period_out_of_scope")`. 정규화 값이 없으면 `blocked("value_unresolved")`. |
| 실제 사례 | **발간연도 != 측정기간(확인):** 삼성 SR은 2025-06-27 발행이고 86쪽 원문은 "2024년 1월1일부터 2024년 12월 31일까지의 경제·사회·환경적 성과와 "를 명시한다. 대응 재무공시는 FY2024·접수번호 `20250311001085`·접수일 2025-03-11이다. **정정·후속 공시(확인):** 동서는 FY2024에 대해 `20250313000532`(2025-03-13)과 `20251210000195`(2025-12-10) 두 접수번호가 존재하며 후자만 3개 아티팩트가 모두 수집됐다. |
| 제안 | (1) 보고 대상기간은 SR 원문의 명시 문구에서만 고정하고 발행일·다운로드 시각으로 대체하지 않는다. (2) 재무 측은 접수번호 단위로 고정하고, 같은 FY에 복수 접수번호가 있으면 **사용한 접수번호를 case 식별자에 포함**한다. (3) 후발·정정 공시가 있으면 기존 결과를 소급 변경하지 않고 새 정책/입력 revision으로 새 결과를 만든다. |
| 반대 사례 | 동서처럼 최초 접수분이 부분 실패한 경우, 후속 접수번호로 조용히 바꾸면 "같은 FY의 다른 문서"가 되어 재현성이 깨진다. 접수번호를 기록하지 않으면 두 결과의 차이가 코드 결함처럼 보인다. |
| 영향 범위 | case 식별자, 수집 재사용 판단, 재현 절차, 사례표의 접수번호 열. |
| 미확정점 | 비12월 결산·다년도 표의 정규화 표기, 정정공시 감지의 자동화 여부(이번 범위 밖). |

---

## DEC-COVERAGE — 설명 검색 범위와 종료 조건

| 항목 | 내용 |
|---|---|
| 결정 ID | DEC-COVERAGE |
| 원문 위치·문구 | `docs/RECONCILIATION_PRODUCT.md`: "후보 개수 제한에 걸린 경우 전체 문서 검색을 완료했다고 간주하지 않습니다." 제품 계약: "Human review can confirm facts and search coverage, never choose a result status." |
| 현재 코드 | `packages/proofops/adapters/dart/candidates.py`의 `MAX_CANDIDATES = 2_000`(상한), CLI 기본 500. 초과 시 `limits.truncated = true`. 카탈로그 `trust.search_complete`는 항상 `false`로 시작한다. C1/C2/C3의 `resolve_difference`와 C4는 `search_complete`가 거짓이면 `blocked("search_incomplete")`를 낸다. |
| 실제 사례 | **측정값(이번 실행):** 삼성 FY2024 사업보고서 ZIP의 XML 멤버 3개에서 텍스트 요소 59,053개, XBRL ZIP 멤버 6개에서 19,070개, 재무제표 JSON 213행 — 합계 약 78,336개. 상한 2,000으로 준비한 카탈로그는 document 894 / xbrl 893 / statements 213 = 2,000개(약 2.6%)만 보유하고 `truncated: true`를 보고했다. 오뚜기·동서도 같은 상한에서 각각 2,000개로 잘렸다. **부수 확인:** 파생 텍스트 아티팩트 자체도 멤버당 2,000줄에서 잘리므로 내려받은 파생본은 문서 전체가 아니다. |
| 제안 | (1) 현재 상한으로는 어떤 실제 사업보고서도 전수 검색이 불가능하므로 `search_complete=true`는 **전수 후보 목록이 아니라 항목별 필수 문서·영역을 지정한 검색 영수증**으로만 부여한다. (2) 필수 영역을 항목별로 고정한다 — C1: 종속기업 목록 주석, C2: 표지 사업연도와 SR 보고 범위 문구, C3: 현금흐름표 투자활동·자본적지출 약정 주석, C4: 부문별 정보 주석. (3) 실패·미판독 영역은 `failed_document_ids`에 남기고 그 상태로는 완료를 부여하지 않는다. |
| 반대 사례 | 후보 2,000개를 다 읽었다는 사실을 "완전 검색"으로 기록하면, 실제로는 97%를 보지 않은 채 `needs_explanation`(설명 없음)이 나온다. 전형적인 오탐 경로다. |
| 영향 범위 | 검색 영수증 스키마, 검토 화면의 검색 범위 확인, C1/C2/C3/C4의 `search_incomplete` 빈도, 정확도 지표의 분모. |
| 미확정점 | 항목별 필수 영역의 최종 목록과, 지정 영역을 찾지 못했을 때의 종료 조건. **미확정이므로 이번 실행에서 `search_complete`는 전부 false로 유지했다.** |

---

## 승인 시 기록할 항목

채택하는 결정마다 아래를 남긴다. 현재는 **어느 항목도 채워지지 않았다.**

| 필드 | 값 |
|---|---|
| 결정 ID | (미승인) |
| 실제 승인자·역할 | (미승인) |
| 승인 일시 | (미승인) |
| 정책 버전 | (미승인) |
| `source_policy_sha256` | (미승인) |
| 적용 범위(tenant/company/기간) | (미승인) |

정책이 바뀌면 새 정책 버전과 새 결과 revision을 만들고 기존 결과의 의미를 소급 변경하지 않는다.
