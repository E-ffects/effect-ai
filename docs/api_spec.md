# AI 서비스 입출력 명세 v0.1

- 작성: 2026-11-18 (AI/DATA 유희범)
- 대상: 백엔드(Spring Boot), 프론트(Vue), 인프라
- 데이터: PCB RMA v4 (기준 시점 2025-12-31)
- 상태: **초안.** 3장 API와 5장 저장 책임을 팀이 확인하면 v1.0으로 확정한다.

## 요약
- AI 서비스는 **FastAPI 하나**다. Spring만 호출하고, 화면(Vue)은 AI를 직접 부르지 않는다.
- API는 4개다: **분류**(바로 응답), **원인분석**(등록 → 상태 조회), **유사 사례**(바로 응답), **사례 반영**(승인 후 알림).
- AI는 DB를 **읽기만** 한다. 업무 상태, 승인, AI 결과 저장은 **Spring**이 한다.
- 에이전트 구성은 A안(자율형)과 B안(혼합형) 두 가지를 정리했다. **어느 쪽이든 이 API는 같다.** 그래서 백·프론트는 지금 바로 개발을 시작해도 된다.
- 오늘 중 같은 형식의 **목업 API**를 띄운다. 실제 AI는 나중에 목업 자리에 그대로 들어간다.

---

## 1. 에이전트 구성

### 1-1. 역할 (A·B 공통)
| 에이전트 | 하는 일 | 입력 | 출력 | 쓰는 데이터 |
|---|---|---|---|---|
| **CS** (앞단) | **접수 창구.** 원문 → 대·소분류, 근거 구절, 정보 부족 판단(재질의). 분류가 끝난 케이스를 오케스트레이터에 넘긴다. 분석이 끝나면 고객 회신 초안을 쓴다 | 원문, 검출 단계, P/N | 분류 결과, 재질의 문구, 회신 초안 | symptom_definition, 과거 접수 문장 |
| **오케스트레이터** | CS가 넘긴 케이스로 분석 시작. 케이스 상태 관리, 다음 에이전트 호출, **기준 시각(as_of) 고정**, 실패 시 대체 경로 | 분류된 케이스 | 최종 결과, 실행 기록 | — |
| **QA** | 심각도·특별특성·분석 수준(L1~L3) 판정, 승인 필요 여부와 사유 | 분류 결과, 고객 | 분석 수준, 승인 사유 | qa_triage 규칙, special_characteristic |
| **공정** | 반품 Lot이 지난 공정·장비, 조건 이탈(규격 이탈, 평소 대비 σ), 설비 이상 이벤트 | Lot | 공정 단계별 신호와 근거 | process_run, process_observation, equipment_event, material_usage |
| **검사** | 반품 Lot이 지난 검사 장비와 검출률, 자재 IQC, 유출 원인 후보 | Lot | 검사 단계별 신호, 유출 원인 후보 | test_run, test_measurement, material_lot, escape_master |
| **원인분석** | 과거 확정 사례 + 공정·검사 근거로 **원인 후보 순위**(원인·공정·장비)와 설명, 개선안 초안 | 위 결과 전부 | 원인 후보, 유출 원인 후보, 유사 사례, 초안 | cause_assessment(승인분), cause_master, corrective_action |

**사람 승인**은 에이전트가 아니라 **화면(Spring)** 이 담당한다. L3 건만 승인 대기로 간다.

### 1-2. B안: 혼합형 (기본안)
```
[접수] /classify                      [분석] /analyses
고객 통보 ─▶ CS ─(분류된 케이스)─▶ 오케스트레이터 ─▶ QA ─┬▶ 공정 ─┐
             │                                            └▶ 검사 ─┴▶ 원인분석 ─┐
             │                                                                   │
             └◀──────────────── 회신 초안 요청 (원인 후보 전달) ◀────────────────┘
```
- **CS는 오케스트레이터 앞단**이다. 접수 때 `/classify`로 먼저 실행돼 분류를 끝내고, 분류된 케이스를 오케스트레이터에 넘긴다. 정보 부족이면 재질의로 끝나고, 고객 회신 후 다시 분류한다.
- 오케스트레이터는 `/analyses`로 시작하며 **CS의 분류 결과를 입력으로 받는다**(다시 분류하지 않음).
- 원인분석이 끝나면 오케스트레이터가 CS에 **회신 초안**을 요청한다. 구조도의 "CS: 접수·분류·회신"과 같다.
- 흐름은 **고정**이다. 공정과 검사는 **동시에** 실행한다.
- 각 에이전트는 정해진 도구(SQL 함수)를 실행하고, **LLM은 해석·설명·초안에만** 쓴다.

