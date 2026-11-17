"""Predictors plug into the evaluation harness: predict(store, claim) -> Prediction.

Baselines
  A  text similarity: answers of the stored similar_cases (character-bigram overlap of the raw text)
  B  symptom vote: most frequent answers among past cases with the same intake label
"""
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Prediction:
    causes: list = field(default_factory=list)       # ranked cause codes
    processes: list = field(default_factory=list)    # ranked process ids (MATERIAL / NONE for non-process causes)
    equipment: list = field(default_factory=list)    # ranked equipment ids
    handoff: bool = False                            # True: no reliable past case, send to QA
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
            return Prediction(handoff=True, note="no decided reference")
        return Prediction(causes=_unique(c.cause_code for c in cases),
                          processes=_unique(c.process for c in cases),
                          equipment=_unique(c.equipment_id for c in cases))


class BaselineB:
    name = "B_symptom_vote"
    description = "같은 접수 소분류의 과거 확정 사례에서 많이 나온 순서"

    def predict(self, store, claim):
        cases = store.past_cases(intake_label=claim["intake_label"]) if claim["intake_label"] else []
        if not cases:
            return Prediction(handoff=True, note="no past case with this label")
        rank = lambda values: [v for v, _ in Counter(v for v in values if v).most_common()]
        return Prediction(causes=rank(c.cause_code for c in cases),
                          processes=rank(c.process for c in cases),
                          equipment=rank(c.equipment_id for c in cases))


PREDICTORS = {"A": BaselineA, "B": BaselineB}
