# PCB RMA 합성 데이터 v5

> **버전 이름:** Drive 파일명과 내부 번호가 다르다.
> - PCB v1 (`PCB_RMA_dataset_v1.zip`) = 내부 v30
> - PCB v2 (`PCB_RMA_dataset_v2.zip`) = 내부 v31
> - PCB v3 (`PCB_RMA_dataset_v3.zip`)
> - PCB v4 = v3 + 품질 손실 금액
> - **PCB v5 (이 폴더)** = v4 + 재발 사건 4개, 헛신호, 지저분한 고객 문장 (AI 평가용)
>
> 문서와 스크립트의 `v30`·`v31`은 내부 번호다. 테이블·컬럼 설명은 `데이터사전_v4.md`를 본다(v5에서 늘어난 `claim_text_variant`는 `PCB_RMA_v5_수정내역.md`).

> **문구 정리 (2026-11-19):** 고객 접수 문장과 고객 커뮤니케이션의 요청 문구 3종을 정리했다 (`원인 분석 및 대책서 회신 부탁드립니다.`, `긴급 봉쇄 조치 내용을 먼저 공유 부탁드립니다.`, `[봉쇄 조치] 영향 Lot ...`). 라벨·근거 구절·그 밖의 값은 그대로다. `patch_request_text.py`, 기록은 `manifest.json`의 `request_text`.
>
> **v5 (2026-11-18):** 재발 사건 4개(P04R·P05R·P12R·P09R, 반품 +610 → 10,610건), 헛신호(원인과 무관한 공정 이상), 10월 이후 고객 문장 12.9% 변형(`claim_text_variant`)을 넣었다. 전체를 다시 생성했으므로 행 내용이 v4와 다르다. 재현: `PYTHONHASHSEED=0 ./run_all.sh <out>`. 설계는 `PCB_RMA_v5_설계.md`, 변경과 평가 결과는 `PCB_RMA_v5_수정내역.md`.
>
> **v4 (2026-11-18):** v3 파이프라인의 `finalize_v3.py`와 `slim_v3.py` 사이에 `patch_v4_loss.py`와 `patch_v4_terms.py`를 넣어 **품질 손실 금액**(가정 단가표 `cost_rule`, 제품 원가 `product_cost`, 손실 원장 `loss_ledger`, 뷰 3개)을 추가했다. 테이블 45개, 뷰 10개. 설계와 결과는 `PCB_RMA_v4_수정내역.md`를 본다.
>
> **v3 (2026-11-17):** v31 위에 `patch_v3.py` → `patch_v3_text.py` → `patch_v3_severity.py` → `finalize_v3.py` → `slim_v3.py`를 적용했다.
> - **테이블 55 → 42개, 뷰 14 → 7개.** 멀티 에이전트 범위(알려진 원인, 영향 Lot 제외)에 필요 없는 13개를 지웠다. 아래 본문에 나오는 영향 Lot 테이블은 이 DB에 없다. 변경 사항은 `PCB_RMA_v3_수정내역.md`를 본다.
> - 투입 수량·조사 연결·원인 승인·출하 시각 정합성 수정
> - 영향 Lot 판정 시점의 클레임 수(`claim_count_at_decision`) 추가
> - 접수 문장 다양화, 정보 부족(`INSUFFICIENT_INFO`)·분류 불가(`UNCLASSIFIED`) 사례, ELEC04 측정 조건, SOLD01·STRC03 경계 문장 추가
> - 심각도를 AIAG-VDA FMEA 기준으로 재정의(5종 상향)하고 분석 수준 재판정 (L3 2,834 → 3,595)
> - 접수 분류 모델의 정답은 `symptom_assessment.intake_label`, 평가 파일에서는 `intake_minor_code`
>
> 아래 본문은 v31 기준이며, 수치는 `manifest.json`이 최신이다.

SK AX 회신 메일(2026-11-16, "RMA AI 프로젝트 관련 현업 기준")을 현업 요구사항으로 가정하고, v28(SSD 조립)을 **PCB 제조 도메인**으로 전환해 다시 생성한 데이터셋이다. 값·사건·고객·공급사는 모두 합성 가정이며 실제 현업 데이터를 대표하지 않는다. 기준 시점은 2025-12-31이다.

## 구성

| 파일 | 내용 |
|---|---|
| `pcb_rma.sqlite` | 전체 DB(테이블 55개, 뷰 14개) |
| `schema.sql` | 테이블·뷰 DDL |
| `sample_csv/` | 테이블별 최대 100행 미리보기 |
| `evaluation/` | 시간 기준 분할 입력·정답(train < 2025-08-01 ≤ validation < 2025-10-01 ≤ test), 영향 Lot 정답 `lot_impact_reference.jsonl` |
| `generation_truth/` | 생성 시나리오·클레임별 참값(서비스 입력으로 쓰지 말 것) |
| `manifest.json` | 행 수, 분포, 시나리오 요약, 검증 결과 |
| `generate_v30.py`, `finalize_v30.py`, `pcb_config.py`, `generation_config.json` | v30 기본 생성. v31 후처리와 검증은 아래 재현 절차 참고 |