| 에이전트 | LLM 사용 |
|---|---|
| CS | 원문 해석, 정보 부족 판단, 회신 초안 |
| QA | 판정 사유 문장 |
| 공정 · 검사 | 도구 결과를 근거 문장으로 정리 |
| 원인분석 | 후보 순위의 설명, 개선안 초안 (순위 자체는 점수식) |
| 오케스트레이터 | 사용 안 함 |

- 같은 입력이면 항상 같은 순위가 나온다. 문장 표현만 조금 다를 수 있다.
- LLM이 장애일 때는 순위와 근거는 그대로 나오고, 설명·초안만 빠진다(`PARTIAL`).

### 1-3. A안: 원인분석 자율형 (확장안)
- 흐름은 B안과 같다. **원인분석 에이전트만** LLM이 도구를 스스로 골라 여러 번 호출한다.
  - 도구 예: "이 Lot의 OL_ETCH 장비 조건", "ET-02 검출률", "과거 같은 원인 사례"
- 최대 호출 횟수(예: 8회)와 시간 제한(60초)을 둔다. 넘으면 B안 결과로 대체한다.
- 순위가 LLM 판단을 거치므로 실행마다 조금씩 다를 수 있다.

### 1-4. 실행 기록
모든 에이전트·도구 호출은 `trace`로 응답에 포함한다. LangSmith 추적 ID도 함께 준다. 화면에서 **"AI가 어떤 순서로 무엇을 봤는지"** 를 그대로 보여 줄 수 있다.

---

## 2. 흐름과 상태값

### 2-1. 케이스 상태 (Spring이 관리)
```
RECEIVED ─▶ CLASSIFIED ─▶ ANALYZING ─▶ CANDIDATES_READY ─┬▶ QA_REVIEW (L1·L2) ─▶ CONFIRMED ─▶ CASE_STORED
                                                          └▶ WAITING_APPROVAL (L3) ─▶ APPROVED / REJECTED
```
| 상태 | 의미 | 바뀌는 시점 |
|---|---|---|
| RECEIVED | 접수 | 접수 등록 |
| CLASSIFIED | 분류 완료 | `/classify` 응답 |
| ANALYZING | AI 분석 중 | `/analyses` 등록 |
| CANDIDATES_READY | 원인 후보 준비 | 분석 결과 SUCCEEDED / PARTIAL |
| QA_REVIEW | QA 확인 (L1·L2) | 분석 수준 L1·L2 |
| WAITING_APPROVAL | 사람 승인 대기 (L3) | 분석 수준 L3 |
| APPROVED / REJECTED | 승인 / 반려 | 승인 화면 |
| CONFIRMED | QA 확정 (L1·L2) | QA 확인 화면 |
| CASE_STORED | 사례 저장 완료 | `/cases/refresh` 호출 후 |

### 2-2. AI 분석 작업 상태 (AI가 응답)
| 상태 | 의미 |
|---|---|
| QUEUED | 대기 |
| RUNNING | 실행 중 (`progress`에 현재 에이전트) |
| SUCCEEDED | 완료 |
| PARTIAL | 완료됐지만 일부 빠짐 (예: LLM 장애로 설명·초안 없음) |
| FAILED | 실패 (`error` 참고) |

### 2-3. 원인 후보 상태
원인 후보는 **제시 → 채택(확정 원인) 또는 기각**이다. AI 응답의 원인 후보는 항상 `PROPOSED`(제시)로 나간다. 채택·기각은 Spring이 승인·확인 화면에서 기록한다.

