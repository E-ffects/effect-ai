"""Predictors plug into the evaluation harness: predict(store, claim) -> Prediction.

Baselines
  A  text similarity: answers of the stored similar_cases (character-bigram overlap of the raw text)
  B  symptom vote: most frequent answers among past cases with the same intake label
Agent
  C  cause analysis agent ranking (agents/cause_analysis.py): symptom vote + same-product recent cases
     + this lot's process / material record
"""
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Prediction:
    causes: list = field(default_factory=list)       # ranked cause codes
    processes: list = field(default_factory=list)    # ranked process ids (MATERIAL / NONE for non-process causes)
    equipment: list = field(default_factory=list)    # ranked equipment ids
    note: str = ""


def _unique(values):
    seen, out = set(), []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


class BaselineA:
    name = "A_text_similarity"
    description = "저장된 similar_cases(원문 글자 2개 단위 겹침) 상위 5건의 확정 답"

    def predict(self, store, claim):
        cases = [store.past_case(r["reference_rma_id"]) for r in store.similar_cases(claim["rma_id"])]
        cases = [c for c in cases if c]
        if not cases:
            return Prediction(note="no decided reference")
        return Prediction(causes=_unique(c.cause_code for c in cases),
                          processes=_unique(c.process for c in cases),
                          equipment=_unique(c.equipment_id for c in cases))


class BaselineB:
    name = "B_symptom_vote"
    description = "같은 접수 소분류의 과거 확정 사례에서 많이 나온 순서"

    def predict(self, store, claim):
        cases = store.past_cases(intake_label=claim["intake_label"]) if claim["intake_label"] else []
        if not cases:
            return Prediction(note="no past case with this label")
        rank = lambda values: [v for v, _ in Counter(v for v in values if v).most_common()]
        return Prediction(causes=rank(c.cause_code for c in cases),
                          processes=rank(c.process for c in cases),
                          equipment=rank(c.equipment_id for c in cases))


class CauseAgentC:
    name = "C_cause_agent"
    description = "원인분석 에이전트 순위: 소분류 투표 + 같은 제품 최근 사례 + 이 Lot의 공정·자재 기록"

    def __init__(self):
        self._conn = None

    def _load(self, conn):
        from .agents.runtime import load_masters
        from .tools.process import ProcessBaseline
        self._conn, self.masters, self.baseline = conn, load_masters(conn), ProcessBaseline(conn)

    def predict(self, store, claim):
        from .agents.cause_analysis import rank_causes
        from .agents.manufacturing import trace
        if self._conn is not store.conn:
            self._load(store.conn)
        candidates = rank_causes(self.masters, store, claim, trace(self.masters, store, self.baseline, claim), top_k=5)
        if not candidates:
            return Prediction(note="no past case with this label")
        return Prediction(causes=[c["cause_code"] for c in candidates],
                          processes=_unique(c["process_id"] for c in candidates),
                          equipment=_unique([c["equipment_id"] for c in candidates]
                                            + [e for c in candidates for e in c["other_equipment"]]))


PREDICTORS = {"A": BaselineA, "B": BaselineB, "C": CauseAgentC}
