# 채택 정책 GAP-001 · GAP-005 구현 상태 (2026-09-29)

근거: `docs/R00_DOMAIN_DECISIONS.md` §12 (2026-09-28 사용자 “추천안으로 일괄 채택”). 채택 문장은 R00 §12 표의 `rule_text`가 정본이다. 외부 기준 검증이나 법적 효력은 주장하지 않는다.

## GAP-001 · A: `project_checklist_completeness_v1`

**이미 구현되어 있다. 코드 변경은 없다.**

- 진리표(`domain/rules/safe_harbor.py`): 고정된 비어 있지 않은 checklist의 모든 item이 검증된 present이면 `true`다. 검증된 absent가 하나라도 있으면 `false`이며, unknown/conflict와 섞여도 `false`다. 그 밖의 unknown/conflict/not_applicable/누락/빈 checklist는 `null`이다. absent는 `ConfirmedFact.search_coverage_verified`를 통과해야 한다.
- E/label은 항상 null이다(세이프하버 경로는 `blocked_rule_gap`). `grade_mapping=null`, `mapping_status=unresolved`, `legal_effect=not_determined`, `GAP-001`이 유지된다. superlative와 세이프하버가 겹치면 `GAP-006`으로 차단된다.
- 버전·활성화: checked-in impl2 pack의 `reasonable_basis_boolean_mapping`은 `null`이다. 정책은 `scripts/review_rulepack.py --checklist-policy project_checklist_completeness_v1 --derived-version <새 버전>`(또는 `derive_checklist_policy_pack`)으로만 **새 ID·버전·hash의 파생 pack**을 만든다. 이 파생 pack은 before hash·before version·경계 벡터·reviewer·시각·source authority를 content에 고정한다. `--apply` 없이는 저장이나 활성화를 하지 않는다. 기존 pack과 run snapshot은 불변이다.
- 새 run에서 쓰는 방법(안전한 경로): dry run으로 파생 pack hash를 확인한 뒤 `--apply`로 기록된 활성화를 한다. `--source-authority`에는 “R00 §12 2026-09-28 사용자 채택 GAP-001·A”를 그대로 적는다. 자동 활성화는 하지 않았다.
- 한계: validator가 인정하는 `review_origin`은 `ai_project_interpretation` 하나다. 적용 행위는 AI coordinator가 하고, 권한 근거(사용자 채택)는 `source_authority`에 기록된다. 별도 `user_adopted` origin은 만들지 않았다(계약 변경이 필요하다). 현재 태깅은 checklist item fact를 생산하지 않으므로, 실제 문서에서는 대부분 `null`이 된다.

## GAP-005 · A: 하위라벨

**현재 동작을 그대로 유지한다. 코드와 규칙집 변경은 없고, 회귀 테스트만 추가했다.**

- 채택 문장 “Only exact goal missing pair [G3,G4] in the §7 example maps to IMPL”은 원문 §7 예시(`PROJECT_DOMAIN_V2_ORIGINAL.md` L608-613: G3·G4·G5·G6 모두 부재, E1 사유가 G3/G4 쌍)의 축약이다. 축약(`missing={G3,G4}`)과 원문 예시 전체(`missing={G3,G4,G5,G6}`)가 서로 달라, coordinator가 2026-09-29에 **원문 예시를 문자 그대로 유지**하기로 결정했다. 확대(G5/G6 무관)나 반전(`{G3,G4}`만)은 하지 않았다.
- 엔진(`domain/rules/engine.py`)은 goal·E1이고, missing이 정확히 `{G3,G4,G5,G6}`이며 모두 검증된 absent이고, unresolved가 없고, excluded가 `{G7,G8}`일 때만 `IMPL`을 준다. 그 밖의 E1/E2는 E/label을 유지하고 `sublabel=null`과 `GAP-005`를 받는다.
- 경계 벡터(`tests/acceptance/test_adopted_gap005_sublabel.py`): 원문 예시는 IMPL이다. `{G3,G4}`만, G3만, G4만, G5 또는 G6만 충족, G3 부분 결손, E2, unknown 포함, G7 trigger, performance/management는 모두 null과 GAP-005이며 E/label은 유지된다. E3는 sublabel이 없다.

## 고정 hash (checked-in impl2, 2026-09-29 측정)

- `pack_content_hash`(manifest+files 그대로): `b77304f866a37784bfd426954451da7ec264b1041890a0a07acd26484ec70366`
- `rubric/exceptions.yaml` canonical: `75c7590ab490b1e89f83d12fdf67ffc053dedcb2a099529d5f9bdb3b2f0a39bb`
- `regulatory/safe_harbor.yaml` canonical: `12db1a2bc19120fac3cdb3d6e993cf9d23c1610efaa85fb5612998c883b3c823`
