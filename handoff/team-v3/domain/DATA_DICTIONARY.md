# CSV 전체 필드 사전

모든 표의 빈칸은 자동으로 absent/0/false가 아니다. 예제는 합성 교육용이다.

2026-09-22 입력 검사 보완: `validate_data.py`는 상태 enum, 수치 원자료/소수값 일치,
유한 수, 문서 참조, 승인·측정 메타데이터도 확인한다. 통과는 원문 진실성이나
정책 승인이 아니다. `--final`은 권리 승인 외에 주석의 adjudicated 상태·실제 확정자,
수치/보증/대조 사례의 확정자도 요구한다. 동일인이 검토·확정을 할 수 있지만
자동 수정 작업자를 사람 확정자로 기록하지 않는다. 미확정 자료는 일반 검토 모드로 전달한다.

numeric 한 행에는 원자료 값 하나만 둔다. 비교 피연산자는 별도 case_id 행으로 두고
계산 관계·파생값은 별도 검산 기록으로 연결한다. 괄호 음수는 음수 소수값과 일치해야 하며,
판독 불가 원문은 보존하고 소수값을 비워 unresolved/not_computable로 남긴다.

ID 관계: corpus.document_id → claims.document_id → claims.claim_id → elements/numeric/assurance/reconciliation.claim_id.

## elements.csv

| 열 | 작성 내용 |
|---|---|
| `annotation_id` | 주석 행의 고유 ID. 수정하면 새 ID/버전. |
| `claim_id` | claims에 등록한 원자 주장 ID. |
| `element_id` | config/rubric와 docs/28의 실제 요소 ID/primitive. 새 요소는 규칙 제안으로. |
| `state` | present / absent / unknown / conflict / not_applicable. |
| `evidence_document_id` | 근거가 있는 corpus 문서 ID. |
| `physical_page` | PDF 파일상 1-based 페이지. 인쇄 쪽수와 다름. |
| `quote` | 원문을 그대로 인용. 해석/요약은 statement/notes에. |
| `bbox_json` | 좌상단 기준 PDF point [x0,y0,x1,y1]. 없으면 빈칸, 가짜0 좌표 금지. |
| `table_id` | 파서/원문에서 식별한 표 ID. 새 가짜 ID로 실제 파서 ID를 흉내내지 않음. |
| `row` | 파서의 행 위치. 사람이 읽은 행 번호면 notes에 기준을 명시. |
| `column` | 파서의 열 위치. 헤더·단위·연도와 함께 확인. |
| `year` | 근거 수치/표 열의 대상 연도. |
| `unit` | 원문 단위. 금액 통화/비율/절대량 구분. |
| `boundary` | 대상 법인·사업장·지역·집계 경계. |
| `binding_reason` | 이 인용이 이 주장/값에 속하는 이유. 같은 숫자라는 이유만으로 인정 금지. |
| `searched_scope` | 부재를 판단하기 위해 실제 읽은 페이지·표·주석·문서 범위. |
| `rule_id` | 적용 규칙 ID. 예: GAP/REC 또는 승인 rulepack ID. |
| `annotation_status` | draft / reviewed / adjudicated / unresolved. |
| `annotator` | 처음 원문을 읽고 태깅한 사람. |
| `adjudicator` | 불일치를 조정하거나 정답을 확정한 사람. 아직 없으면 빈칸. |

## assurance.csv

