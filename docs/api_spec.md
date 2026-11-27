# AI 서비스 입출력 명세 v0.3

- 작성: 2026-11-18 (AI/DATA 유희범)
- 대상: 백엔드(Spring Boot), 프론트(Vue), 인프라
- 데이터: PCB RMA v5 (기준 시점 2025-12-31. 2026-11-19부터 팀 공식, 이전은 v4)
- 상태: **초안.** 3장 API와 5장 저장 책임을 팀이 확인하면 v1.0으로 확정한다.

## 요약
- AI 서비스는 **FastAPI 하나**다. **화면은 Spring만 부르고, Spring이 AI 서버를 부른다**(3-0, Spring_API_v4 2026-11-20 확정). 화면이 AI 서버를 직접 부르지 않는다.
- API는 4개다: **분류**(바로 응답), **원인분석**(등록 → 상태 조회), **유사 사례**(바로 응답), **사례 반영**(승인 후 알림).
- AI는 DB를 **읽기만** 한다. 업무 상태, 승인, AI 결과 저장은 **Spring**이 한다.
- 에이전트는 **5개**(CS, 오케스트레이터, QA, 제조, 원인분석)이고, 팀 구조도를 그대로 따른다. CS가 앞단에서 접수·분류하고, QA와 제조가 분석 전후로 두 번씩 일하며, 원인분석 결과는 QA가 검토해 L3(Major) 건의 사람 승인을 요청한다.
- 에이전트 구성은 A안(자율형)과 B안(혼합형) 두 가지를 정리했다. **어느 쪽이든 이 API는 같다.** 그래서 백·프론트는 지금 바로 개발을 시작해도 된다.
- **역할 경계:** 에이전트 로직(LangGraph 그래프, 도구, 프롬프트, LangSmith 추적 코드, 평가)은 **AI(유희범)** 가 `rma_agent` 패키지로 제공한다. FastAPI 서비스(주소, 작업 등록·조회, 오류 응답, Dockerfile)와 **목업**은 **구태우**, LangSmith 계정·키·관측 설정은 **이상윤**, Spring 연동·저장은 **윤서정**이 맡는다.
- 목업은 Spring의 `MockAiClient`가 `samples/*.json`(실제 에이전트 응답)을 돌려주는 방식이다(Spring_API_v4 5-4). 실제 AI는 FastAPI가 `rma_agent` 함수를 불러 같은 형식으로 돌려준다.

---

## 1. 에이전트 구성

### 1-1. 역할 (A·B 공통)
팀 구조도(CS → 오케스트레이터 → QA·제조 → 원인분석 → 사람 승인 → 사례 저장·공정 개선·검사 기준 개선)를 그대로 따른다.

| 에이전트 | 하는 일 | 입력 | 출력 | 쓰는 데이터 |
|---|---|---|---|---|
| **CS** (앞단) | **접수·분류·회신.** 원문 → 대·소분류, 근거 구절, 정보 부족 판단(재질의). 분류가 끝난 케이스를 오케스트레이터에 넘긴다. 마지막에 QA가 검토한 결과로 **고객 회신 초안**을 쓴다 | 원문, 검출 단계, P/N | 분류 결과, 재질의 문구, 회신 초안 | symptom_definition, 과거 접수 문장 |
| **오케스트레이터** | **케이스 상태 관리·라우팅.** CS가 넘긴 케이스로 분석을 시작하고, 에이전트 호출 순서를 정하며, **기준 시각(as_of)을 고정**하고, 실패 시 대체 경로로 보낸다 | 분류된 케이스 | 최종 결과, 실행 기록 | — |
| **QA** | **판단·승인 요청.** ① 분석 전: 심각도·특별특성·고객으로 **분석 수준(L1~L3)** 판정 ② 원인분석 후: 후보마다 **근거가 충분한지 검토**(원인별 필요 근거 규칙)하고, L3(Major)면 **사람 승인을 요청** | 분류, 고객 / 원인 후보 | 분석 수준, 후보별 검토 결과, 승인 사유, 검토 의견 | qa_triage 규칙, special_characteristic, qa_causal_requirement |
| **제조** | **공정·검사 이력, 개선안.** ① 분석 전: **반품 Lot 하나를 따라가며** 공정·검사를 함께 조회(지난 공정·장비, 조건 이탈, 설비 이상 이벤트, 검사 장비별 검출률, 자재 Lot·IQC) ② 원인분석 후: 1순위 원인 후보에 맞는 **공정 개선안**(조건·FMEA·CP)과 **검사 기준 개선안**(IQC·AOI·E-Test) 초안 | Lot / 원인 후보 | Lot 경로와 단계별 신호·근거, 유출 원인 후보, 개선안 초안 | process_run, process_observation, equipment_event, test_run, test_measurement, material_usage, material_lot, cause_master, escape_master |
| **원인분석** | **이전 사례 패턴으로 원인 후보 제시.** 이전 사례 저장소(확정 원인·조치 결과)와 QA·제조 결과로 **원인 후보 순위**(원인·공정·장비)와 설명, 유사 사례 | 분류, 분석 수준, 제조 결과 | 원인 후보, 유출 원인 후보, 유사 사례 | cause_assessment(승인분), claim_cause, corrective_action |

- 에이전트는 **5개**다: CS, 오케스트레이터, QA, 제조, 원인분석.
- QA와 제조는 오케스트레이터와 **양방향**이다. 분석 전에 한 번, 원인분석 후에 한 번 더 호출된다.
- **사람 승인 게이트**는 에이전트가 아니라 **화면(Spring)** 이다. **L3(Major) 건만** 승인 대기로 가고, 승인 항목은 원인 확정과 4M 변경이다. L1·L2 건은 QA 확인 화면에서 확정한다.
- 승인·확정된 원인과 조치는 **이전 사례 저장소**에 들어가 다음 접수 건의 원인분석에 재사용된다(`/cases/refresh`).

### 1-2. B안: 혼합형 (기본안)
```
[접수] /classify            [분석] /analyses
고객 통보 ─▶ CS ─(분류된 케이스)─▶ 오케스트레이터
                                    │  ① QA: 분석 수준 판정 (L1~L3)
                                    │  ② 제조: Lot 추적 (공정·검사 이력)
                                    ▼
             이전 사례 저장소 ───▶ ③ 원인분석: 원인 후보 순위
                                    ▼
                                 ④ QA: 후보별 근거 검토, 승인 요청 (L3)
                                    ▼
                                 ⑤ 제조: 1순위 후보의 공정·검사 개선안
                                    ▼
                                 ⑥ CS: 고객 회신 초안
                                    ▼
                           사람 승인 게이트 (L3) / QA 확인 (L1·L2)
                                    ▼
                    사례 저장 · 공정 개선 · 검사 기준 개선 ──▶ 이전 사례 저장소 (다음 건 재사용)
```
- **CS는 오케스트레이터 앞단**이다. 접수 때 `/classify`로 먼저 실행돼 분류를 끝낸다. 정보 부족이면 재질의로 끝나고, 고객 회신 후 다시 분류한다.
- 오케스트레이터는 `/analyses`로 시작하며 **CS의 분류 결과를 입력으로 받는다**(다시 분류하지 않음).
- ①과 ②는 서로 의존하지 않으므로 **동시에** 실행한다.
- **원인분석 결과는 QA로 돌아간다(④).** 구조도의 "QA: 판단·승인 요청"이다.
- 개선안(⑤)은 원인 후보가 나와야 쓸 수 있으므로 원인분석 뒤에 제조 에이전트를 한 번 더 호출한다. 구조도의 "제조: 공정·검사 이력, **개선안**"이다.
- 흐름은 **고정**이다. 각 에이전트는 정해진 도구(SQL 함수)를 실행하고, **LLM은 해석·설명·초안에만** 쓴다.

| 에이전트 | LLM 사용 |
|---|---|
| CS | 원문 해석, 정보 부족 판단, 회신 초안 |
| QA | 검토 의견과 승인 사유 문장 (분석 수준과 근거 충분 여부는 규칙) |
| 제조 | 도구 결과를 근거 문장으로 정리, 개선안 초안 |
| 원인분석 | 후보 순위의 설명 (순위 자체는 점수식) |
| 오케스트레이터 | 사용 안 함 |

- 같은 입력이면 항상 같은 순위가 나온다. 문장 표현만 조금 다를 수 있다.
- LLM이 장애일 때는 순위와 근거는 그대로 나오고, 설명·초안만 빠진다(`PARTIAL`).

### 1-3. A안: 원인분석 자율형 (확장안)
- 흐름은 B안과 같다. **원인분석 에이전트만** LLM이 도구를 스스로 골라 여러 번 호출한다.
  - 도구 예: "이 Lot의 OL_ETCH 장비 조건", "ET-02 검출률", "과거 같은 원인 사례"
- **QA가 "근거 부족"으로 판정하면(④) 원인분석을 한 번 더 실행**한다(부족한 근거를 찾아보도록 요청). 두 번째에도 부족하면 그대로 표시하고 넘어간다.
- 최대 호출 횟수(예: 8회)와 시간 제한(60초)을 둔다. 넘으면 B안 결과로 대체한다.
- 순위가 LLM 판단을 거치므로 실행마다 조금씩 다를 수 있다.

### 1-4. 실행 기록
모든 에이전트·도구 호출은 `trace`로 응답에 포함한다. LangSmith 추적 ID도 함께 준다. 화면에서 **"AI가 어떤 순서로 무엇을 봤는지"** 를 그대로 보여 줄 수 있다.

