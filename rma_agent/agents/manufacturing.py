"""Manufacturing agent: follow the returned lot through process and inspection; draft improvements later.

trace()        before cause analysis: process route with signals, inspection equipment, material lots
escapes()      after cause analysis: why the defect was not caught (past escape findings for the top cause +
               this lot's inspection equipment and its detection rate versus peers)
improvement()  standard actions for the top cause (process) and the top escape (inspection criteria)
"""
from collections import Counter
from datetime import datetime, timedelta

from langsmith import traceable
from pydantic import BaseModel

from . import llm

from ..tools.inspection import PHASE_OF, detection_rates, low_detection, lot_inspections, test_item_for
from ..tools.process import EVENT_LOOKBACK_DAYS, process_signals

_hide = lambda inputs: {k: v for k, v in inputs.items() if k not in ("masters", "store", "baseline")}


@traceable(name="lot_trace", run_type="tool", process_inputs=_hide)
def trace(masters, store, baseline, claim):
    lot_id = claim.get("lot_id")
    if not lot_id:
        return {"lot_id": None, "steps": [], "inspections": {}, "materials": []}
    return {"lot_id": lot_id, "steps": process_signals(store, baseline, lot_id),
            "inspections": lot_inspections(store, lot_id), "materials": store.material_usage(lot_id)}


@traceable(name="escape_analysis", run_type="tool", process_inputs=_hide)
def escapes(masters, store, claim, lot_trace, candidates, limit=1):
    if not candidates:
        return []
    found = Counter(e["escape_code"] for e in store.escapes(candidates[0]["_investigations"]))
    minor = masters["symptoms"].get(claim.get("intake_label"), {}).get("minor_name")
    out = []
    for code, n in found.most_common(limit):
        master = masters["escapes"].get(code, {})
        process_id = master.get("detection_process_id")
        phase = PHASE_OF.get(process_id)
        tested = lot_trace["inspections"].get(phase, {}) if phase else {}
        equipment = max(tested, key=lambda eq: tested[eq][1]) if tested else None
        evidence, sentences = [], [f"과거 같은 원인 조사 {n}건에서 확인된 유출 원인입니다."]
        if equipment:
            item = test_item_for(minor) if phase == "ET" else None
            since, rates = detection_rates(store, phase, item)
            mine = rates.get(equipment)
            if mine:
                others = ", ".join(f"{eq} {r['rate']:.2%}" for eq, r in sorted(rates.items()) if eq != equipment)
                evidence.append({"type": "INSPECTION",
                                 "summary": f"{item or phase} 검출률 {equipment} {mine['rate']:.2%} "
                                            f"({mine['fails']:,}/{mine['tests']:,}), {others} ({since[:10]} ~ 분석 시각)",
                                 "ref_table": "test_run", "ref_id": f"{item or phase}@{equipment}",
                                 "occurred_at": tested[equipment][2]})
                low = low_detection(rates, equipment)
                sentences.append(f"이 Lot은 {equipment}에서 검사됐고, 최근 검출률이 {mine['rate']:.2%}로 "
                                 + ("다른 장비보다 낮습니다." if low else "다른 장비와 비슷합니다."))
            since = (datetime.fromisoformat(store.as_of) - timedelta(days=EVENT_LOOKBACK_DAYS)).isoformat()
            for e in store.equipment_events([equipment], since=since):
                if e["result"] == "ABNORMAL":
                    evidence.append({"type": "EQUIPMENT_EVENT",
                                     "summary": f"{equipment} {e['event_type']}: {e['description']}",
                                     "ref_table": "equipment_event", "ref_id": f"{e['event_type']}@{equipment}",
                                     "occurred_at": e["occurred_at"]})
        out.append({"escape_code": code, "escape_name": master.get("escape_name", code),
                    "detection_process_id": process_id, "equipment_id": equipment,
                    "summary": " ".join(sentences), "evidence": evidence})
    return out


@traceable(name="standard_actions", run_type="tool", process_inputs=_hide)
def improvement(masters, candidates, escape_list):
    process, inspection = [], []
    if candidates:
        top = masters["causes"].get(candidates[0]["cause_code"], {})
        if top.get("standard_action"):
            process.append({"cause_code": candidates[0]["cause_code"], "action": top["standard_action"],
                            "standard_action": top["standard_action"], "owner_dept": top.get("action_owner_dept")})
    for e in escape_list:
        master = masters["escapes"].get(e["escape_code"], {})
        if master.get("standard_action"):
            inspection.append({"escape_code": e["escape_code"], "action": master["standard_action"],
                               "standard_action": master["standard_action"]})
    return {"process": process, "inspection": inspection}


class _Improvement(BaseModel):
    process_action: str
    inspection_action: str | None = None


IMPROVE = """개선안 초안을 써라.
- process_action: 1순위 원인 후보의 표준 조치를 이 Lot·장비의 근거에 맞게 구체화한 공정 개선안 1~2문장.
- inspection_action: 유출 원인 후보가 있으면 그 표준 조치를 검사 장비 근거에 맞게 구체화한 검사 기준 개선안 1~2문장, 없으면 null.
- 표준 조치의 범위를 벗어나는 새 조치나 새 수치를 만들지 않는다."""


def improve_with_llm(plan, candidates, escape_list, outcome):
    if not plan["process"]:
        return
    top = candidates[0]
    facts = {"1순위 원인": {"원인": top["cause_name"], "공정": top["process_name"], "장비": top["equipment_id"] or "특정 불가",
                        "근거": [e["summary"] for e in top["evidence"]], "표준 조치": plan["process"][0]["standard_action"]}}
    if plan["inspection"]:
        e = escape_list[0]
        facts["유출 원인"] = {"원인": e["escape_name"], "검사 장비": e["equipment_id"],
                          "근거": [x["summary"] for x in e["evidence"]], "표준 조치": plan["inspection"][0]["standard_action"]}
    result = llm.write("improvement", IMPROVE, facts, _Improvement,
                       lambda r: [r["process_action"], r.get("inspection_action") or ""], outcome)
    if result:
        plan["process"][0]["action"] = result["process_action"]
        if plan["inspection"] and result.get("inspection_action"):
            plan["inspection"][0]["action"] = result["inspection_action"]
