# REC-002 · REC-006 구현 (revision `rec-002-006-v1`, opt-in `rec-002-006-v2`)

근거: `docs/R00_DOMAIN_DECISIONS.md` §12 (2026-09-28 사용자 채택). 이 문서는 구현 범위와 남은 통합 차단만 기록한다. 정책 문장은 §12가 정본이다.

## 호환성 원칙

- 계약 schema 1.1 (`input`/`policy`/`output`)과 순수 domain 엔진(`reconciliation-engine-1.2.0`)은 **변경하지 않았다**.
- `service.reconcile(...)`는 `adopted_revision=None`(기본값)이면 기존 경로와 동일하게 동작한다. receipt만 넘기고 revision을 지정하지 않으면 `ValueError`로 거부한다.
- `adopted_revision="rec-002-006-v1"`이면 application 계층 게이트가 추가되고, 결과 `engine_version`에 `+app-rec-002-006-v1` 접미사가 붙는다(output 1.1에서 자유 문자열 필드). 저장된 1.1 결과를 재생할 때 어느 규칙 집합이 만들었는지 구분할 수 있다.
- revision을 지정했는데 receipt가 없거나 검토 확인이 없으면 `blocked`다. 기존 경로로 조용히 되돌아가지 않는다.
- `adopted_revision="rec-002-006-v2"`만 **output schema 1.2**를 낸다(아래 REC-006 A 절). v1과 legacy 결과·snapshot·projection은 바이트 그대로다.

## 신뢰 입력: revision receipt

operator가 가져오는 객체이며 가져온 시점에는 권한이 없다.

| 키 | 내용 |
|---|---|
| `revision` | `"rec-002-006-v1"` |
| `explanation_bindings` | `{source_id: {claim_id, item, package_id, document_id, artifact_sha256, locator, quote, compared:{sustainability_source_id, sustainability_normalized, financial_source_id, financial_normalized}}}` |
| `filing_history.cutoff` | `{date: as_of_date, granularity: "date_inclusive"}` (날짜 단위만 지원하며, 당일 접수분을 포함한다. 시각 단위 증명은 주장하지 않는다) |
| `filing_history.search` | `{endpoint:"/api/list.json", request:{corp_code,bgn_de,end_de,last_reprt_at,pblntf_ty,pblntf_detail_ty,page_count}, pages:[{page_no, sha256}]}` |
| `filing_history.classification` | `{rcept_no: {lineage:"family"|"unrelated", basis}}`: 검토자가 분류하며 제목으로 추론하지 않는다 |
| `filing_history.pinned` | `{rcept_no, period_start, period_end, consolidation, period_evidence_source_ids, consolidation_evidence_source_ids}` |
| `confirmation` | **신뢰 저장소만 주입한다**: `{actor, confirmed_on, receipt_sha256}`. receipt 본문(confirmation 제외)의 canonical hash와 일치해야 한다 |

## REC-002: 설명 인용 결합 (`revision.explanation_bound`)

- 설명은 기존 검증(문서 registry 동일 tenant/company/package/회계연도/기간, 원문 hash, locator·quote, `explanation` role, coverage reviewed)을 통과하고, receipt의 binding이 **해당 claim·item·package와 비교 대상 두 사실(source_id + registry가 신뢰한 normalized 값)** 에 정확히 일치할 때만 인정한다.
- 검증은 되었지만 결합되지 않은 후보는 설명에서 제외하고 `explanation_candidate_unbound` 사유를 추가한다. 이 후보는 검증된 reviewed source로 packet에 남으므로 coverage 판정은 닫힌다.
- packet이 직접 지정한 설명이 결합되지 않았으면 `blocked / explanation_binding_missing`이다.
- 완결 검색에서 결합된 설명이 없으면 domain이 `needs_explanation`을 낸다. 검색이 불완전하거나 원문을 읽을 수 없으면 `blocked`다(기존 REC-005 경로).

## REC-006: 정정 공시 고정 (`revision.verify_filing_history`)

