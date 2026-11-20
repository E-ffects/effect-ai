# AI 패키지 연결 안내 (FastAPI · LangSmith)

- 대상: 구태우(FastAPI), 윤서정(Spring), 프론트, 이상윤(LangSmith)
- **구조:** 화면 → Spring(`/api/...`) → AI 서버(FastAPI `/ai/v1/...`) → `rma_agent`. 화면은 AI 서버를 직접 부르지 않는다(명세 3-0, Spring_API_v4 2026-11-20 확정).
- 기준: `feature/ai-baseline` 브랜치, 명세 `docs/api_spec.md` v0.3
- 문의: 유희범(AI/DATA)

## 1. 준비 (공통)
```bash
git clone https://github.com/E-ffects/effect-ai.git
cd effect-ai
git checkout feature/ai-baseline
pip install -r requirements.txt
cp .env.example .env
```
- **데이터:** 팀 PostgreSQL을 쓰면 `RMA_DATABASE_URL`만 넣는다(SQLite와 결과가 같음을 확인했다). 로컬에서는 `data/pcb_rma_v5.sqlite`가 있어야 한다. git에 없으니 유희범에게 `PCB_RMA_dataset_v5_full.zip`을 받아 안의 `pcb_rma.sqlite`를 이 이름으로 둔다. 다른 위치면 `.env`에 `RMA_DB=경로`.
- **확인:** `python3 -m pytest -q`가 모두 통과하면 준비 끝이다(LLM은 부르지 않는다).

## 2. `.env` 설정
`.env`는 git에 올라가지 않는다. 키를 코드·채팅·Jira에 붙여 넣지 않는다.

| 변수 | 필수 | 값 | 담당 |
|---|---|---|---|
| `RMA_LLM` | 선택 | `openai:gpt-5.4-mini` (기본). `off`면 LLM 없이 템플릿 문장 | — |
| `OPENAI_API_KEY` | LLM 쓸 때 | OpenAI 키 | 팀 키를 각자 `.env`에 |
| `LANGSMITH_API_KEY` | 추적 쓸 때 | LangSmith 키 | 이상윤 |
| `LANGSMITH_PROJECT` | 선택 | `effect-ai` | 이상윤 |
| `RMA_DATABASE_URL` | 선택 | 팀 PostgreSQL을 읽을 때. 예: `postgresql://rma@db:5432/rma` (비밀번호는 `PGPASSWORD`나 `~/.pgpass`). 있으면 SQLite 대신 이걸 쓴다. 적재는 `db/postgres/README.md` | 윤서정 |
| `RMA_DB_SCHEMA` | 팀 DB면 필수 | PCB 테이블이 있는 스키마. 팀 DB는 `pcb` | 윤서정 |
| `RMA_DB` | 선택 | SQLite 경로 (기본 `data/pcb_rma_v5.sqlite`) | — |
| `RMA_CAUSE_RANKER` | 선택 | 원인 후보 학습 재정렬. `models/cause_ranker_v5.json`이면 켬, 비우거나 `off`면 끔(기본). 응답 모양은 같다 | 팀 결정 |
| `RMA_SERVICE_JWT_SECRET` | AI 서버 | Spring `jwt.secret`과 같은 값(3장 참고 서버). 로컬에서만 `RMA_AUTH=off` | 윤서정·구태우 |

- **사용 금지 모델:** gpt-6-astra, gpt-6.1-sol, gpt-6-sol, gpt-5.5-pro. 설정해도 쓰지 않고 템플릿으로 동작한다.
- **키가 없으면:** LLM은 템플릿 문장, LangSmith는 추적 없이 그대로 돈다. 개발 중에는 `RMA_LLM=off`가 빠르다(분석 약 0.6초, LLM 켜면 약 10초).

## 3. FastAPI가 부르는 함수 (구태우)
AI 서버(FastAPI)는 `rma_agent`의 아래 함수만 부른다. 에이전트 내부가 바뀌어도 이 함수들의 입출력은 명세대로 유지한다.

