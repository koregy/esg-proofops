# 제출용 환경(E) 범위 후보 탐색 — 제품 연결 (R03 부분)

상태: 구현·로컬 검증 완료(2026-09-29). 같은 날 후속 작업에서 PDF 파싱을 별도 제한 프로세스로 옮겼다(아래 "격리 작업자"). 후보 제안 기능이며 E 범위 전수 처리 완료, 정확도, 근거 부재 증명을 뜻하지 않는다.

## 무엇이 바뀌었나

| 구분 | 이전 | 이후 |
|---|---|---|
| 섹션 탐색 로직 | `evaluation/report_sections.py`(평가 전용, 경로 입력) | `packages/proofops/adapters/parsing/report_sections.py`의 `inspect_pdf_bytes(content, expected_sha256=)`. 메모리 바이트만 입력, 파일 경로·URL 입력 없음 |
| 평가 CLI/`analyze_report.py --auto-scope` | 같은 모듈 | `evaluation/report_sections.py`는 호환 래퍼. `inspect(Path)`의 출력(`source_path`, `map_sha256` 포함)은 동일. 실제 87쪽 PDF와 3쪽 PDF에서 구·신 결과가 완전히 같음 확인 |
| API | 없음 | `GET /v1/versions/{version_id}/scope-proposal?expected_sha256=` (`version_scope_proposal`) |
| 웹 | 페이지 수동 입력만 | RunForm에서 "환경(E) 범위 후보 찾기" → 결과 확인 → "제안 페이지를 지정 범위로 적용" → 검토자 수정 가능 |

정책 hash(`POLICY_HASH`)와 판정 규칙은 바꾸지 않았다. 기존 실행·보고서 hash, run snapshot, DB 스키마는 영향이 없다(migration 없음).

## API 계약 (추가만)

- 인증: 세션 쿠키, 최소 역할 `editor`, 테넌트 경계는 기존 `version_snapshot(tenant, id)`를 사용한다. 다른 테넌트나 없는 버전은 `404 RESOURCE_NOT_FOUND`로 존재 여부를 드러내지 않는다. 읽기 전용 GET이라 CSRF·Idempotency-Key는 필요 없다.
- 입력: 경로의 `version_id`(UUID)와 필수 `expected_sha256`(64 hex)만 받는다. 서버는 `UploadService.read_original`로 해당 테넌트의 검증된 원본만 읽고, 크기와 SHA를 다시 확인한다. 사용자 경로·URL 입력이 없으므로 SSRF나 임의 로컬 파일 읽기 경로가 없다.
- 한도: 100MiB, 500쪽(`MAX_PDF_BYTES`/`MAX_PAGES`)이며 업로드 게이트와 같다. 사용자별 6회/분, 프로세스당 동시 계산 1건이다. 동시 요청은 `429 SCOPE_INSPECTION_BUSY`와 `Retry-After`를 받는다. 결과 캐시는 (tenant, version, sha, policy hash) 키로 최대 16개이며, 버전과 원본이 불변이라 다른 원본을 반환할 수 없다.
- 실패 코드:

| 코드 | HTTP | 의미 |
|---|---|---|
| `SOURCE_SHA_MISMATCH` | 409 | `expected_sha256`이 버전 SHA와 다름(stale) |
| `UPLOAD_INTEGRITY_MISMATCH` | 409 | 저장 원본이 검증 SHA와 다름 |
| `VERSION_NOT_READY` | 409 | 버전 상태가 ready가 아님(아직 수락 전인 업로드는 버전이 없어 404) |
| `PAGE_COUNT_MISMATCH` | 409 | 검사 페이지 수가 수락 기록과 다름 |
| `SECTION_SOURCE_TOO_LARGE` / `SECTION_SOURCE_ENCRYPTED` / `SECTION_SOURCE_INVALID` / `SECTION_INSPECTION_FAILED` | 422 | 한도 초과·암호화·읽기 실패. PDF 내부 오류 문자열은 노출하지 않음 |
| `SOURCE_UNAVAILABLE` | 503 | 원본 파일을 읽을 수 없음 |
| `SECTION_INSPECTION_TIMEOUT` | 503 | 작업자가 제한 시간을 넘어 강제 종료됨. `retryable=false`(같은 원본은 같은 결과), 수동 페이지 지정 안내 |
| `SECTION_INSPECTION_RESOURCE_LIMIT` | 422 | 메모리·출력 한도 초과로 강제 종료됨 |
| `SECTION_INSPECTION_UNAVAILABLE` | 503 | 격리 수단(Job Object/세션)을 얻지 못해 실행하지 않음. `Retry-After: 60` |

- 응답 `DocumentScopeProposal`: `status=candidate_only`, `full_scope_declared=false`, `apply_as=declared_subset`, `source_sha256`, `policy_sha256`, `map_sha256`, `parser_versions`, `proposed_pages`(= 환경 본문 후보 ∪ 데이터·부록 근거 후보), 후보별 페이지 목록, `unknown_pages`, `conflict_pages`, `other_candidate_pages`, 섹션 경계(제목 200자 제한), `issue_counts`, `coverage=unvalidated`, `live_model=not_run`, 한계 목록을 담는다. 서버 경로(`source_path`)와 페이지 본문 미리보기는 공개 응답에서 제외한다.

## 격리 작업자

`packages/proofops/adapters/parsing/report_sections_worker.py`의 `inspect_pdf_bytes_isolated`가 API 대신 PDF 구조를 읽는다.