| 값 | 화면 표현 |
|---|---|
| PROPOSED | 제시 |
| ADOPTED | 채택 |
| REJECTED | 기각 |

---

## 3. API

- 기본 주소: `http://{ai-host}/ai/v1` (클러스터 내부, Spring만 호출)
- 공통 헤더: `X-Request-Id` (Spring이 생성, 로그 추적용), `Content-Type: application/json`
- 시각: ISO 8601, `+09:00`
- **기준 시각 `as_of`:** 생략하면 서버 설정값을 쓴다. 시연 기본값은 `2025-12-31T23:59:00+09:00`. AI는 이 시각 이전에 알 수 있었던 데이터만 쓴다.

| # | 메서드·경로 | 방식 | 목표 응답 시간 | 용도 |
|---|---|---|---|---|
| 1 | `POST /classify` | 바로 응답 | 3초 이내 | 접수 화면, CS 분류 |
| 2 | `POST /analyses` | 등록 (202) | 0.5초 이내 | 원인분석 시작 |
| 3 | `GET /analyses/{analysis_id}` | 조회 | 0.5초 이내 | 결과·진행 상태. B안 5~15초, A안 15~60초 후 완료 |
| 4 | `GET /claims/{rma_id}/similar` | 바로 응답 | 2초 이내 | RMA 상세, 챗봇 |
| 5 | `POST /cases/refresh` | 바로 응답 | 1초 이내 | 승인·확정 후 사례 저장소 갱신 알림 |
| 6 | `GET /health` | 바로 응답 | — | 상태 확인 (LLM 연결 포함) |

챗봇(`POST /chat`)은 2차 범위로, 4번 유사 사례를 근거로 쓰는 형태로 추가한다.

### 3-1. `POST /classify`
**요청**
```json
{
  "rma_id": "RMA-08743",
  "raw_text": "A01 수입검사 담당입니다. P/N PCB-ECU6-A1 (Lot LT-01603) SMT 리플로 공정에서 미세 패턴 사이 브리지 형태의 쇼트가 보입니다. 투입 1,750pcs 중 26pcs 불량입니다. 불량 샘플 4pcs 회송 예정입니다. 동일 Lot 재고 확인과 선별 여부를 회신 바랍니다.",
  "detection_stage": "SMT_REFLOW",
  "reported_part_no": "PCB-ECU6-A1",
  "customer_id": "A01",
  "as_of": "2025-11-20T14:40:46+09:00"
}
```
`rma_id`는 선택이다. 접수 등록 전 미리보기에서는 원문만 보내도 된다.

**응답 200: 분류됨**
```json
{
  "request_id": "req-8f2c",
  "status": "CLASSIFIED",
  "major_code": "ELEC",
  "major_name": "전기적 불량",
  "minor_code": "ELEC02",
  "minor_name": "쇼트",
  "confidence": 0.94,
  "evidence_spans": ["미세 패턴 사이 브리지 형태의 쇼트가 보입니다"],
  "severity": 8,
  "characteristic_symbol": null,
  "alternatives": [
    {"minor_code": "ELEC05", "minor_name": "절연저항 저하·누설", "confidence": 0.03}
  ],
  "follow_up_questions": [],
  "model_version": "cs-classifier-0.1",
  "elapsed_ms": 820
}
```