| 함수 | API | 반환 | 시간 |
|---|---|---|---|
| `classify(raw_text, detection_stage=None, part_no=None, customer_id=None, as_of=None)` | `POST /classify` | 3-1 응답 dict (`request_id`는 FastAPI가 채움) | LLM 켜면 약 1.3초 |
| `run_analysis(rma_id, as_of=None, mode="B", top_k=3, on_progress=None)` | `POST /analyses` 작업 안에서 | 3-3 응답 dict (`analysis_id`는 FastAPI가 채움) | LLM 켜면 약 10초 |
| `similar_cases(rma_id, as_of=None, limit=5)` | `GET /claims/{id}/similar` | 3-4 응답 dict | 0.1초 |
| `refresh_cases()` | `POST /cases/refresh` | 사례 수(int) | 0.3초 |
| `triage(rma_id, as_of=None)` | `POST /triage` | 3-7 응답 dict (새 접수의 QA 판정안) | 0.01초 |
| `pattern_priority(as_of=None, weights=None, limit=None)` | `GET /priority/patterns` (2단계) | 3-8 응답 dict | 0.1초 |
| `response_priority(as_of=None, weights=None, limit=None)` | `GET /priority/responses` (2단계) | 3-8 응답 dict | 0.1초 |

- `classify`는 과거 접수 검색(약 0.03초) 뒤 LLM이 소분류를 고른다(LLM 켜면 약 1.3초). LLM이 없거나 실패하면 검색 결과로 답한다. 처음 호출 때 접수 색인을 만든다(약 0.6초).
- `as_of`를 주면 그 시각 이전에 분류된 접수만 예시로 쓴다. 생략하면 전체를 쓴다.
- Spring의 `POST /classify` 요청은 `reported_part_no`로 온다. `classify(part_no=body["reported_part_no"], ...)`로 넘긴다.
- **인증:** Spring이 같은 `jwt.secret`으로 발급한 서비스 토큰(`role = "SYSTEM"`, 유효 10분)만 받는다. 서명·만료·`role`을 확인하고 아니면 401. `SYSTEM`에는 결과 전체를 준다(CS 필터는 Spring이 한다).
- Spring은 `as_of`를 보내지 않는다. `rma_agent`가 정한 `as_of`가 응답에 들어가니 그대로 돌려주면 Spring이 저장한다.

### `run_analysis` 쓰는 법
- **동기 함수다.** 요청 스레드에서 기다리지 말고 작업으로 돌린다(스레드 풀 또는 BackgroundTasks). 여러 건을 동시에 돌려도 된다(DB는 읽기 전용, 연결은 스레드 공유 가능).
- **처음 호출 때 한 번** DB·사례 저장소를 불러온다(약 0.3초). 서비스 시작 때 `refresh_cases()`를 한 번 불러 두면 첫 요청이 빠르다.
- **`as_of`를 생략하면** 그 접수의 분석 시작 시각(QA 접수 판정·분류가 끝난 시각)을 쓴다. 응답의 `as_of`에 실제 쓴 값이 들어 있다.
- **진행률:** `on_progress(agent, steps_done, steps_total)`가 단계마다 불린다. 3-3 `RUNNING` 응답의 `progress`에 그대로 넣는다. `agent`는 `ORCHESTRATOR`, `QA`, `MANUFACTURING`, `CAUSE_ANALYSIS`, `CS`.
- **상태:** 반환 dict의 `status`는 `SUCCEEDED` 또는 `PARTIAL`(LLM 장애, 순위·근거는 정상)이다. 예외가 나면 `FAILED`로 기록한다.
- **시간 제한:** 명세는 B안 30초다. 넘으면 FastAPI가 `ANALYSIS_TIMEOUT`(504)으로 처리한다.

### 오류
함수는 `rma_agent.errors`의 예외를 낸다. 각 예외에 명세 4장의 `code`와 `http_status`가 들어 있고, `exc.body()`가 응답 본문 `{"detail": "화면 문구", "code": "CLAIM_NOT_FOUND"}`을 만든다. 프론트 `api.js`는 `detail`을 그대로 보여 준다.

| 예외 | `code` | HTTP | 언제 |
|---|---|---|---|
| `InvalidInput` | `INVALID_INPUT` | 400 | `raw_text`·`rma_id` 비어 있음, `mode`가 A/B가 아님, `top_k`·`limit` 범위 밖 |
| `ClaimNotFound` | `CLAIM_NOT_FOUND` | 404 | 없는 접수, `as_of`보다 늦게 접수된 건 |
| `AsOfInvalid` | `AS_OF_INVALID` | 422 | `as_of` 형식 오류(시간대 필수, 예 `2025-11-20T14:40:46+09:00`), QA 판정 전이라 아직 분석 불가 |
| `ServiceUnavailable` | `SERVICE_UNAVAILABLE` | 503 | DB 파일을 열 수 없음 |