| 열 | 작성 내용 |
|---|---|
| `case_id` | 수치/보증/연계 정답 사례의 고유 ID. |
| `claim_id` | claims에 등록한 원자 주장 ID. |
| `document_id` | 문서/버전의 고유 ID. corpus와 모든 정답표를 연결한다. |
| `physical_page` | PDF 파일상 1-based 페이지. 인쇄 쪽수와 다름. |
| `quote` | 원문을 그대로 인용. 해석/요약은 statement/notes에. |
| `provider` | 검증/보증 기관명 원문. |
| `standard` | 검증의견서에 기재된 기준 원문. |
| `level` | limited / reasonable / unknown 등 실제 보증 수준. |
| `period_start` | 실제 보고기간 시작일 YYYY-MM-DD. |
| `period_end` | 실제 보고기간 종료일 YYYY-MM-DD. |
| `entities` | 보증 대상 조직·사업장. |
| `metrics` | 보증 대상 지표. |
| `exclusions` | 명시적으로 제외된 지표/조직/기간. |
| `expected_status` | assurance: covered/not_covered/undetermined; reconciliation: matched/needs_explanation/not_applicable. 보류면 빈칸. |
| `reason` | 기대 결과를 선택한 원문 기반 설명. |
| `annotator` | 처음 원문을 읽고 태깅한 사람. |
| `adjudicator` | 불일치를 조정하거나 정답을 확정한 사람. 아직 없으면 빈칸. |

## acceptance.csv

| 열 | 작성 내용 |
|---|---|
| `metric` | 평가 지표 이름. 예: element_precision, normal_difference_false_positive_rate. |
| `proposed_threshold` | 성능 합격선 제안. 실제 관측값과 구분. |
| `denominator` | 지표의 분모 정의. 예: gold상 정상 차이 사례 수. 보류 처리 규칙 포함. |
| `supported_scope` | 어떤 기능·문서·기업 범위에 대한 지표인지. |
| `sample_count` | 실제 평가 표본 수. 목표 수가 아님. |
| `observed_value` | 실제로 측정한 값. 미측정이면 빈칸. |
| `interval` | 신뢰구간 및 산정법. 표본 부족시 임의 생성 금지. |
| `domain_reviewer` | 해석을 검토한 담당자. |
| `approval_status` | proposed / approved / unresolved. |

## claims.csv

| 열 | 작성 내용 |
|---|---|
| `annotation_id` | 주석 행의 고유 ID. 수정하면 새 ID/버전. |
| `document_id` | 문서/버전의 고유 ID. corpus와 모든 정답표를 연결한다. |
| `claim_id` | claims에 등록한 원자 주장 ID. |
| `physical_page` | PDF 파일상 1-based 페이지. 인쇄 쪽수와 다름. |
| `quote` | 원문을 그대로 인용. 해석/요약은 statement/notes에. |
| `char_start` | 해당 원문 블록에서 code point 시작 위치. 모르면 빈칸. |
| `char_end` | 끝 다음 code point 위치(end-exclusive). |
| `bbox_json` | 좌상단 기준 PDF point [x0,y0,x1,y1]. 없으면 빈칸, 가짜0 좌표 금지. |
| `statement` | 원자 주장 의미를 한 문장으로 요약. |
| `track` | goal / performance / management / unknown. |
| `topic` | 기후·에너지·용수·폐기물 등 실제 환경 주제. |
| `claim_year` | 이 주장이 명시한 측정/목표연도. 보고서 발간연도로 채우지 않음. |
| `annotation_status` | draft / reviewed / adjudicated / unresolved. |
| `annotator` | 처음 원문을 읽고 태깅한 사람. |
| `adjudicator` | 불일치를 조정하거나 정답을 확정한 사람. 아직 없으면 빈칸. |
| `notes` | 불명확한 점·수정 이유·예외 설명. |

## numeric.csv

