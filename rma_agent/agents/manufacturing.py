"""Manufacturing agent: follow the returned lot through process and inspection; draft improvements later.

trace()        before cause analysis: process route with signals, inspection equipment, material lots
escapes()      after cause analysis: why the defect was not caught (past escape findings for the top cause +
               this lot's inspection equipment and its detection rate versus peers)
improvement()  standard actions for the top cause (process) and the top escape (inspection criteria)
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from langsmith import traceable
from pydantic import BaseModel

from . import llm

from ..tools.inspection import PHASE_OF, detection_rates, low_detection, lot_inspections, tested_at, test_item_for
from ..tools.names import event_name, test_item_name
from ..tools.process import EVENT_LOOKBACK_DAYS, process_signals

_hide = lambda inputs: {k: v for k, v in inputs.items() if k not in ("masters", "store", "baseline")}


@traceable(name="lot_trace", run_type="tool", process_inputs=_hide)
def trace(masters, store, baseline, claim):
    lot_id = claim.get("lot_id")
    if not lot_id:
        return {"lot_id": None, "steps": [], "inspections": {}, "materials": []}
    return {"lot_id": lot_id, "steps": process_signals(store, baseline, lot_id),
            "inspections": lot_inspections(store, lot_id), "materials": store.material_usage(lot_id)}


SHARED_WINDOW_HOURS = 24   # "same time window": the other lot ran on the same machine within this many hours
CANDIDATE_LOT_LIMIT = 20


def _hours(a, b):
    return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 3600


@traceable(name="candidate_lots", run_type="tool", process_inputs=_hide)
def candidate_lots(masters, store, claim, lot_trace, candidates, limit=CANDIDATE_LOT_LIMIT):
    """Other lots that shared a production machine (within SHARED_WINDOW_HOURS) or a material lot with the returned lot.

    A list for QA to check, not a prediction: each lot shows what it shared, whether it has shipped and how many returns
    it already has (all as of as_of). Inspection steps are left out (sharing a tester is not a production commonality).
    Lots sharing the leading cause's machine or material come first. Returns (items, total found).
    """
    lot_id = lot_trace.get("lot_id")
    if not lot_id:
        return [], 0
    procs = masters["processes"]
    top = candidates[0] if candidates else None
    shared = defaultdict(list)
    for step in lot_trace["steps"]:
        if procs.get(step["process_id"], {}).get("is_inspection"):
            continue
        start = (datetime.fromisoformat(step["started_at"]) - timedelta(hours=SHARED_WINDOW_HOURS)).isoformat()
        end = (datetime.fromisoformat(step["started_at"]) + timedelta(hours=SHARED_WINDOW_HOURS)).isoformat()
        for run in store.equipment_runs(step["equipment_id"], start, end, step["process_id"]):
            if run["lot_id"] == lot_id:
                continue
            gap = round(_hours(run["started_at"], step["started_at"]), 1)
            key = ("EQUIPMENT", step["equipment_id"], step["process_id"])
            if any((x["type"], x.get("equipment_id"), x.get("process_id")) == key for x in shared[run["lot_id"]]):
                continue  # one entry per machine; several loads on it count once
            shared[run["lot_id"]].append({
                "type": "EQUIPMENT", "process_id": step["process_id"],
                "process_name": procs.get(step["process_id"], {}).get("process_name", step["process_id"]),
                "equipment_id": step["equipment_id"], "this_lot_at": step["started_at"], "other_lot_at": run["started_at"],
                "gap_hours": gap})
    materials = {m["material_lot_id"]: m for m in lot_trace["materials"]}
    for use in store.material_lot_users(materials):
        if use["lot_id"] == lot_id or any(x.get("material_lot_id") == use["material_lot_id"] for x in shared[use["lot_id"]]):
            continue
        shared[use["lot_id"]].append({"type": "MATERIAL", "material_lot_id": use["material_lot_id"],
                                      "material_type": use["material_type"], "supplier_id": use["supplier_id"]})
    if not shared:
        return [], 0
    info, ships, claims = store.lots(shared), store.lot_shipments(shared), store.lot_claim_counts(shared)

    def matches_top(items):
        if not top:
            return False
        if top.get("cause_category") == "MATERIAL":
            return any(x["type"] == "MATERIAL" for x in items)
        return bool(top.get("equipment_id")) and any(x.get("equipment_id") == top["equipment_id"] for x in items)

    rows = []
    for other, items in shared.items():
        if other not in info:
            continue
        first_ship, boards = ships.get(other, (None, 0))
        gaps = [x["gap_hours"] for x in items if x["type"] == "EQUIPMENT"]
        rows.append({"lot_id": other, "spec_id": info[other]["spec_id"], "started_at": info[other]["started_at"],
                     "completed_at": info[other]["completed_at"], "shared": items, "shared_count": len(items),
                     "matches_top_cause": matches_top(items), "shipped": first_ship is not None,
                     "first_shipped_at": first_ship, "shipped_boards": boards, "known_returns": claims.get(other, 0),
                     "_gap": min(gaps) if gaps else SHARED_WINDOW_HOURS + 1})
    rows.sort(key=lambda r: (not r["matches_top_cause"], -r["shared_count"], r["_gap"], r["lot_id"]))
    if top:  # the leading cause's machine or material first inside each lot
        first = lambda x: not (x.get("equipment_id") == top.get("equipment_id") or
                               (top.get("cause_category") == "MATERIAL" and x["type"] == "MATERIAL"))
        for r in rows:
            r["shared"].sort(key=first)
    for r in rows:
        del r["_gap"]
    return rows[:limit], len(rows)


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
        tested = tested_at(lot_trace, process_id, masters["equipment_process"]) if phase else {}
        equipment = max(tested, key=lambda eq: tested[eq][1]) if tested else None
        evidence, sentences = [], [f"과거 같은 원인 조사 {n}건에서 확인된 유출 원인입니다."]
        if equipment:
            item = test_item_for(minor) if phase == "ET" else None
            since, rates = detection_rates(store, phase, item)
            rates = {eq: r for eq, r in rates.items()
                     if masters["equipment_process"].get(eq, process_id) == process_id}  # peers in the same process
            mine = rates.get(equipment)
            if mine and any(r["fails"] for r in rates.values()):  # all-zero rates say nothing about this lot
                label = test_item_name(item) if item else masters["processes"].get(process_id, {}).get("process_name", process_id)
                others = ", ".join(f"{eq} {r['rate']:.2%}" for eq, r in sorted(rates.items()) if eq != equipment)
                evidence.append({"type": "INSPECTION",
                                 "summary": f"{label} 검출률 {equipment} {mine['rate']:.2%} "
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
                                     "summary": f"{equipment} {event_name(e['event_type'])}: {e['description']}",
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
