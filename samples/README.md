# samples

실제 `classify()`·`run_analysis()` 응답(LLM gpt-5.4-mini, v4 데이터). 백엔드 목업과 화면 개발에 쓴다.
다시 만들기: `python3 scripts/make_samples.py`

| 파일 | 경우 |
|---|---|
| RMA-08743.json | 차량 L3, 공정 원인(외층 에칭 잔동)과 검사 유출 (명세 예시) |
| RMA-09325.json | 차량 L2, 자재 원인(동박 표면 처리) |
| RMA-08240.json | 반도체 L2, 공정 원인(디스미어)과 장비 |
| RMA-09340.json | 반도체 L1, 단발 원인(취급 스크래치), 장비 특정 불가, `LLM_OUTPUT_REJECTED` 경고 예시 |
| RMA-08288.json | 정보 부족 접수 → 재질의 (`analysis` 없음) |

각 파일: `claim`(접수 정보), `classify`(3-1 응답), `analysis`(3-3 응답).
