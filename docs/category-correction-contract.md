# 세이프하버 범주 정정 계약 (R24, `claim_category_removal_v1`)

대상 결함: 태깅 packet과 3개 guarded 헤더가 같은 값으로 합의했지만 원문이 그 범주를
지지하지 않는 claim은 `apply_source_exceptions`에서 `GAP-001`로 조기 반환되어
**요소 검토를 완벽히 끝내도 영구히 등급을 받을 수 없고**, 잘못된 범주의 체크리스트가
safe-harbor 리더에 노출된다. 기존 요소 검토(`track`/`elements`)로는 범주를 바꿀 수 없고
`_review_safe_harbor`는 정의상 이미 기록된 범주만 문서화한다.

이 계약은 그 한 가지를 최소로 정정한다. 진단·소비자 추적 근거는
`outputs/agent-results/R24/category-review-gap/diagnosis.md`에 있다.

## 1. v1 범위 — 제거만

`corrected_category`는 **반드시 `null`**이다. 비어 있지 않은 값은
`CATEGORY_CORRECTION_UNSUPPORTED` (422)로 거부한다. 범주 **추가·교체**는 검토된 근거가
입증하지 않는 새로운 규제 분류를 주장하는 일이므로, 구체적 필요가 확인될 때까지
지원하지 않는다. 정책 이름이 범위를 그대로 말한다(`..._removal_v1`).

정정 대상은 의미 분류 하나다. **등급을 보장하지 않는다.** 요소가 미해결이면 정정 후에도
`blocked_evidence` / `grade=null`로 남는다.

## 2. 요청 계약 (정확히 9키)

```json
{
  "policy": "claim_category_removal_v1",
  "input_snapshot_sha256": "<canonical_hash(inputs.snapshot())>",
  "track": "performance",
  "observed_category": "emissions_estimate",
  "corrected_category": null,
  "claim_source_refs": [ "...replay된 claim.source_refs 전체와 바이트 동일..." ],
  "basis_refs": [ "...packet에서 해소되는 verified ref 1..n..." ],
  "source_authority": "적용한 기준 원문(5..1000자)",
  "reason": "그 기준을 이 문장에 적용한 판단(5..1000자)"
}
```

키 집합이 다르면 `CATEGORY_REVIEW_INVALID` (422).

### 가드

| # | 가드 | 실패 |
|---|---|---|
| G1 | `policy == claim_category_removal_v1` | `CATEGORY_REVIEW_INVALID` 422 |
| G2 | `input_snapshot_sha256 == canonical_hash(inputs.snapshot())` (tenant/run/document/claim/graph/packet/rulepack/tag_runs/consensus 일괄 pin) | `CATEGORY_REVIEW_INVALID` 422 |
| G3 | `track == body["track"]` | `CATEGORY_REVIEW_INVALID` 422 |
| G4 | `observed_category`가 고정된 rulepack `regulatory/safe_harbor.yaml`의 `category_checklists` 키에 존재 | `CATEGORY_REVIEW_INVALID` 422 |
| G5 | `corrected_category is None` | `CATEGORY_CORRECTION_UNSUPPORTED` 422 |
| G6 | `observed_category`가 **모든 guarded 헤더의 유일 값이며 동시에 packet 값과 같다** | `CATEGORY_REVIEW_OBSERVED_MISMATCH` 409 |
| G7 | `claim_source_refs`가 replay된 `claim.source_refs`와 canonical 동일(부분 인용 금지) | `WHOLE_CLAIM_REQUIRED` 422 |
| G8 | claim refs와 `basis_refs` 전부 `verify_source_ref(...) == verified` | `CATEGORY_SOURCE_REJECTED` 422 |
| G9 | `basis_refs`가 비어 있지 않고, packet ref 색인에 canonical 일치로 존재하며 `source_scope in (local_claim, same_table)` | `CATEGORY_SOURCE_REJECTED` 422 |
| G10 | `source_authority`/`reason` 각각 5..1000자 | `CATEGORY_REVIEW_INVALID` 422 |
| G11 | guarded 헤더가 갈리면 정정으로 봉합하지 못한다 | `CATEGORY_REVIEW_REQUIRED` 409 |

`_review_category`는 **`ConfirmedFact`를 0개** 만든다. 범주는 관찰된 사실이 아니라 분류
헤더다. 헤더·packet·prompt/model/replica 해시는 영구히 원본 값을 유지하며, 정정은
`ConfirmedTags.safe_harbor_category`만 `null`로 바꾼다.

## 3. safe-harbor 검토와의 충돌 — fail closed

`effective_category`가 있고 `effective_safe_harbor`도 있으면
`CATEGORY_REVIEW_CONFLICTS_SAFE_HARBOR` (409)로 **거부**한다. 신규 공급이든 `reopen`
carry든 같다. 체크리스트는 제거 대상 범주 자체를 문서화한 기록이므로 한 revision에
섞지 않고, 조용히 드롭하지도 않는다. CLI도 두 JSON을 함께 주면 같은 코드로 먼저 막는다.

