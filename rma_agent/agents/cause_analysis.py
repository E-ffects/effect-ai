"""Cause analysis agent: rank cause candidates from past approved cases and the lot's production record.

Score of a cause code (0~1, for ranking within one answer, not a probability):
  0.40 x vote    share among past cases with the same intake label (relative to the most frequent cause)
  0.35 x recent  same product (spec) cases with this cause in the last RECENT_DAYS (relative to the most frequent)
  0.25 x signal  the lot's own record at the cause's process: strong 1.0 / medium 0.5,
                 or for material causes a material lot shared with past cases of this cause
Systemic causes the lot shows no trace of are discounted: x0.5 when it used the same machine (process causes),
x0.2 otherwise (material causes: x0.2 unless a problem lot is shared; same-supplier lots are shown, not scored).
Equipment is the one this lot actually went through (the step with the strongest signal when the lot passed the
process on several machines; the others are listed in other_equipment), given only when the lot shows a signal there or the
cause usually has a specific equipment in past cases (otherwise null = sporadic, not attributable).
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from langsmith import traceable
from pydantic import BaseModel

from . import llm

from ..asof import process_token
from ..tools.inspection import tested_at
from ..tools.names import certainty_name, event_name, item_name

RECENT_DAYS = 60
W_VOTE, W_RECENT, W_SIGNAL = 0.40, 0.35, 0.25
SIGNAL_SCORE = {"strong": 1.0, "medium": 0.5, "none": 0.0}
# Systemic causes (past cases usually name an equipment, or a material lot) should show up in this lot's record.
# Without a signal at that process the score is cut, more when the lot did not pass any equipment seen in past cases.
SYSTEMIC_SHARE = 0.5
PENALTY_NO_MATCH = 0.8          # tuned on 2025-07~09, checked on 2025-10~ (results/)
PENALTY_MATCH_NO_SIGNAL = 0.5

_hide = lambda inputs: {k: v for k, v in inputs.items() if k not in ("masters", "store")}


def _shift(ts, days):
    return (datetime.fromisoformat(ts) + timedelta(days=days)).isoformat(timespec="seconds")


def _day(ts):
    return ts[:10] if ts else ""


def _step_evidence(step, process_name):
    out = []
    if step["out_of_spec"] or step["z_by_item"]:
        item, z = max(step["z_by_item"].items(), key=lambda kv: abs(kv[1]), default=(None, 0.0))
        parts = []
        if step["out_of_spec"]:
            parts.append(f"규격 이탈 {step['out_of_spec']}건")
        if item and abs(z) >= 3:
            parts.append(f"{item_name(item)} {z:+.1f}σ (직전 90일 대비)")
        if parts:
            out.append({"type": "PROCESS_SIGNAL",
                        "summary": f"{process_name} · {step['equipment_id']} " + ", ".join(parts),
                        "ref_table": "process_run", "ref_id": f"{step['process_id']}@{step['equipment_id']}",
                        "occurred_at": step["started_at"]})
    for e in step["abnormal_events"]:
        out.append({"type": "EQUIPMENT_EVENT", "summary": f"{step['equipment_id']} {event_name(e['event_type'])}: {e['description']}",
                    "ref_table": "equipment_event", "ref_id": f"{e['event_type']}@{step['equipment_id']}",
                    "occurred_at": e["occurred_at"]})
    return out


@traceable(name="rank_causes", run_type="tool", process_inputs=_hide)
def rank_causes(masters, store, claim, lot_trace, top_k=3):
    """Ranked cause candidates with evidence and a template explanation. [] when no past case has this label."""
    label = claim.get("intake_label")
    cases = store.past_cases(intake_label=label) if label else []
    if not cases:
        return []
    minor = masters["symptoms"].get(label, {}).get("minor_name", label)
    by_cause = defaultdict(list)
    for case in cases:
        by_cause[case.cause_code].append(case)
    n_max = max(len(group) for group in by_cause.values())
    since = _shift(store.as_of, -RECENT_DAYS)
    recent_by_cause = {code: [c for c in group if c.applied_at >= since and c.spec_id == claim.get("spec_id")]
                       for code, group in by_cause.items()}
    recent_max = max(len(v) for v in recent_by_cause.values()) or 1
    steps = defaultdict(list)  # a lot can pass one process on several machines (loads, rework)
    for step in lot_trace["steps"]:
        steps[step["process_id"]].append(step)
    lot_materials = {m["material_lot_id"]: m for m in lot_trace["materials"]}

    candidates = []
    for code, group in by_cause.items():
        master = masters["causes"].get(code, {})
        category = master.get("cause_category") or group[-1].cause_category
        process_id = master.get("process_id")
        process_name = masters["processes"].get(process_id, {}).get("process_name", process_id)
        cause_name = master.get("cause_name", code)
        recent = recent_by_cause[code]
        with_equipment = sum(1 for c in group if c.equipment_id) / len(group)
        vote, recent_score = len(group) / n_max, len(recent) / recent_max

        signal, equipment, other_equipment, evidence, sentences, fit = 0.0, None, [], [], [], 1.0
        if recent:
            last = max(recent, key=lambda c: c.applied_at)
            evidence.append({"type": "SIMILAR_CASE",
                             "summary": f"{last.rma_id} ({_day(last.opened_at)}, 같은 제품) → {cause_name}, "
                                        f"{last.equipment_id or '장비 특정 불가'}, {certainty_name(last.certainty)}",
                             "ref_table": "rma_claim", "ref_id": last.rma_id, "occurred_at": last.opened_at})
            sentences.append(f"같은 제품({claim.get('spec_id')})의 {minor} 반품이 최근 {RECENT_DAYS}일 동안 "
                             f"{len(recent)}건 {cause_name}(으)로 확정됐습니다.")
        evidence.append({"type": "SIMILAR_CASE",
                         "summary": f"과거 {minor} 반품 {len(cases)}건 중 {len(group)}건이 {cause_name}",
                         "ref_table": "cause_assessment", "ref_id": code, "occurred_at": None})
        if not recent:
            sentences.append(f"과거 {minor} 반품 {len(cases)}건 중 {len(group)}건이 이 원인으로 확정됐습니다.")

        if category == "MATERIAL":
            past_lots = {m for c in group for m in c.material_lot_ids}
            shared = [m for m in lot_materials if m in past_lots]
            if shared:
                signal = 1.0
                for m in shared:
                    evidence.append({"type": "MATERIAL",
                                     "summary": f"자재 Lot {m} ({lot_materials[m]['material_type']}): 과거 {cause_name} 확정 사례와 같은 Lot",
                                     "ref_table": "material_lot", "ref_id": m, "occurred_at": lot_materials[m]["issued_at"]})
                sentences.append(f"이 Lot에 쓰인 자재 Lot {', '.join(shared)}이(가) 과거 확정 사례와 같습니다.")
            elif past_lots and sum(1 for c in group if c.material_lot_ids) / len(group) >= SYSTEMIC_SHARE:
                fit = 1 - PENALTY_NO_MATCH  # past cases name problem material lots; this lot used none of them
                # Same supplier and material type, other lot: shown for QA (a supplier problem can spread over its
                # lots) but not scored. Scoring it at x0.5 did not help on 2025-07~09 (results/v5_material_supplier.md).
                past_sources = set(store.material_lot_sources(past_lots).values())
                same_source = [m for m, row in lot_materials.items()
                               if (row["supplier_id"], row["material_type"]) in past_sources]
                for m in same_source:
                    row = lot_materials[m]
                    evidence.append({"type": "SUPPLIER",
                                     "summary": f"자재 Lot {m} ({row['material_type']}): 과거 {cause_name} 확정 사례와 "
                                                f"같은 공급사({row['supplier_id']})의 다른 Lot",
                                     "ref_table": "material_lot", "ref_id": m, "occurred_at": row["issued_at"]})
                if same_source:
                    suppliers = ", ".join(sorted({lot_materials[m]["supplier_id"] for m in same_source}))
                    sentences.append(f"과거 사례의 문제 자재 Lot을 이 Lot은 쓰지 않았지만, 자재 Lot {', '.join(same_source)}은(는) "
                                     f"같은 공급사({suppliers})의 다른 Lot입니다.")
                else:
                    sentences.append("과거 사례의 문제 자재 Lot을 이 Lot은 쓰지 않았습니다.")
        elif category == "INSPECTION" and process_id:
            tested = tested_at(lot_trace, process_id, masters["equipment_process"])
            if tested and with_equipment >= 0.5:
                equipment = max(tested, key=lambda eq: tested[eq][1])
        elif process_id and process_id in steps:
            past_equipment = Counter(c.equipment_id for c in group if c.equipment_id)
            ordered = sorted(steps[process_id], key=lambda st: (-SIGNAL_SCORE[st["signal"]], -st["max_abs_z"],
                                                                 -past_equipment[st["equipment_id"]], st["started_at"]))
            step = ordered[0]
            alternates = [st["equipment_id"] for st in ordered[1:] if st["equipment_id"] != step["equipment_id"]]
            signal = SIGNAL_SCORE[step["signal"]]
            step_evidence = _step_evidence(step, process_name)
            evidence += step_evidence
            if signal or with_equipment >= 0.5:
                equipment = step["equipment_id"]
                other_equipment = list(dict.fromkeys(alternates))
            if step_evidence:
                sentences.append(f"이 Lot은 {_day(step['started_at'])} {step['equipment_id']}에서 {process_name} 공정을 거쳤고, "
                                 + "; ".join(e["summary"] for e in step_evidence) + ".")
            else:
                sentences.append(f"이 Lot의 {process_name} 공정({step['equipment_id']}) 기록에는 이상 신호가 없습니다.")
            if with_equipment >= SYSTEMIC_SHARE and not signal:
                if {st["equipment_id"] for st in steps[process_id]} & set(past_equipment):
                    fit = 1 - PENALTY_MATCH_NO_SIGNAL
                else:
                    fit = 1 - PENALTY_NO_MATCH
                    sentences.append(f"과거 사례의 장비({', '.join(sorted(past_equipment))})를 이 Lot은 거치지 않았습니다.")

        candidates.append({
            "cause_code": code, "cause_name": cause_name, "cause_category": category,
            "process_id": process_token(process_id, category), "process_name": process_name,
            "equipment_id": equipment,
            "score": round((W_VOTE * vote + W_RECENT * recent_score + W_SIGNAL * signal) * fit, 2),
            "explanation": " ".join(sentences), "evidence": evidence,
            "other_equipment": other_equipment, "_vote": len(group), "_investigations": sorted({c.investigation_id for c in group}),
        })
    candidates.sort(key=lambda c: (-c["score"], -c["_vote"], c["cause_code"]))
    for rank, c in enumerate(candidates, 1):
        c["rank"] = rank
        c["status"], c["source"] = "PROPOSED", "AI"
    return candidates[:top_k]


_GRAMS = {}


def _text_bigrams(rma_id, text):
    """Character bigrams of a claim text (letters and digits, no spaces). Cached by claim: texts do not change."""
    grams = _GRAMS.get(rma_id)
    if grams is None:
        cleaned = "".join(ch for ch in text or "" if ch.isalnum() or ch == " ")
        grams = _GRAMS[rma_id] = frozenset(cleaned[i:i + 2] for i in range(len(cleaned) - 1) if " " not in cleaned[i:i + 2])
    return grams


@traceable(name="similar_cases", run_type="tool", process_inputs=_hide)
def similar_case_items(masters, store, claim, limit=5):
    """Past approved cases for the screen: the same intake label first, ranked by text similarity (character-bigram
    Jaccard of the whole intake text), most recent first on ties. Without a symptom label every past case is a
    candidate. Filtering by label keeps boilerplate (greetings, requests, "Lot unknown") from pulling in cases of
    another symptom: same cause and label in the top 5 rose from 85.5% to 91.9% on 2025-10~ (chosen on 2025-08~09,
    results/20261119_similar_cases_method.md)."""
    label = claim.get("intake_label")
    labelled = label and label not in ("INSUFFICIENT_INFO", "UNCLASSIFIED")
    pool = store.past_cases(intake_label=label) if labelled else store.past_cases()
    query = _text_bigrams(claim["rma_id"], claim.get("raw_text"))
    scored = []
    for order, case in enumerate(pool):
        grams = _text_bigrams(case.rma_id, case.raw_text)
        union = len(query | grams)
        scored.append((len(query & grams) / union if union else 0.0, order, case))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    chosen = [(case, round(score, 2)) for score, _, case in scored[:limit]]
    chosen = chosen[:limit]
    actions = defaultdict(list)
    for a in store.corrective_actions({c.investigation_id for c, _ in chosen}):
        actions[a["investigation_id"]].append({"category": a["action_category"], "description": a["description"],
                                              "implemented_at": a["implemented_at"]})
    return [{"rma_id": c.rma_id, "opened_at": c.opened_at, "minor_code": c.intake_label, "cause_code": c.cause_code,
             "process_id": c.process, "equipment_id": c.equipment_id, "certainty": c.certainty, "similarity": score,
             "snippet": c.raw_text[:60] + ("…" if len(c.raw_text) > 60 else ""),
             "corrective_actions": actions.get(c.investigation_id, [])} for c, score in chosen]


class _CauseExplanation(BaseModel):
    cause_code: str
    explanation: str


class _Explanations(BaseModel):
    items: list[_CauseExplanation]


EXPLAIN = """원인 후보마다 설명을 2~3문장으로 써라.
- 이 후보가 이 순위인 이유를 근거(같은 제품 최근 사례, 과거 같은 불량 사례 수, 이 Lot의 공정 신호·설비 이벤트·자재)로 쓴다.
- 근거가 약하거나 없는 부분(예: 이 Lot 공정 기록에 이상 없음)도 쓴다.
- items에 후보마다 cause_code와 explanation을 넣는다."""


def explain_with_llm(candidates, claim, classification, outcome):
    """Replace template explanations with LLM sentences (same facts). Keeps templates on failure."""
    if not candidates:
        return
    facts = {"반품": {"고객": claim["customer_id"], "제품": claim.get("reported_part_no"), "Lot": claim.get("lot_id"),
                    "불량 형태": classification.get("minor_name"), "원문 근거": classification.get("evidence_spans")},
             "원인 후보": [{"rank": c["rank"], "cause_code": c["cause_code"], "원인": c["cause_name"],
                        "공정": c["process_name"], "장비": c["equipment_id"] or "특정 불가", "점수": c["score"],
                        "근거": [e["summary"] for e in c["evidence"]]} for c in candidates]}
    result = llm.write("explanations", EXPLAIN, facts, _Explanations,
                       lambda r: [i["explanation"] for i in r["items"]], outcome)
    if result:
        by_code = {i["cause_code"]: i["explanation"] for i in result["items"]}
        for c in candidates:
            c["explanation"] = by_code.get(c["cause_code"], c["explanation"])