**응답 200: 정보 부족** (`status`가 `INSUFFICIENT_INFO`이면 화면에서 재질의 문구를 보여 준다)
```json
{
  "request_id": "req-91aa",
  "status": "INSUFFICIENT_INFO",
  "major_code": "ELEC",
  "major_name": "전기적 불량",
  "minor_code": null,
  "minor_name": null,
  "confidence": 0.41,
  "evidence_spans": ["기능 검사에서 NG가 납니다"],
  "severity": null,
  "characteristic_symbol": null,
  "alternatives": [
    {"minor_code": "ELEC01", "minor_name": "오픈(단선)", "confidence": 0.33},
    {"minor_code": "ELEC02", "minor_name": "쇼트", "confidence": 0.29}
  ],
  "follow_up_questions": [
    "증상 확인을 위해 문의드립니다. 불량 현상(오픈·쇼트·간헐·외관·실장 등), 발생 위치, 재현 조건(상온 상시 또는 온도·진동 후)을 알려 주십시오."
  ],
  "model_version": "cs-classifier-0.1",
  "elapsed_ms": 760
}
```
`status` 값: `CLASSIFIED`, `INSUFFICIENT_INFO`(정보 부족), `UNCLASSIFIED`(18종 밖)

### 3-2. `POST /analyses`
**요청**
```json
{
  "rma_id": "RMA-08743",
  "as_of": "2025-11-20T14:40:46+09:00",
  "options": {"top_k": 3, "include_drafts": true, "mode": "B"}
}
```
- `mode`: `"B"`(기본) 또는 `"A"`. 시연 중 비교용이다. 운영 화면에서는 생략한다.
- 같은 `rma_id`와 `as_of`로 이미 실행 중인 작업이 있으면 **그 작업 ID를 돌려준다**(중복 실행 방지).

**응답 202**
```json
{
  "analysis_id": "ana-20251120-08743-01",
  "rma_id": "RMA-08743",
  "status": "QUEUED",
  "created_at": "2025-11-20T14:40:47+09:00",
  "poll_after_ms": 1000
}
```

### 3-3. `GET /analyses/{analysis_id}`
**응답 200: 실행 중**
```json
{
  "analysis_id": "ana-20251120-08743-01",
  "rma_id": "RMA-08743",
  "status": "RUNNING",
  "progress": {"current_agent": "PROCESS", "steps_done": 2, "steps_total": 6},
  "result": null
}
```