### 1-5. AI 패키지와 FastAPI의 경계
AI 서버(FastAPI)는 아래 함수만 호출한다. 에이전트 내부(LangGraph 그래프, 도구, 프롬프트)는 AI 담당이 바꿔도 FastAPI 코드는 바뀌지 않는다.

| 함수 (`rma_agent`) | 대응 API | 반환 |
|---|---|---|
| `classify(raw_text, detection_stage=None, part_no=None, customer_id=None, as_of=None)` | `POST /classify` | 3-1 응답의 dict |
| `run_analysis(rma_id, as_of=None, mode="B", top_k=3, on_progress=None)` | `POST /analyses` 작업 안에서 실행 | 3-3 응답의 `result`, `trace`, `warnings` dict. 진행 상황은 `on_progress(agent, steps_done, steps_total)`로 알림 |
| `similar_cases(rma_id, as_of=None, limit=5)` | `GET /claims/{id}/similar` | 3-4 응답의 dict |
| `refresh_cases()` | `POST /cases/refresh` | 사례 수 |
| `triage(rma_id, as_of=None)` | `POST /triage` | 3-7 응답의 dict (새 접수의 QA 판정안) |
| `pattern_priority(as_of=None, weights=None, limit=None)` | `GET /priority/patterns` | 3-8 응답의 dict (패턴별 품질 조치 우선순위) |
| `response_priority(as_of=None, weights=None, limit=None)` | `GET /priority/responses` | 3-8 응답의 dict (반품별 고객 응대 우선순위) |
| `run_follow_up(rma_id, cause_code, as_of=None)` | `POST /followups` | 2차 회신 초안·처리방안 (승인된 원인 기준) |
| `chat(request)` | `POST /chat` | 3-9 응답의 dict (에이전트 패널 질문 답변) |

- `analysis_as_of(rma_id, as_of=None)`: `run_analysis`와 같은 검사(접수 존재, QA 판정 여부, `as_of` 형식)만 하고 쓸 `as_of`를 돌려준다. `POST /analyses` 등록 때 404/422를 바로 주는 데 쓴다.
- 참고 서버: effect-ai `server/app.py`가 이 장의 경로를 모두 구현해 두었다(서비스 토큰 검증, 분석 작업 관리 포함). 실행법은 `docs/integration_guide.md` 3장.
- 작업 등록·상태 저장·시간 초과 처리는 FastAPI가 한다. `run_analysis`는 동기 함수이며 끝나면 결과를 돌려준다.
- LangSmith 추적은 `rma_agent` 안에서 남긴다. 환경 변수(`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`)만 서비스에 넣으면 된다.

---

## 2. 흐름과 상태값

### 2-1. 접수·분석 상태 (Spring이 관리, 2026-11-19 팀 결정)
케이스 하나의 상태를 네 가지로 나눠서 본다. 한국어 처리 상태(`claim_status`)는 쓰지 않는다.

| 상태 | 값 | 출처·저장 |
|---|---|---|
| `cs_status` | `OPEN` / `RESPONDED` / `CLOSED` | `rma_claim.cs_status` (데이터 값 그대로) |
| `qa_status` | `RECEIVED` / `TRIAGED` / `ANALYZING` / `CAUSE_DECIDED` / `CLOSED`. `RECEIVED`는 새 접수의 QA 판정 전(새 값, 원본 데이터에는 없음) | `rma_claim.qa_status`. 이 컬럼은 비울 수 없어서(NOT NULL) 판정 전에도 값을 넣는다. 스키마 변경 없음 |
| `analysis_state` | `NOT_REQUESTED` / `QUEUED` / `RUNNING` / `SUCCEEDED` / `PARTIAL` / `FAILED` | AI 작업 상태(2-2). Spring `ai_analysis`에 저장 |
| `approval_status` | `NOT_REQUIRED` / `WAITING` / `APPROVED` / `REJECTED` / `CONFIRMED` | Spring이 새로 관리 |

- 새 접수 흐름: 접수(`rma_claim` 저장, `qa_status = RECEIVED`) → 분류(`POST /classify`) + **QA 판정안(`POST /triage`, 3-7)** → Spring이 판정안을 바로 `qa_triage`에 저장(`decided_by = "QA_AGENT"`), `qa_status = TRIAGED` → 원인분석(`POST /analyses`) 자동 시작. 임시 판정(`provisional: true`)만 품질관리자가 확인한 뒤 저장한다. 원인분석은 `qa_triage`가 있어야 시작할 수 있다(2026-11-20 팀 결정, Spring_API_v4 4-6).
- AI는 `qa_status`를 읽지 않는다. 판정 전후는 `qa_triage` 행이 있는지로 판단한다.
- `PARTIAL`: 원인 순위·근거는 정상이고 LLM 문장만 빠졌다(`warnings: ["LLM_UNAVAILABLE"]`).
- `approval_status`: L3는 `WAITING` → `APPROVED`/`REJECTED`, L1·L2는 QA 확인 시 `CONFIRMED`. 분석 전은 `NOT_REQUIRED`. 승인·확정 뒤 Spring이 `POST /cases/refresh`를 부른다.

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

- 기본 주소: `http://{ai-host}/ai/v1` (Spring 설정 `ai.base-url`, local `http://127.0.0.1:8080/ai/v1`). 호출하는 쪽은 Spring뿐이다(3-0).
- 공통 헤더: `Content-Type: application/json`. `X-Request-Id`는 있으면 로그에 쓴다(선택). **`Authorization: Bearer <서비스 토큰>`**: Spring이 같은 `jwt.secret`으로 발급한 토큰(`sub = "spring"`, `role = "SYSTEM"`, 유효 10분)을 AI 서버가 검증한다. 사용자 토큰은 넘어오지 않는다.
- AI 서버는 `SYSTEM`에 **전체 결과**를 준다. 고객대응관리자(CS) 화면용 필터(원인·설비·Lot·`trace` 제외)는 **Spring이** 적용한다(Spring_API_v4 4-5). `rma_agent` 함수의 입출력은 그대로다.
- 시각: ISO 8601, `+09:00`
- **기준 시각 `as_of`:** Spring은 보내지 않는다(Spring_API_v4). 생략하면 AI가 그 접수의 분석 시작 시각(QA 판정·접수 분류가 저장된 시각 중 늦은 쪽)으로 정해 응답의 `as_of`로 돌려주고, Spring은 그 값을 저장한다. AI는 이 시각 이전에 알 수 있었던 데이터만 쓴다.
- 시간 제한(Spring 쪽): 연결 2초, 응답은 `classify`·`triage` 10초, 그 밖 5초. 연결 실패·5xx·시간 초과는 Spring이 `AI_UNAVAILABLE`(503)로 바꾼다.

| # | 메서드·경로 | 방식 | 목표 응답 시간 | 용도 |
|---|---|---|---|---|
| 1 | `POST /classify` | 바로 응답 | 3초 이내 | 접수 화면, CS 분류 |
| 2 | `POST /analyses` | 등록 (202) | 0.5초 이내 | 원인분석 시작 |
| 3 | `GET /analyses/{analysis_id}` | 조회 | 0.5초 이내 | 결과·진행 상태. B안 5~15초, A안 15~60초 후 완료 |
| 4 | `GET /claims/{rma_id}/similar` | 바로 응답 | 2초 이내 | RMA 상세, 챗봇 |
| 5 | `POST /cases/refresh` | 바로 응답 | 1초 이내 | 승인·확정 후 사례 저장소 갱신 알림 |
| 6 | `GET /health` | 바로 응답 | — | 상태 확인 (LLM 연결 포함) |
| 7 | `POST /triage` | 바로 응답 | 0.5초 이내 | 새 접수의 QA 판정안(분석 수준). Spring이 바로 `qa_triage`에 저장(임시 판정만 품질관리자 확인 후) |

| 8 | `POST /chat` | 바로 응답 | 3초 이내 (LLM 12초 제한) | 에이전트 패널 질문 (3-9) |

### 3-0. 호출 경로 (Spring_API_v4, 2026-11-20 확정)
```
브라우저 ── /api/... ──▶ Spring (:8000) ── 서비스 토큰(SYSTEM) ── /ai/v1/... ──▶ AI 서버 (FastAPI :8080) → rma_agent
                              │                                                          │
                              └── pcb·app 스키마 읽기/쓰기                 pcb 스키마 읽기 전용 ──┘
```
| Spring이 부르는 때 | AI API | Spring이 하는 일 |
|---|---|---|
| 접수 저장 직후 | `POST /classify` (3-1) | 분류됨이면 `symptom_assessment` 저장, 정보 부족·분류 불가면 재질의(`NEEDS_INFO`) |
| 분류 저장 직후 | `POST /triage` (3-7) | `provisional: false`면 `qa_triage` 바로 저장, `true`면 품질관리자 확인 대기 |
| QA 판정 저장 직후 (자동), 재분석 버튼 | `POST /analyses` (3-2) | `ai_analysis(QUEUED)` 생성 |
| 등록 뒤 백그라운드로 `poll_after_ms` 간격 | `GET /analyses/{analysis_id}` (3-3) | 상태·진행 갱신, 끝나면 결과 저장 (B안 최대 40초, A안 100초) |
| 2단계 (유사 사례 화면) | `GET /claims/{rma_id}/similar` (3-4) | |
| 원인 판정 저장 뒤 | `POST /cases/refresh` (3-5) | 실패하면 1분 간격 최대 5회 재시도 |
| 상태 표시 | `GET /health` (3-6) | |

