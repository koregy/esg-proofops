# Reviewed whole-document absence (`search-absence-link-v1`) — consumer contract

R00 §12 공통 가드: `unknown → absent`는 전체 문서 검색 범위·판독 가능성·검색 기록이 완결되고, 전체 corpus에 대한 명시적 검토가 `absent_confirmed`일 때만 허용한다.

- **Producer:** 조건 판정은 [`search-coverage-receipt-contract.md`](search-coverage-receipt-contract.md)의 producer가 한다.
- **이 문서의 범위:** 그 결과를 불변 review revision의 `absent` 사실로 연결하는 consumer다.

## 구성

### `application/tagging/absence_link.py`
- **정책:** `POLICY_HASH`에 다음을 고정한다. 영수증·검토 schema, `absent_confirmed`, `search_prerequisites_complete`, 복합 요소 규칙, 제외 요소(P4, P6).
- **요청:** 다음을 고정한다.
  - 정책과 정책 해시
  - loader 입력 snapshot 해시
  - `claim_id`와 주장 전체 source refs
  - rulepack SHA
  - track
  - 요소별 항목 `{element_id, absent_facts, receipt_sha256, review_sha256}`

  요청에는 content address만 담긴다. client boolean과 자기 해시는 권한이 되지 못한다.
- **`derive_absences`:** 신뢰 port(`LocalSearchCoverageStore`)에서 다음을 수행한다.
  1. `replay`로 영수증을 현재 PDF, graph, claim, run snapshot에서 다시 계산한다.
  2. 영수증 identity를 검토 입력과 대조한다. 대조 항목: tenant, run, claim, claim revision, claim source ids, element, 문서 버전, 원문 SHA, parse manifest, graph SHA, rulepack SHA, `element_state_effect="none"`.
  3. 검색이 완결되지 않았으면 `ABSENCE_SEARCH_INCOMPLETE`로 거절한다.
  4. `absence_prerequisite`로 저장된 검토를 재검증한다. `absent_confirmed`가 아니면 `ABSENCE_NOT_CONFIRMED`로 거절한다.

  모든 조건을 통과한 경우에만 `ConfirmedFact(absent, search_coverage_verified=True)`를 만든다.
- **복합 요소:** `absent_facts`는 요소의 전체 primitive 목록이어야 한다. 예: G3 = `baseline_year`+`baseline_value`.
  - base 요소나 primitive 중 하나라도 `unknown`이 아니면(present, conflict, absent) `ABSENCE_BASE_NOT_UNKNOWN`으로 거절한다.
  - 부분 부재는 표현하지 않는다. 해당 요소는 unknown으로 남는다.
- **제외 요소:** P4(보증 연결)와 P6(수치 점검)은 결정적 서비스의 결과다. 불일치나 미확정을 부재로 바꾸지 않는다.
- **GAP-004:** 이 모듈은 `absent`만 만든다. 다른 쪽의 수치·연도·기준값을 주장에 귀속하는 동작은 없다. 그런 값이 있으면 producer 검토 지침에 따라 `undetermined` 또는 `not_absent`가 된다.

### `application/reviews.py`
- **진입점:** `ReviewService(search_coverage=…)`, `resolve_ai_delegated_review(absence_review=…)`.
- **도출·replay 위치:** 도출(신규)과 replay(재검토)는 writer 트랜잭션 전에 수행한다. producer port가 자체 트랜잭션을 열기 때문이다. `build`는 결과를 적용할 head에 다시 고정한다.
- **body 요소:** 해당 요소는 `state=absent`, `reason_code=search-absence-link-v1`이어야 한다. refs, 값, `credited_from`은 비어 있어야 한다.
- **재검토 carry:** body가 모든 항목을 계속 `absent`로 두면 carry된다. 이때 영수증을 byte 단위로 다시 계산하고, `carried_from`에 원래 출처를 보존한다.
- **idempotency:** 요청이 idempotency 식별자에 포함된다.
- **출처:** 기록되는 출처는 `ai_delegated`다. HTTP의 사람 경로는 요청을 제출할 수 없고, carry만 받는다.

