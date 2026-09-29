# Solar Pro 3 live 태깅 선택 (NEW run 전용)

사용자가 학생용 Solar Pro 3을 추출뿐 아니라 예비분류·태깅·관계 역할에도 쓸 수 있도록, **새 run에서 명시적으로 선택**하는 경로를 추가했다. 기본값은 `solar-pro4`로 그대로다.

## 원칙

- 기존 파일럿 `--model`은 추출만 제어한다. 태깅 모델은 별도 `--tagging-model {solar-pro4,solar-pro3}`(기본 `solar-pro4`)로 고른다.
- 한 run의 preliminary·tagging·relation 세 역할은 **같은 모델**이어야 한다. 입력 예약 정책이 하나로 고정되고 모델별로 검증되기 때문이다. 섞이면 run 생성이 `CONFIG_GATE_BLOCKED`, worker는 `LIVE_TAGGING_MODEL_MISMATCH`로 멈춘다.
- Pro 3 정책은 commit e4a7455의 `input_reservation_pro3.solar_pro3_capacity_policy()`를 **그대로** 쓴다(2^18 보수적 입력 상한, 2026-09-29T08:55Z–2026-10-02 유효). Pro 4 정책·기존 snapshot·hash·기본 설정은 바꾸지 않았다.
- fallback은 없다. Pro 4 probe는 Pro 3 run을 처리할 수 없고 그 반대도 마찬가지다. 공급자가 다른 모델명으로 답하면 `UPSTAGE_RECEIPT_INVALID_RESERVATION_RETAINED`로 멈추고 예약을 보존한다.
- 같은 공유 ledger(`LOCAL_UPSTAGE_LEDGER_PATH`/기본 ledger)를 쓰고, 각 호출은 **실제 Pro 3 가격 snapshot**(`upstage-solar-pro3-2026-09-09`)으로 정산한다. 학생용 무제한은 사용자 선언일 뿐 공급자 검증을 거친 권리가 아니다. 비용 0 처리, cap 상향, entitlement 인정은 하지 않는다. Pro 4는 기존 USD20 한도 안에 남는다.

## 경로 (settings → snapshot → registry → preflight → transport)

1. **pilot settings**: `live_tagging_settings(..., tagging_model=)`가 세 역할의 `TaggingSettings.model_id`와 정책(Pro 3이면 Pro 3 정책)을 고정한다. `--capacity-policy-refresh`는 Pro 4 전용이므로 Pro 3과 함께 쓰면 거부한다.
2. **registry**: tagger runtime binding의 `model_id`는 고정된 settings의 모델에서 가져온다(더 이상 `solar-pro4` 하드코딩 아님). `tagging_settings_sha256`와 정책 hash가 결합된다.
3. **run 생성** (`application/runs.py`): 허용 모델 `{solar-pro3, solar-pro4}`와 단일 모델 조건을 검사한다. 이후 기존 `check_local_upstage_tagger`(binding 모델 = settings 모델, model hash)와 `validate_capacity_policy(policy, model_id=…)`(Pro 3은 전용 validator로 분기)가 그대로 적용된다.
4. **worker** (`proofops_worker/tagging_model.py`): snapshot의 역할별 모델에서 probe를 고르고 process당 한 번만 만든다. 기존 Pro 4 probe는 legacy run용으로 그대로 둔다. transport는 `settings.model_id == probe.model`을 다시 확인한다.
5. **resume**: manifest에는 Pro 3일 때만 `tagging_model`을 기록한다(legacy manifest는 형태가 그대로다). resume은 저장된 모델을 복원하고, 다른 `--tagging-model` 요청은 거부한다. 기존 run은 snapshot 모델로 계속 진행된다.

## 검증

- `tests/integration/test_solar_pro3_tagging.py`: Pro 3 세 역할과 Pro 3 정책을 고정한 run 생성 성공, Pro 4 정책·Pro 4 binding·역할 간 혼합·미지원 모델·정책 만료·role 예산 부족 거부.
- `tests/integration/test_solar_pro3_live_tagging_worker.py`: fake HTTP로 snapshot이 Pro 3 probe를 선택하는지, 세 transport가 같은 probe를 쓰는지, 호출 body의 모델, ledger 정산 가격(Pro 3)을 확인한다. Pro 4 probe·Pro 4 응답·Pro 4 정책·혼합·미지원·누락은 거부하고, legacy Pro 4 snapshot은 그대로 Pro 4를 쓴다.
- 파일럿·analyze_report 흐름 테스트는 `tests/unit/test_solar_pro3_pilot_flag.py`에 있다.

## 하지 않은 것

- 실제 Upstage 호출, 키 접근, 유료 실행은 **not_run**이다.
- Pro 3 context 128K는 공식 페이지 문구일 뿐이며 tokenizer parity나 실제 용량 검증은 하지 않았다. 정책 만료(2026-10-02) 이후에는 새 정책 revision이 필요하다.
- Pro 3 태깅 품질·정확도는 평가하지 않았다.