| 열 | 작성 내용 |
|---|---|
| `case_id` | 수치/보증/연계 정답 사례의 고유 ID. |
| `claim_id` | claims에 등록한 원자 주장 ID. |
| `document_id` | 문서/버전의 고유 ID. corpus와 모든 정답표를 연결한다. |
| `physical_page` | PDF 파일상 1-based 페이지. 인쇄 쪽수와 다름. |
| `quote` | 원문을 그대로 인용. 해석/요약은 statement/notes에. |
| `table_id` | 파서/원문에서 식별한 표 ID. 새 가짜 ID로 실제 파서 ID를 흉내내지 않음. |
| `row` | 파서의 행 위치. 사람이 읽은 행 번호면 notes에 기준을 명시. |
| `column` | 파서의 열 위치. 헤더·단위·연도와 함께 확인. |
| `metric` | 수치가 뜻하는 지표 원문 이름. |
| `value_raw` | 원문 숫자 표기. 쉼표/괄호/결측기호 보존. |
| `value_decimal` | 단위 승수 적용 전 정확한 소수 문자열. 결측은 빈칸. |
| `unit` | 원문 단위. 금액 통화/비율/절대량 구분. |
| `multiplier` | 정규 단위 변환 승수. 예: 천이면1000. 임의 환율 적용 금지. |
| `year` | 근거 수치/표 열의 대상 연도. |
| `scope` | GHG Scope1/2/3 등 적용되는 범위. 비GHG에 억지 적용하지 않음. |
| `scope2_basis` | location_based / market_based / unknown, 해당 없으면 빈칸. |
| `boundary` | 대상 법인·사업장·지역·집계 경계. |
| `denominator` | 원단위 분모(매출/생산량 등). 절대량이면 빈칸. |
| `footnote_quote` | 값에 실제 적용되는 각주 원문. 귀속 불명확하면 사유 기록. |
| `expected_check` | consistent / inconsistent / not_comparable / not_computable / unresolved. |
| `reason` | 기대 결과를 선택한 원문 기반 설명. |
| `annotator` | 처음 원문을 읽고 태깅한 사람. |
| `adjudicator` | 불일치를 조정하거나 정답을 확정한 사람. 아직 없으면 빈칸. |

## reconciliation.csv

| 열 | 작성 내용 |
|---|---|
| `case_id` | 수치/보증/연계 정답 사례의 고유 ID. |
| `claim_id` | claims에 등록한 원자 주장 ID. |
| `financial_document_id` | 재무 측 corpus 문서 ID. |
| `rule_id` | 적용 규칙 ID. 예: GAP/REC 또는 승인 rulepack ID. |
| `case_type` | match / explained_difference / unexplained_difference / normal_difference / accounting_boundary. |
| `sr_quote` | 지속가능성 측 원문 인용. |
| `sr_page` | 지속가능성 원본의 물리 페이지. |
| `financial_quote` | 재무 측 원문 인용. |
| `financial_locator` | 접수번호+주석 제목/원문 element 등 재현 위치. |
| `comparison_kind` | entity_set / facility_set / currency_amount / period / classification / unknown. |
| `sustainability_value` | SR 측 원문 값·범위 표현. |
| `financial_value` | 재무 측 원문 값·범위 표현. |
| `period` | 이 대조의 실제 기간 표현. 양쪽 차이는 reason에. |
| `boundary` | 대상 법인·사업장·지역·집계 경계. |
| `explanation_quote` | 해당 차이의 설명 원문. 못 찾으면 빈칸. 추정 문구 금지. |
| `explanation_document_id` | 설명 문구의 corpus 문서 ID. |
| `explanation_locator` | 설명 문구 위치. 원문 없는 가짜 위치 금지. |
| `search_coverage` | complete / incomplete / not_run 및 구체적 범위는 reason. |
| `expected_execution_state` | completed / blocked / not_run. completed에만 status 부여. |
| `expected_status` | assurance: covered/not_covered/undetermined; reconciliation: matched/needs_explanation/not_applicable. 보류면 빈칸. |
| `reason_code` | 상태 사유 기계 코드. 동일 의미에 같은 코드 사용. |
| `policy_id` | 판정에 적용한 승인 정책 버전. 합성/미승인 구분. |
| `annotator` | 처음 원문을 읽고 태깅한 사람. |
| `adjudicator` | 불일치를 조정하거나 정답을 확정한 사람. 아직 없으면 빈칸. |

## corpus.csv

