# AI 패키지 연결 안내 (FastAPI · LangSmith)

- 대상: 구태우(FastAPI), 이상윤(LangSmith)
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
- **데이터:** `data/pcb_rma_v4.sqlite`가 있어야 한다. git에 없으니 유희범에게 `PCB_RMA_dataset_v4_full.zip`을 받아 안의 `pcb_rma.sqlite`를 이 이름으로 둔다. 다른 위치면 `.env`에 `RMA_DB=경로`.
- **확인:** `python3 -m pytest -q`가 모두 통과하면 준비 끝이다(LLM은 부르지 않는다).

## 2. `.env` 설정
`.env`는 git에 올라가지 않는다. 키를 코드·채팅·Jira에 붙여 넣지 않는다.

| 변수 | 필수 | 값 | 담당 |
|---|---|---|---|
| `RMA_LLM` | 선택 | `openai:gpt-5.4-mini` (기본). `off`면 LLM 없이 템플릿 문장 | — |
| `OPENAI_API_KEY` | LLM 쓸 때 | OpenAI 키 | 팀 키를 각자 `.env`에 |
| `LANGSMITH_API_KEY` | 추적 쓸 때 | LangSmith 키 | 이상윤 |
| `LANGSMITH_PROJECT` | 선택 | `effect-ai` | 이상윤 |
| `RMA_DB` | 선택 | SQLite 경로 (기본 `data/pcb_rma_v4.sqlite`) | — |

- **사용 금지 모델:** gpt-6-astra, gpt-6.1-sol, gpt-6-sol, gpt-5.5-pro. 설정해도 쓰지 않고 템플릿으로 동작한다.
- **키가 없으면:** LLM은 템플릿 문장, LangSmith는 추적 없이 그대로 돈다. 개발 중에는 `RMA_LLM=off`가 빠르다(분석 약 0.6초, LLM 켜면 약 10초).

## 3. FastAPI가 부르는 함수 (구태우)
FastAPI는 `rma_agent`의 함수 4개만 부른다. 에이전트 내부가 바뀌어도 이 함수들의 입출력은 명세대로 유지한다.

| 함수 | API | 반환 | 시간 |
|---|---|---|---|
| `classify(raw_text, detection_stage=None, part_no=None, customer_id=None, as_of=None)` | `POST /classify` | 3-1 응답 dict (`request_id`, `elapsed_ms`는 FastAPI가 채움) | 1~3초 |
| `run_analysis(rma_id, as_of=None, mode="B", top_k=3, on_progress=None)` | `POST /analyses` 작업 안에서 | 3-3 응답 dict (`analysis_id`는 FastAPI가 채움) | LLM 켜면 약 10초 |
| `similar_cases(rma_id, as_of=None, limit=5)` | `GET /claims/{id}/similar` | 3-4 응답 dict | 0.1초 |
| `refresh_cases()` | `POST /cases/refresh` | 사례 수(int) | 0.3초 |

> `classify`는 오늘(11/18) 구현 중이다. 그 전까지는 `NotImplementedError`를 낸다.

### `run_analysis` 쓰는 법
- **동기 함수다.** 요청 스레드에서 기다리지 말고 작업으로 돌린다(스레드 풀 또는 BackgroundTasks). 여러 건을 동시에 돌려도 된다(DB는 읽기 전용, 연결은 스레드 공유 가능).
- **처음 호출 때 한 번** DB·사례 저장소를 불러온다(약 0.3초). 서비스 시작 때 `refresh_cases()`를 한 번 불러 두면 첫 요청이 빠르다.
- **`as_of`를 생략하면** 그 접수의 분석 시작 시각(QA 접수 판정·분류가 끝난 시각)을 쓴다. 응답의 `as_of`에 실제 쓴 값이 들어 있다.
- **진행률:** `on_progress(agent, steps_done, steps_total)`가 단계마다 불린다. 3-3 `RUNNING` 응답의 `progress`에 그대로 넣는다. `agent`는 `ORCHESTRATOR`, `QA`, `MANUFACTURING`, `CAUSE_ANALYSIS`, `CS`.
- **상태:** 반환 dict의 `status`는 `SUCCEEDED` 또는 `PARTIAL`(LLM 장애, 순위·근거는 정상)이다. 예외가 나면 `FAILED`로 기록한다.
- **시간 제한:** 명세는 B안 30초다. 넘으면 FastAPI가 `ANALYSIS_TIMEOUT`(504)으로 처리한다.

### 오류
함수는 `rma_agent.errors`의 예외를 낸다. 각 예외에 명세 4장의 `code`와 `http_status`가 들어 있으니 그대로 응답하면 된다.

