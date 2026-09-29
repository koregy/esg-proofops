# P6 결정적 수치 대조 연결 (`numeric-link-v1`)

P6(본문 수치와 표 일치)는 LLM 투표가 아니라 순수 수치 검사의 결과다. tagging은 모델이 준 P6를 `unknown`과 `DETERMINISTIC_CHECK_REQUIRED:P6`로 막고, review는 P6 present를 같은 prior fact가 있을 때만 받는다. 이번 작업은 그 prior fact의 producer를 P4 `assurance-link-v1`, absence `search-absence-link-v1`과 같은 구조로 추가했다.

## 구성

- `packages/proofops/application/tagging/numeric_link.py` (신규)
  - 요청: policy·hash, 로더 snapshot hash, 전체 claim span, 명시적으로 수락된 **comparison** binding 하나. binding은 observation id, claim 안의 보고값 span, metric/scope/subject/scope2 basis/조직경계/unit/분모/기간/quantity kind를 담는다. 추가 키(예: tolerance)는 거부한다.
  - observation은 재생된 원본 graph에서 `normalize_tables`로 다시 계산한다. 표만 대상이므로 서술 문장은 observation이 되지 않는다. 모든 source ref가 재검증될 때만 `verified`로 올리며, domain 검사가 이를 다시 확인한다.
  - claim 자신의 block에서 나온 observation은 `NUMERIC_SELF_COMPARISON`으로 거부한다. 각 차원은 검증된 claim subspan 또는 검증된 ClaimContext와 같아야 하며, ClaimContext가 다른 값을 가리키면 `NUMERIC_CONTEXT_CONFLICT`로 거부한다. 같은 값의 다른 표 행을 임의 선택해도 entity 차원 불일치로 결론을 만들 수 없다.
  - `consistent` → `numerical_check` present(`consistent`, scope `computed_check`), `inconsistent` → conflict(`inconsistent`)다. 근거는 31장 GAP-003 행("source-verified 동일 범위 본문·표 불일치 → 해당 수치 사실 conflict")과 `ConfirmedFact`의 conflict 지원이다.
  - `not_computable`/`not_comparable`/`needs_review`는 fact를 만들지 않는다. P6는 `unknown`으로 남고 절대 `absent`가 되지 않으며, receipt에 사유가 남는다.
  - `replay_numeric_receipt`: 현재 snapshot·source로 다시 계산하고 byte 단위로 같아야 한다.
- `application/reviews.py` (공유 파일, 좁은 hunk만)
  - `numeric_review` 옵션을 추가했다. 새 요청은 derive하고, 이미 저장된 receipt를 가진 재검토는 replay한다.
  - P6 body가 파생 fact와 다르면 `NUMERIC_ELEMENT_MISMATCH`, 결정되지 않은 결과면 `NUMERIC_NOT_DECIDED`로 거부한다. 성공하면 `tag["numeric_review"]`에 receipt를 남기고, 요청을 retry identity에 포함한다.
  - 새로 입력된 P6 `conflict`도 present와 마찬가지로 같은 prior fact를 요구한다(`DETERMINISTIC_CHECK_REQUIRED`). 이미 저장된 revision의 conflict는 같은 fact로 carry된다. facility context 경로는 기존대로 P6를 unknown으로 둔다.
- `adapters/local/assurance_head.py` (공유 파일)
  - `check_numeric_integrity`는 receipt hash, policy, request, snapshot pin, P6 element, `numerical_check` fact가 서로 일치하는지 확인한다.
  - `NumericProofVerifier`는 transaction 밖에서 source로부터 전체 replay를 한다.
  - `_VERIFIER_ATTRIBUTES`에 `numeric_review`를 추가해 기존 모든 head 소비자(claim API, export, summary, comparison, reconciliation, rescore, review resolve)가 같은 fail-closed guard를 거친다. verifier가 없으면 `NUMERIC_VERIFIER_UNAVAILABLE`이다.
- `apps/api/src/proofops_api/composition.py`: `numeric_verifier`를 연결했다.
- `scripts/link_numeric_review.py` (신규)
  - `observations`: 읽기 전용이며 검증 상태와 차원을 보여 준다.
  - `link`: 기본은 dry run이고, `--apply`는 If-Match·idempotency 조건으로 실제 서비스를 호출한다. `--re-review`를 지원한다. 결정되지 않은 결과는 쓰지 않는다.
  - P4·absence·numeric 세 검증기와 ReviewService의 의견서·검색 증빙 로더를 함께 연결한다. 기존 `link_assurance_p4.py`, `link_absence_review.py`의 compose도 같은 연결을 사용해 혼합 receipt가 있는 head를 읽고 재검토할 수 있다. 로더 누락 시 무기록 거절, stale If-Match 거절, 정상 재검토 시 두 receipt의 불변 승계를 검사한다.

## 불변 조건

- 단위 변환은 normalizer의 명시적 규칙(`천 X` → X×1000)만 쓴다. 허용 오차를 새로 만들지 않고, domain의 반올림 구간 규칙만 쓴다.
- P6는 R00 §7 A-2의 추가 요소다. E/label/range를 바꾸지 않으며 테스트로 확인했다.
- 이전 tag·decision revision은 불변이다. 기록되는 출처는 `ai_delegated`이며 사람·gold·법적 승인이 아니다.

## 한계

- 이번 버전은 comparison(표 값 1개 대 본문 값) 하나만 지원한다. sum/reduction/growth/product_reduction은 domain에 있지만, 연결 정책은 추가 검토 후 새 버전으로 다룬다.
- 결정적 present/conflict 통합 테스트는 명시적 합성 parser 출력과 실제 ReviewService/SQLite/head reader를 사용한다. 별도 테스트는 실제 합성 PDF를 OpenDataLoaderParser로 파싱해 표 셀이 생성되는 것, 검토 전 셀이 `unverified`로 남는 것, PDF 바이트 변조가 원본 digest 검사에서 거부되는 것을 확인한다. 실제 PDF에서 P6 present까지의 검토·승격 절차와 고객 보고서 적용은 not_run이다. 운영에서는 `tags.load_inputs`가 run의 저장 graph를 재생한다.
- API와 세 review CLI composition은 세 proof verifier를 연결한다. 이 범위 밖의 reader에 numeric verifier가 없으면 numeric head는 fail-closed로 거부된다.
