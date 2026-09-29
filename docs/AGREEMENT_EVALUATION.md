# 두 평가자 간 가중 κ

계획서의 사람 간 일치도 지표를 `evaluation.metrics.agreement`와 오프라인 CLI로 계산한다. 기존 `evaluation.metrics.pipeline`의 정확도·macro-F1·혼동행렬은 유지한다.

```sh
python -m evaluation.agreement_eval --input paired-ratings.json --output agreement-result.json
```

입력은 `schema: paired-grade-ratings-v1`, 비어 있지 않은 `dataset_id`, 서로 다른 두 `reviewers`(`id`, `kind`), `independence_declared: true`, `cases`를 갖는다. `kind`는 `human`, `ai_delegated`, `synthetic` 중 하나다. 각 case에는 고유 `claim_id`, 양의 정수 `claim_revision`, 64자리 원문 `source_sha256`, 주장 인용 `claim_quote_sha256`, 두 개의 `ratings`가 필요하다. 등급은 E0~E3이며 보류·미평가는 명시적으로 `null`을 입력한다. 두 평가는 같은 원문 버전과 주장 revision을 대상으로 작성해야 한다.

출력은 전체 사례 수, 두 평가가 모두 존재하는 수, 제외된 claim ID, 평가 완결 비율, 정확 일치율, 4×4 혼동행렬, 선형·제곱 가중 κ를 함께 담는다. 미완결 쌍은 κ 분모에서 제외하되 전체 사례 수와 제외 목록에는 남긴다. 기대 불일치가 0인 단일 등급 표본은 κ가 정의되지 않으므로 `degenerate_marginals`와 `null`을 반환한다. 평가 쌍이 없으면 `no_complete_pairs`다.

가중치는 등급 간 거리를 3으로 나눈 값 또는 그 제곱이다. 관찰 불일치와 각 평가자의 주변분포에서 계산한 기대 불일치로 `κ = 1 - 관찰 불일치 / 기대 불일치`를 계산한다. 결과를 보고할 때 사용한 가중 방식과 완결 쌍 수를 같이 명시한다.

평가자 신원·독립성은 입력자의 선언이며 이 도구가 인증하지 않는다. 입력 해시는 버전을 고정하지만 원문 진위나 gold 승인을 대신하지 않는다. AI 또는 합성 평가를 사람 간 일치도로 표시하지 않는다. 출력은 새 파일로만 기록하며 기존 파일을 덮어쓰지 않는다. 실제 독립 평가 입력 없이 프로젝트의 κ 또는 모델 정확도를 주장하지 않는다.
