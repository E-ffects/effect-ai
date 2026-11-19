# PCB RMA v30 · 메일 회신 반영 내역

근거: RE: [SKALA 프로젝트 문의] RMA AI 프로젝트 관련 현업 기준 (SK AX 제조AX서비스7팀 이재민, 2026-11-16). 회신 내용을 현업 요구사항으로 가정했다.

## 1. 타겟 제품 및 제조~출하 프로세스

| 메일 | v30 반영 |
|---|---|
| 반도체 부품 중 PCB 제조를 Target으로 상정 | 제품을 SSD 완제품에서 PCB 원판(6종)으로 전환. 반도체(메모리 모듈·SSD 기판)와 차량 전장(ECU·BMS·레이더) 고객을 함께 둠 |
| VDA 6.3 / IATF 16949 품질 기준 | 차량 고객 10개사에 IATF/VDA 품질 기준·PPAP 표시, 8D·PFMEA·CP 체계 적용 |
| 원자재 입고 → 내층 → 적층 → 외층 → 표면처리 등 후공정 → 최종검사 → 포장·출하 | `process_master.stage_group` 7단계, 세부 공정 20개, 설비 49대 |

## 2. 원인 분류 및 분석 데이터

| 메일 | v30 반영 |
|---|---|
| 배송·물류, 고객 과실은 공정 품질 체계에서 확인 어려움 → 제외 | v28의 호스트·OS·드라이버·운송·포장 손상 원인 분기 전부 삭제. 원인 범주는 공정 21 / 자재 4 / 검사 1개(+ 불량 미재현·분석 중) |
| 제조 공정 중심 + 자재(부품)·검사 영역 원인 분석 | 22개 시나리오마다 발생 원인(공정·자재)과 유출 원인(검사: 샘플링·방법·시료 대표성·SPC 대응·IQC·E-Test·AOI·출하검사)을 함께 둠(`escape_assessment`) |
| 원인 분석은 접수 직후 시작, 분석 수준은 영향 범위·위험도·관리 수준에 따름 | `qa_triage`에서 접수 당일 심각도·특별특성·고객 산업·반복 여부로 L1/L2/L3 판정(`analysis_level_rule`). L3 = 8D 전체 |
| 제품 정보(설계·사양·등급) | `product_spec`(층수·두께·표면처리·원판 등급·임피던스·HDI) |
| 생산 이력(작업지시·Lot·공정별 투입/완료·설비·조건·재작업) | `production_lot`, `process_run`(로드·패널 범위·레시피), `process_observation`(공정 조건 33항목·SPC 플래그), `material_usage` |
| 품질·시험(IQC/OQC, AOI, E-Test, 도금 두께, 단면, SPC, 과거 NC) | `test_run`/`test_measurement`: 패널별 E-Test, Lot별 AOI, FQC(단면·XRF·TDR·솔더 플로트·휨·이온 오염 등), OQC, IQC. `inspection_plan`에 샘플링 개정 이력 |
| 출하·고객 정보(출하일·수량·인도·포장·고객 측 시험) | `shipment`, `shipment_lot`(패널 범위), 포장 공정 조건, `customer_test_report`(측정 조건·출처 확인 여부) |
| 접수 후 실험·분석(현미경·X-ray·성분 분석·재현 시험·양품 비교) | RMA_LAB 시험: 반송품 / 동일 Lot 보관 시료 / 골든 시료 / 미사용 자재 재고. 단면·SEM/EDS·X-ray·리플로 재현(베이킹 전후)·IST·TDR 등 |

## 3. 영향 Lot 판정 및 승인 기준

