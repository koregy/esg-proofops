# 원문 이미지 대조: Upstage Document Parse

새 실행에서 `--native-upstage-ocr`를 지정하면 Windows/Linux에서 사용할 수 없는
기본 렌더링 판독기를 Document Parse의 이미지 판독으로 보완한다. API가 반환한
문장을 새로운 원문 근거로 채택하는 기능이 아니다.

## 검증 조건

- 기존 원문 문자·위치·가시성 검사를 모두 통과하고, 렌더링 판독 결과만 정확히
  `UnsupportedPlatform`인 문단을 대상으로 한다.
- 원본 PDF의 해당 영역을 216 dpi로 렌더링하고, 6 px 여백을 붙인 이미지 전용 PDF를 전송한다.
- API 판독과 원문 단어가 기존 정규화 및 고정된 곡선 따옴표 변환으로 일치해야 한다.
  숫자 변경, 문장 누락, 공백 제거, 유사 문자열 일치는 인정하지 않는다.
- 원본 해시, 검증 코드·라이브러리 버전, 이미지 대응표, 요청·응답 해시와 비용 기록을 고정한다.
  재열람 시 원본에서 이미지를 다시 만들고 저장된 응답을 재생하며 API를 재호출하지 않는다.
- 처리 상한 때문에 건너뛴 문단과 불일치는 계속 미확정이다. 선택 페이지의 성공은 전체 문서의
  판독 완료 또는 근거 부재를 뜻하지 않는다.

## 실행

기존 공용 사용 장부와 로컬 키 파일을 사용한다. 다음 명령은 먼저 실행 계획만 출력한다.
실제 호출은 같은 명령에 `--invoke`를 추가한다. `--state`는 새 빈 디렉터리여야 한다.

```sh
uv run python scripts/analyze_report.py \
  --pdf /path/to/report.pdf --pages 28 \
  --report-year 2025 --period-start 2024-01-01 --period-end 2024-12-31 \
  --state .local/new-report-run \
  --key-file /path/to/.env.upstage.local \
  --budget-ledger .local/upstage/submission-20260929.sqlite3 \
  --native-upstage-ocr --upstage-ocr-max-calls 3 \
  --tagging-model solar-pro3
```

Java 21을 자동으로 찾지 못하면 `--java-path`로 실행 파일을 지정한다. 이 실행기는 추출에
Solar Pro 3를 사용한다. `--tagging-model solar-pro3`는 사전 분류·요소 태깅·관계 추출에도
같은 모델을 고정한다. 옵션 생략 시 기존 태깅 모델 Solar Pro 4를 유지한다.

표의 역할과 위치별 문맥을 사용하는 새 실행은 `--preliminary-table-role
--position-context-order --extraction-context`를 함께 지정할 수 있다. 실행기는 필요한
사전 분류 문맥·표 문맥 옵션을 자동으로 포함한다. 위치 순서 옵션과
`--preliminary-actor-role`은 함께 사용하지 않는다.

이미지 대조는 한 호출당 최대 10개 문단을 처리하며 `--upstage-ocr-max-calls`는 1~20이다.
`--native-windows-ocr`, `--native-quote-typography`, 기존 raster 경로와 함께 사용하지 않는다.
미정산 요청은 자동 재전송하지 않는다. 학생 계정의 무료 사용 여부와 별개로 공용 장부는
기존 승인 한도와 표준 요금 추정 기록을 유지한다. 장부를 초기화하지 않는다.

## 결과 및 이식성

파싱 체크포인트의 `native_paragraph_upstage_ocr_coverage`는 대상, 요청, 건너뜀,
일치·불일치 원천 ID와 `complete`를 제공한다. 이 값은 선택된 대상 문단의 처리 범위다.
주장의 인용 범위 검증은 별도로 수행하며, 원천 문단 검증만으로 주장을 확정하지 않는다.

현재 Windows/Linux에서 만든 증빙은 macOS에서 그대로 재검증할 수 없다. macOS의 Vision
판독 결과가 저장된 `UnsupportedPlatform` 기본 영수증과 달라 재생을 거절한다. macOS에서는
별도 새 실행을 생성한다. 따라서 이 경로를 운영체제 간 이식 가능한 검증으로 표시하지 않는다.

자동 시험은 양성 경로, 오독·공백 삭제·빈 응답, 미정산 요청 재전송 방지, 영수증 변조,
검증 코드 변경, 다른 실행의 증빙 혼입 및 macOS 재생 거절을 포함한다. 실제 보고서 실행
성공률은 별도 실행 기록으로 확인해야 하며 자동 시험 통과율과 구분한다.
