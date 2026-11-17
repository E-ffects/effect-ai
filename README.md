# effect-ai

E!ffect 팀 RMA 분석 AI Agent의 AI/DATA 저장소다. 지금은 `rma_agent`(시점 통제 데이터 접근과 평가 틀)만 있고, 에이전트·도구·FastAPI는 이 위에 붙인다.

## 준비
```bash
pip install -r requirements.txt
```
`data/pcb_rma_v3.sqlite`가 필요하다. 받는 방법은 `data/README.md`를 본다. 데이터 파일, `.env`, API 키는 커밋하지 않는다(`.gitignore`에 등록됨).

## 구조
| 파일 | 역할 |
|---|---|
| `rma_agent/db.py` | DB 연결 (읽기 전용) |
| `rma_agent/asof.py` | **시점 통제.** `AsOfStore(conn, as_of)`는 `as_of` 이전에 알 수 있던 데이터만 돌려준다 |
| `rma_agent/predictors.py` | 예측기. 기준선 A(글자 유사도), B(같은 소분류의 과거 원인) |
| `rma_agent/truth.py` | 평가용 정답. **예측기에서 import 금지** |
| `rma_agent/evaluate.py` | 접수 건을 시간 순서대로 넣고 원인·공정·장비 적중률을 계산 |
| `tests/test_asof.py` | 미래 데이터가 새지 않는지 확인하는 테스트 |
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
```bash
python3 -m rma_agent.evaluate -p A B
```
```bash
python3 -m rma_agent.evaluate -p B --start 2025-07-01 --limit 300 --no-save
```

## 예측기 추가하는 법
`predictors.py`에 `predict(store, claim) -> Prediction`을 가진 클래스를 만들고 `PREDICTORS`에 등록한다.
- `Prediction.causes / processes / equipment`: 순위가 매겨진 후보 목록
- `Prediction.handoff`: 근거가 부족해 QA로 넘길 때 True
- 공정 후보에서 자재 원인은 `MATERIAL`, 판정 불가 원인은 `NONE`으로 쓴다.

## 현재 기준선 (2026-11-17, 2025-10-01 이후 접수 2,953건)
| 예측기 | 원인@3 | 공정@3 | 장비@3 | 처음 보는 원인 → QA |
|---|---:|---:|---:|---:|
| A 글자 유사도 | 73.3% | 76.1% | 87.3% | 16.9% |
| B 같은 소분류의 과거 원인 | **88.4%** | **93.3%** | **93.8%** | 25.3% |

목표는 B를 넘고, 처음 보는 원인을 QA로 넘기는 비율을 크게 올리는 것이다. 자세한 결과는 `results/`를 본다.
