# Document Parse 실행과 후보 전달

`scripts/parse_report_api.py`는 원본 PDF를 최대 10쪽씩 처리하고 요청·응답·원문 해시와 물리 쪽 번호를 보관한다. 기본 실행은 계획 확인이며 `--invoke`를 붙이면 API를 호출한다. 아래 명령은 저장소 루트에서 설치된 가상환경의 Python으로 실행한다.

```sh
python scripts/parse_report_api.py --pdf /path/report.pdf --all-pages --ledger /path/session.sqlite3 --out-dir .local/parse-report
python scripts/parse_report_api.py --pdf /path/report.pdf --all-pages --ledger /path/session.sqlite3 --key-file /path/.env.upstage.local --out-dir .local/parse-report --invoke
```

장부는 기존 세션 승인과 누적 사용을 유지하는 동일 파일을 사용한다. 키 파일에는 `UPSTAGE_API_KEY=...`를 저장하고 저장소에 올리지 않는다. 학생 계정의 무료 제공 여부와 별개로 장부의 비용은 표준 요율 기반 추정치이며 청구 금액은 아니다.

중단 후에는 같은 인자에 `--resume`을 추가한다. 완료된 배치는 다시 호출하지 않는다. 성공 여부가 불명확하거나 실패한 호출 기록이 있으면 자동 재호출을 거절하므로 기록을 삭제해서 우회하지 않는다. 일부 쪽만 처리하려면 `--all-pages` 대신 `--pages 2,28,106,130-133`을 사용한다. 이 경우 보고서 전체 검색 완료를 의미하지 않는다.

## 오프라인 후보 패키지

API 결과를 받으면 별도 디렉터리에 원문에 결속된 후보를 내보낸다. 이 단계는 네트워크나 API 키를 사용하지 않는다.

```sh
python scripts/import_parse_api_candidates.py import --parse-dir .local/parse-report --pdf /path/report.pdf --out-dir .local/parse-candidates --require-complete
python scripts/import_parse_api_candidates.py verify --sidecar .local/parse-candidates --pdf /path/report.pdf
```

`derived/review.csv`에서 후보 텍스트·쪽·좌표를 검토한다. 검증 명령은 원문과 보관된 API 응답에서 후보를 재생성하여 변조·불일치를 확인한다. 이 무결성 검증은 원문 인용의 시각적 검증이나 주장 승인과 다르다. 표는 표 전체 위치만 제공하며 셀별 숫자 근거를 만들지 않는다.

기본 식별자는 오프라인 후보용이다. `--tenant-id`, `--document-id`, `--document-version-id`, `--parse-manifest-id`, `--object-version-id` 다섯 인자를 모두 제공하면 해당 식별자에 결속되지만 제품 run 등록·그래프 교체·확정 태그 생성은 수행하지 않는다. 원문 검증 및 검토 revision을 거친 근거만 후속 판정에 사용할 수 있다.

## 2026-09-29 실제 실행

사용자가 제공한 기아 PDF(`d0d814d98c4aeedbbdb2bf8631b8981ae5cde94dec32aa32c57510420274da1f`) 134쪽의 14개 배치가 완료됐다. 후보 2,536개, 그중 표 264개이며 셀 후보는 0개다. Standard 처리의 장부상 비용 추정치는 USD 1.474다. 원문·응답·후보 본문은 로컬 비공개 파일로 보관한다.

Windows 원문 판독 및 제품 run 연결은 별도 구현·검증 단계다. 후보 수를 검증 완료 주장 수나 E등급 확정 수로 표시하지 않는다.
