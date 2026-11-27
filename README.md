# effect-ai

E!ffect 팀 RMA 분석 AI Agent의 AI/DATA 저장소다. 에이전트 5개(CS, 오케스트레이터, QA, 제조, 원인분석)를 LangGraph 그래프로 묶었고, LangSmith로 실행을 추적한다. AI 서버(FastAPI, 구태우)는 `rma_agent`의 함수 5개(`classify`, `triage`, `run_analysis`, `similar_cases`, `refresh_cases`)만 호출한다(`docs/api_spec.md` 1-5). **연결 방법은 `docs/integration_guide.md`.**

## 준비
```bash
pip install -r requirements.txt
```
`data/pcb_rma_v6.sqlite`가 필요하다(없으면 v5·v4 순서로 찾는다). 팀 PostgreSQL을 쓰면 `RMA_DATABASE_URL`만 넣는다. 받는 방법은 `data/README.md`를, 데이터를 만드는 스크립트와 버전별 체크섬은 `datagen/README.md`를 본다. 데이터 파일, `.env`, API 키는 커밋하지 않는다(`.gitignore`에 등록됨).

## 구조
| 파일 | 역할 |
|---|---|
| `rma_agent/db.py` | DB 연결 (읽기 전용) |
| `rma_agent/asof.py` | **시점 통제.** `AsOfStore(conn, as_of)`는 `as_of` 이전에 알 수 있던 데이터만 돌려준다 |
| `rma_agent/__init__.py` | **FastAPI가 부르는 함수:** `classify`, `triage`, `run_analysis`, `analysis_as_of`, `similar_cases`, `refresh_cases`, `pattern_priority`, `response_priority`, `run_follow_up`, `chat` |
| `rma_agent/rag.py` · `rma_agent/agents/chat.py` | 에이전트 패널 질문(`POST /ai/v1/chat`): 화면 문맥 + RAG 검색(과거 사례·기준 문서, 임베딩+글자 혼합, 시점·역할 제한) |
| `server/app.py` | 참고 AI 서버(FastAPI, `/ai/v1`, 서비스 토큰 검증). 실행법은 `docs/integration_guide.md` 3장 |
| `rma_agent/agents/graph.py` | LangGraph 그래프(B안 흐름), 실행 기록·진행률, LangSmith 연결 |
| `rma_agent/agents/cs.py` · `qa.py` · `manufacturing.py` · `cause_analysis.py` | 에이전트별 로직 |
| `rma_agent/agents/classifier.py` | CS 접수 분류: 과거 접수 검색 + LLM(소분류 정의·예시) |
| `rma_agent/agents/runtime.py` | 공유 자원(DB, 사례 저장소, 공정 기준선, 기준 테이블). 한 번만 불러온다 |
| `rma_agent/tools/process.py` · `inspection.py` | 공정 신호(조건 이탈·σ·설비 이벤트), 검사 장비 검출률 |
| `rma_agent/ranker.py` · `models/` | 원인 후보 학습 재정렬(선택). 모델은 계수만 담은 JSON |
| `rma_agent/predictors.py` | 예측기. 기준선 A(글자 유사도), B(같은 소분류의 과거 원인), C(원인분석 에이전트) |
| `rma_agent/truth.py` | 평가용 정답. **예측기에서 import 금지** |
| `rma_agent/evaluate_classify.py` | CS 분류 정확도 평가 (`python3 -m rma_agent.evaluate_classify --llm 114`) |
| `rma_agent/evaluate.py` | 접수 건을 시간 순서대로 넣고 원인·공정·장비 적중률을 계산 |
| `tests/` | 미래 데이터가 새지 않는지(`test_asof.py`), 그래프 출력이 명세대로인지(`test_agents.py`), 샘플이 실제 출력과 같은지(`test_contract.py`), 서버가 Spring 흐름대로 도는지(`test_server.py`) |
| `results/` | 평가 결과 기록 (날짜별 md, json) |

## 규칙
1. **에이전트와 도구는 `AsOfStore`로만 데이터를 읽는다.** DB에 직접 쿼리하지 않는다.
2. 분석 시점(`as_of`)은 `analysis_start(conn, rma_id)`: QA 접수 판정이 끝나고 접수 소분류가 나온 시각이다.
3. 각 데이터가 "알려진" 시각의 기준은 `asof.py` 맨 위 설명을 따른다. 설비 이벤트는 발생 시각이 아니라 **기록 시각** 기준이다.
4. 과거 사례는 **원인 판정 승인 + 반품별 적용**이 모두 끝난 것만 쓴다.