- 포트: **AI 서버 8080, Spring 8000** (2026-11-19 팀 결정).
- 화면은 AI 서버를 직접 부르지 않는다. 프론트에 `/api/ai` 프록시 규칙은 필요 없다.
- AI 서버 연결 전에는 Spring의 `MockAiClient`가 `samples/*.json`을 돌려준다(Spring_API_v4 5-4). `ai.mode=http`로 바꾸면 실제 호출이다.
- 분석 작업 상태는 FastAPI 메모리에 있다. 결과의 영구 저장(`app.ai_analysis`)은 Spring이 폴링해서 한다. AI 서버가 재시작되어 조회가 404면 Spring이 `ANALYSIS_LOST`로 처리한다.

### 3-1. `POST /classify`
**요청**
```json
{
  "rma_id": "RMA-08927",
  "raw_text": "A01 수입검사 담당입니다. P/N PCB-ECU6-A1 (Lot LT-01603) SMT 리플로 공정에서 미세 패턴 사이 브리지 형태의 쇼트가 보입니다. 투입 1,750pcs 중 26pcs 불량입니다. 불량 샘플 4pcs 회송 예정입니다. 동일 Lot 재고 확인과 선별 여부를 회신 바랍니다.",
  "detection_stage": "SMT_REFLOW",
  "reported_part_no": "PCB-ECU6-A1",
  "customer_id": "A01",
  "as_of": "2025-11-20T12:41:37+09:00"
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
- `request_id`는 FastAPI가 붙인다. `elapsed_ms`, `model_version`(LLM을 썼으면 `+llm`), `warnings`는 `rma_agent.classify()`가 준다.
- `warnings`: `LLM_UNAVAILABLE`(LLM 장애, 과거 접수 검색만으로 분류), `LLM_OUTPUT_REJECTED`(LLM이 없는 소분류 코드를 냄, 검색 결과 사용).
- `evidence_spans`는 원문에 글자 그대로 있는 구절만 준다.
- `severity`, `characteristic_symbol`은 소분류 기준값(`symptom_definition`)이다. 고객·반복을 반영한 최종 심각도와 분석 수준은 QA 접수 판정에서 정한다.

### 3-2. `POST /analyses`
**요청**
```json
{
  "rma_id": "RMA-08927",
  "as_of": "2025-11-20T12:41:37+09:00",
  "options": {"top_k": 3, "include_drafts": true, "mode": "B"}
}
```
- `mode`: `"B"`(기본) 또는 `"A"`. 시연 중 비교용이다. 운영 화면에서는 생략한다.
- 같은 `rma_id`와 `as_of`로 이미 실행 중인 작업이 있으면 **그 작업 ID를 돌려준다**(중복 실행 방지).

**응답 202**
```json
{
  "analysis_id": "ana-20251120-08743-01",
  "rma_id": "RMA-08927",
  "status": "QUEUED",
  "created_at": "2025-11-20T14:40:47+09:00",
  "poll_after_ms": 1000
}
```

### 3-3. `GET /analyses/{analysis_id}`
> 예시는 v5 데이터의 RMA-08927(차량 고객 A01, Lot LT-01603, 쇼트, 외층 에칭 잔동)이다. 점수·문장 같은 값은 설명용이고, 실제 응답 전체는 effect-ai `samples/RMA-08927.json`에 있다.

**응답 200: 실행 중**
```json
{
  "analysis_id": "ana-20251120-08743-01",
  "rma_id": "RMA-08927",
  "status": "RUNNING",
  "progress": {"current_agent": "MANUFACTURING", "steps_done": 2, "steps_total": 7},
  "result": null
}
```

**응답 200: 완료** (B안, v5 데이터 기준 예시)
```json
{
  "analysis_id": "ana-20251120-08743-01",
  "rma_id": "RMA-08927",
  "as_of": "2025-11-20T12:41:37+09:00",
  "mode": "B",
  "status": "SUCCEEDED",
  "result": {
    "classification": {"major_code": "ELEC", "minor_code": "ELEC02", "minor_name": "쇼트", "evidence_spans": ["미세 패턴 사이 브리지 형태의 쇼트가 보입니다"]},
    "qa_review": {
      "severity": 8,
      "characteristic_symbol": null,
      "analysis_level": "L3",
      "needs_approval": true,
      "approval_reasons": ["차량 고객 + 심각도 8 이상"],
      "candidate_checks": [
        {"rank": 1, "cause_code": "OL_ETCH_RESIDUE", "evidence_status": "PARTIAL",
         "satisfied": ["생산 기록 근거 (OLE-02 조건 이탈, 노즐 막힘 기록)", "같은 원인 확정 사례"],
         "missing": ["출처 확인된 반송품 시험 (시료 4pcs 회수 예정)"]},
        {"rank": 2, "cause_code": "ET_RETEST_MISJUDGE", "evidence_status": "INSUFFICIENT",
         "satisfied": ["같은 소분류 과거 사례"], "missing": ["이 Lot의 재검 통과 기록"]},
        {"rank": 3, "cause_code": "FOREIGN_MATERIAL", "evidence_status": "INSUFFICIENT",
         "satisfied": ["같은 소분류 과거 사례"], "missing": ["PSR 공정 이상 기록"]}
      ],
      "review_note": "1순위 후보는 생산 기록과 확정 사례 근거가 있으나, 반송품 시험 결과가 아직 없어 확정 전 시료 분석이 필요합니다. 차량 고객 L3 건으로 승인을 요청합니다."
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
    "candidate_lots": {"window_hours": 24, "total": 62, "items": [{"lot_id": "LT-01602", "spec_id": "SEMI-SSD8H", "started_at": "2025-10-16T10:15:45+09:00", "completed_at": "2025-10-24T07:52:46+09:00", "shared": [{"type": "EQUIPMENT", "process_id": "OL_ETCH", "process_name": "외층 에칭", "equipment_id": "OLE-02", "this_lot_at": "2025-10-21T02:20:29+09:00", "other_lot_at": "2025-10-20T07:19:59+09:00", "gap_hours": 19.0}], "shared_count": 16, "matches_top_cause": true, "shipped": true, "first_shipped_at": "2025-10-25T08:45:59+09:00", "shipped_boards": 4278, "known_returns": 0}]},
    "recurrence_check": {"status": "NOT_APPLICABLE", "corrective_action": null, "checks": null, "reason": "1순위 원인 외층 에칭 잔동에 대해 분석 시점까지 시행된 발생 원인 조치가 없습니다.", "suggestion": "신규"},
    "similar_cases": [
      {"rma_id": "RMA-08486", "opened_at": "2025-11-05T10:32:14+09:00", "claim_date": "2025-11-05", "minor_code": "ELEC02", "minor_name": "쇼트", "cause_code": "OL_ETCH_RESIDUE", "cause_name": "외층 에칭 잔동", "process_id": "OL_ETCH", "equipment_id": "OLE-02", "certainty": "CONFIRMED", "similarity": 0.68, "basis": "같은 증상+같은 제품+같은 Lot+같은 고객+문장 유사", "past_action": null, "recurrence": false, "days_to_resolve": 14, "snippet": "[불량 통보] A01 P/N PCB-ECU6-A1 (Lot LT-01603) ICT·기능검사에서 인접 패턴 …", "corrective_actions": []},
      {"rma_id": "RMA-00300", "opened_at": "2025-01-24T08:59:29+09:00", "claim_date": "2025-01-24", "minor_code": "ELEC02", "minor_name": "쇼트", "cause_code": "FOREIGN_MATERIAL", "cause_name": "공정 이물 혼입", "process_id": "SM", "equipment_id": null, "certainty": "PROBABLE", "similarity": 0.63, "basis": "같은 증상+같은 제품+문장 유사", "past_action": "인쇄실 청정도 관리 강화", "recurrence": false, "days_to_resolve": 72, "snippet": "긴급 품질 이슈 공유드립니다(A04). P/N PCB-ECU6-A1 (Lot LT-00170, D/C 245…", "corrective_actions": [{"category": "PROCESS_LOCAL", "description": "인쇄실 청정도 관리 강화", "implemented_at": "2025-04-14T08:41:16+09:00"}]},
      {"rma_id": "RMA-08504", "opened_at": "2025-11-06T08:40:49+09:00", "claim_date": "2025-11-06", "minor_code": "ELEC02", "minor_name": "쇼트", "cause_code": "OL_ETCH_RESIDUE", "cause_name": "외층 에칭 잔동", "process_id": "OL_ETCH", "equipment_id": "OLE-02", "certainty": "CONFIRMED", "similarity": 0.62, "basis": "같은 증상+같은 제품+같은 Lot+같은 고객+문장 유사", "past_action": null, "recurrence": false, "days_to_resolve": 13, "snippet": "A01 품질보증팀입니다. 불량 발생 건 통보드립니다. P/N PCB-ECU6-A1 (Lot LT-01603,…", "corrective_actions": []}
    ],
    "drafts": {
      "customer_reply": "A01 품질 담당자님, PCB-ECU6-A1 (Lot LT-01603) 쇼트 불량 통보 확인했습니다. 현재 외층 에칭 공정의 잔동을 유력 원인으로 보고 회수 시료 4pcs 분석을 진행합니다. 동일 Lot 재고는 출하 보류 후 E-Test 100% 재검 선별을 진행하며, 결과를 회신드리겠습니다.",
      "improvement": {
        "process": [{"cause_code": "OL_ETCH_RESIDUE", "action": "스프레이 노즐 막힘 점검 주기 단축(주→일) 및 압력 모니터링", "owner_dept": "공정기술팀"}],
        "inspection": [{"escape_code": "ESC_ET_FIXTURE", "action": "픽스처 핀 수명 관리 및 일일 골든 보드 검증"}]
      },
      "service_action": {
        "action_type": "SORTING_SUPPORT_AND_CREDIT", "action_name": "선별 지원·대금 공제", "board_qty": 109,
        "basis": "유사 사례 5건 중 3건이 선별 지원·대금 공제", "similar_case_count": 5,
        "expected_days": 6, "expected_days_basis": "과거 선별 지원·대금 공제 전체 처리 결정→실행 1658건의 중앙값",
        "alternatives": [{"action_type": "NO_CHARGE_SAMPLE_RETURN", "action_name": "무상 시료 회수", "count": 1},
                         {"action_type": "CREDIT_NOTE", "action_name": "대금 공제", "count": 1}]
      }
    }
  },
  "trace": [
    {"step": 1, "agent": "ORCHESTRATOR", "action": "as_of 고정, CS 분류 결과(ELEC02) 수신", "llm_used": false, "elapsed_ms": 40},
    {"step": 2, "agent": "QA", "action": "분석 수준 판정 L3 (차량 고객, 심각도 8)", "tools": ["triage_rules"], "llm_used": false, "elapsed_ms": 30},
    {"step": 3, "agent": "MANUFACTURING", "action": "Lot LT-01603 추적: 공정 경로·조건 이탈·설비 이벤트, 검사 장비·검출률, 자재", "tools": ["lot_process_history", "process_signals", "equipment_events", "lot_tests", "detection_rate", "material_usage"], "llm_used": true, "elapsed_ms": 1800},
    {"step": 4, "agent": "CAUSE_ANALYSIS", "action": "이전 사례 검색, 원인 후보 순위·설명", "tools": ["past_cases", "similar_cases"], "llm_used": true, "elapsed_ms": 3600},
    {"step": 5, "agent": "QA", "action": "후보별 근거 검토, 승인 요청 (L3)", "tools": ["causal_requirements"], "llm_used": true, "elapsed_ms": 700},
    {"step": 6, "agent": "MANUFACTURING", "action": "1순위 후보(OL_ETCH_RESIDUE)·유출(ESC_ET_FIXTURE)의 공정·검사 개선안 초안", "tools": ["standard_actions"], "llm_used": true, "elapsed_ms": 900},
    {"step": 7, "agent": "CS", "action": "QA 검토 결과로 고객 회신 초안 작성, 처리방안 제안 선별 지원·대금 공제", "tools": ["reply_template", "service_actions"], "llm_used": true, "elapsed_ms": 900}
  ],
  "warnings": [],
  "langsmith_run_id": "run-3f6e1c",
  "model_versions": {"llm": "설정값", "prompt": "agents-0.4", "rules": "pcb-v5", "cause_ranker": null},
  "elapsed_ms": 8040
}
```

**필드 메모**
- `drafts.improvement`: 제조 에이전트가 1순위 원인 후보(공정 개선)와 유출 원인 후보(검사 기준 개선)에 맞춰 쓴 초안.
- `drafts.service_action`: 고객 처리방안 제안(Spring_API_v4 4-7). CS 에이전트가 **화면의 유사 사례(`similar_cases`)가 실제로 어떻게 처리됐는지**(`service_action`, `as_of`까지 결정된 것)를 보고 가장 많은 처리를 제안한다. 같으면 더 최근 것.
  - `action_type`: `REPLACEMENT_SHIPMENT`(대체품 출하) / `CREDIT_NOTE`(대금 공제) / `SORTING_SUPPORT_AND_CREDIT`(선별 지원·대금 공제) / `NO_CHARGE_SAMPLE_RETURN`(무상 시료 회수). `action_name`은 화면 표시용 이름
  - `board_qty`: 이 반품의 신고 불량 수량(`claimed_defect_qty`). 무상 시료 회수면 0
  - `similar_case_count`: 처리 기록이 있는 유사 사례 수(최대 5). `basis`는 그 근거 문장이며 원인·설비 이름을 넣지 않는다(CS 화면에 그대로 보여도 됨)
  - `expected_days`: 그 처리의 결정→실행 일수 중앙값. 유사 사례 중 실행된 것이 없으면 과거 같은 처리 전체로 계산한다(`expected_days_basis`에 어느 쪽인지 적음)
  - `alternatives`: 유사 사례에서 나온 다른 처리와 건수
  - 과거 처리 기록이 하나도 없으면 `null`이다
  - 점검: 2025-10월 이후 처리 기록이 있는 반품 3,319건에서 실제 처리와 68.0% 일치(같은 유력 원인의 과거 반품 기준은 67.2%, `scripts/eval_service_action.py`). 제안이며, 처리 결정은 CS가 한다
- `cause_candidates[].score`: 0~1. 같은 응답 안의 순위 비교용이며, 확률이 아니다.
- `model_versions.cause_ranker`: 원인 후보 학습 재정렬(WBS 4.12) 모델 이름, 꺼져 있으면 `null`(기본). 켜면 손으로 정한 점수의 상위 3개 안에서만 순서를 바꾸고, 그 3개의 `score`는 모델 확률이 된다. 필드와 모양은 같다(결과 `results/20261120_cause_ranker_agent_v5.md`).
- `qa_review`: QA 에이전트의 판정(분석 전: 분석 수준·승인 필요 여부)과 검토(원인분석 후: 후보별 근거) 결과. `candidate_checks[].evidence_status`는 `SUFFICIENT`(확정에 필요한 근거 충분), `PARTIAL`(일부 있음, 예: 반송품 시험 대기), `INSUFFICIENT`(근거 부족). 기준은 원인별 필요 근거 규칙(`qa_causal_requirement`)이다.
- `evidence[].type`: `SIMILAR_CASE`, `PROCESS_SIGNAL`, `EQUIPMENT_EVENT`, `INSPECTION`, `MATERIAL`, `SUPPLIER`
  - `SUPPLIER`: 이 Lot의 자재가 과거 확정 사례의 문제 자재와 같은 공급사·같은 종류의 다른 Lot이다. 참고 정보이며 점수와 QA 생산 근거에는 쓰지 않는다.
- `evidence[].ref_table` / `ref_id`: 화면에서 근거를 눌렀을 때 이동할 원천 레코드
- 장비는 **이 Lot이 실제로 지난 장비**를 준다. 과거 사례의 장비와 다를 수 있다. 예: P01R은 과거 사례가 DRL-03이지만 답은 DRL-01이다.
- 단발 불량처럼 장비를 특정할 수 없으면 `equipment_id`는 `null`이다. 화면에는 "특정 불가(단발)"로 표시한다.
- `drafts.improvement.*[].standard_action`: 기준 테이블의 표준 조치 원문. `action`은 제조 에이전트가 이 Lot 근거에 맞춰 구체화한 문장(LLM이 꺼져 있으면 표준 조치와 같다).
- `trace[].llm_used`: 그 단계의 문장을 LLM이 썼으면 `true`. `model_versions.llm`이 `null`이면 LLM 없이 템플릿 문장이다.
- `warnings`: `LLM_UNAVAILABLE`(LLM 호출 실패, 상태 `PARTIAL`, 템플릿 문장 사용), `LLM_OUTPUT_REJECTED`(LLM 문장에 근거에 없는 숫자가 있어 템플릿 사용), `NO_CONFIRMED_CASE`, `NO_LOT`, `MODE_A_NOT_READY`, `SYMPTOM_NOT_CONFIRMED`(접수 증상이 정보 부족·분류 불가. 원인 후보는 참고용이고, 고객 회신 초안은 원인 대신 재질의를 담는다).
- `cause_candidates[].other_equipment`: 이 Lot이 같은 공정을 여러 장비로 나눠 지났을 때 나머지 장비 목록(신호 강한 순). 보통 빈 배열이다.
- 자재 원인이면 `process_id`는 `"MATERIAL"`이고, `evidence`에 문제 자재 Lot이 들어간다.
- 같은 원인이 한 번도 확정된 적이 없어서 근거가 될 과거 사례가 없으면, 후보는 비어 있고 `warnings`에 `NO_CONFIRMED_CASE`가 들어간다.

#### 후보 Lot (`result.candidate_lots`)
반품 Lot과 **같은 생산 설비를 앞뒤 24시간 안에 썼거나, 같은 자재 Lot을 쓴** 다른 Lot 목록이다. QA가 확인할 목록이며 예측(적중률)이 아니다. 6단계 제조 에이전트가 원인 순위 뒤에 만든다.
- 검사 공정(E-Test, AOI, FQC, OQC 등)은 공통점에서 뺀다. 검사기를 같이 쓴 것은 생산 공통점이 아니다.
- 순서: 1순위 원인의 설비(자재 원인이면 자재 Lot)를 공유한 Lot → 공통점 수 → 가장 가까운 시간 차. 각 Lot의 `shared`도 1순위 원인의 설비·자재가 먼저 온다.
- 모든 값은 `as_of` 시점에 알 수 있는 것만 쓴다(출하, 반품 수 포함).

| 필드 | 뜻 |
|---|---|
| `window_hours` | "같은 시간대"의 기준(24) |
| `total` | 공통점이 있는 Lot 전체 수. `items`는 앞에서 최대 20개 |
| `items[].lot_id`, `spec_id`, `started_at`, `completed_at` | 다른 Lot과 생산 기간(`completed_at`은 `as_of` 전에 끝났을 때만) |
| `items[].shared[]` | 공통점. `type`이 `EQUIPMENT`면 `process_id`, `process_name`, `equipment_id`, `this_lot_at`(반품 Lot 투입), `other_lot_at`, `gap_hours`. `MATERIAL`이면 `material_lot_id`, `material_type`, `supplier_id` |
| `items[].shared_count` | 공통점 수 |
| `items[].matches_top_cause` | 1순위 원인의 설비(자재 원인이면 자재 Lot)를 공유했는가 |
| `items[].shipped`, `first_shipped_at`, `shipped_boards` | `as_of`까지 출하 여부, 첫 출하 시각, 출하 수량 |
| `items[].known_returns` | 그 Lot에서 `as_of`까지 들어온 반품 수(이 반품 제외) |

예시 (RMA-08927, 실제 출력에서 `items` 2개와 각 `shared` 2개만 보임):
```json
{
  "window_hours": 24,
  "total": 62,
  "items": [
    {
      "lot_id": "LT-01602",
      "spec_id": "SEMI-SSD8H",
      "started_at": "2025-10-16T10:15:45+09:00",
      "completed_at": "2025-10-24T07:52:46+09:00",
      "shared": [
        {
          "type": "EQUIPMENT",
          "process_id": "OL_ETCH",
          "process_name": "외층 에칭",
          "equipment_id": "OLE-02",
          "this_lot_at": "2025-10-21T02:20:29+09:00",
          "other_lot_at": "2025-10-20T07:19:59+09:00",
          "gap_hours": 19.0
        },
        {
          "type": "EQUIPMENT",
          "process_id": "IL_ETCH",
          "process_name": "내층 에칭·박리",
          "equipment_id": "ILE-01",
          "this_lot_at": "2025-10-17T21:05:37+09:00",
          "other_lot_at": "2025-10-17T05:45:14+09:00",
          "gap_hours": 15.3
        }
      ],
      "shared_count": 16,
      "matches_top_cause": true,
      "shipped": true,
      "first_shipped_at": "2025-10-25T08:45:59+09:00",
      "shipped_boards": 4278,
      "known_returns": 0
    },
    {
      "lot_id": "LT-01599",
      "spec_id": "AUTO-ECU6",
      "started_at": "2025-10-16T15:31:18+09:00",
      "completed_at": "2025-10-25T06:00:15+09:00",
      "shared": [
        {
          "type": "EQUIPMENT",
          "process_id": "OL_ETCH",
          "process_name": "외층 에칭",
          "equipment_id": "OLE-02",
          "this_lot_at": "2025-10-21T02:20:29+09:00",
          "other_lot_at": "2025-10-21T03:44:43+09:00",
          "gap_hours": 1.4
        },
        {
          "type": "EQUIPMENT",
          "process_id": "IL_IMG",
          "process_name": "내층 D/F 라미·노광·현상",
          "equipment_id": "ILI-01",
          "this_lot_at": "2025-10-17T12:31:00+09:00",
          "other_lot_at": "2025-10-16T20:46:12+09:00",
          "gap_hours": 15.7
        }
      ],
      "shared_count": 15,
      "matches_top_cause": true,
      "shipped": true,
      "first_shipped_at": "2025-10-27T09:42:44+09:00",
      "shipped_boards": 2257,
      "known_returns": 5
    }
  ]
}
```

#### 재발 판정 (`result.recurrence_check`)
원인 분석 다음 단계(5단계 QA)에서, 1순위 원인에 대해 **분석 시점까지 시행된 발생 원인 조치**(`corrective_action`, 설비 또는 자재 Lot이 정해진 것)와 이 Lot을 비교한다. 정답 데이터 `recurrence_review`와 같은 규칙이다.

| `status` | 조건 |
|---|---|
| `RECURRENCE` | 이 Lot의 생산 시작이 같은 원인의 조치 시행(`implemented_at`) 이후 |
| `PRE_ACTION_PRODUCT` | 이 Lot이 모든 조치보다 먼저 생산됨. 재발이 아니라 조치 전 생산품 |
| `PENDING_CAUSE_REVIEW` | 접수 증상이 아직 확인되지 않음(정보 부족·분류 불가), 또는 조치 뒤 생산됐지만 1순위 원인이 이 Lot에 미친 근거(원인 공정의 조건 이탈, 조치하지 않은 설비의 이상, 자재 원인)가 없음. 원인부터 확인한 뒤 다시 판정 |
| `NOT_APPLICABLE` | 원인 후보가 없음, 1순위 원인이 단발성(설비·자재를 특정하지 못함), 그 원인에 시행된 조치가 없음, 또는 Lot을 모름 |

- **조치 범위(`checks.in_action_scope`)는 판정을 바꾸지 않고 이유를 설명한다.** 범위 밖에서 재발하면 "수평 전개 누락", 범위 안에서 재발하면 "조치 효과 재확인"으로 쓴다.
- 비교할 조치는 이 Lot이 범위 안에 있는 조치를 먼저, 그다음 최근 조치를 고른다.
- 정확도 (v5, 반품별 분석 시작 시점, `results/20261119_recurrence_v5.md`): `recurrence_review` 2,606건과 일치 90.1%, 재발 재현율 82.0%. 검토 대상이 아닌 반품까지 포함한 **재발 정밀도 73.2%**. 이 중 원인분석 평가와 같은 범위(분석 시점에 원인이 알려진 반품)만 보면 정밀도 81.1%다. 나머지 오류는 대부분 원인분석 1순위가 틀린 경우다. 정답은 나중에 확정된 원인을 쓰고 에이전트는 분석 시점의 1순위 원인을 쓰므로, 원인분석이 틀리면 재발 판정도 틀린다. 그래서 생산 근거가 없으면 재발로 단정하지 않는다.

| 필드 | 뜻 |
|---|---|
| `corrective_action` | 비교한 조치: `corrective_action_id`, `investigation_id`, `description`, `implemented_at`, `scope`(`equipment` 또는 `supplier`·`material_lots`) |
| `checks.cause_code`, `cause_name` | 비교에 쓴 1순위 원인 |
| `checks.symptom_confirmed` | 접수 증상이 확인됐는가 |
| `checks.lot_id`, `lot_started_at`, `produced_after_action` | 이 Lot의 생산 시작과 조치 이후 생산 여부 |
| `checks.in_action_scope` | 이 Lot이 조치 범위의 설비를 거쳤거나 자재 Lot을 썼는가(범위가 없으면 `null`) |
| `reason` | 화면에 그대로 보여 줄 한 문장 |
| `suggestion` | 화면 "재발여부 확정"의 AI 제안 단어. `RECURRENCE`이고 조치 범위 안이면 `재발`, 범위 밖이면 `확산형 재발`, `PRE_ACTION_PRODUCT`는 `조치 전 생산품`(화면에 버튼 추가 필요), 조치가 없거나 단발성이면 `신규`, 그 밖은 `null`(제안 없음). 화면의 `{suggestion, basis}`에는 `suggestion`과 `reason`을 쓴다 |

예시 4가지 (v5 실제 출력, `//` 줄은 설명):
```jsonc
// RECURRENCE (확산형 재발): RMA-08218 (디스미어 부족 재발, 조치는 DSM-02에만, 재발은 DSM-01)
{
  "status": "RECURRENCE",
  "corrective_action": {
    "corrective_action_id": "CA-000023",
    "investigation_id": "INV-25-0009",
    "description": "DSM-02 과망간산 용액 교체 기준(처리 면적 1,500㎡) 적용",
    "implemented_at": "2025-07-30T02:35:44+09:00",
    "scope": {
      "equipment": [
        "DSM-02"
      ]
    }
  },
  "checks": {
    "cause_code": "DESMEAR_INSUFFICIENT",
    "cause_name": "디스미어 부족",
    "cause_match": true,
    "symptom_confirmed": true,
    "lot_id": "LT-01550",
    "lot_started_at": "2025-10-04T19:43:28+09:00",
    "produced_after_action": true,
    "in_action_scope": false
  },
  "reason": "Lot LT-01550은(는) 조치 CA-000023(2025-07-30 시행) 이후에 생산됐고 원인이 같습니다. 조치 범위(설비 DSM-02) 밖에서 다시 발생해, 조치가 다른 설비·자재로 수평 전개되지 않은 것으로 보입니다.",
  "suggestion": "확산형 재발"
}
// PRE_ACTION_PRODUCT: RMA-07085 (드릴 비트, 조치 전에 생산된 Lot)
{
  "status": "PRE_ACTION_PRODUCT",
  "corrective_action": {
    "corrective_action_id": "CA-000001",
    "investigation_id": "INV-25-0001",
    "description": "DRL-03 비트 수명 카운터 인터록 적용 및 카운터 리셋 로직 수정",
    "implemented_at": "2025-04-16T07:51:15+09:00",
    "scope": {
      "equipment": [
        "DRL-03"
      ]
    }
  },
  "checks": {
    "cause_code": "DRILL_BIT_WEAR",
    "cause_name": "드릴 비트 수명 초과",
    "cause_match": true,
    "symptom_confirmed": true,
    "lot_id": "LT-00545",
    "lot_started_at": "2025-03-03T19:18:57+09:00",
    "produced_after_action": false,
    "in_action_scope": true
  },
  "reason": "Lot LT-00545은(는) 조치 CA-000001(2025-04-16 시행) 전에 생산된 제품입니다. 재발이 아니라 조치 전 생산품의 반품입니다.",
  "suggestion": "조치 전 생산품"
}
// PENDING_CAUSE_REVIEW: RMA-07936 (정보 부족 접수)
{
  "status": "PENDING_CAUSE_REVIEW",
  "corrective_action": {
    "corrective_action_id": "CA-000038",
    "investigation_id": "INV-25-0015",
    "description": "보전 후 원점 확인 체크리스트 및 초품 승인 필수화",
    "implemented_at": "2025-10-07T02:35:27+09:00",
    "scope": {
      "equipment": [
        "RTR-02"
      ],
      "products": "ALL"
    }
  },
  "checks": {
    "cause_code": "ROUT_OFFSET",
    "cause_name": "라우터 원점 편차",
    "cause_match": true,
    "symptom_confirmed": false,
    "lot_id": "LT-01345",
    "lot_started_at": "2025-08-22T19:56:01+09:00",
    "produced_after_action": false,
    "in_action_scope": true
  },
  "reason": "과거 조치(CA-000038, 2025-10-07 시행)와 원인이 같아 보이지만, 접수 증상이 아직 확인되지 않아 고객 회신으로 원인을 먼저 확인해야 재발 여부를 정할 수 있습니다.",
  "suggestion": null
}
// NOT_APPLICABLE (신규): RMA-08927 (외층 에칭 잔동, 분석 시점까지 시행된 조치 없음)
{
  "status": "NOT_APPLICABLE",
  "corrective_action": null,
  "checks": null,
  "reason": "1순위 원인 외층 에칭 잔동에 대해 분석 시점까지 시행된 발생 원인 조치가 없습니다.",
  "suggestion": "신규"
}
```

### 3-4. `GET /claims/{rma_id}/similar?as_of=...&limit=5`
**응답 200**
```json
{
  "rma_id": "RMA-08927",
  "as_of": "2025-11-20T12:41:37+09:00",
  "items": [
    {
      "rma_id": "RMA-08486",
      "opened_at": "2025-11-05T10:32:14+09:00",
      "claim_date": "2025-11-05",
      "minor_code": "ELEC02",
      "minor_name": "쇼트",
      "cause_code": "OL_ETCH_RESIDUE",
      "cause_name": "외층 에칭 잔동",
      "process_id": "OL_ETCH",
      "equipment_id": "OLE-02",
      "certainty": "CONFIRMED",
      "similarity": 0.68,
      "basis": "같은 증상+같은 제품+같은 Lot+같은 고객+문장 유사",
      "past_action": null,
      "recurrence": false,
      "days_to_resolve": 14,
      "snippet": "[불량 통보] A01 P/N PCB-ECU6-A1 (Lot LT-01603) ICT·기능검사에서 인접 패턴 …",
      "corrective_actions": []
    }
  ]
}
```
`as_of` 이전에 **승인된** 사례만 나온다. 아직 원인이 정해지지 않은 접수는 나오지 않는다.
순서는 **같은 접수 증상**의 과거 사례 중 접수 문장 유사도(`similarity`, 0~1, 글자 단위) 순이다. 같은 점수면 최근 사례가 먼저 나온다. 정보 부족·분류 불가 접수는 증상 구분 없이 찾는다.

| 필드 | 뜻 |
|---|---|
| `similarity` | 접수 문장 글자 유사도(0~1) |
| `basis` | 판단 근거를 `+`로 이은 문자열. 순서대로 `같은 증상`, `같은 제품`, `같은 Lot` 또는 `같은 설비(설비)`, `같은 고객`, `문장 유사` 중 해당하는 것 |
| `past_action` | 그 사례 조사의 발생 원인 대책(없으면 첫 대책, 분석 시점까지 정해진 것만). 없으면 `null` |
| `recurrence` | 그 사례의 사건이 분석 시점까지 재발로 다시 열렸는가 |
| `days_to_resolve` | 그 사례의 접수부터 원인 확정까지 일수 |
| `claim_date`, `cause_name`, `minor_name` | 화면 표시용(접수일, 원인 이름, 소분류 이름) |
| `corrective_actions[]` | 그 조사의 대책 전체(`category`, `description`, `implemented_at`) |

### 3-5. `POST /cases/refresh`
Spring이 승인·확정 결과를 DB에 저장한 **뒤에** 호출한다. AI는 DB에서 새 사례를 읽어 검색 색인을 갱신한다.

**요청**
```json
{"rma_id": "RMA-08927", "analysis_id": "ana-20251120-08743-01", "decision": "APPROVED", "decided_at": "2025-11-21T10:12:00+09:00"}
```
**응답 200**
```json
{"rma_id": "RMA-08927", "indexed": true, "case_count": 9924}
```

### 3-6. `GET /health`
```json
{"status": "ok", "db": "ok", "llm": "ok", "version": "0.1.0", "rules": "pcb-v5"}
```
`llm`이 `"down"`이면 분석 결과가 `PARTIAL`로 나온다.

### 3-7. `POST /triage`
새 접수에는 `qa_triage`가 없어서 원인분석을 시작할 수 없다. 접수 분류(소분류)가 `symptom_assessment`에 저장된 뒤 이 API로 QA 판정안을 받는다(2026-11-20 팀 결정, Spring_API_v4 4-6).
- `provisional: false`: Spring이 판정안을 **바로 `qa_triage`에 저장**하고(`decided_by = "QA_AGENT"`) 원인분석을 자동으로 시작한다. 품질관리자는 원인 판정 전까지 판정을 수정할 수 있다(`PUT /api/rmas/{rmaId}/triage`, L2 → L3로 올리면 승인 절차로 바뀜).
- `provisional: true`(임시 판정, 접수 증상이 정보 부족·분류 불가): 저장하지 않고 품질관리자가 확인·수정한 뒤 저장하고 원인분석을 시작한다.

**요청**
```json
{"rma_id": "RMA-08927"}
```
`as_of`(선택)를 생략하면 접수 분류가 저장된 시각으로 판정한다.

**응답 200** (v5 RMA-08927, QA 판정 저장 전 시점)
```json
{
  "rma_id": "RMA-08927",
  "minor_code": "ELEC02",
  "customer_industry": "AUTOMOTIVE",
  "severity": 8,
  "characteristic_id": null,
  "characteristic_symbol": null,
  "repeat_signal": 1,
  "analysis_level": "L3",
  "needs_approval": true,
  "approval_reasons": [
    "차량 고객 + 심각도 8 이상"
  ],
  "provisional": false,
  "rationale": "불량 형태 쇼트, 심각도 8, 고객 AUTOMOTIVE, 동일 고객·불량 형태 30일 내 3건째 이상 → L3",
  "as_of": "2025-11-20T12:00:00+09:00"
}
```
- 규칙은 `analysis_level_rule`과 같다. L3: 심각도 9 이상, CC 특별특성, 차량 고객 + 심각도 8 이상. L2: 심각도 6 이상, SC 특별특성, 같은 고객·같은 증상 30일 안 3건째 이상. 그 밖은 L1.
- 심각도·특별특성은 소분류 정의(`symptom_definition`, `special_characteristic`), 업종은 `customer`, 반복은 그 고객의 같은 증상 접수(분석 시점까지 재분류된 것은 확인된 증상으로)에서 계산한다.
- 소분류가 정보 부족·분류 불가면 `provisional: true`, 임시 L2다. 차량 고객이면 QA가 당일 직접 확인한다는 문구가 붙는다.
- `needs_approval`은 L3이면 `true`. `approval_reasons`는 그 이유(L3 조건만, L2 이하는 빈 배열)다. 반복·SC 같은 L2 조건은 `rationale`에만 적는다. 분석 결과의 `qa_review.approval_reasons`도 같은 값이다.
- 대조 (v5): 저장된 `qa_triage` 10,210건과 분석 수준 99.8%, 모든 필드 99.6% 일치(`scripts/eval_triage.py`).
- 오류: 없는 접수 `CLAIM_NOT_FOUND`(404), 접수 분류가 아직 없거나 `as_of`가 그 전이면 `AS_OF_INVALID`(422).

---

### 3-8. `GET /priority/patterns`, `GET /priority/responses` (우선순위 점수, 요구사항 FR-03)
LLM 없이 DB 값으로 계산하는 점수다(0~100). 점수마다 항목별 점수와 근거 문장(`parts`)이 있어 화면에서 마우스를 올리면 그대로 보여 주면 된다. 가중치는 설정값이다(`weights`로 바꿀 수 있고 합이 1이 되도록 다시 나눈다). Spring이 같은 계산을 직접 해도 되고, 이 API를 불러도 된다.

**요청 (쿼리)**: `as_of`(선택, 생략하면 데이터의 마지막 접수 시각), `limit`(선택), `weights`(선택, JSON 예: `{"loss": 0.4, "count": 0.2, "severity": 0.2, "trend": 0.2}`)

**품질 조치 우선순위** (`/priority/patterns`, 패턴 = 조사 `investigation`, `as_of`에 열려 있는 것)

| 항목 | 가중치 | 계산 |
|---|---:|---|
| 발생 건수·수량 (`count`) | 30% | 최근 30일 반품 수와 불량 수량. 각각 가장 큰 패턴 대비 비율의 평균 |
| 손실 금액 (`loss`) | 30% | 최근 30일 추정 손실(`loss_ledger`). 가장 큰 패턴 대비 비율 |
| 심각도 (`severity`) | 20% | 최근 30일 반품의 최고 QA 심각도 / 10 |
| 증가 추세 (`trend`) | 20% | 패턴의 주 증상(가장 많은 접수 소분류)을 가진 전체 반품의 최근 30일 / 직전 30일 증가율. 0~100%로 자름(+100% 이상 만점) |

- 재발 이력은 넣지 않는다(재발은 따로 모니터링).
- 최근 30일만 보는 이유: 누적으로 세면 조치 후 잠잠해진 오래된 패턴이 위에 남는다. 매월 말 점수와 이후 60일 실제 손실의 순위상관이 누적 +0.03, 최근 30일 +0.38이었다(`scripts/eval_priority.py`).
- 추세를 패턴 자신의 반품이 아니라 같은 증상 전체로 재는 이유: 패턴은 조사가 열린 뒤에만 반품이 모여 늘 "새로 생김"으로 보이기 때문이다.
- `status`는 지금 상태(분석 중, 조치 진행, 효과 확인 중)다. `as_of` 이후에 닫힌 조사는 그 시점 상태가 데이터에 없어 `status: null`, `status_name: "진행 중"`이다.
- 연결된 반품이 없는 조사(분기별 단발 묶음)도 열려 있으면 목록 끝에 0점대로 나온다.

```json
{
  "as_of": "2025-12-31T18:56:13+09:00",
  "weights": {"count": 0.3, "loss": 0.3, "severity": 0.2, "trend": 0.2},
  "items": [
    {
      "rank": 1, "investigation_id": "INV-25-0023", "title": "오픈(단선) — 공통점 추적", "analysis_level": "L3",
      "status": "MONITORING", "status_name": "효과 확인 중", "opened_at": "2025-11-17T10:18:22+09:00",
      "main_symptom": "ELEC01", "claim_count": 218, "total_claim_count": 258, "defect_qty": 10551, "loss_krw": 1076025493,
      "score": 88.9,
      "reason": "발생 건수·수량 30.0점 · 손실 금액 27.9점 · 심각도 16.0점 · 증가 추세 15.0점",
      "parts": {
        "count": {"name": "발생 건수·수량", "weight": 0.3, "score": 1.0, "points": 30.0, "basis": "최근 30일 반품 218건 · 불량 10,551pcs (가장 큰 패턴 대비 100% · 100%)"},
        "loss": {"name": "손실 금액", "weight": 0.3, "score": 0.9296, "points": 27.9, "basis": "최근 30일 추정 손실 10.76억 원 (가장 큰 패턴 대비 93%)"},
        "severity": {"name": "심각도", "weight": 0.2, "score": 0.8, "points": 16.0, "basis": "최근 30일 최고 심각도 8/10"},
        "trend": {"name": "증가 추세", "weight": 0.2, "score": 0.7477, "points": 15.0, "basis": "같은 증상(ELEC01) 반품 최근 30일 194건 / 직전 30일 111건 (+75%)"}
      },
      "recent_rma_ids": ["RMA-10603", "RMA-10596", "RMA-10591", "RMA-10585", "RMA-10583"]
    }
  ]
}
```
`claim_count`·`defect_qty`·`loss_krw`는 최근 30일 값, `total_claim_count`는 `as_of`까지 연결된 전체 반품 수다.

**고객 응대 우선순위** (`/priority/responses`, CS 회신이 끝나지 않은 반품 `cs_status <> 'CLOSED'`)

| 항목 | 가중치 | 계산 |
|---|---:|---|
| 심각도 (`severity`) | 30% | QA 심각도 / 10 |
| 처리 경과일 (`elapsed`) | 25% | 접수 후 일수 / 30 (30일 이상 만점) |
| 제품 중요도 (`importance`) | 25% | 제품 용도: AUTOMOTIVE 1.0, SEMICONDUCTOR 0.8, INDUSTRIAL 0.6 (**가정값**, 팀 확인 필요) |
| 클레임 수량 (`qty`) | 20% | 클레임 불량 수량, 로그 척도로 가장 큰 반품 대비 |

- 안전 특성(CC) 반품은 점수와 관계없이 맨 위에 고정한다(`pinned: true`). v5 마지막 시점 기준 열린 반품 996건 중 44건.
- 손실 금액은 품질 지표라 넣지 않는다.
- CS 상태는 저장된 현재 값이다(상태 이력이 데이터에 없음). `as_of`는 경과일과 대상 반품 범위에만 쓴다.

```json
{
  "as_of": "2025-12-31T18:56:13+09:00",
  "weights": {"severity": 0.3, "elapsed": 0.25, "importance": 0.25, "qty": 0.2},
  "items": [
    {
      "rank": 1, "rma_id": "RMA-09816", "customer_id": "A04", "opened_at": "2025-12-09T13:46:20+09:00",
      "cs_status": "RESPONDED", "part_no": "PCB-ECU6-A1", "pinned": true, "score": 83.1,
      "reason": "안전 특성(CC) 반품이라 맨 위 고정 · 심각도 27.0점 · 제품 중요도 25.0점 · 처리 경과일 18.5점 · 클레임 수량 12.6점",
      "parts": {
        "severity": {"name": "심각도", "weight": 0.3, "score": 0.9, "points": 27.0, "basis": "심각도 9/10 · 안전 특성(CC)"},
        "elapsed": {"name": "처리 경과일", "weight": 0.25, "score": 0.7405, "points": 18.5, "basis": "접수 후 22일 (30일 이상은 만점)"},
        "importance": {"name": "제품 중요도", "weight": 0.25, "score": 1.0, "points": 25.0, "basis": "AUTOMOTIVE 제품 (중요도 1.0)"},
        "qty": {"name": "클레임 수량", "weight": 0.2, "score": 0.6289, "points": 12.6, "basis": "클레임 불량 24pcs"}
      }
    }
  ]
}
```
- 오류: `as_of` 형식 오류 `AS_OF_INVALID`(422), 모르는 가중치 이름이나 음수 가중치 `INVALID_INPUT`(400).

---

### 3-9. `POST /chat` (에이전트 패널 질문, Spring `AiClient.chat`)
AI 서버는 대화를 저장하지 않는다. Spring이 질문, 최근 대화(최대 10개), 화면 문맥(역할별 필터 적용 후)을 보내고, 답을 `app.chat_message`에 저장한다.

**요청**
```json
{"thread_id": "6f1c...", "audience": "QUALITY", "scope": {"type": "RMA", "id": "RMA-08927"},
 "message": "왜 1순위가 외층 에칭 잔동이야?",
 "history": [{"role": "user", "text": "원인 알려줘"}, {"role": "assistant", "text": "1순위는 ..."}],
 "context": {"rma": {"rma_id": "RMA-08927", "...": "접수 상세(RmaDetailResponse)"},
             "analysis": {"analysis_id": "...", "as_of": "...", "result": {"...": "3-3 result"}, "cause_decision": null}}}
```
- `audience`: `QUALITY` / `CS`. `scope.type`: `RMA`(상세 화면) / `PATTERN`(불량 패턴, QUALITY만). PATTERN의 `context`는 `{period, pattern, report}`(패턴 상세·보고서 응답).
- 분석 전 RMA는 `context.analysis = null`.

**응답 200**
```json
{"status": "ANSWERED", "intent": "EVIDENCE",
 "answer": "1순위가 외층 에칭 잔동인 이유는 같은 제품의 최근 사례 RMA-08668에서 확정됐고, 이 Lot에서도 OLE-02 스프레이 압력 -4.0σ와 노즐 7개 막힘·교체가 확인됐기 때문입니다. 다만 반송품 시험 결과가 대기 중이라 최종 확정은 아닙니다.",
 "evidence": [{"type": "CAUSE_CANDIDATE", "rma_id": "RMA-08927", "label": "1순위 외층 에칭 잔동 (OLE-02)"}],
 "suggestions": [], "warnings": [], "model_version": "chat-0.1+openai:gpt-5.4-mini", "elapsed_ms": 2300}
```
| `status` | 뜻 |
|---|---|
| `ANSWERED` | LLM이 문맥의 사실만으로 답함 |
| `NEED_ANALYSIS` | 분석이 없는 RMA에 원인·근거·사례·Lot·재발·처리방안·회신을 물음. `suggestions: [{task: "start_analysis", label: "분석 시작"}]` |
| `OUT_OF_SCOPE` | 반품·품질과 관계없는 질문, 지시를 바꾸라는 요구. `evidence` 없음 |
| `FALLBACK` | LLM이 꺼져 있거나 실패, 또는 근거에 없는 숫자를 씀. 같은 사실로 만든 템플릿 답변 (`warnings`: `LLM_UNAVAILABLE` / `LLM_OUTPUT_REJECTED`) |

- **답을 만드는 순서:** 질문의 의도(`intent`: `CAUSE`, `EVIDENCE`, `SIMILAR`, `LOT`, `RECURRENCE`, `ACTION`, `REPLY`, `STATUS`, `SUMMARY`, `OTHER`)를 낱말로 정한다 → 그 의도에 필요한 문맥 부분만 사실로 뽑는다 → **RAG 검색**으로 과거 사례와 사내 기준 문서를 더한다(아래) → LLM이 그 사실만으로 2~4문장을 쓴다.
- **RAG 검색 (`rma_agent/rag.py`):** 문서 약 1만 개. 승인된 과거 사례(증상·확정 원인·공정·설비·원문), 원인 기준표(메커니즘·필요 근거·QA 확정 기준·표준 조치·담당), 유출 기준표, 증상 정의(포함·제외 규칙), 대책, 조사. 점수 = 0.5 × 임베딩 유사도(OpenAI `text-embedding-3-small` 512차원) + 0.5 × 글자 점수(질문 글자쌍 포함률 + 설비·Lot 번호 일치). 문서마다 알려진 시각이 있어 `as_of`(분석 시작 시각, 패턴은 기간 끝) 이후 문서와 그 RMA 자신은 검색되지 않는다. 의도별로 찾는 문서가 다르다(근거 질문은 원인·유출 기준 + 사례, 처리방안은 대책·원인 기준, 유사 사례는 사례 + 대책). CS는 사례만, 원인·설비·Lot을 뺀 채로 받는다. 비교 결과 `results/20261127_rag_v5.md`. "그럼 2순위는?"처럼 의도가 없는 후속 질문은 직전 질문의 의도를 이어받는다.
- **CS:** 원인·설비·공정·Lot은 문맥에 있어도 쓰지 않는다(Spring 필터에 더한 이중 차단). CS가 원인·근거·Lot·재발을 물으면 LLM 없이 정해진 안내 문구로 답한다. `evidence`는 `SIMILAR_CASE`, `RMA`만.
- `evidence[].type`: `CAUSE_CANDIDATE`, `LOT`, `SIMILAR_CASE`(RMA 범위·검색된 과거 사례), `RMA`(패턴 근거), `KNOWLEDGE`(검색된 기준 문서, `rma_id`는 `null`, 예 `"원인 기준: 외층 에칭 잔동"`). 최대 5개. CS에는 `SIMILAR_CASE`만.
- `warnings`에 `RAG_NOT_READY`가 있으면 서버 시작 직후 색인을 만드는 중이라 검색 없이 답한 것이다(캐시가 있으면 약 3초).
- 시간: 문맥만으로 답하므로 LLM 1회, 실측 1~3초. LLM 호출 제한 12초(재시도 없음), Spring 제한 20초.
- 오류: `message` 비어 있음·1,000자 초과, `audience`·`scope` 값 오류, CS의 PATTERN 질문은 400 `INVALID_INPUT`.

---

## 4. 오류
**오류 응답 형식** (화면의 `api.js`가 `detail`을 그대로 보여 준다)
```json
{"detail": "RMA-99999 접수를 찾을 수 없습니다", "code": "CLAIM_NOT_FOUND"}
```
- `detail`: 화면에 보여 줄 한국어 문구. `code`: 처리 분기용 코드.
- `rma_agent`의 예외(`rma_agent.errors`)가 `body()`로 이 형식을 만든다.

| HTTP | code | 상황 | 화면 처리 |
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
| PCB v5 원천 데이터 | 적재 후 **읽기 전용** | 테이블 46개 |

AI 서비스는 DB에 **쓰지 않는다.** DB 계정은 읽기 전용으로 준다.

---

## 6. A안·B안 비교 (백·프론트 영향)
| 항목 | B 혼합형 (기본) | A 원인분석 자율형 |
|---|---|---|
| API | 같음 | 같음 |
| 원인분석 완료 시간 | 5~15초 | 15~60초 |
| 같은 입력 → 같은 순위 | 예 | 실행마다 다를 수 있음 |
| `trace` 단계 수 | 항상 7단계 (분석 시작 → QA 판정 → 제조 Lot 추적 → 원인분석 → QA 검토 → 제조 개선안 → CS 회신) | 원인분석 안에서 도구 호출이 4~8회 추가 |
| 시간 초과 | 30초 | 90초 |
| LLM 호출 비용 (1건) | 약 5회 | 약 10~15회 |
| 화면 처리 | 진행 상태 표시 | 진행 상태 + 오래 걸릴 때 안내 문구 |
| 평가 | 시간 순서 재생으로 점수 산출 | 같음 (여러 번 실행 평균) |

**권장:** 화면은 **폴링(1초 간격) + 진행 상태 표시 + 90초 시간 초과 안내**로 만든다. 그러면 A·B 어느 쪽으로 가도 화면을 고칠 필요가 없다.

---

## 7. 화면별 적용

### 7-1. AI가 실행되는 화면은 두 곳
- **RMA 접수:** CS 에이전트 (`/classify`)
- **RMA 상세:** 오케스트레이터 → QA 판정 · 제조 Lot 추적 → 원인분석 → QA 검토 → 제조 개선안 → CS 회신 (`/analyses`)

나머지 화면은 **저장된 분석 결과나 Spring 집계**를 보여 준다. 그래서 대시보드·패턴·알림·보고서 화면은 AI 완성과 상관없이 지금 만들 수 있다.

| 화면 · 사용자 | 사용자가 하는 일 → 호출 | 움직이는 에이전트 | 화면에 나오는 것 (필드) |
|---|---|---|---|
| **RMA 접수** · CS 담당 | 통보 원문 붙여넣기 → 등록 · `/classify` | CS | 대·소분류, 근거 구절. 정보 부족이면 재질의 문구 (`follow_up_questions`) |
| **RMA 조회 → 상세** · 품질관리자 | 상세 열기 또는 "AI 분석" · `/analyses` → 1초 조회 | 오케스트레이터, QA, 제조, 원인분석, CS | 원인 후보 표 (`cause_candidates`), 검토 근거 (`evidence`), 유출 원인 (`escape_candidates`), 유사 사례, QA 검토 (`qa_review`), 회신·개선안 초안 (`drafts`), 진행 단계 (`progress`, `trace`) |
| **승인** · 품질관리자 (L3) | 원인 후보 채택·기각, 의견 → Spring 저장 → `/cases/refresh` | 없음 (사람 판단) | `qa_review.approval_reasons`, `candidate_checks`, `review_note`. 채택된 후보가 확정 원인이 되어 다음 반품 분석에 재사용됨 |
| **대시보드** · 품질관리자 | 열기 · Spring 집계 | 없음 | 승인 대기 수, 원인 공정·장비 TOP(채택 기준), 손실 금액(`loss_ledger`) |
| **불량 패턴 분석** · 품질관리자 | 패턴(조사) 선택 · Spring 집계 + `/claims/{id}/similar` | 원인분석 결과 재사용 | 패턴별 확정 원인·공정·장비, 관련 반품, 손실 합계(`loss_by_investigation`), **우선순위 점수와 근거**(3-8 `/priority/patterns`) |
| **알림 및 재발 관리** · 품질관리자 | 알림 확인 · 저장된 분석 결과 | 원인분석 결과 재사용 | 새 반품의 1순위가 과거 확정 원인과 같으면 재발 의심 알림 (예: P01 → P01R) |
| **AI 보고서** · 품질관리자 | 보고서 생성 · 저장된 분석 결과 | 원인분석·QA·CS 결과 재사용 | 원인 후보와 검토 근거, QA 검토 의견, 개선안·회신 초안 |
| **AI 어시스턴트** · 전체 (2차) | 질문 · `/chat` | 유사 사례 근거 | 현재 RMA 맥락의 답변과 근거 사례 |

### 7-2. RMA 상세 화면의 영역 배치 (제안)
| 영역 | 에이전트 | 필드 |
|---|---|---|
| 접수 분류 | CS | `classification` |
| 원인 후보 표 (순위·원인·공정·장비·점수·상태) | 원인분석 | `cause_candidates` |
| 검토 근거 (공정 조건 이탈, 설비 이벤트, 검출률) | 제조 | `cause_candidates[].evidence`, `escape_candidates` |
| 유사 사례 | 원인분석 | `similar_cases` |
| QA 검토 · 승인 사유 · 채택/기각 버튼 | QA → 사람 | `qa_review` |
| 회신 초안 | CS | `drafts.customer_reply` |
| 개선안 초안 (공정 개선 · 검사 기준 개선) | 제조 | `drafts.improvement` |
| 처리방안 제안 (대체품 출하·대금 공제 등) | CS | `drafts.service_action` |
| 진행 단계 | 오케스트레이터 | `progress`, `trace` |

발표에서도 이 화면 하나로 "에이전트가 각각 무엇을 했는지"를 보여 줄 수 있다.

**용어** (화면·발표): 원인 후보(제시·채택·기각), 확정 원인, 추정 원인, 검토 근거

---

## 8. 일정
| 날짜 | AI | 백·프론트가 할 수 있는 것 |
|---|---|---|
| 11/18 | 명세 v0.3 공유 | 구태우: 명세 JSON으로 목업 API → 연동·화면 개발 시작 |
| 11/19~20 | 명세 확정 v1.0, 분류 API 실제 연결 | 접수 화면 실제 분류 |
| 11/23~26 | 원인분석 B안 실제 연결 | RMA 상세·승인 화면 실제 데이터 |
| 11/27 | 인계 (구태우 님 FastAPI 통합) | 통합 |
| 12/1~3 | A안 시도(여유 시), 평가, 고정 | 통합 테스트 준비 |

## 확인 요청
- **윤서정 님:** 5장 저장 책임과 `ai_analysis` 테이블, 2-1 상태값
- **구태우 님:** 3-3 응답 필드가 RMA 상세·승인 화면에 충분한지, 폴링 방식, **FastAPI 서비스와 목업 API 담당**(1-5 함수 경계 확인)
- **이상윤 님:** FastAPI 서비스 한 개 배포, 환경 변수(DB 읽기 전용 계정, LLM 키, `AS_OF_DEFAULT`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`), **LangSmith 계정·프로젝트 설정**