### `adapters/local/assurance_head.py`
P4와 같은 소비자 증명 문맥을 쓴다. `AbsenceProofVerifier`는 `HeadReceiptVerifier` 형태를 따른다.
- **트랜잭션 밖:** 영수증이 있는 head마다 입력을 replay하고, producer 영수증과 검토를 replay한다.
- **트랜잭션 안:** `check_assurance_tag`가 다음을 확인한다.
  - head 영수증의 내부 무결성: 요소와 사실이 absent이고 `search_coverage_verified`인지
  - 정책 해시
  - 입력 고정값
  - 증명에 대한 tag revision·해시 재고정
- **fail-closed:**
  - verifier 부재: `ABSENCE_EVIDENCE_UNAVAILABLE`
  - replay 실패: `ABSENCE_REDERIVATION_FAILED:*`
  - 영수증 변조: `ABSENCE_HEAD_REJECTED`
  - 정책명을 달았는데 영수증이 없음: `ABSENCE_PROOF_MISSING`
- **기존 head:** 영수증이 없는 head는 그대로 동작한다.

### 연결
- `apps/api/.../composition.py`: `<db dir>/search-coverage` store를 연결한다.
- `scripts/link_absence_review.py`: 신뢰 CLI다.
  - 기본은 dry-run이다.
  - `--apply`는 If-Match와 idempotency key를 사용하고, 적용 뒤 증명 가드를 거친 claim reader로 다시 읽는다.
  - `--re-review`와 `--correction-json`(track 변경)을 지원한다.

## 시험 — `tests/integration/test_absence_review_link.py`

합성 입력을 쓴다. 문서는 실제 Helvetica PDF 바이트이고, 텍스트는 block box 안에 있다. 모델·키·네트워크는 쓰지 않는다.

**양성:**
- goal 주장 "… 2030 … 40%"를 쓴다. G1과 G2는 주장 안 근거로 present다.
- G3~G6은 완결된 전체 검색과 `absent_confirmed` 검토를 거쳐 absent가 된다.
- G7·G8은 trigger 부재 적용성 검토로 제외된다.
- 결과는 **E1 / INCOMPLETE / IMPL**이며, §7 exact branch다.
- API 왕복: 실제 claims router 상세 조회가 absent 요소와 IMPL, ETag를 돌려준다.
- 재검토 carry와 idempotency 재생을 확인한다.

**음성 (모두 아무것도 기록되지 않는다):**
- 주장 국소 packet만으로 absent 제출: `COVERAGE_OR_APPLICABILITY_REQUIRED`
- 검색 미완결 (`ABSENCE_SEARCH_INCOMPLETE`):
  - 등록 2쪽 미판독
  - 이미지 영역
  - 텍스트 층에만 있고 block에 없는 표 행
- `not_absent` 또는 `undetermined` 검토
- 오래된 claim revision, 원문 PDF 변경
- 오래된 입력, rulepack·정책 해시 불일치, 주장 일부만 지정
- 복합 부분 부재, 다른 요소의 영수증
- P4 요청
- base 요소가 conflict인 경우
- 타 테넌트로 복사한 영수증, port 부재
- 다른 block의 연도를 G3 기준연도로 쓰는 전역 수치 귀속(GAP-004)
- 한 idempotency key로 다른 요청, 재검토 없는 두 번째 resolve

**소비자:**
- 게시 뒤 원문 PDF가 바뀌면 HTTP 409다.
- 같은 content address로 다시 쓴 영수증 파일은 거절한다.
- 변조된 head 영수증은 거절한다.
- verifier가 없으면 거절한다.
- 기존 head는 변경 없이 제공된다.

## 한계
- 실제 Windows run은 producer 조건상 대부분 incomplete일 것이다. 렌더러가 없으면 block이 검증되지 않고, 이미지가 있는 쪽이 많다. 그런 경우 absent는 나오지 않는다(fail-closed).
- 소비자 읽기마다 producer replay 비용이 든다. PDF 판독, graph, claim 재구성이 포함되며 캐시는 없다.
- 전 문서 검토의 의미 판단은 사람 또는 위임 AI의 기록이다. 코드는 범위·형식·결속만 검증하고 의미를 판정하지 않는다.