1. `last_reprt_at=N`만 허용한다. `Y`는 이전 접수 이력을 숨기므로 `filing_search_latest_only`로 막는다.
2. `corp_code`는 identity와 같아야 한다. `end_de ≥ cutoff`, `bgn_de ≤ financial_period_start`여야 하고, 아니면 창이 불완전한 것으로 본다.
3. 원시 페이지 bytes를 **SHA-256으로 다시 읽어** `assemble_filing_pages`로 재파싱한다. 페이지 1..total_page가 모두 있어야 하고, total_count·total_page는 페이지 간에 안정적이어야 하며, 행 수가 일치하고 `rcept_no` 중복이 없어야 한다. 하나라도 어긋나면 `filing_search_incomplete`, hash가 다르면 `filing_page_tampered`다.
4. cutoff 이전(`rcept_dt ≤ as_of_date`) 행은 모두 분류되어야 한다. cutoff 이후 행은 기록에 보존하되 판정에 쓰지 않는다.
5. family 중 가장 늦은 `rcept_dt`가 하나여야 한다. 같은 날짜가 둘 이상이면 접수번호로 순서를 추측하지 않고 `filing_order_ambiguous`로 막는다.
6. `pinned.rcept_no = identity.rcept_no = 최신 family`, `financial_published_at = 그 rcept_dt`여야 한다. stale 원본이면 `financial_filing_not_latest`다.
7. pinned 기간·연결범위는 identity와 같아야 하고 null이나 `unknown`이면 안 된다. 기간은 **pinned 재무 문서의 검증된 인용**에 원문 형식 그대로 적혀 있어야 한다. 허용 형식은 ISO, `2024.12.31`, `2024년 12월 31일`, `20241231`이며, 결정적으로 정규화하고 제목(`report_nm`)은 파싱하지 않는다.
8. v1에서는 cutoff까지 family 공시가 없으면 `blocked / financial_filing_not_available_as_of`다. schema 1.1에 not_applicable 사유 필드가 없기 때문이다. 명시적 not_applicable은 v2(아래)에서만 낸다.

## REC-006 A: 적시 공시 없음 → 명시적 not_applicable (`rec-002-006-v2`, input/output 1.2)

정책 원문(§12 REC-006 A): “complete official lookup with no timely same-period FS follows explicit completed not_applicable reason.” v2는 v1의 모든 게이트를 같은 순서·같은 코드로 적용하고, family 공시가 cutoff까지 **하나도 없을 때만** 아래를 추가로 검사한다.

**조회 시각의 출처(검토 반영).** list.json 페이지 bytes에는 시각이 없다. 그래서 조회 시각은 **collector 시계**에서만 온다. `collect_filing_history(..., clock=)`는 요청 직전에 시계(시간대 포함 필수)를 읽어 페이지마다 `retrieved_at`(UTC)을 기록하고, record를 `opendart-list-history-2`로 만든다. clock이 없으면 기존 `opendart-list-history-1` 모양 그대로다. 제품 경로에서는 store의 서버 전용 `collect_filing_history(auth, client, …)`가 **store 자신의 시계**로 collector를 실행한다. 이때 record와 원시 페이지를 canonical record hash로 불변 저장한다(tenant 범위 `reconciliation_filing_collection` 표, UPDATE/DELETE 차단 trigger, `_rec_collections/`). receipt는 `filing_history.collection_sha256`로 그 record를 지명하기만 한다. `search`는 record의 `search_version`·`endpoint`·`request`·`pages`(`retrieved_at` 포함)를 **그대로** 옮겨야 하고, 시각을 추가하거나 고칠 수 없다. 평가 시 service는 신뢰 record(`filing_collection`)에서만 시각을 읽는다. CLI의 `--filing-collection`은 다른 registry 파일과 같은 **operator 제출 증거**이며, 실제 서버 수집의 암호학적 증명이 아니다. 수집 시계의 출처를 증명하는 것은 store가 직접 실행해 tenant 소유로 저장한 collection뿐이다. store는 그 표에서만 record를 읽는다. 표는 기존 DB에 `CREATE TABLE IF NOT EXISTS`로 추가되는 additive migration이다. schema version은 1 그대로이고 기존 사례는 바뀌지 않는다(테스트). 실패한 수집(시간대 없는 시계 등)은 page나 record를 남기지 않는다. 존재하지 않거나 다른 tenant의 collection을 지명한 등록은 아무것도 쓰지 않고 거부된다. fetch 실패는 `incomplete` record로만 남아 부재를 증명하지 못한다.

