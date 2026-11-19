# PCB RMA v3 · 수정 내역

- 작성: 2026-11-17
- 기준: PCB v2 (`PCB_RMA_dataset_v2.zip`, 내부 번호 v31) 위에 후처리 5개를 적용함
- 근거: `../../PCB_v2_데이터검사_결과.md`, `../../PCB_소분류_라벨_예외기준.md`

## 재현 방법
v31 폴더에서 아래 순서로 실행한다. 각 스크립트는 앞 단계 결과가 맞는지 확인한 뒤에만 돌아간다.

```
python3 patch_v3.py <dir>          # 구조 수정
python3 patch_v3_text.py <dir>     # 접수 문장 수정
python3 patch_v3_severity.py <dir> # 심각도 기준(AIAG-VDA) 적용
python3 finalize_v3.py <dir>       # 유사 사례, 평가 분할, 샘플 CSV, manifest 갱신
python3 slim_v3.py <dir>           # 멀티 에이전트 범위에 필요 없는 테이블 제거 (55 → 42개)
```

## 1. 구조 수정 (`patch_v3.py`)

| 검사 결과 | v3 반영 | 건수 |
|---|---|---:|
| 즉시 3: 통보의 투입 수량이 고객이 받은 수량보다 큼 | 해당 고객에 인도된 Lot 수량 이하로 조정하고, 통보 문장의 "투입 N pcs"도 함께 고침 | 6,455 |
| 즉시 2: 단발 불량이 접수 5시간 만에 원인 이름의 조사에 연결됨 | 연결 시각을 원인 적용 시각으로 옮김. 기준 시점까지 원인이 적용되지 않은 건은 연결을 해제함 | 2,191 이동 / 217 해제 |
| 즉시 4: `claim_count`에 판정 이후 클레임 포함 | `lot_impact_assessment.claim_count_at_decision` 추가. `impacted_lots_without_claims` 뷰는 이 값 기준으로 바꿈 | 437행에 차이 |
| (연쇄) 연결 해제된 클레임의 Lot 판정 | 클레임이 조사에 연결되기 전의 단건 Lot 판정을 삭제함 | 110 |
| 권장 9: 승인 전 원인 적용 | 적용 시각을 승인 시각으로 옮기고, 서비스 조치와 고객 회신도 함께 이동. 승인이 없던 E-Test 재검 원인 3건은 평가 하루 뒤 승인으로 보완. 기준 시점까지 승인이 없는 1건(CAUSE-000030)은 적용 보류 | 1,111 이동 / 75 보류 |
| 권장 8: D4 완료가 원인 승인보다 앞섬 | D4 완료 시각을 원인 승인 시각으로 맞춤 | 22 |
| 참고 13: Lot 완료 전 출하 | 출하 시각을 Lot 완료 1시간 뒤로 옮김 | 2 |

**영향 Lot 수치 기준:** IMPACTED 832행 중
- 기준 시점까지 클레임이 없는 행: 503
- 판정 시점에 클레임이 없던 행: **631** ← ③ 영향 Lot 추적의 평가 기준

## 2. 접수 문장 수정 (`patch_v3_text.py`)

| 내용 | 건수 |
|---|---:|
| 증상 표현 다양화: 증상마다 새 표현 3~5개 추가, 약 절반의 문장에 적용 | 4,676 |
| 띄어쓰기를 없앤 문장 (입력 노이즈) | 504 |
| **정보 부족** `INSUFFICIENT_INFO`: 애매한 통보 → CS 재질의 → 고객 회신 → QA 판정 전 재분류 | 300 |
| **분류 불가** `UNCLASSIFIED`: 18종 밖 증상(실크 번짐, 색상·광택 차이 등). 불량 미재현 건 중에서 선택, QA 판정은 임시 L2 | 100 |
| SOLD01 경계 문장: "BGA 볼 일부가 패드에 젖지 않아 미접합" (휨 언급 없음 → SOLD01) | 136 |
| ELEC04 측정 조건 포함 (`customer_test_report` 출처 확인됨) | 170 |
| ELEC04 측정 조건 일부만 있음 (픽스처만 있고 쿠폰 정보 없음 → 출처 미확인) | 40 |