| 메일 | v30 반영 |
|---|---|
| 건수·비율만으로 판정하지 않고 동일 Lot·자재·설비·조건·시간대 등으로 종합 판단 | `lot_impact_assessment`: 후보 Lot별 공통점(자재 Lot, 설비·로드, 조건 이탈, 시간대), 클레임 수, 보관 시료 결과, 판정(IMPACTED / CLEARED / SUSPECT_MONITOR). 영향 Lot 832개 중 503개는 클레임이 없는데도 공통점으로 판정됨 |
| CS 가이드보다 접수 후 품질 부서가 판단 | 시료 회수·분석 수준 모두 QA가 결정(`return_authorization.decided_by='QA'`). v28의 CS 승인 기준 분기 제거 |
| 불량률보다 불량 형태·심각도·특별특성으로 관리 | `symptom_definition.severity`·`characteristic_id`, `special_characteristic`(CC 2 / SC 8 / 일반 3) |

## 4. 후속 조치(8D) 및 병목

| 메일 | v30 반영 |
|---|---|
| 시정 조치 선정 → 실행·유효성 검증 → 재발 방지·예방 | `qa_report` + `eight_d_step`(D1~D8), `corrective_action.action_role`(OCCURRENCE·ESCAPE·PREVENTION·HORIZONTAL), `effectiveness_check` |
| 고객 처리 방안 승인 | `customer_disposition_approval`(선별 후 사용·반품 교체·재베이킹 특채 등) |
| FMEA·CP 변경 | `document_change`(PFMEA S/O/D 전후, 관리계획서, 작업표준, IQC·검사 기준). 검사 계획 개정은 `inspection_plan` 개정 이력에 연결 |
| 공급업체 조치 요구 | `supplier_action`(SCAR 4건: CCL·프리프레그·PSR 잉크·동박) |
| 병목: 데이터 수집, 영향 Lot 추적, 원인 분석·확정, 효과성 검증 | `analysis_activity` 6종 소요 기간, `bottleneck_by_activity`. 데이터 출처가 많을수록 수집·추적 기간이 길어지도록 생성 |

## 대표 사례

- **P01 → P01R (재발)**: DRL-03 드릴 비트 수명 카운터 이상으로 차량용 기판에 간헐 오픈이 발생. 시정조치는 DRL-03에만 적용됐고 수평 전개가 빠짐 → 7월 DRL-01에서 같은 원인으로 160건 재발. DRL-03 조치 효과성은 EFFECTIVE, 예방조치는 NOT_EFFECTIVE, 재발 판정은 RECURRENCE로 남음.
- **P13 (발생 + 유출)**: OLE-02 노즐 막힘으로 잔동 쇼트가 발생. ET-02 픽스처 핀이 마모돼 쇼트 검출률이 2.93%에 그침(ET-01 12.3%, FP-01 12.6%) → 클레임이 ET-02 검사 Lot에 몰림.
- **P18 (포장 흡습)**: 실링 바 온도 저하. 리플로 재현 시험에서 받은 그대로는 블리스터가 나오고 베이킹 후에는 0건 → 같은 블리스터 증상의 적층 미경화(P02)와 구분됨.
- **NTF 측정 조건**: 고객 TDR 값(픽스처 정보 없음)은 출처 미확인으로 근거에서 제외됨. 표준 조건 재측정 결과는 규격 내.

## v28 대비 바뀐 점

- 제품·공정·설비·증상 18종·원인 28종·시험 56종을 새로 정의하고 전체 데이터를 다시 생성. 접수 10,000건과 평가 분할 형식은 유지.
- v11~v28의 SSD 전용 보강 테이블(S04 모니터링, S39 재고 대사, 주간 공급계획, 환불·비용 등)은 옮기지 않음. 필요하면 PCB 기준으로 다시 설계해야 함.
- 승계한 규칙: 출처가 확인되지 않은 시험으로는 원인을 확정하지 않음(v28 S08), 명명 규칙(rma_claim, rma_item, claim_cause, cause_assessment 등).

## 검증 (manifest.json `checks`)

무결성·외래 키 0건, 비제조 원인 0건, 인도 전 발생 클레임 0건, 출하 범위 밖 반송 패널 0건, 영향 판정 이후 출하된 영향 Lot 0건, 출처 확인 시험 없이 확정한 원인 0건, L3 조사 중 8D 전체가 없는 건 0건.