| 검사 | 실패 코드 (항상 `financial_filing_not_available_as_of`와 함께) |
|---|---|
| 신뢰 collection record가 주어짐 | `filing_lookup_collection_missing` |
| receipt `collection_sha256` = record canonical hash, receipt `search` = record 검색부 | `filing_lookup_collection_mismatch` |
| record가 `opendart-list-history-2`이고 `state=complete` | `filing_lookup_collection_incomplete` |
| 모든 페이지에 시간대 있는 `retrieved_at` | `filing_lookup_retrieval_unrecorded` |
| 첫 페이지 요청의 UTC 날짜 > cutoff(당일 요청은 그날 늦은 접수를 배제하지 못함) | `filing_lookup_cutoff_day_open` / `filing_lookup_before_cutoff` |
| 마지막 페이지 요청 UTC 날짜 ≤ `confirmation.confirmed_on` ≤ `evaluation_date` | `filing_lookup_confirmed_before_retrieval` / `filing_lookup_evaluated_before_confirmation` / `filing_lookup_retrieval_future` |
| `pblntf_ty`·`pblntf_detail_ty`가 비어 있음(승인된 완결 필터 집합 없음) | `filing_lookup_filtered` |
| packet이 재무 공시를 지명하지 않음: `financial_document_version`·`rcept_no`·`financial_published_at`·재무 fact 모두 null, 재무 role source 없음, `pinned` 없음 | `financial_filing_identity_conflict` |
| 검토자 선언 `filing_history.no_timely_filing = {family_basis, period_start, period_end, consolidation, period_evidence_source_ids, consolidation_evidence_source_ids}` | `no_timely_filing_unreviewed` |
| 선언 기간·연결범위 = identity. 근거 source는 검증된 **sustainability role** 문서의 인용이고, 기간 인용에 시작·종료일이 원문대로 적혀 있음 | `financial_period_unverified` / `financial_consolidation_unverified` / `financial_evidence_missing` |

store에서 `confirmed_on`은 검토 이벤트 기록 시각(UTC 날짜)이고 평가일은 평가 기록 시각(UTC 날짜)이다. 수집 → 검토 → 평가의 기록 순서와 날짜 순서가 모두 검사된다. UTC 날짜 비교는 OpenDART(한국 서비스) 날짜에 대해 보수적이다. UTC 이동선 동쪽의 모든 시간대에서, 요청 시각이 cutoff 날짜가 끝난 뒤임을 뜻한다.

v2 공통: `cutoff > evaluation_date`이면 `filing_cutoff_future`로 막는다(pinned 경로 포함). 다음은 v1과 같은 코드로 `blocked`다: 검색 불완전, 페이지 변조, 미분류, 같은 날 family 복수, receipt 변조, v1 receipt 재사용, 등록 tenant 불일치. 즉 **source unknown**(검색·분류·수집 시각이 불완전)은 언제나 blocked다. **genuinely no timely filing**(store 시계로 cutoff 다음 날 이후 수집, 완결·비필터, 전 행 분류, family 0건)만 not_applicable이다. cutoff 이후 접수된 family 행은 기록에 남고 판정에 쓰이지 않는다. v2 pinned 경로는 collection을 요구하지 않는다. receipt가 collection을 지명하면 같은 결합 검사를 한다.

- **input 1.2** `contracts/reconciliation/input-1.2.schema.json`(패키지 사본 동일): input 1.1과 같고 `schema_version="1.2"`와 nullable `identity.financial_document_version`만 다르다(테스트로 차이 고정). v2만 받는다. legacy·v1은 input 1.1만 받으므로 1.2는 schema 오류다. 재무 문서를 지명한 1.2 packet은 그대로 1.1 경로를 탄다(엔진에는 1.1로 전달). 재무 문서가 없는 1.2 packet은 부재 증명 외에는 `financial_document_version_missing`으로 막힌다. **부재 사례에 placeholder 문서 ID를 쓰지 않는다.** input 1.1의 문자열 version은 부재 증명에서 `financial_filing_identity_conflict`다.
- **결과**: `execution_state=completed`, `status=not_applicable`, `not_applicable_reason="financial_filing_not_available_as_of"`, `reason_codes=[같은 값]`, `financial_value=null`, `review_required=false`(엔진 관례: completed), `engine_version="reconciliation-application-rec006-1+app-rec-002-006-v2"`. 재무 fact가 없으므로 fact binding, 설명 coverage, domain 비교는 실행하지 않는다. 정책 승인, 원문 hash, registry 결합, claim role 검증은 그대로 거친다.
- **`filing_lookup`** 필드는 다음과 같다.
  - `outcome`: `pinned` 또는 `no_timely_filing`
  - `cutoff`
  - `collection_sha256`, `retrieved_from`, `retrieved_until`: pinned이고 collection이 없으면 null
  - `search_request`, `page_sha256[]`
  - `pinned_rcept_no`: null 가능
  - `family_basis`: null 가능
  - `filings[{rcept_no, rcept_dt, report_nm, timely, lineage}]`: `report_nm`은 원문대로 보존하고 파싱하지 않는다.

  blocked 결과는 `filing_lookup=null`이다.