## 도메인 요약

- 제품 6종: 차량 ECU 6층, BMS 후동 4층, ADAS 레이더 8층 HDI, 메모리 모듈 10층, SSD 컨트롤러 8층 HDI, 산업 제어 4층.
- 공정 20단계, 7개 대공정: 원자재 입고 → 내층 제작 → 적층(산화·프레스·드릴) → 외층 제작(디스미어·도금·노광·에칭·AOI) → 후공정(PSR·표면처리·라우팅·세척) → 최종검사(E-Test·FQC) → 포장·출하(베이킹·진공 포장·출하검사).
- 생산 1,864 Lot, 패널 334,720매. 공정 이력은 Lot 또는 로드(프레스·드릴·도금·표면처리·포장) 단위로 남는다.
- 고객 24개사(차량 10, 반도체 8, 산업 6). 불량 통보 10,000건. B2B 통보 문장에 P/N, Lot, Date code, 검출 단계, 투입·불량 수량이 들어 있다.

## 주요 조회

| 목적 | 뷰/테이블 |
|---|---|
| 접수~원인 한 줄 조회 | `qa_case_overview` |
| QA 접수 판정(심각도·특별특성·분석 수준) | `qa_triage`, `analysis_level_rule` |
| 영향 Lot 판정(공통점 기준) | `lot_impact_assessment`, `lot_impact_overview`, `impacted_lots_without_claims` |
| 고객 증상과 QA 재현 비교 | `qa_symptom_reproduction` |
| 원인 후보 검토 | `cause_candidate`, `cause_assessment`, `escape_assessment`, `cause_confirmation_basis` |
| 후속 조치 | `corrective_action`, `document_change`(PFMEA·CP·WI), `supplier_action`(SCAR), `customer_disposition_approval`, `containment_action` |
| 효과성·재발 | `effectiveness_check`, `monitoring_cohort`, `recurrence_review` |
| 병목 분석 | `investigation_activity_duration`, `bottleneck_by_activity` |
| 공정 SPC·특별특성 | `spc_out_of_spec_by_equipment_month`, `special_characteristic_results` |

## 규칙

- 원인 범주는 PROCESS·MATERIAL·INSPECTION과 판정 불가(UNDETERMINED: 불량 미재현·분석 중)뿐이다. 배송·물류·고객 과실·호스트 설정 원인은 없다.
- `cause_assessment`가 CONFIRMED이려면 출처가 확인된 반송품 시험과 생산 기록(공정·설비·자재)이 근거로 있어야 한다. 이 기준은 제조 원인을 확정할 때 적용한다. 불량 미재현은 시험한 조건과 해당 시료 범위에서만 해석한다. 고객 측 측정값(`customer_test_report`)은 시료 출처·측정 조건이 확인된 경우에만 근거로 쓴다.
- 영향 Lot은 클레임 건수·비율로 정하지 않는다. 동일 Lot·자재 Lot·설비(로드)·공정 조건·시간대 공통점과 보관 시료 시험으로 판정한다. 클레임이 없어도 영향 Lot이 될 수 있다.
- 반품 시료 회수는 CS 가이드가 아니라 QA 접수 판정(`return_authorization.decided_by='QA'`)으로 결정한다.

## v31 감사 및 재현

- 변경 사항과 확인할 사례는 `PCB_RMA_v31_감사및수정내역.md` 참고.
- 기존 시간 분할은 `evaluation/`에 유지했다. 사건 단위 분할은 `evaluation/investigation_holdout/`에 추가했다.
- 생성 정답, 최종 QA 판정, 시험 선택·결과는 합성 규칙의 영향을 받는다. 두 분할의 점수는 실제 현업 성능을 증명하지 않는다.
- 새 출력 디렉터리로 재생성할 때 이 폴더에서 `python3 generate_v30.py <out>`, `python3 finalize_v30.py <out>`, `python3 patch_v31.py <out>`, `python3 reconcile_v31.py <out>`, `python3 patch_v3.py <out>`, `python3 patch_v3_text.py <out>`, `python3 patch_v3_severity.py <out>`, `python3 finalize_v3.py <out>`, `python3 slim_v3.py <out>` 순서로 실행한다. 두 후처리 스크립트로 SQLite·샘플 CSV·스키마·평가 분할·manifest를 갱신한다.