## 실행
```bash
python3 -m pytest -q tests
```
```python
from rma_agent import run_analysis
out = run_analysis("RMA-08927")   # as_of 생략 시 분석 시작 시각. 반환 형식은 docs/api_spec.md 3-3
```
```bash
python3 -m rma_agent.evaluate -p A B
```
```bash
python3 -m rma_agent.evaluate -p B --start 2026-07-01 --limit 300 --no-save
```

## 에이전트 흐름 (B안)
```
orchestrator ─┬─ QA: 분석 수준 ────┐
              └─ 제조: Lot 추적 ───┴─▶ 원인분석 ─▶ QA: 근거 검토 ─▶ 제조: 개선안 ─▶ CS: 회신 초안
```
- 순위는 점수식, 근거 검토는 규칙(`qa_causal_requirement`)이다. **LLM은 문장만** 쓴다: 원인 설명(원인분석), 검토 의견(QA), 개선안(제조), 고객 회신(CS).
- LLM에는 도구 결과(사실 JSON)만 주고, 출력에 사실에 없는 숫자가 있으면 버리고 템플릿 문장을 쓴다(`LLM_OUTPUT_REJECTED`).
- LLM이 꺼져 있거나 키가 없으면 템플릿 문장으로 동작한다. 호출이 실패하면 상태가 `PARTIAL`, 경고가 `LLM_UNAVAILABLE`이고 순위·근거는 그대로다.
- 원인 점수 = 0.40 × 같은 소분류 투표 + 0.35 × 같은 제품 최근 60일 사례 + 0.25 × 이 Lot의 공정·자재 신호.
- 학습 재정렬(선택, 기본 꺼짐): `RMA_CAUSE_RANKER=models/cause_ranker_v6.json`이면 위 점수의 상위 3개 순서를 로지스틱 회귀(`rma_agent/ranker.py`)가 다시 정한다. 2026-10월 이후 원인@1 78.2% → 85.0%, 장비@1 89.7% → 97.9%, 원인@3 그대로(`results/20261120_cause_ranker_agent_v5.md`). 다시 학습: `python3 -m rma_agent.ranker train`.
- 장비는 이 Lot이 실제로 지난 장비 중 신호가 가장 강한 것이다. 같은 공정을 여러 장비로 나눠 지났으면 나머지는 `other_equipment`에 둔다.

## 설정
`.env.example`을 `.env`로 복사해 채운다(`.env`는 커밋되지 않는다).
- `RMA_LLM`: `provider:model`. 기본 `openai:gpt-5.4-mini`, `off`면 템플릿만 쓴다. 사용 금지 모델(gpt-6-astra, gpt-6.1-sol, gpt-6-sol, gpt-5.5-pro)은 설정해도 쓰지 않는다(`llm.BLOCKED_MODELS`).
- 모델 비교(RMA-08743, 2026-11-18): gpt-4.1-mini는 근거 판정을 틀리게 옮겨 제외, gpt-5.4-mini(약 10초)와 gpt-5.5(약 13초)는 정확. 시연 때 문장 품질을 더 원하면 `openai:gpt-5.5`. Gemini는 `google_genai:모델명`(패키지 `langchain-google-genai`, 키 `GOOGLE_API_KEY`).
- `OPENAI_API_KEY`: OpenAI 키. 테스트는 LLM을 부르지 않는다(`tests/conftest.py`에서 `RMA_LLM=off`).

## LangSmith
`LANGSMITH_API_KEY`(필수), `LANGSMITH_PROJECT`(선택)를 환경 변수로 넣으면 추적이 켜진다. 그래프와 도구 호출이 한 트레이스로 남고, 응답의 `langsmith_run_id`가 그 ID다. 키가 없으면 추적 없이 그대로 실행된다.

## 예측기 추가하는 법
`predictors.py`에 `predict(store, claim) -> Prediction`을 가진 클래스를 만들고 `PREDICTORS`에 등록한다.
- `Prediction.causes / processes / equipment`: 순위가 매겨진 후보 목록
- 공정 후보에서 자재 원인은 `MATERIAL`, 판정 불가 원인은 `NONE`으로 쓴다.

## 평가 범위
**알려진 원인만 평가한다.** 분석 시작 시각 이전에 같은 원인이 이미 승인된 반품만 점수를 매기고, 처음 보는 원인의 반품은 범위 밖이라 제외한다.

