# 08 · 화면·상태·인터랙션 명세

> ESG ProofOps · 개발 명세 1.0 · 2026-09-08
> 도메인 정본: `sources/PROJECT_DOMAIN_V2_ORIGINAL.md` (원문 2.0, 2026-09-07).

## 1. UX 의 우선순위
첫 화면은 회사 점수가 아니라 **무엇을 고쳐야 하는지, 어떤 근거가 부족한지, 무엇을 아직 읽지 못했는지**를 보여준다. 판정 배지는 E-grade·label, review_status, decision_status 를 구별한다. 판독 실패를 빨간 E0로 표시하지 않는다. 입증됨 옆에는 “이 공시 안의 근거 충족”이라는 범위 설명을 둔다.

## 2. 화면 계약
| Route | 구성/컴포넌트 | 데이터 | 상태와 상호작용 |
|---|---|---|---|
| `/login` | 설명, Cognito 로그인 버튼 | BFF auth redirect | 세션 만료 시 return_to 허용 route 만 보존 |
| `/documents` | DocumentTable, tenant selector, 새 등록 | documents, session | 최초 skeleton; 빈 목록은 업로드 CTA; 권한 없음 안내 |
| `/documents/new` | company/기간/산업/rights form, PDF picker, mode | document_create→version_create→upload_complete | 파일 upload 진행률과 서버 검증 분리; 재시도 중 중복버전 방지 |
| `/documents/:documentId` | VersionList, metadata, 실행 버튼 | document_get, versions_list | ready 버전만 선택, 실행 전에 범위/규칙/동의/예산 표시 |
| `/runs/:runId` | RunStageTimeline, CoveragePanel, cancel/retry | run_get, cost_get | 2초 poll→백그라운드10초, 완료 중지; partial 경고 항상 표시 |
| `/runs/:runId/summary` | MissingElements, ReviewCounts, GradeDistribution, Denominator | summary_get | 분모0은 “적용 대상 없음/아직 판정 없음”으로0%와 구별 |
| `/runs/:runId/claims` | 필터, ClaimTable, saved URL filters | claims_list | page/grade/track/review 필터, cursor 기반 더 보기 |
| `/runs/:runId/claims/:claimId` | PDFSourceViewer, ClaimDetail, ElementGrid, SourceLinks | claim_get, source_get, source_view | 좌측 PDF·우측 태깅; 클릭하면 정확한 page/bbox; 출처 부족하면 하이라이트 금지 |
| `/runs/:runId/quality` | PageQualityTable, ParserConflictDiff, Coverage | quality_get, observations_get | 파서 후보값을 나란히, 임의 합치기 없음; 영향받는 claim 이동 |
| `/runs/:runId/reviews` | ReviewQueue, 3-run 비교, review form | reviews_list, claim_get, review_resolve | reviewer 만 수정; confirm modal 에 태깅 변화와 재채점 예정 표시 |
| `/runs/:runId/assurance` | 기관/수준/기간/경계, coverage matrix | assurance_get | undetermined 회색 설명; all-report-verified 배지 금지 |
| `/runs/:runId/safe-harbor` | category checklist, basis gaps, legal disclaimer | safe_harbor_get | 미확정 매핑은 “근거 기록 제공, 법적 효력 미판정” |
| `/runs/:runId/audit` | 변경 timeline, 버전/hash 복사 | audit_get | 사용자 표시명, 시간, 변경 사유; 원문본문 로그 없음 |
| `/runs/:runId/cost` | stage 별 calls/token/cache/cost | cost_get | unknown_cost 를0원으로 표시하지 않음 |
| `/runs/:runId/report` | export 범위/포맷, snapshot history | export_create/get/download | 검토용 부분 결과 허용 checkbox 기본 true(2026-09-25 구현 b70e897); 미완료 포함·최종본 아님을 표시하고, 해제 시 최종화 게이트 적용; 최종 배지와 검토용 배지 구별 |
| `/runs/:runId/comparison` | prior version selector, change candidates | comparison_create/get | P1 flag, 전년 없으면 not_run; 삭제는 후보로만 표시 |
| `/settings/rules` | 규칙 version·근거 승인 상태 | rulepacks_list, rulepack_activate | 관리자만 active 변경, 미검증 clause 표시 |
| `/settings/runtime` | preflight checks, region/consent info | preflight | live probe 가 API 호출을 발생시킴을 표시, 관리자만 실행 |
| `/settings/evaluation` | dataset split·metric report | evaluation_get | admin read-only; 평가 실행/골드 업로드는 CLI |