제거된 범주의 체크리스트 항목명(`category_checklists[observed_category]`)은 **새 사실
기반에서만** 제외된다(`previous_names`). 이전 revision의 사실과 receipt는 불변으로 남는다.

## 4. 재검토 carry

`reopen`에서 정정을 다시 공급하지 않으면 직전 head의 `category_review.request`를
**같은 가드 체인으로 재검증한 뒤** 이어받는다. 사람이 나중에 재검토해도 동일하며,
receipt의 `carried_from`에 최초 위임 출처(`origin=ai_delegated` 등)를 보존해
사람 재검토가 AI 위임 조상을 지우지 못한다. 관찰 헤더·packet 값은 receipt에 계속 원본으로
pin되고 `confirmed_tags.safe_harbor_category`는 정정된 `null`을 유지한다.

`track`을 바꾸는 재검토에서 carried 정정은 G3 때문에 거부된다(fail closed). 다른 track에서
같은 범주 판단을 그대로 이어쓰지 않으려는 의도이며, 필요하면 새 정정을 명시적으로 공급한다.

## 5. 호출 표면 — HTTP 불변

- `ReviewService.resolve_ai_delegated_review(..., category_review=None)`
- `ReviewService.resolve_review(..., category_review=None)` — 신뢰 백엔드 전용 키워드.
  HTTP 라우터는 이 값을 공급하지 않으므로 **요청/응답 스키마와 `contracts/openapi.yaml`은
  변경 없다.** 정정 없이 호출하면 오늘과 동일하게 동작한다.
- `scripts/review_ai_delegated.py --category-review-json <path>` — dry-run이 기본이며,
  dry-run에서 **실제 요청을 replay된 loader 입력으로 검증**한 뒤 `--apply`를 요구한다.
  `--re-review` dry-run은 claim head 포인터로 **현재 tag head**를 읽어 carried 체크리스트
  충돌도 `--apply` 전에 거부한다(최신 행 추측이 아니라 `resolve`와 같은 접근 경로).

멱등성: 공급된 모든 신뢰 옵션(`applicability_review`/`safe_harbor_review`/`category_review`)이
`_trusted_ai_review`로 retry identity에 들어간다. AI 라벨(`extra_tag`) 유무와 무관하므로
**사람 표면에서도** 같은 키로 receipt만 바꾼 재요청은 `IDEMPOTENCY_CONFLICT`가 된다.
신뢰 옵션이 없으면 identity는 기존과 바이트 동일하다.

## 6. 호환성·롤백

| 항목 | 내용 |
|---|---|
| DB | 스키마 변경·신규 테이블·migration 없음. `tag_revision` JSON에 additive 키 1개 |
| API | HTTP 필드·상태 코드 불변. 신규 오류 코드는 HTTP로 노출되지 않음 |
| 구 reader | 미지의 키를 무시. `encode_revision_record`가 `tag`를 통째로 보존하므로 export 코드 변경 없이 receipt가 실린다 |
| 경합 | 기존 `If-Match` + `Idempotency-Key` + `store.resolve` CAS 그대로 |

**롤백의 정직한 한계.** 롤백은 "새 정정을 더 기록하지 않는 것"이다. 이미 기록된 revision과
receipt는 불변으로 유지되고 현재 head도 정정된 상태로 남는다. `observed`와 `current`를
뒤집는 역방향 검토는 제공하지 않는다 — G6가 `observed_category`를 **원본 헤더·packet
값**에 묶기 때문에 `observed=null`을 진술할 수 없다. 정정을 공급하지 않는 이후 재검토는
직전 receipt를 carry·재검증하므로 제거가 조용히 되돌아가지 않는다. 관찰된 범주가 다시
기록되는 경우는 **이 구현 자체를 되돌렸을 때**(carry 로직이 사라지면 범주는 다시 헤더에서
온다) 또는 같은 claim을 **새 run에서 다시 태깅**했을 때다. 후자는 그 run에서 새 정정을
공급해야 한다.

## 7. 검증

`tests/integration/test_category_review.py` (실제 store/loader/engine, 합성 입력, 유료 호출 0):
가드 거부 13종, 공급·carry 충돌 fail closed, 정당한 제거, 사람 재검토 carry+AI 조상,
이전 revision 바이트 불변, safe-harbor 리더에 잘못된 체크리스트 미노출, export 인코딩
receipt 보존, stale If-Match, 같은 키 + 변경된 receipt 충돌(AI·사람 두 표면), CLI dry-run의
실제 요청 검증.

실제 문서 정확도 개선이나 UI 전체 증명은 주장하지 않는다.
