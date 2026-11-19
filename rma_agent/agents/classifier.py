"""CS agent at intake: customer text -> symptom subcategory (POST /classify).

Two steps:
  1. Retrieval (no LLM): past intake texts labelled before as_of, character-bigram TF-IDF cosine.
     The k nearest give a score per subcategory -> confidence and alternatives. This alone is the fallback.
  2. LLM: subcategory definitions (inclusion / exclusion rules) + the nearest past examples + the text.
     It picks the subcategory, quotes evidence spans and writes a follow-up question when information is missing.
     Output is checked: the code must exist, spans must appear in the text verbatim.

status: CLASSIFIED, INSUFFICIENT_INFO (ask the customer again), UNCLASSIFIED (outside the 18 subcategories).
"""
import bisect
import json
import math
import re
import threading
import time
from collections import Counter, defaultdict

from pydantic import BaseModel

from . import llm
from ..errors import InvalidInput, check_as_of

MODEL_VERSION = "cs-classifier-0.1"
K = 15
MIN_CONFIDENCE = 0.45          # retrieval-only: below this the text is treated as insufficient
NOT_CLASSIFIED = ("INSUFFICIENT_INFO", "UNCLASSIFIED")
DEFAULT_QUESTION = ("증상 확인을 위해 문의드립니다. 불량 현상(오픈·쇼트·간헐·외관·실장 등), 발생 위치, "
                    "재현 조건(상온 상시 또는 온도·진동 후)을 알려 주십시오.")

_CODE = re.compile(r"\S*\d\S*")  # tokens with digits: P/N, Lot, D/C, quantities


def _bigrams(text):
    text = _CODE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return Counter(text[i:i + 2] for i in range(len(text) - 1))