| 예외 | `code` | HTTP | 언제 |
|---|---|---|---|
| `InvalidInput` | `INVALID_INPUT` | 400 | `rma_id` 비어 있음, `mode`가 A/B가 아님, `top_k`·`limit` 범위 밖 |
| `ClaimNotFound` | `CLAIM_NOT_FOUND` | 404 | 없는 접수, `as_of`보다 늦게 접수된 건 |
| `AsOfInvalid` | `AS_OF_INVALID` | 422 | `as_of` 형식 오류(시간대 필수, 예 `2025-11-20T14:40:46+09:00`), QA 판정 전이라 아직 분석 불가 |
| `ServiceUnavailable` | `SERVICE_UNAVAILABLE` | 503 | DB 파일을 열 수 없음 |

`ANALYSIS_NOT_FOUND`, `ANALYSIS_IN_PROGRESS`, `ANALYSIS_TIMEOUT`은 작업 관리 쪽이라 FastAPI가 만든다.

### 예시 (뼈대)
```python
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import rma_agent
from rma_agent.errors import RmaAgentError

@asynccontextmanager
async def lifespan(app):
    rma_agent.refresh_cases()  # load DB and case library once at start
    yield


app = FastAPI(lifespan=lifespan)
pool = ThreadPoolExecutor(max_workers=4)
jobs = {}  # analysis_id -> dict (실서비스에서는 Redis 등)


@app.exception_handler(RmaAgentError)
def agent_error(request, exc):
    return JSONResponse(status_code=exc.http_status,
                        content={"error": {"code": exc.code, "message": exc.message, "request_id": str(uuid.uuid4())[:8]}})


@app.post("/analyses", status_code=202)
def create_analysis(body: dict):
    analysis_id = f"ana-{uuid.uuid4().hex[:12]}"
    options = body.get("options", {})
    job = jobs[analysis_id] = {"analysis_id": analysis_id, "rma_id": body["rma_id"], "status": "QUEUED", "result": None,
                               "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}

    def progress(agent, done, total):
        job.update(status="RUNNING", progress={"current_agent": agent, "steps_done": done, "steps_total": total})

    def work():
        try:
            job.update(rma_agent.run_analysis(body["rma_id"], as_of=body.get("as_of"), mode=options.get("mode", "B"),
                                              top_k=options.get("top_k", 3), on_progress=progress))
        except RmaAgentError as exc:
            job.update(status="FAILED", error={"code": exc.code, "message": exc.message})
        except Exception as exc:
            job.update(status="FAILED", error={"code": "INTERNAL_ERROR", "message": str(exc)})

    pool.submit(work)
    return {k: job[k] for k in ("analysis_id", "rma_id", "status", "created_at")} | {"poll_after_ms": 1000}


@app.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str):
    return jobs[analysis_id]  # 없으면 ANALYSIS_NOT_FOUND 처리
```
- `POST /analyses`의 입력 검증(없는 접수 등)을 등록 시점에 하고 싶으면 `rma_agent.similar_cases(rma_id, as_of, limit=1)`을 먼저 불러 보면 된다. 같은 검사를 하고 0.1초면 끝난다.

### 바로 해 볼 값
| rma_id | 내용 |
|---|---|
| `RMA-08743` | 명세 3-3의 예시. 차량 고객 L3, 1순위 외층 에칭 잔동(OLE-02), 유출 ET-02 |
| `RMA-99999` | 없는 접수 → `CLAIM_NOT_FOUND` |
| `RMA-08743` + `as_of="2025-11-20T10:00:00+09:00"` | QA 판정 전 → `AS_OF_INVALID` |

## 4. LangSmith (이상윤)
1. LangSmith에서 프로젝트 `effect-ai`를 만들고 API 키를 발급한다.
2. 서비스 환경(서버의 `.env` 또는 배포 환경 변수)에 `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=effect-ai`를 넣는다. 코드 수정은 필요 없다. 키가 있으면 추적이 자동으로 켜진다(`LANGSMITH_TRACING=false`로 끌 수 있다).
3. 키를 팀원에게 줄 때는 채팅이 아니라 각자 `.env`에 직접 넣도록 전달한다.

**트레이스 구조** (분석 1건 = 트레이스 1개)
```
rma_analysis RMA-08743            태그: mode:B, pcb-v4 / 메타데이터: rma_id, as_of, mode
├─ orchestrator
├─ qa_triage ─ triage_rules
├─ mfg_trace ─ lot_trace
├─ cause_analysis ─ rank_causes, similar_cases, llm_explanations
├─ qa_review ─ causal_requirements, llm_review_note
├─ mfg_improvement ─ escape_analysis, standard_actions, llm_improvement
└─ cs_reply ─ reply_draft, llm_customer_reply
```
- 응답의 `langsmith_run_id`가 이 트레이스의 ID다. 화면이나 로그에서 LangSmith 링크를 만들 때 쓴다(키가 없으면 `null`).
- 필터 예: 메타데이터 `rma_id = RMA-08743`, 태그 `mode:B`.
- 전송이 실패해도 분석은 그대로 끝난다(로그에 경고만 남는다).

**확인해 줄 것**
- 트레이스가 한 분석당 하나로 묶여 보이는지, LLM 호출(`llm_*`)의 토큰·지연이 보이는지
- 팀 플랜에서 보관 기간과 월 트레이스 한도