### 스키마 변경
- `symptom_definition`: `INSUFFICIENT_INFO`, `UNCLASSIFIED` 추가 (major `NA`, severity 0)
- `symptom_assessment.intake_label`: 접수 문장 기준 라벨. **접수 분류 모델의 정답**
- `symptom_assessment.reclassified_at`: 정보 부족 건이 고객 회신으로 재분류된 시각
- `primary_label`: QA가 확정한 증상 (정보 부족 건은 회신 후 소분류)
- `analysis_level_rule` L2 조건에 "소분류 미확정(분류 불가) 임시 판정" 추가
- `communication`: CS 재질의(`author_role='CS'`)와 고객 회신(`author_role='customer'`) 600건 추가

## 3. 심각도 기준 적용 (`patch_v3_severity.py`)
AIAG-VDA FMEA 핸드북(2019) 심각도 표를 기준으로 했다. 자사 공장, 고객 공장, 최종 사용자 영향 중 가장 높은 값을 쓰고, 검출도는 제외했다. 소분류별 근거는 `../../PCB_소분류_라벨_예외기준.md` 6장에 있다.

| 소분류 | 변경 | 이유 |
|---|---|---|
| ELEC01 오픈, ELEC02 쇼트 | 7 → 8 | 유출 시 주요 기능 상실 |
| STRC04 홀·배럴 크랙 | 8 → 9 | 최종 영향이 ELEC03(간헐 오픈)과 같음 |
| ELEC04 임피던스 | 6 → 7 | 고속 신호 품질 저하 = 주요 기능 저하 |
| DIM01 외형 치수 | 5 → 6 | 조립 불가 → 전량 선별 (DIM02·STRC03과 같음) |

**분석 수준 재판정 (규칙은 그대로)**
- 변경된 건수: ELEC02 L2→L3 471건, ELEC01 L2→L3 290건, DIM01 L1→L2 25건
- 전체 분포: L1 559 / L2 5,846 / **L3 3,595** (기존 L3 2,834)

**조사 처리**
- 시나리오 조사 INV-25-0020(P13 쇼트), INV-25-0021(오픈)은 L3로 올리고, 보고서를 8D 전체로 바꿨다. 둘 다 진행 중인 조사라 D7은 "미착수"로 추가했다.
- 단발·불량 미재현 분기 묶음 15개는 L2를 유지했다. 접수 때 L3로 판정된 건이 있지만 공통점 추적에서 단발 결함으로 확인돼 8D 약식으로 관리한다는 사유를 `scope_definition.level_note`에 남겼다.
- `document_change`의 PFMEA 심각도를 새 값 기준으로 다시 계산했다.

## 4. 파생 파일 (`finalize_v3.py`)
- `similar_cases`: 바뀐 문장으로 다시 계산 (접수 시점 이전 자료만 사용, v31과 같은 방식)
- `evaluation/`: 분할 구성은 그대로 두고 입력·정답만 DB에서 다시 만듦. 정답에 `intake_minor_code` 추가
- `lot_impact_reference.jsonl`: `impacted_lots_without_claims_at_decision` 추가
- `sample_csv/`, `schema.sql`, `manifest.json` (`v3_patch`, `v3_text`, `intake_labels`)

## 5. 범위 축소 (`slim_v3.py`)
멀티 에이전트 범위에 맞춰 필요 없는 테이블을 지웠다. 범위는 다음과 같다.
- 알려진 원인만 다룸
- 8D 진행 관리와 영향 Lot 추적은 제외
- L3 = 사람 승인 게이트

**테이블 55 → 42개, 뷰 14 → 7개**

| 지운 테이블 | 이유 |
|---|---|
| `qa_report`, `eight_d_step`, `containment_action`, `document_change`, `supplier_action`, `customer_disposition_approval`, `monitoring_cohort` | 8D 진행 관리 |
| `lot_impact_assessment` | 영향 Lot 추적 제외 |
| `analysis_activity` | 조사 병목 분석 |
| `return_authorization`, `return_receipt` | 시료 회수 물류. 반송품 시험은 `test_run`에 직접 연결됨 |
| `order_item`, `panel` | 주문·패널 상세. 추적은 `shipment`·Lot 단위로 충분 |