**응답 200: 완료** (B안, 실제 v4 데이터 기준)
```json
{
  "analysis_id": "ana-20251120-08743-01",
  "rma_id": "RMA-08743",
  "as_of": "2025-11-20T14:40:46+09:00",
  "mode": "B",
  "status": "SUCCEEDED",
  "result": {
    "classification": {"major_code": "ELEC", "minor_code": "ELEC02", "minor_name": "쇼트", "evidence_spans": ["미세 패턴 사이 브리지 형태의 쇼트가 보입니다"]},
    "triage": {
      "severity": 8,
      "characteristic_symbol": null,
      "analysis_level": "L3",
      "needs_approval": true,
      "approval_reasons": ["차량 고객 + 심각도 8 이상", "동일 고객·불량 형태 30일 내 3건 이상 반복"]
    },
    "trace_summary": {"lot_id": "LT-01603", "spec_id": "AUTO-ECU6", "part_no": "PCB-ECU6-A1"},
    "cause_candidates": [
      {
        "rank": 1,
        "status": "PROPOSED",
        "source": "AI",
        "cause_code": "OL_ETCH_RESIDUE",
        "cause_name": "외층 에칭 잔동",
        "cause_category": "PROCESS",
        "process_id": "OL_ETCH",
        "process_name": "외층 에칭",
        "equipment_id": "OLE-02",
        "score": 0.86,
        "explanation": "같은 제품(PCB-ECU6-A1)의 쇼트 반품이 최근 OLE-02 외층 에칭 잔동으로 확정된 사례가 있고, 이 Lot도 2025-10-21 OLE-02에서 에칭됐으며 그때 스프레이 압력이 평소보다 3.3σ 낮았습니다. 10-31 노즐 7개 막힘 기록이 있습니다.",
        "evidence": [
          {"type": "SIMILAR_CASE", "summary": "RMA-08576 (2025-11-13, 같은 P/N, 쇼트·잔동 의심) → 외층 에칭 잔동, OLE-02, 확정", "ref_table": "rma_claim", "ref_id": "RMA-08576", "occurred_at": "2025-11-13T00:00:00+09:00"},
          {"type": "PROCESS_SIGNAL", "summary": "OL_ETCH · OLE-02 스프레이 압력 -3.3σ (직전 90일 대비)", "ref_table": "process_run", "ref_id": "LT-01603/OL_ETCH", "occurred_at": "2025-10-21T02:20:29+09:00"},
          {"type": "EQUIPMENT_EVENT", "summary": "OLE-02 노즐 점검: 스프레이 노즐 7개 막힘 확인·교체", "ref_table": "equipment_event", "ref_id": "NOZZLE_CHECK@OLE-02", "occurred_at": "2025-10-31T19:58:59+09:00"}
        ]
      },
      {
        "rank": 2,
        "status": "PROPOSED",
        "source": "AI",
        "cause_code": "ET_RETEST_MISJUDGE",
        "cause_name": "E-Test 재검 판정 오처리",
        "cause_category": "INSPECTION",
        "process_id": "ET",
        "process_name": "전기검사(E-Test)",
        "equipment_id": null,
        "score": 0.41,
        "explanation": "같은 소분류(쇼트)의 과거 확정 사례 중 가장 많은 원인입니다. 다만 이 Lot에서 재검 통과 기록은 확인되지 않았습니다.",
        "evidence": [
          {"type": "SIMILAR_CASE", "summary": "과거 쇼트 반품 217건 중 99건이 E-Test 재검 판정 오처리 (단발)", "ref_table": "cause_assessment", "ref_id": "ET_RETEST_MISJUDGE", "occurred_at": null}
        ]
      },
      {
        "rank": 3,
        "status": "PROPOSED",
        "source": "AI",
        "cause_code": "FOREIGN_MATERIAL",
        "cause_name": "공정 이물 혼입",
        "cause_category": "PROCESS",
        "process_id": "SM",
        "process_name": "PSR(솔더레지스트) 인쇄·경화",
        "equipment_id": null,
        "score": 0.22,
        "explanation": "과거 쇼트 반품 중 공정 이물로 확정된 사례가 있으나, 이 Lot의 PSR 공정 조건은 정상입니다.",
        "evidence": [
          {"type": "SIMILAR_CASE", "summary": "과거 쇼트 반품 217건 중 58건이 공정 이물 혼입", "ref_table": "cause_assessment", "ref_id": "FOREIGN_MATERIAL", "occurred_at": null}
        ]
      }
    ],
    "escape_candidates": [
      {
        "escape_code": "ESC_ET_FIXTURE",
        "escape_name": "E-Test 픽스처 접촉 불량",
        "detection_process_id": "ET",
        "equipment_id": "ET-02",
        "summary": "이 Lot은 ET-02에서 검사됐고, ET-02의 쇼트 검출률이 최근 1.84%로 다른 장비(ET-01 4.16%, FP-01 4.56%)보다 낮습니다. 10-31 픽스처 핀 38개 마모 기록이 있습니다.",
        "evidence": [
          {"type": "INSPECTION", "summary": "ET_SHORT 검출률 ET-02 1.84% (162/8,823), ET-01 4.16%, FP-01 4.56% (2025-10-13 ~ 분석 시각)", "ref_table": "test_run", "ref_id": "ET_SHORT@ET-02", "occurred_at": "2025-10-23T13:48:56+09:00"},
          {"type": "EQUIPMENT_EVENT", "summary": "ET-02 픽스처 점검: 핀 38개 마모 확인", "ref_table": "equipment_event", "ref_id": "FIXTURE_CHECK@ET-02", "occurred_at": "2025-10-31T15:47:38+09:00"}
        ]
      }
    ],
    "similar_cases": [
      {"rma_id": "RMA-08576", "opened_at": "2025-11-13T00:00:00+09:00", "minor_code": "ELEC02", "cause_code": "OL_ETCH_RESIDUE", "process_id": "OL_ETCH", "equipment_id": "OLE-02", "certainty": "CONFIRMED", "similarity": 0.81, "snippet": "ICT·기능검사에서 쇼트 판정이 나오며 육안상 잔동이 의심됩니다", "corrective_actions": []},
      {"rma_id": "RMA-08427", "opened_at": "2025-11-08T00:00:00+09:00", "minor_code": "ELEC02", "cause_code": "OL_ETCH_RESIDUE", "process_id": "OL_ETCH", "equipment_id": "OLE-02", "certainty": "CONFIRMED", "similarity": 0.78, "snippet": "ICT·기능검사에서 인접 패턴 간 쇼트가 발생했습니다", "corrective_actions": []},
      {"rma_id": "RMA-06930", "opened_at": "2025-09-27T00:00:00+09:00", "minor_code": "ELEC02", "cause_code": "ET_RETEST_MISJUDGE", "process_id": "ET", "equipment_id": null, "certainty": "PROBABLE", "similarity": 0.52, "snippet": "고객 수입검사(IQC)에서 전원과 GND 사이 저항이 …", "corrective_actions": []}
    ],
    "drafts": {
      "customer_reply": "A01 품질 담당자님, PCB-ECU6-A1 (Lot LT-01603) 쇼트 불량 통보 확인했습니다. 현재 외층 에칭 공정의 잔동을 유력 원인으로 보고 회수 시료 4pcs 분석을 진행합니다. 동일 Lot 재고는 출하 보류 후 E-Test 100% 재검 선별을 진행하며, 결과를 회신드리겠습니다.",
      "improvement": {
        "process": [{"cause_code": "OL_ETCH_RESIDUE", "action": "스프레이 노즐 막힘 점검 주기 단축(주→일) 및 압력 모니터링", "owner_dept": "공정기술팀"}],
        "inspection": [{"escape_code": "ESC_ET_FIXTURE", "action": "픽스처 핀 수명 관리 및 일일 골든 보드 검증"}]
      }
    }
  },
  "trace": [
    {"step": 1, "agent": "ORCHESTRATOR", "action": "as_of 고정, CS 분류 결과(ELEC02) 수신", "llm_used": false, "elapsed_ms": 40},
    {"step": 2, "agent": "QA", "action": "분석 수준 판정 L3", "llm_used": true, "elapsed_ms": 600},
    {"step": 3, "agent": "PROCESS", "action": "Lot 공정 경로·조건 이탈·설비 이벤트 조회", "tools": ["lot_process_history", "process_signals", "equipment_events"], "llm_used": true, "elapsed_ms": 1200},
    {"step": 4, "agent": "INSPECTION", "action": "검사 장비·검출률·자재 조회", "tools": ["lot_tests", "detection_rate", "material_usage"], "llm_used": true, "elapsed_ms": 1100},
    {"step": 5, "agent": "CAUSE_ANALYSIS", "action": "유사 사례 검색, 후보 순위·설명·개선안 초안", "tools": ["past_cases", "similar_cases"], "llm_used": true, "elapsed_ms": 4200},
    {"step": 6, "agent": "CS", "action": "고객 회신 초안 작성", "llm_used": true, "elapsed_ms": 900}
  ],
  "warnings": [],
  "langsmith_run_id": "run-3f6e1c",
  "model_versions": {"llm": "설정값", "prompt": "cause-analysis-0.1", "rules": "pcb-v4"},
  "elapsed_ms": 8040
}
```