`ANALYSIS_NOT_FOUND`, `ANALYSIS_IN_PROGRESS`, `ANALYSIS_TIMEOUT`은 작업 관리 쪽이라 FastAPI가 만든다.

### 참고 서버 `server/app.py` (바로 실행 가능)
명세 3장의 모든 경로를 `rma_agent` 함수에 연결한 FastAPI 앱이다. 그대로 써도 되고, 경로를 구태우 님 앱으로 옮겨도 된다. 경로마다 `rma_agent` 함수 하나만 부르므로 AI 쪽이 바뀌어도 이 파일은 바뀌지 않는다.
```bash
pip install -r requirements.txt
RMA_SERVICE_JWT_SECRET=<Spring jwt.secret과 같은 값> uvicorn server.app:create_app --factory --port 8080
RMA_AUTH=off uvicorn server.app:create_app --factory --port 8080   # 로컬 전용, 토큰 검사 안 함
```
- **기본 주소** `http://127.0.0.1:8080/ai/v1`. `GET /health`만 토큰 없이 부를 수 있다.
- **서비스 토큰:** Spring `JwtProvider`처럼 `jwt.secret`의 UTF-8 바이트로 HMAC 검증한다(비밀값 길이에 따라 HS256/384/512). `role = "SYSTEM"`, `exp` 필수, `type`이 있으면 `ACCESS`. 아니면 401(서명·만료) 또는 403(역할). 비밀값이 없으면 서버가 시작하지 않는다.
- **분석 작업:** 메모리에 두고 스레드 4개로 돈다. 같은 `rma_id`·`as_of`·`mode`가 실행 중이면 그 `analysis_id`를 돌려준다(202). 없는 접수·QA 판정 전은 등록 때 바로 404/422(`rma_agent.analysis_as_of`). B 30초, A 90초를 넘으면 `FAILED` + `ANALYSIS_TIMEOUT`이고 늦게 끝난 결과는 버린다. 재시작하면 작업이 사라지고 조회는 404 `ANALYSIS_NOT_FOUND`(Spring은 `ANALYSIS_LOST`로 처리).
- **오류 본문**은 모두 `{"detail", "code"}`다. 요청 형식 오류도 400 `INVALID_INPUT`으로 바꾼다(FastAPI 기본 422 아님, Spring의 422는 `AS_OF_INVALID`만).
- **시연 리허설:** 서버를 띄운 뒤 `python3 scripts/demo_flow.py`가 Spring 순서대로 샘플 6건을 불러 시간·문구를 보여 준다(`--out results/...md`로 저장). 2026-11-20 결과 `results/20261120_demo_flow_v5.md`.
- **검증:** `tests/test_server.py`가 Spring 흐름(분류 → 판정 → 분석 등록·폴링 → 유사 사례 → 갱신), 토큰, 오류 형식, 중복 실행, 시간 초과를 확인한다. `tests/test_contract.py`는 `samples/*.json`이 실제 출력과 같은 모양인지(판정은 값까지) 확인한다. 샘플이 어긋나면 `python3 scripts/make_samples.py`로 다시 만든다.

### 바로 해 볼 값
| rma_id | 내용 |
|---|---|
| `RMA-08927` | 명세 3-3의 예시(v5). 차량 고객 L3, 1순위 외층 에칭 잔동(OLE-02), 유출 ET-02 |
| `RMA-08218` | 재발 판정 `RECURRENCE` (디스미어 부족, 조치 범위 밖 DSM-01) |
| `RMA-99999` | 없는 접수 → `CLAIM_NOT_FOUND` |
| `RMA-08927` + `as_of="2025-11-20T10:00:00+09:00"` | QA 판정 전 → `AS_OF_INVALID` |

## 4. Spring 연결 (윤서정)
Spring이 AI 서버를 부르는 순서·저장·오류 처리는 `Spring_API_v4.md`(4~6장)가 기준이다. AI 쪽에서 알아 둘 점만 적는다.