- **output schema** `contracts/reconciliation/output-1.2.schema.json`(패키지 사본 동일)은 1.1 필드에 `not_applicable_reason`, `filing_lookup`을 더한다. `status=not_applicable`이면 사유가 필수이고, 아니면 null이다. REC-006 사유는 `completed`와 `outcome=no_timely_filing`을 요구하며 그 역도 성립한다. `no_timely_filing`은 collection hash와 시각을 요구한다. domain N/A(`not_comparable`, `period_out_of_scope`, `c3_trigger_absent`)는 해당 코드를 사유로 옮긴다. 예시 `example-output-1.2.json`은 실제 실행 결과이며 테스트로 고정한다.
- **1.1 독자**: `service.downgrade_to_1_1()`은 REC-006 N/A를 v1과 같은 `blocked`(`financial_filing_not_available_as_of`, `output_1_2_required`)로 보인다. 다른 결과는 두 필드만 뺀다.
- **전달 경로**
  - CLI `evaluation/reconciliation_cli.py`: `--adopted-revision`, `--revision-receipt`, `--filing-pages DIR(<sha256>.json)`, `--filing-collection`, `--evaluation-date`를 받는다. 플래그가 없으면 legacy 경로 그대로다. revision 입력만 주면 거부한다.
  - projection: 1.2 결과에만 `reconciliation-presentation-2`를 쓴다. 필드는 `result_schema_version`, `not_applicable_reason`, `filing_lookup`, `result`, `http_result_view="output-1.1-downgrade"`이고, 최상위 `execution_state`/`status`도 1.2 값이다.
  - store: HTTP `ReconciliationResult`가 1.1로 닫혀 있다. 그래서 저장 `result`는 1.1 downgrade이고, 전체 1.2 결과는 열린 `projection.result`에 담는다. `CaseDetail`·`RevisionSnapshot` DTO 검증을 테스트한다.

## 구성 요소

- `packages/proofops/application/reconciliation/revision.py`: 순수 stdlib 게이트, 페이지 조립기, 날짜 정규화.
- `packages/proofops/application/reconciliation/service.py`: opt-in kwargs `adopted_revision`, `revision_receipt`, `filing_page_reader`. legacy 경로는 `_reconcile(receipt=None)`로 동일하다.
- `packages/proofops/adapters/dart/filings.py`: `collect_filing_history(client, …)`. `last_reprt_at=N`, `max_pages` 상한, 실패는 클래스명만 기록(키·URL 비노출), `(record, pages_by_sha256)` 반환. 네트워크 호출은 호출자가 client를 주입할 때만 일어난다. 테스트는 fake transport만 쓴다.
- `packages/proofops/adapters/local/reconciliation_store.py`: 선택 bundle 키 `adopted_revision`/`revision_receipt`/`filing_pages`를 받는다. 가져올 때 `confirmation`을 제거하고, 페이지를 `_rec_pages/`에 hash 검증 후 배타적으로 생성한다. 검토 이벤트가 `revision_receipt_sha256`을 기록하고, 평가 시 최신 검토 이벤트 hash가 일치할 때만 confirmation을 주입한다. case detail schema가 닫혀 있으므로(`additionalProperties: false`) receipt와 `revision_receipt_state`는 열린 객체인 `provenance`에만 노출한다. **revision 키가 없는 사례는 snapshot·detail·review event 형태가 그대로다.**

## 검증