**필드 메모**
- `cause_candidates[].score`: 0~1. 같은 응답 안의 순위 비교용이며, 확률이 아니다.
- `evidence[].type`: `SIMILAR_CASE`, `PROCESS_SIGNAL`, `EQUIPMENT_EVENT`, `INSPECTION`, `MATERIAL`
- `evidence[].ref_table` / `ref_id`: 화면에서 근거를 눌렀을 때 이동할 원천 레코드
- 장비는 **이 Lot이 실제로 지난 장비**를 준다. 과거 사례의 장비와 다를 수 있다. 예: P01R은 과거 사례가 DRL-03이지만 답은 DRL-01이다.
- 단발 불량처럼 장비를 특정할 수 없으면 `equipment_id`는 `null`이다. 화면에는 "특정 불가(단발)"로 표시한다.
- 자재 원인이면 `process_id`는 `"MATERIAL"`이고, `evidence`에 문제 자재 Lot이 들어간다.
- 같은 원인이 한 번도 확정된 적이 없어서 근거가 될 과거 사례가 없으면, 후보는 비어 있고 `warnings`에 `NO_CONFIRMED_CASE`가 들어간다.

### 3-4. `GET /claims/{rma_id}/similar?as_of=...&limit=5`
**응답 200**
```json
{
  "rma_id": "RMA-08743",
  "as_of": "2025-11-20T14:40:46+09:00",
  "items": [
    {"rma_id": "RMA-08576", "opened_at": "2025-11-13T00:00:00+09:00", "minor_code": "ELEC02", "cause_code": "OL_ETCH_RESIDUE", "process_id": "OL_ETCH", "equipment_id": "OLE-02", "certainty": "CONFIRMED", "similarity": 0.81, "snippet": "ICT·기능검사에서 쇼트 판정이 나오며 육안상 잔동이 의심됩니다", "corrective_actions": []}
  ]
}
```
`as_of` 이전에 **승인된** 사례만 나온다. 아직 원인이 정해지지 않은 접수는 나오지 않는다.