| 열 | 작성 내용 |
|---|---|
| `document_id` | 문서/버전의 고유 ID. corpus와 모든 정답표를 연결한다. |
| `company_id` | 기업 고유 ID. 동일 기업의 연도별 보고서는 같은 company_id. |
| `company_name` | 보고서 발간 기업의 원문 이름. |
| `dart_corp_code` | DART 8자리 코드. 텍스트로 보존하고 회사명으로 추측하지 않는다. |
| `document_role` | sustainability / financial / integrated 중 문서 역할. |
| `fiscal_year` | 발간 연도가 아닌 보고 회계연도. |
| `period_start` | 실제 보고기간 시작일 YYYY-MM-DD. |
| `period_end` | 실제 보고기간 종료일 YYYY-MM-DD. |
| `published_at` | 원문 발간/공시일. 미확인시 빈칸. |
| `consolidation` | consolidated / separate / unknown. |
| `rcept_no` | DART 접수번호. 정정 버전별 구분. 없는 문서는 빈칸. |
| `source_url` | 원본의 공식 접근 URL. 검색결과 URL 대신 원문. |
| `local_filename` | 공유 원본의 파일명. 개인 컴퓨터 절대경로보다 상대 이름. |
| `sha256` | 원본 bytes의 64자리 SHA256. A가 도구로 계산해 전달. |
| `rights_status` | pending / approved / restricted; 공개되어 있어도 재배포 권리를 추정하지 않음. |
| `split` | train / dev / test / challenge. 기업 단위로 고정. |
| `layout_types` | 단일단/다단/병합표/스캔/각주 등 ; 로 구분. |
| `reviewer` | 문서 정보를 확인한 실제 검토자. |
| `notes` | 불명확한 점·수정 이유·예외 설명. |

## rules.csv

| 열 | 작성 내용 |
|---|---|
| `rule_id` | 적용 규칙 ID. 예: GAP/REC 또는 승인 rulepack ID. |
| `source_section` | 원문 명세의 절 번호. 공식 표준 조항과 구분. |
| `question` | 아직 결정되지 않은 구체적인 질문. |
| `proposal` | 원문과 사례에 근거한 제안. 승인되지 않은 값을 확정처럼 쓰지 않음. |
| `positive_case_ids` | 규칙이 적용/충족되는 실제 사례 ID를 ; 로 구분. |
| `negative_case_ids` | 규칙이 적용 안 되거나 불충족인 사례 ID. |
| `boundary_case_ids` | 경계값·모호한 예외 사례 ID. |
| `official_source_url` | 확인한 공식 기준/법령/발표 원문 URL. |
| `clause` | 직접 대조한 공식 조항 번호. 미확인시 빈칸. |
| `verified_on` | 공식 원문 확인일 YYYY-MM-DD. |
| `rights_notes` | 원문 저장·인용·재배포 허용범위와 확인 근거. |
| `decision_status` | unresolved / proposed / approved / rejected. |
| `domain_reviewer` | 해석을 검토한 담당자. |
| `product_approver` | 제품에 해당 해석 적용을 승인한 책임자. |
| `approved_on` | 실제 승인일, 아직 없으면 빈칸. |
| `rulepack_before` | 변경 전 rulepack SHA, A가 제공. |
| `rulepack_after` | 변경 후 rulepack SHA, A가 제공. |


## 보조 양식 (제품 입력/기존 CSV8 스키마와 별개)
요구사항_사례연결.csv: requirement_id(REQUIREMENT_TRACE의 ID 또는 OPEN_DECISIONS ID), document_id, claim_id, case_id,
state(present/absent/unknown/conflict/not_applicable 등 해당 검토 상태), physical_page, quote, notes, owner_question.
용도: GHG 네 세부점검·세이프하버·산업·C5·버전 충돌을 실제 사례와 연결. 이 표가 자동으로 제품 primitive가 되지는 않습니다.
정답 확정의 기술적 trace는 A가 검토해 schema/태그에 변환합니다.
질문과_진행상황.csv: 진행/질문/수정이유 보조 기록. 이 둘은 validate_data.py의 기존8종 검사 대상이 아닙니다.