- `tests/reconciliation/test_rec002_rec006.py` (28): 결합 설명 matched, 무관 인용 제외, legacy 대비, 다른 차이·claim·package 결합 제외, 지정 설명 미결합 차단, 불완전 검색, 원문 변조, receipt 변조, receipt 누락·미확인, stale 원본, cutoff 이후 정정, 미분류, 누락 페이지·`Y`·창·corp 불일치, 페이지 변조, 같은 날 모호, 적시 공시 없음, 기간 인용 누락·제목만, 연결범위, cutoff 형식, 날짜 정규화, 수집기 페이지·상한·drift·중복·실패·013을 검사한다.
- `tests/reconciliation/test_rec002_rec006_store.py` (6): 실제 verified run anchor 위에서 import 시 confirmation 제거와 미검토 차단, 검토 후 재생 게이트 도달, 관리 페이지 변조, receipt 없는 revision 차단, 페이지 누락 거부, legacy 형태 불변을 검사한다.
- `tests/reconciliation/test_rec006_no_filing.py`(35개)는 fixture bundle builder로 만든 실제 input 1.2 모양에서 다음을 검사한다.
  - 정상 결과: v2 N/A 결과·사유·schema·rcept_no 이력·collector 시각, 빈 공식 목록, collector 시계 페이지 기록과 clock 없는 legacy record 불변, 예시 계약 고정
  - 호환성: v1·legacy의 input 1.2 거부, v1/1.1 독자 blocked 유지, schema 역검사, input 1.2와 1.1의 차이 고정, projection 버전 분리, v2 pinned 결과의 1.1 downgrade = v1 결과
  - 조회 시각 출처: receipt의 시각 변경·추가 거부, collection 편집 거부, collection 누락·legacy·불완전, confirmed_on이 수집 이전이거나 평가일이 확인 이전인 경우, 수집이 cutoff 이전·당일·평가일 이후인 경우, cutoff 미래
  - 불완전 수집과 순서: 불완전 수집(페이지·`Y`·창·미분류), 실제 필터 수집, cutoff 당일 family 접수, 같은 날 family 복수
  - 변조와 격리: receipt 변조·v1 receipt·누락, 페이지·SR 원문 변조, tenant 불일치
  - 증거 요건: family·기간·연결범위 선언의 누락·불일치·날짜 없는 인용, 재무 공시 식별자 충돌(1.2 version·1.1 version 포함), 재무 문서 없는 1.2 packet 차단
  - 전달 경로: CLI v2(collection 유무)·v1·legacy·stray 입력; store 시계 수집 → register → review → approve → evaluate와 HTTP DTO 검증·교차 tenant 거부; store 경로에서 시각 편집 receipt 차단, 없는·타 tenant collection 거부, collection receipt의 페이지 import 거부, collection 표 불변, legacy store 모양 불변

## 남은 통합 (미구현·미실행)

- 실제 OpenDART 수집: root가 2026-09-29 09:05 UTC에 Kia 사업보고서 목록을 수집했다(공개 메타데이터 `evidence/kia-filing-collection-20260929.json`, 원시 receipt bytes는 비공개). 이 수집은 `pblntf_detail_ty=A001`로 **필터된** 검색이고, 검토자 lineage 분류·재무 기간 근거·cutoff 마감 후 조회 완결성을 증명하지 않는다. 따라서 v2 no-timely-filing 요건을 충족하지 않는다. 실제 사례 receipt 작성과 검토자 분류는 여전히 **not_run**이다.
- composition/HTTP·CLI가 신규 사례를 `adopted_revision`으로 등록하도록 강제하는 기본값 전환은 하지 않았다. 현재는 bundle에 명시해야 한다. 강제하려면 import 계약 변경과 rollback 정의가 필요하다.
- store의 `explanation_search`는 여전히 `_no_candidates`다. 저장소 경로에서 REC-002는 packet이 지정한 설명에만 적용된다.
- REC-006의 “완결 조회·적시 공시 없음 → 명시적 not_applicable”은 opt-in v2/input·output 1.2로 구현했다. v1은 계속 `blocked`다. 기본값 전환, HTTP 전용 1.2 필드, web 표시는 하지 않았다(API·web 소유 밖). legacy·v1은 input 1.2를 schema 오류로 거부한다. 기존 발견: input 1.1 packet의 재무 fact source_id가 null이면 legacy 경로가 `_bind_facts`에서 KeyError로 닫힌다(출력 없음). frozen 경로라 고치지 않았다.
- `pblntf_ty`/`pblntf_detail_ty` 필터 검색을 완결로 인정하는 승인된 유형 집합은 없다(coordinator 확인). 그래서 N/A는 필터 없는 목록만 인정한다.
- store 수집 메서드는 서버 전용이다. HTTP·composition 연결과 실제 OpenDART 키 주입은 하지 않았다(API 소유 밖, 실행 not_run).
- store의 수집 시각, 확인일, 평가일은 모두 UTC다. 비교는 UTC 날짜로 하며 KST 기준보다 보수적으로 작동한다.
- 연결범위 증거는 pinned 문서의 검증된 인용과 검토자 결합까지만 확인한다. 의미 판정(연결/별도 문구 해석)은 하지 않는다.
- HTTP 응답은 기존 `provenance`(열린 객체)로만 receipt를 노출한다. 전용 필드·검토 UI는 없다.