- **실제 서버로 붙여 보기:** `server/app.py`를 띄우고(3장) `ai.mode=http`, `ai.base-url=http://127.0.0.1:8080/ai/v1`로 바꾸면 된다. 서버의 `RMA_SERVICE_JWT_SECRET`은 Spring `jwt.secret`과 같은 값이다.
- **목업부터:** AI 서버가 없을 때는 `MockAiClient`가 `samples/*.json`을 돌려준다. 샘플마다 `classify`, `triage`(QA 판정안), `analysis`(분석 결과, 분류 불가 샘플은 없음)가 실제 에이전트 응답 그대로 들어 있다.
- **새 접수가 분석되려면 DB에 이 행이 있어야 한다:** `rma_claim`, `rma_item`(`lot_id`가 있어야 Lot 추적), `symptom_assessment`(분류 결과), `qa_triage`(QA 판정, `decided_at`). 접수가 없으면 `CLAIM_NOT_FOUND`(404), 분류나 QA 판정이 아직 없으면 `AS_OF_INVALID`(422)를 준다.
- **원인 판정을 저장할 때 (`cause_assessment` 새 행):** `cause_category`(`cause_master`에서), `assessed_at`은 DB 필수다. 채택한 원인 후보의 `process_id`, `equipment_id`도 채워야 다음 분석에서 그 사례가 장비 근거로 쓰인다. `claim_cause.decided_at`과 `cause_assessment.approved_at`이 둘 다 있어야 사례로 잡힌다.
- **저장한 뒤 `POST /cases/refresh`:** AI가 사례 저장소를 다시 읽는다. 부르지 않으면 새 확정 원인이 다음 분석에 안 쓰인다.
- **CS 화면 필터:** AI는 전체 결과를 준다. CS에게는 Spring이 `classification`, `qa_review.{analysis_level, needs_approval}`, `drafts.customer_reply`, `drafts.service_action`만 넘긴다. `drafts.service_action.basis`에는 원인·설비 이름이 없어 그대로 보여도 된다. 과거 처리 기록이 없으면 `service_action`이 `null`이니 "제안 없음"으로 표시한다.
- **시간 제한:** `classify`는 LLM을 켜면 약 1.3초, `triage`는 0.01초라 10초 제한 안에 들어온다. 분석은 약 10초라 폴링으로 받는다.

**프론트:** 화면은 Spring API만 부른다(`/api/...`). AI 서버 주소나 `/api/ai` 프록시 규칙은 필요 없다. 분석 결과의 모양은 `GET /api/rmas/{rmaId}/analysis`의 `result`가 명세 3-3 `result`와 같으므로 `samples/*.json`의 `analysis.result`를 보고 그리면 된다.

## 5. LangSmith (이상윤)
1. LangSmith에서 프로젝트 `effect-ai`를 만들고 API 키를 발급한다.
2. 서비스 환경(서버의 `.env` 또는 배포 환경 변수)에 `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=effect-ai`를 넣는다. 코드 수정은 필요 없다. 키가 있으면 추적이 자동으로 켜진다(`LANGSMITH_TRACING=false`로 끌 수 있다).
3. 키를 팀원에게 줄 때는 채팅이 아니라 각자 `.env`에 직접 넣도록 전달한다.

**트레이스 구조** (분석 1건 = 트레이스 1개)
```
rma_analysis RMA-08927            태그: mode:B, pcb-v5 / 메타데이터: rma_id, as_of, mode
├─ orchestrator
├─ qa_triage ─ triage_rules
├─ mfg_trace ─ lot_trace
├─ cause_analysis ─ rank_causes, similar_cases, llm_explanations
├─ qa_review ─ causal_requirements, llm_review_note
├─ mfg_improvement ─ escape_analysis, standard_actions, llm_improvement
└─ cs_reply ─ reply_draft, llm_customer_reply
```
- 응답의 `langsmith_run_id`가 이 트레이스의 ID다. 화면이나 로그에서 LangSmith 링크를 만들 때 쓴다(키가 없으면 `null`).
- 필터 예: 메타데이터 `rma_id = RMA-08927`, 태그 `mode:B`.
- 전송이 실패해도 분석은 그대로 끝난다(로그에 경고만 남는다).

**확인해 줄 것**
- 트레이스가 한 분석당 하나로 묶여 보이는지, LLM 호출(`llm_*`)의 토큰·지연이 보이는지
- 팀 플랜에서 보관 기간과 월 트레이스 한도
