# TASK-009 검증 증거

- 날짜: 2026-09-09 (Asia/Seoul)
- 범위: `packages/proofops/application/tagging/tracks.py`, `tests/acceptance/test_tracks.py`
- fixture: AT-009, 명시적 합성·로컬·사람 작성 후보만 사용
- 외부 모델 호출/AWS 변경/고객 데이터 처리: 없음

## 구현 결과

`validate_track_candidates(Claim[], structured candidates)`가 각 claim에 정확히 하나의
`goal|performance|management` 후보와 독립적인 safe-harbor category를 연결한다. 원래
`Claim`을 불변 결과에 그대로 보존하므로 tenant/document/source/model·prompt·rule provenance를
잃지 않으며, topic을 사용한 track 재분류는 하지 않는다. 외부 claim, 중복·누락 후보,
혼합 tenant/source identity, source 없는 claim, 미등록 track/category, grade/label 계열의
추가 필드는 fail-closed로 거부한다.

## TDD RED → GREEN

1. RED: `uv run pytest tests/acceptance/test_tracks.py -q`
   - exit 1
   - `10 failed in 0.05s`
   - 원인: `ModuleNotFoundError: No module named 'proofops.application.tagging'`
2. 최소 구현 후 GREEN: `uv run pytest tests/acceptance/test_tracks.py -q`
   - exit 0
   - `10 passed in 0.03s`
3. 중요도 리뷰에서 unknown track/category 사례가 foreign claim 검증에 먼저 걸리는 테스트
   문제를 발견해 각 mutation이 해당 경계를 직접 타도록 수정했다.

## 최종 검증

모든 명령은 저장소 루트 `<local-project-root>`에서 실행했다.

| 구분 | 정확한 명령 | 결과 |
|---|---|---|
| lint | `uv run ruff check packages/proofops/application/tagging/tracks.py tests/acceptance/test_tracks.py` | exit 0, `All checks passed!` |
| format | `uv run ruff format --check packages/proofops/application/tagging/tracks.py tests/acceptance/test_tracks.py` | exit 0, `2 files already formatted` |
| type | `uv run mypy packages/proofops/application/tagging/tracks.py tests/acceptance/test_tracks.py` | exit 0, `Success: no issues found in 2 source files` |
| acceptance/dependency | `uv run pytest tests/acceptance/test_tracks.py tests/acceptance/test_claims.py -q` | exit 0, `28 passed in 1.70s` |
| unit | `uv run pytest tests/unit -q` | exit 0, `16 passed in 2.09s` |
| contract | `uv run pytest tests/contracts/test_package_contracts.py -q` | exit 0, `29 passed, 2 warnings in 1.51s` |
| integration | `uv run pytest tests/integration -q` | exit 0, `48 passed, 2 warnings in 17.16s` |
| package contract | `uv run python scripts/validate_package.py` | exit 0, `697 passed, 0 failed`; 문서/계약 검사만 수행 |
| build | `uv build --package proofops` | exit 0, sdist와 wheel 생성 성공; wheel에 `application/tagging/tracks.py` 포함 확인 |
| security | `uv run pip-audit` | exit 0, 알려진 취약점 없음; 로컬 workspace 패키지 4개는 PyPI 미등록으로 audit 제외 |
| whitespace | `if rg -n '[[:blank:]]+$' packages/proofops/application/tagging/tracks.py tests/acceptance/test_tracks.py evidence/task-009.md; then exit 1; else exit 0; fi` | exit 0, trailing whitespace 없음 |

계약·통합 테스트의 2개 warning은 기존 FastAPI/Starlette TestClient deprecation이다.
새 dependency는 추가하지 않았다.

## not_run / blocked gate

- 실제 제품 모델·Bedrock/AgentCore 호출: `not_run` (작업 금지 범위)
- AWS mutation 및 staging/E2E 배포: `not_run` (작업 금지 범위)
- 실제 고객/비공개 문서 처리: `not_run` (작업 금지 범위)
- 데이터 권리·법률·규칙 gap 승인 및 사람 전용 gate: `blocked/not_run`; 본 작업이 임의 해제하지 않음
- `GET /v1/runs/{run_id}/claims/{claim_id}` 실제 라우트 E2E: `not_run`; AT-009 계약이 허용한 순수 함수 경로를 검증했고 API/shared contract 파일은 담당 범위 밖이라 변경하지 않음