- 부모(API)는 크기·SHA를 먼저 확인한다. 불일치하면 프로세스를 만들지 않는다. 그 뒤 원본을 전용 임시 폴더(`.parse-*`)에 쓰고 `python -I <worker.py> <work>`를 고정 argv로 실행한다. shell과 사용자 입력 argv는 없다. 환경변수는 최소 집합만 넘기고 부모 환경은 상속하지 않는다.
- 실행기는 파서 어댑터에서 이미 검증한 `OpenDataLoaderParser._execute`를 재사용한다. Windows는 일시중지 상태로 생성한 뒤 Job Object에 넣고 재개한다(메모리·CPU·프로세스 수 상한, 닫으면 전체 종료). POSIX는 새 세션을 쓰고 `killpg`로 트리를 종료한다. 벽시계 제한 시간, 메모리 감시, 출력 바이트 감시, 제한된 수거(reap)를 적용하며, 종료 후 임시 폴더를 지운다. 시간 초과 시 기다리기만 멈추는 것이 아니라 자식 프로세스 트리를 실제로 종료한다.
- 기본 한도: 180초, 1GiB, 결과 32MiB. `SCOPE_PROPOSAL_TIMEOUT_SECONDS`(1..900), `SCOPE_PROPOSAL_MEMORY_MIB`(64..8192)로 조정하며, 범위 밖 값은 시작 시 실패한다. 조용히 넓히지 않는다.
- 자식은 in-process와 같은 `inspect_pdf_bytes` 결과를 JSON으로 돌려준다. 부모는 `source_sha256`, `policy_sha256`, `status`, `source_path` 부재와 `map_sha256` 자기 hash를 다시 확인한다. 실제 87쪽 PDF와 합성 PDF에서 in-process 결과와 완전히 같았다(hash 호환).
- 단일 실행 슬롯(429)과 캐시는 그대로다. 작업자가 끝나거나 강제 종료되면 슬롯을 반드시 반환한다.

## UI 규칙

1. 문서 버전을 선택해도 자동 요청하지 않는다. 버튼을 눌러야 계산한다.
2. 응답의 `version_id`, `source_sha256`, 페이지 수, 상태, 페이지 목록이 현재 버전과 맞지 않으면 사용하지 않는다(위조/stale 방지).
3. 제안을 적용해도 `scope=declared_subset`와 `selected_pages`만 채운다. `full`로 바꾸지 않으며, unknown·conflict 페이지는 자동으로 넣지 않고 표시만 한다.
4. 적용 후 검토자가 페이지를 추가하거나 삭제할 수 있다. 수정 여부는 화면에 표시된다. 실행 요청 본문은 기존 `RunCreate` 그대로다(추가 필드 없음).
5. 후보가 없으면 "환경 내용이 없다는 뜻이 아님"이라고 안내하고 수동 지정을 유지한다.

## 롤백

- API: 환경변수 `SCOPE_PROPOSAL_ENABLED=0`으로 재시작하면 라우트가 등록되지 않는다(404, OpenAPI에서도 빠짐). 코드 롤백은 `documents.py`의 `if scope_proposals:` 블록 제거로 충분하다.
- 웹: 라우트가 꺼지면 버튼은 남아 있고 누르면 "범위 제안을 사용할 수 없습니다. 페이지를 직접 지정하세요."가 표시된다. 수동 입력 경로는 그대로다. 버튼까지 없애려면 RunForm의 "환경 범위 제안" `<section>`을 제거한다.
- 데이터: 저장 상태가 없으므로 되돌릴 데이터가 없다. 이미 만든 run은 `selected_pages`만 기록되어 있어, 과거 run·판정·보고서 hash는 바뀌지 않는다.

## 한계

- 요청은 여전히 동기다. 작업자가 끝나거나 제한 시간(기본 180초)에 강제 종료될 때까지 HTTP 요청이 기다린다. 실제 87쪽 PDF는 작업자에서 약 88초 걸렸다. 비동기 작업 큐와 진행률은 없다. 프록시 timeout이 제한 시간보다 짧으면 클라이언트가 먼저 끊기지만, 작업자는 제한 시간 안에 종료된다.
- 클라이언트가 연결을 끊어도 작업자는 제한 시간까지 계속 실행된다(자원 상한 안에서). 연결 끊김 즉시 취소는 구현하지 않았다.
- Windows 메모리 상한은 Job Object가, POSIX는 표본 감시(watchdog)가 담당한다. POSIX에는 커널 메모리 상한이 없고 표본 사이에 짧게 초과할 수 있다(파서 어댑터와 같은 한계). 컨테이너/cgroup 한도는 배포 쪽 통제로 남는다.
- 결과 크기 32MiB 안에서 제목·미리보기 문자열 개수는 별도로 세지 않는다. 공개 응답은 미리보기를 빼고 제목을 200자로 자른다.
- 제안 출처(`map_sha256`)는 run 레코드에 저장되지 않는다. `RunCreate`가 strict라 필드를 추가하지 않았다. 필요하면 별도 계약 버전으로 추가한다.
- 로컬 합성(local-synthetic) 구성에서만 마운트된다(기존 documents router와 같은 조건).
- 후보 품질은 기존 평가 결과와 같다: 범위 끝 경계는 다음 앵커 직전까지 연장되며, 표·그림 검증은 not_run이다.