### 3-5. `POST /cases/refresh`
Spring이 승인·확정 결과를 DB에 저장한 **뒤에** 호출한다. AI는 DB에서 새 사례를 읽어 검색 색인을 갱신한다.

**요청**
```json
{"rma_id": "RMA-08743", "analysis_id": "ana-20251120-08743-01", "decision": "APPROVED", "decided_at": "2025-11-21T10:12:00+09:00"}
```
**응답 200**
```json
{"rma_id": "RMA-08743", "indexed": true, "case_count": 9924}
```

### 3-6. `GET /health`
```json
{"status": "ok", "db": "ok", "llm": "ok", "version": "0.1.0", "rules": "pcb-v4"}
```
`llm`이 `"down"`이면 분석 결과가 `PARTIAL`로 나온다.

---

## 4. 오류
**오류 응답 형식**
```json
{"error": {"code": "CLAIM_NOT_FOUND", "message": "RMA-99999 접수를 찾을 수 없습니다", "request_id": "req-8f2c"}}
```
| HTTP | code | 상황 | Spring 처리 |
|---|---|---|---|
| 400 | INVALID_INPUT | 필수 값 누락, 형식 오류 (원문 비어 있음 등) | 입력 확인 메시지 |
| 404 | CLAIM_NOT_FOUND | 없는 접수, 또는 `as_of` 이전에 접수되지 않은 건 | 접수 확인 |
| 404 | ANALYSIS_NOT_FOUND | 없는 분석 ID | 다시 등록 |
| 409 | ANALYSIS_IN_PROGRESS | 같은 건 실행 중 (응답에 기존 `analysis_id` 포함) | 기존 ID로 조회 |
| 422 | AS_OF_INVALID | `as_of` 형식 오류, 데이터 범위 밖 | 기준 시각 확인 |
| 503 | SERVICE_UNAVAILABLE | DB 연결 불가 | 재시도 (최대 3회, 1초 간격) |
| 504 | ANALYSIS_TIMEOUT | 분석 시간 초과 (B 30초, A 90초) | 결과는 `FAILED`로 남음, 다시 등록 |

**LLM 장애는 오류로 보내지 않는다.** 규칙과 SQL로 낼 수 있는 결과(분류, 분석 수준, 후보 순위, 근거)는 그대로 주고, `status: PARTIAL`, `warnings: ["LLM_UNAVAILABLE"]`로 표시한다. 설명과 초안만 빠진다.

---