## 현재 결과 (2026-11-19 v5, 2026-11-27 v6에서 같은 값 확인, 2026-10-01 이후 접수 중 알려진 원인 2,537건)
| 예측기 | 원인@1 | 원인@3 | 공정@1 | 공정@3 | 장비@1 | 장비@3 |
|---|---:|---:|---:|---:|---:|---:|
| A 글자 유사도 | 56.0% | 76.4% | 58.5% | 78.8% | 52.3% | 67.6% |
| B 같은 소분류의 과거 원인 | 52.7% | 91.2% | 56.8% | 93.6% | 51.3% | 76.7% |
| **C 원인분석 에이전트** | **78.2%** | **97.8%** | **80.3%** | **98.5%** | **89.7%** | **99.7%** |

- v5는 재발 사건, 헛신호, 실제 같은 고객 문장을 넣어 v4보다 어렵다. 같은 에이전트가 v4에서는 원인@1 85.5%, 원인@3 97.5%였다(`results/20261118-1303_A-B-C.md`). 원인@3은 v5에서도 그대로다.
- 같은 사건 후속(1,920건) 원인@1 82.9%, **다른 사건 같은 원인(617건) 63.5%**. 처음 겪는 사건에 대한 실력은 뒤쪽에 가깝다.
- 재발 사건별 원인@1: P04R 90.6%, P05R 78.7%, P12R 70.0%, P09R(자재, 신호 없음) 12.5%. 헛신호가 걸친 Lot은 80.2%로 끌려가지 않았다.
- 사건성 원인(과거 사례가 특정 장비·자재 Lot을 가리킴)인데 이 Lot에 신호도, 같은 장비·자재도 없으면 점수를 깎는다(0.8, 같은 장비만 지났으면 0.5). 값은 2026-07~09에서 고르고 2026-10 이후에 그대로 적용했다. 다른 사건 원인@1이 54.7% → 69.0%로 올랐다.
- 약한 곳: 불량 미재현(ELEC04·DIM01, 분석 시작 시점엔 반송품 시험 결과가 없음), 오픈(ELEC01, 취급 스크래치와 재검 오처리처럼 단발 원인끼리 구분이 어려움), 정보 부족(INSUFFICIENT_INFO). 자세한 결과는 `results/`를 본다.

## 재발 판정과 후보 Lot (2026-11-19, v5)
- **재발 판정** (`result.recurrence_check`, QA 5단계): 1순위 원인에 시행된 발생 원인 조치와 Lot 생산 시점을 비교해 `RECURRENCE`, `PRE_ACTION_PRODUCT`, `PENDING_CAUSE_REVIEW`, `NOT_APPLICABLE` 중 하나를 낸다. 정답 `recurrence_review` 2,606건과 일치 90.1%, 재발 재현율 82.0%, 검토 대상이 아닌 반품까지 포함한 재발 정밀도 73.2%(분석 시점에 원인이 알려진 반품만 보면 81.1%) (`scripts/eval_recurrence.py`, `results/20261119_recurrence_v5.md`). 원인분석이 틀리면 재발 판정도 틀리므로, 1순위 원인이 이 Lot에 미친 근거(원인 공정의 조건 이탈, 조치하지 않은 설비의 이상, 자재)가 없으면 `PENDING_CAUSE_REVIEW`로 보낸다. 규칙은 2026-07~09에서 고르고 10월 이후로 확인했다.
- **후보 Lot** (`result.candidate_lots`, 제조 6단계): 반품 Lot과 같은 생산 설비를 앞뒤 24시간 안에 썼거나 같은 자재 Lot을 쓴 다른 Lot, 공통점, 출하 여부, 반품 수. 적중률은 재지 않는다.

## CS 분류 결과 (2026-11-18, 2026-10-01 이후 접수 2,953건, 정답은 접수 시 CS 소분류)
| 방식 | 정확도 | 정보 부족 판정 정밀도 / 재현율 |
|---|---:|---:|
| 과거 접수 검색만 (LLM 없음, 전체) | 95.2% | 43.4% / 93.2% |
| **검색 + LLM gpt-5.4-mini** (소분류별 6건 표본 114건) | **100.0%** | 100% / 100% |

- 검색만 쓰면 처음 보는 표현(MARK01 라벨 불일치, SOLD03 패드 들뜸)을 정보 부족으로 잘못 판정해 불필요한 재질의가 생긴다. LLM이 소분류 정의를 읽고 바로잡는다.
- 접수 문장이 정형화된 합성 데이터라 실제보다 쉽다. LLM 표본은 114건이다.