class IntakeIndex:
    """Past intake texts with their labels, sorted by labelling time, for nearest-neighbour search."""

    def __init__(self, conn):
        rows = conn.execute("""select r.rma_id, r.raw_text, s.intake_label, s.evidence_spans_json, s.created_at
                               from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
                               where s.intake_label is not null order by s.created_at, r.rma_id""").fetchall()
        self.items, self.times = [], []
        df = Counter()
        grams = []
        for r in rows:
            g = _bigrams(r["raw_text"])
            grams.append(g)
            df.update(g.keys())
        n = len(rows)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        for r, g in zip(rows, grams):
            vec = self._weigh(g)
            spans = [s for s in json.loads(r["evidence_spans_json"] or "[]") if s in r["raw_text"]]
            self.items.append({"rma_id": r["rma_id"], "text": r["raw_text"], "label": r["intake_label"],
                               "spans": spans, "vec": vec})
            self.times.append(r["created_at"])

    def _weigh(self, grams):
        vec = {t: c * self.idf.get(t, 1.0) for t, c in grams.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def nearest(self, text, as_of=None, k=K, exclude=None):
        query = self._weigh(_bigrams(text))
        end = bisect.bisect_left(self.times, as_of) if as_of else len(self.items)
        scored = []
        for item in self.items[:end]:
            if item["rma_id"] == exclude:
                continue
            vec = item["vec"]
            small, big = (query, vec) if len(query) < len(vec) else (vec, query)
            sim = sum(v * big.get(t, 0.0) for t, v in small.items())
            scored.append((sim, item))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


def label_scores(neighbours):
    """Similarity-weighted vote per label, normalised to sum 1."""
    scores = defaultdict(float)
    for sim, item in neighbours:
        scores[item["label"]] += sim
    total = sum(scores.values()) or 1.0
    return sorted(((label, s / total) for label, s in scores.items()), key=lambda x: -x[1])


class _Alternative(BaseModel):
    minor_code: str
    confidence: float


class _Decision(BaseModel):
    minor_code: str
    confidence: float
    evidence_spans: list[str]
    alternatives: list[_Alternative]
    follow_up_question: str | None = None


INSTRUCTION = """고객 불량 통보 원문을 소분류 하나로 분류하라.
- minor_code는 아래 소분류 정의의 코드 중 하나다. 포함 규칙과 제외 규칙을 따른다.
- 증상 서술이 없거나 결과만 있으면(기능 불량, NG, 동작 안 함 등) INSUFFICIENT_INFO다. Lot·D/C 누락은 증상 정보 부족이 아니다.
- 증상 서술은 충분한데 어느 정의에도 맞지 않으면 UNCLASSIFIED다.
- evidence_spans: 불량 증상을 서술한 원문 구절만 글자 그대로 복사한다(보통 1개, 많아야 2개). 인사, 제품·Lot·수량, 시료 회송, 요청 문장은 넣지 않는다. 고쳐 쓰지 않는다.
- confidence: 0~1. alternatives: 다음으로 가능성 있는 소분류 최대 2개와 confidence.
- INSUFFICIENT_INFO면 follow_up_question에 고객에게 보낼 재질의 문장을 쓴다(불량 현상, 발생 위치, 재현 조건 등 빠진 정보를 묻는다). 아니면 null.
- 비슷한 과거 접수 예시는 참고용이다. 이 원문의 증상 서술로 판단한다."""


class Classifier:
    def __init__(self, conn, masters):
        self.index = IntakeIndex(conn)
        self.symptoms = masters["symptoms"]
        symbols = {r[0]: r[1] for r in conn.execute("select characteristic_id, symbol from special_characteristic")}
        self.symbol = {code: symbols.get(s["characteristic_id"]) for code, s in self.symptoms.items()}
        self.definitions = [{"code": code, "대분류": s["major_name"], "소분류": s["minor_name"],
                             "포함": s["inclusion_rules"], "제외": s["exclusion_rules"]} for code, s in self.symptoms.items()]

    def classify(self, raw_text, detection_stage=None, part_no=None, customer_id=None, as_of=None, exclude=None):
        started = time.perf_counter()
        if not raw_text or not raw_text.strip():
            raise InvalidInput("raw_text가 비어 있습니다")
        as_of = check_as_of(as_of)
        neighbours = self.index.nearest(raw_text, as_of, exclude=exclude)
        votes = label_scores(neighbours)
        outcome = llm.Outcome()
        decision = self._llm_decision(raw_text, detection_stage, neighbours, votes, outcome)
        if decision is None:
            decision = self._retrieval_decision(raw_text, neighbours, votes)
        out = self._response(decision)
        out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        out["model_version"] = MODEL_VERSION + ("+llm" if outcome.used else "")
        out["warnings"] = ["LLM_UNAVAILABLE"] if outcome.failed else ["LLM_OUTPUT_REJECTED"] if outcome.rejected else []
        return out

    # ---------- decisions
    def _retrieval_decision(self, raw_text, neighbours, votes):
        top, conf = votes[0] if votes else ("INSUFFICIENT_INFO", 0.0)
        if conf < MIN_CONFIDENCE and top not in NOT_CLASSIFIED:
            top = "INSUFFICIENT_INFO"
        spans = []
        for _, item in neighbours:  # a neighbour's evidence span that also appears in this text
            if item["label"] == top:
                spans += [s for s in item["spans"] if s in raw_text]
        alternatives = [{"minor_code": c, "confidence": round(p, 2)} for c, p in votes if c != top and c not in NOT_CLASSIFIED][:2]
        return {"minor_code": top, "confidence": round(conf, 2), "evidence_spans": list(dict.fromkeys(spans))[:2],
                "alternatives": alternatives, "follow_up_question": DEFAULT_QUESTION if top == "INSUFFICIENT_INFO" else None}

    def _llm_decision(self, raw_text, detection_stage, neighbours, votes, outcome):
        examples = []
        for _, item in neighbours[:8]:
            examples.append({"원문": item["text"], "소분류": item["label"]})
        facts = {"원문": raw_text, "검출 단계": detection_stage, "소분류 정의": self.definitions,
                 "비슷한 과거 접수": examples,
                 "과거 접수 기준 후보": [{"code": c, "비율": round(p, 2)} for c, p in votes[:4]]}
        result = llm.write("classify", INSTRUCTION, facts, _Decision, lambda r: [], outcome)
        if not result:
            return None
        if result["minor_code"] not in self.symptoms:
            outcome.rejected = True
            return None
        result["evidence_spans"] = [s for s in result["evidence_spans"] if s and s in raw_text][:2]
        result["alternatives"] = [a for a in result["alternatives"]
                                  if a["minor_code"] in self.symptoms and a["minor_code"] != result["minor_code"]][:2]
        if result["minor_code"] == "INSUFFICIENT_INFO" and not result.get("follow_up_question"):
            result["follow_up_question"] = DEFAULT_QUESTION
        return result

    # ---------- response (docs/api_spec.md 3-1)
    def _response(self, d):
        code = d["minor_code"]
        alternatives = [{"minor_code": a["minor_code"], "minor_name": self.symptoms[a["minor_code"]]["minor_name"],
                         "confidence": round(float(a["confidence"]), 2)} for a in d["alternatives"]]
        if code in NOT_CLASSIFIED:
            majors = {self.symptoms[a["minor_code"]]["major_code"] for a in alternatives}
            major = majors.pop() if len(majors) == 1 else None
            return {"status": code, "major_code": major,
                    "major_name": self.symptoms[alternatives[0]["minor_code"]]["major_name"] if major else None,
                    "minor_code": None, "minor_name": None, "confidence": round(float(d["confidence"]), 2),
                    "evidence_spans": d["evidence_spans"], "severity": None, "characteristic_symbol": None,
                    "alternatives": alternatives,
                    "follow_up_questions": [d["follow_up_question"]] if code == "INSUFFICIENT_INFO" else []}
        s = self.symptoms[code]
        return {"status": "CLASSIFIED", "major_code": s["major_code"], "major_name": s["major_name"],
                "minor_code": code, "minor_name": s["minor_name"], "confidence": round(float(d["confidence"]), 2),
                "evidence_spans": d["evidence_spans"], "severity": s["severity"],
                "characteristic_symbol": self.symbol.get(code), "alternatives": alternatives, "follow_up_questions": []}


_classifier = None
_lock = threading.Lock()


def get_classifier():
    global _classifier
    from .runtime import get_runtime
    with _lock:
        if _classifier is None:
            rt = get_runtime()
            _classifier = Classifier(rt.conn, rt.masters)
        return _classifier
