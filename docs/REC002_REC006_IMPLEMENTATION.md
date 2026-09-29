# REC-002 · REC-006 구현 (revision `rec-002-006-v1`)

근거: `docs/R00_DOMAIN_DECISIONS.md` §12 (2026-09-28 사용자 채택). 이 문서는 구현 범위와 남은 통합 차단만 기록한다. 정책 문장은 §12가 정본이다.

## 호환성 원칙

- 계약 schema 1.1 (`input`/`policy`/`output`)과 순수 domain 엔진(`reconciliation-engine-1.2.0`)은 **변경하지 않았다**.
- `service.reconcile(...)`는 `adopted_revision=None`(기본값)이면 기존 경로와 동일하게 동작한다. receipt만 넘기고 revision을 지정하지 않으면 `ValueError`로 거부한다.
- `adopted_revision="rec-002-006-v1"`이면 application 계층 게이트가 추가되고, 결과 `engine_version`에 `+app-rec-002-006-v1` 접미사가 붙는다(output 1.1에서 자유 문자열 필드). 저장된 1.1 결과를 재생할 때 어느 규칙 집합이 만들었는지 구분할 수 있다.
- revision을 지정했는데 receipt가 없거나 검토 확인이 없으면 `blocked`다. 기존 경로로 조용히 되돌아가지 않는다.

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
8. cutoff까지 family 공시가 없으면 `blocked / financial_filing_not_available_as_of`다. schema 1.1의 not_applicable 설명 필드가 없어서 REC-006의 “명시적 not_applicable”은 만들지 않았다.

## 구성 요소

- `packages/proofops/application/reconciliation/revision.py`: 순수 stdlib 게이트, 페이지 조립기, 날짜 정규화.
- `packages/proofops/application/reconciliation/service.py`: opt-in kwargs `adopted_revision`, `revision_receipt`, `filing_page_reader`. legacy 경로는 `_reconcile(receipt=None)`로 동일하다.
- `packages/proofops/adapters/dart/filings.py`: `collect_filing_history(client, …)`. `last_reprt_at=N`, `max_pages` 상한, 실패는 클래스명만 기록(키·URL 비노출), `(record, pages_by_sha256)` 반환. 네트워크 호출은 호출자가 client를 주입할 때만 일어난다. 테스트는 fake transport만 쓴다.
- `packages/proofops/adapters/local/reconciliation_store.py`: 선택 bundle 키 `adopted_revision`/`revision_receipt`/`filing_pages`를 받는다. 가져올 때 `confirmation`을 제거하고, 페이지를 `_rec_pages/`에 hash 검증 후 배타적으로 생성한다. 검토 이벤트가 `revision_receipt_sha256`을 기록하고, 평가 시 최신 검토 이벤트 hash가 일치할 때만 confirmation을 주입한다. case detail schema가 닫혀 있으므로(`additionalProperties: false`) receipt와 `revision_receipt_state`는 열린 객체인 `provenance`에만 노출한다. **revision 키가 없는 사례는 snapshot·detail·review event 형태가 그대로다.**

## 검증

- `tests/reconciliation/test_rec002_rec006.py` (28): 결합 설명 matched, 무관 인용 제외, legacy 대비, 다른 차이·claim·package 결합 제외, 지정 설명 미결합 차단, 불완전 검색, 원문 변조, receipt 변조, receipt 누락·미확인, stale 원본, cutoff 이후 정정, 미분류, 누락 페이지·`Y`·창·corp 불일치, 페이지 변조, 같은 날 모호, 적시 공시 없음, 기간 인용 누락·제목만, 연결범위, cutoff 형식, 날짜 정규화, 수집기 페이지·상한·drift·중복·실패·013을 검사한다.
- `tests/reconciliation/test_rec002_rec006_store.py` (6): 실제 verified run anchor 위에서 import 시 confirmation 제거와 미검토 차단, 검토 후 재생 게이트 도달, 관리 페이지 변조, receipt 없는 revision 차단, 페이지 누락 거부, legacy 형태 불변을 검사한다.

## 남은 통합 (미구현·미실행)

- 실제 OpenDART 수집, 실제 기업 receipt 작성, 검토자 분류는 **not_run**이다(키·네트워크·승인 범위 밖).
- composition/HTTP·CLI가 신규 사례를 `adopted_revision`으로 등록하도록 강제하는 기본값 전환은 하지 않았다. 현재는 bundle에 명시해야 한다. 강제하려면 import 계약 변경과 rollback 정의가 필요하다.
- store의 `explanation_search`는 여전히 `_no_candidates`다. 저장소 경로에서 REC-002는 packet이 지정한 설명에만 적용된다.
- REC-006의 “완결 조회·적시 공시 없음 → 명시적 not_applicable”은 schema 1.1에 사유 필드가 없어 `blocked`로 남긴다. 새 output 버전이 필요하다.
- 연결범위 증거는 pinned 문서의 검증된 인용과 검토자 결합까지만 확인한다. 의미 판정(연결/별도 문구 해석)은 하지 않는다.
- HTTP 응답은 기존 `provenance`(열린 객체)로만 receipt를 노출한다. 전용 필드·검토 UI는 없다.