- 지운 뷰: `lot_impact_overview`, `impacted_lots_without_claims`, `investigation_activity_duration`, `bottleneck_by_activity`, `eight_d_progress`, `corrective_action_status`, `customer_open_dispositions`
- `shipment.order_item_id`, `rma_item.panel_id`는 값은 그대로 두고 외래 키만 뺐다.
- **남긴 조치 정보:** `corrective_action`, `effectiveness_check`, `recurrence_review`. 사례 저장소의 "그때 한 조치·효과·재발 여부"에 쓴다.
- **분석 수준 규칙의 산출물을 에이전트 흐름에 맞춰 바꿈** (`analysis_level_rule`)
  - L1: 소분류·회신 초안
  - L2: 유사 사례 기반 원인·공정·장비 후보 + QA 확인
  - L3: 후보와 근거 + **사람 승인(원인 확정·격리·4M 변경)** 후 사례 저장
- `manifest.json`의 `checks`는 이 단계 전, 전체 v3에서 계산한 값이다.
- `evaluation/lot_impact_reference.jsonl`은 남아 있지만 이번 범위에서는 쓰지 않는다.

### L3 (사람 승인 대상) 현황
| 구분 | L3 | 전체 | 비율 |
|---|---:|---:|---:|
| 차량 | 3,112 | 5,446 | 57.1% |
| 반도체 | 483 | 3,747 | 12.9% |
| 산업 | 0 | 807 | 0% |
| **합계** | **3,595** | 10,000 | **36.0%** |

- L3의 **86.6%가 차량 고객**이다 (심각도 반영 전 83%).
- 소분류별 L3: ELEC03 971, STRC02 600, ELEC02 471, SOLD02 446, ELEC05 441, STRC04 376, ELEC01 290
- L3 조사: 11개 (시나리오)
- 미처리 226건 중 L3 61건, 그중 12월 접수 27건 → **승인 게이트 시연 후보**

## 이번에 고치지 않은 것
| 항목 | 이유와 대응 |
|---|---|
| 즉시 1: 원인 클래스가 학습·테스트에 겹치지 않음 | 시나리오 하나가 원인 하나이고 한 시기에만 발생해서 생긴 문제. 시나리오를 다시 설계해야 함. **평가 방식으로 대응**: 22개 시나리오를 시점별로 다시 돌리고, 원인 후보 순위로 평가 |
| 소분류 일부(SOLD02·SOLD03·MARK01)가 시간 분할 학습에 없음 | 위와 같은 이유. 조사 분할을 쓰거나 독립 평가셋으로 평가 |
| 권장 6: 반송품 시험 결과가 원인을 완벽히 가름 | 원인 판정 흐름 전체에 영향이 커서 보류 |
| 권장 7: `same_material_json`이 일치 여부를 담지 않음 | ③ 구현 때 의심 자재 Lot과 비교하는 특징을 만들어 씀 |
| 권장 10: 유출 원인 근거 내용 불일치 | 8D 초안 작업 때 근거를 `equipment_event`에서 직접 가져오는 방식으로 대응 |

## 확인 결과 (v3 재검사)
- 무결성 ok, 외래 키 위반 0
- 위 수정 항목 위반 0: 투입 수량 > 인도 수량, 조기 조사 연결, 승인 전 적용, D4 순서, 출하 순서
- 시간 순서 위반 0: 발생 < 인도, 판정 < 접수, 회수 승인 < 판정, 기준 시점 이후 메시지
- 분석 수준이 규칙과 100% 일치 (분류 불가 = 임시 L2 포함)
- 문장 기준선(TF-IDF + 로지스틱 회귀, 접수 라벨):
  - 시간 분할: 학습에 있는 클래스 정확도 0.996
  - 조사 분할: 학습에 있는 클래스 정확도 0.894

  표현을 다양화했지만 여전히 템플릿 문장이라 쉽다. **사람이 직접 쓴 독립 평가셋이 실제 평가 기준이다.**