## 3. 컴포넌트 계층
`AppShell → TenantNav → RunLayout → CoverageBanner + SectionNav + Outlet`을 기본으로 한다. 주장 상세는 `ClaimWorkspace → SourcePane / EvidencePane / ReviewDrawer`다. EvidencePane 은 `ClaimQuote`, `TrackBadge`, `ElementTable`, `AssuranceCard`, `DecisionCard`, `BasisList` 순서다. ElementRow 는 present/absent/unknown/conflict/N/A 의 문자+아이콘과 근거 수, 인정범위(local/global)를 표시한다.

## 4. 편집 상태와 충돌
리뷰 편집 중 요소값은 local draft 이고 query cache 의 server tag 를 직접 mutate 하지 않는다. 제출 시 If-Match/base_tag_revision 을 보낸다. 412이면 사용자 draft 를 보존한 채 서버 최신 태깅과 diff 를 표시한다. 사용자가 다시 검토하고 제출해야 하며 자동 last-write-wins 를 하지 않는다. 저장 전 페이지 이탈은 경고, 저장 성공 이후 query invalidate 를 run/claim/review/summary 에 수행한다. 규칙 gap 은 성공적인 태깅 확정 뒤에도 별도 경고로 남을 수 있다.

## 5. 디자인 토큰과 반응형
Inter 또는 시스템 sans + Noto Sans KR 가 설치된 환경은 fallback 으로 사용한다(폰트 파일 배포 불필요). 본문16px, 보조14px, 표14px, 제목24/20px, line-height1.5. 간격4/8/12/16/24/32px, radius6/10px, content max1440px. 좌측 PDF60%/근거40%, 화면1024px 미만은 탭 전환, 640px 미만 table 을 핵심 필드 card 로 변환한다. 주요 버튼 최소44px 터치 영역, focus ring, heading 계층, label/aria-describedby 필수다.

상태는 색만으로 구분하지 않고 텍스트/아이콘을 병행한다. 긴 숫자는 줄 바꾸지 않되 가로 스크롤이 페이지 전체를 밀지 않게 한다. PDF canvas 와 별도로 접근 가능한 원문 text panel 을 제공한다. 원문의 HTML/Markdown 은 text 로 render 하고 sanitize 없이 innerHTML 로 삽입하지 않는다.

## 6. 공통 상태
로딩은 레이아웃 크기를 유지하는 skeleton, 빈 결과는 필터 해제/미처리 여부를 설명한다. 네트워크 오류는 마지막 성공 데이터에 stale 배지와 재시도 버튼을 둔다. 인증 만료는 draft 를 민감정보 없는 상태로 유지할 수 있는 범위에서 로그인을 유도한다. tenant 변경 시 기존 데이터·PDF URL·draft·query cache 를 전부 제거한다. source URL403/만료는 서버에서 새 권한 확인 후 한 번 재발급하고 계속 실패하면 오류를 보여준다.


## 7. 문서 등록의 레지스트리 의존성
문서 등록 전에 companies 와 runtime-options 를 조회한다. 기업이 없으면 editor 가 legal_name/aliases 로 새 기업을 생성할 수 있다. 승인된 rights/consent/runtime 값이 없으면 등록·실행 버튼을 비활성화하고 관리자 구성 필요를 표시한다. 임의 UUID 입력이나 알 수 없는 default profile 로 우회하지 않는다.