## 5. 저장 책임
| 데이터 | 저장 주체 | 비고 |
|---|---|---|
| 접수, 케이스 상태 | **Spring** | 2-1 상태값 |
| AI 분석 결과 (응답 JSON 전체) | **Spring** | `ai_analysis` 테이블 제안: analysis_id, rma_id, as_of, mode, status, result_json, created_at, completed_at |
| 원인 후보의 채택·기각, 승인자, 의견 | **Spring** | 승인·확인 화면 |
| 확정 원인 (사례 저장소의 원천) | **Spring** | 채택된 후보를 `cause_assessment` / `claim_cause`와 같은 형식으로 저장 → `/cases/refresh` 호출 |
| 분석 작업의 진행 상태 | **AI** (메모리 또는 Redis) | `analysis_id`로 조회. 완료 결과는 Spring이 받아서 저장 |
| 검색 색인 (임베딩) | **AI** | DB에서 다시 만들 수 있음 |
| PCB v4 원천 데이터 | 적재 후 **읽기 전용** | 테이블 45개 |

AI 서비스는 DB에 **쓰지 않는다.** DB 계정은 읽기 전용으로 준다.

---

## 6. A안·B안 비교 (백·프론트 영향)
| 항목 | B 혼합형 (기본) | A 원인분석 자율형 |
|---|---|---|
| API | 같음 | 같음 |
| 원인분석 완료 시간 | 5~15초 | 15~60초 |
| 같은 입력 → 같은 순위 | 예 | 실행마다 다를 수 있음 |
| `trace` 단계 수 | 항상 6단계 (분석 시작 → QA → 공정 → 검사 → 원인분석 → CS 회신) | 원인분석 안에서 도구 호출이 4~8회 추가 |
| 시간 초과 | 30초 | 90초 |
| LLM 호출 비용 (1건) | 약 5회 | 약 10~15회 |
| 화면 처리 | 진행 상태 표시 | 진행 상태 + 오래 걸릴 때 안내 문구 |
| 평가 | 시간 순서 재생으로 점수 산출 | 같음 (여러 번 실행 평균) |

**권장:** 화면은 **폴링(1초 간격) + 진행 상태 표시 + 90초 시간 초과 안내**로 만든다. 그러면 A·B 어느 쪽으로 가도 화면을 고칠 필요가 없다.

---

## 7. 화면별 사용
| 화면 | 호출 | 쓰는 필드 |
|---|---|---|
| 접수 등록 | `/classify` (CS, 오케스트레이터 앞단) | 분류, 근거 구절, 정보 부족 시 재질의 문구 |
| RMA 상세 | `/analyses` → 조회, `/claims/{id}/similar` | 원인 후보 표(순위·원인·공정·장비·점수·상태), 근거 목록, 유사 사례, 실행 기록 |
| 승인 대기 (L3) | Spring 목록 | `triage.approval_reasons`, 1순위 후보, 근거, 회신·개선안 초안 |
| 대시보드 | Spring 집계 | "원인 공정·장비 TOP"은 채택된 원인 기준, 손실 금액은 `loss_ledger` 기준 (AI 호출 없음) |
| 챗봇 (2차) | `/chat` | 답변, 근거 사례 |

**용어** (화면·발표): 원인 후보(제시·채택·기각), 확정 원인, 추정 원인, 검토 근거

---

## 8. 일정
| 날짜 | AI | 백·프론트가 할 수 있는 것 |
|---|---|---|
| 11/18 | 명세 v0.1 공유, **목업 API**(고정 JSON) 배포 | 목업으로 연동·화면 개발 시작 |
| 11/19~20 | 명세 확정 v1.0, 분류 API 실제 연결 | 접수 화면 실제 분류 |
| 11/23~26 | 원인분석 B안 실제 연결 | RMA 상세·승인 화면 실제 데이터 |
| 11/27 | 인계 (구태우 님 FastAPI 통합) | 통합 |
| 12/1~3 | A안 시도(여유 시), 평가, 고정 | 통합 테스트 준비 |

## 확인 요청
- **윤서정 님:** 5장 저장 책임과 `ai_analysis` 테이블, 2-1 상태값
- **구태우 님:** 3-3 응답 필드가 RMA 상세·승인 화면에 충분한지, 폴링 방식
- **이상윤 님:** FastAPI 서비스 한 개, 환경 변수(DB 읽기 전용 계정, LLM 키, `AS_OF_DEFAULT`), LangSmith 사용 여부
