# effect-ai

E!ffect 팀 RMA 분석 AI Agent의 AI/DATA 저장소다. 에이전트 5개(CS, 오케스트레이터, QA, 제조, 원인분석)를 LangGraph 그래프로 묶었고, LangSmith로 실행을 추적한다. FastAPI(구태우)는 `rma_agent`의 함수 4개만 호출한다(`docs/api_spec.md` 1-5).

## 준비
```bash
pip install -r requirements.txt
```
`data/pcb_rma_v4.sqlite`가 필요하다(없으면 v3). 받는 방법은 `data/README.md`를 본다. 데이터 파일, `.env`, API 키는 커밋하지 않는다(`.gitignore`에 등록됨).

## 구조
| 파일 | 역할 |
|---|---|
| `rma_agent/db.py` | DB 연결 (읽기 전용) |
| `rma_agent/asof.py` | **시점 통제.** `AsOfStore(conn, as_of)`는 `as_of` 이전에 알 수 있던 데이터만 돌려준다 |
| `rma_agent/__init__.py` | **FastAPI가 부르는 함수:** `run_analysis`, `similar_cases`, `refresh_cases`, `classify`(다음 단계) |
| `rma_agent/agents/graph.py` | LangGraph 그래프(B안 흐름), 실행 기록·진행률, LangSmith 연결 |
| `rma_agent/agents/cs.py` · `qa.py` · `manufacturing.py` · `cause_analysis.py` | 에이전트별 로직 |
| `rma_agent/agents/runtime.py` | 공유 자원(DB, 사례 저장소, 공정 기준선, 기준 테이블). 한 번만 불러온다 |
| `rma_agent/tools/process.py` · `inspection.py` | 공정 신호(조건 이탈·σ·설비 이벤트), 검사 장비 검출률 |
| `rma_agent/predictors.py` | 예측기. 기준선 A(글자 유사도), B(같은 소분류의 과거 원인), C(원인분석 에이전트) |
| `rma_agent/truth.py` | 평가용 정답. **예측기에서 import 금지** |
| `rma_agent/evaluate.py` | 접수 건을 시간 순서대로 넣고 원인·공정·장비 적중률을 계산 |
| `tests/` | 미래 데이터가 새지 않는지(`test_asof.py`), 그래프 출력이 명세대로인지(`test_agents.py`) |
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
out = run_analysis("RMA-08743")   # as_of 생략 시 분석 시작 시각. 반환 형식은 docs/api_spec.md 3-3
```
```bash
python3 -m rma_agent.evaluate -p A B
```
```bash
python3 -m rma_agent.evaluate -p B --start 2025-07-01 --limit 300 --no-save
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

## 현재 결과 (2026-11-18, v4 데이터, 2025-10-01 이후 접수 중 알려진 원인 1,907건)
| 예측기 | 원인@1 | 원인@3 | 공정@1 | 공정@3 | 장비@1 | 장비@3 |
|---|---:|---:|---:|---:|---:|---:|
| A 글자 유사도 | 55.0% | 73.3% | 57.4% | 76.1% | 77.2% | 87.3% |
| B 같은 소분류의 과거 원인 | 51.4% | 88.4% | 64.6% | 93.3% | 78.4% | 93.8% |
| **C 원인분석 에이전트** | **79.1%** | **96.4%** | **82.8%** | **97.5%** | **94.5%** | **99.2%** |

- C는 블리스터(STRC01) 원인@3을 45.4% → 95.0%, 오픈(ELEC01)을 80.6% → 87.6%로 올렸다.
- 약한 곳: **다른 사건 같은 원인**(원인@1 54.7%)과 정보 부족(INSUFFICIENT_INFO). 자세한 결과는 `results/`를 본다.
