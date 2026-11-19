"""QA agent: analysis level before cause analysis, evidence check of each candidate after it.

Both are rules. Level and approval come from the QA triage record (analysis_level_rule);
the evidence check follows qa_causal_requirement (what a cause needs before it can be confirmed).
"""
from langsmith import traceable
from pydantic import BaseModel

from . import llm

_hide = lambda inputs: {k: v for k, v in inputs.items() if k not in ("masters", "store")}
PRODUCTION_EVIDENCE = ("PROCESS_SIGNAL", "EQUIPMENT_EVENT", "MATERIAL")


@traceable(name="triage_rules", run_type="tool", process_inputs=_hide)
def triage(store, claim):
    t = store.triage(claim["rma_id"])
    if t is None:
        raise LookupError(f"{claim['rma_id']}: QA triage not decided at {store.as_of}")
    sev, symbol = t["severity"], t["characteristic_symbol"]
    reasons = []
    if sev >= 9:
        reasons.append("심각도 9 이상")
    if symbol == "CC":
        reasons.append("CC 특별특성 관련")
    if t["customer_industry"] == "AUTOMOTIVE" and sev >= 8:
        reasons.append("차량 고객 + 심각도 8 이상")
    if symbol == "SC":
        reasons.append("SC 특별특성 관련")
    if t["repeat_signal"]:
        reasons.append("동일 고객·불량 형태 30일 내 3건 이상 반복")
    return {"severity": sev, "characteristic_symbol": symbol, "analysis_level": t["analysis_level"],
            "needs_approval": t["analysis_level"] == "L3", "approval_reasons": reasons, "rationale": t["rationale"]}


@traceable(name="causal_requirements", run_type="tool", process_inputs=_hide)
def review(masters, store, claim, candidates, triage_result):
    lab = [t for t in store.item_lab_tests(claim["rma_item_id"]) if t["origin_verified"]]
    lot_lab = store.lot_tests(claim["lot_id"], phases=["RMA_LAB"]) if claim.get("lot_id") else []
    comparison = [t for t in lot_lab if t["subject_type"] in ("RETAINED_SAMPLE", "GOLDEN_SAMPLE")]
    checks = []
    for c in candidates:
        req = masters["requirements"].get(c["cause_code"])
        production = [e for e in c["evidence"] if e["type"] in PRODUCTION_EVIDENCE]
        satisfied, missing = ["같은 원인 확정 사례"], []
        if req is None or req["requires_production_record"]:
            if production:
                satisfied.append("생산 기록 근거 (" + ", ".join(e["summary"] for e in production[:2]) + ")")
            else:
                missing.append(f"생산 기록 근거 ({c['process_name'] or '해당 공정'} 조건 이탈·설비·자재 기록)")
        if req and req["min_verified_lab_evidence"]:
            if len(lab) >= req["min_verified_lab_evidence"]:
                satisfied.append(f"출처 확인된 반송품 시험 {len(lab)}건")
            else:
                missing.append("출처 확인된 반송품 시험 (결과 대기)")
        if req and req["requires_comparison_sample"]:
            if comparison:
                satisfied.append(f"비교 시료 시험 {len(comparison)}건")
            else:
                missing.append("비교 시료(보관 시료·골든 샘플) 시험")
        status = "SUFFICIENT" if not missing else "PARTIAL" if len(satisfied) > 1 else "INSUFFICIENT"
        checks.append({"rank": c["rank"], "cause_code": c["cause_code"], "evidence_status": status,
                       "satisfied": satisfied, "missing": missing,
                       "requirement": req["requirement_text"] if req else None})
    return {**{k: triage_result[k] for k in ("severity", "characteristic_symbol", "analysis_level",
                                             "needs_approval", "approval_reasons")},
            "candidate_checks": checks, "review_note": review_note(candidates, checks, triage_result)}


def review_note(candidates, checks, triage_result):
    if not candidates:
        return "같은 접수 소분류의 확정 사례가 없어 원인 후보를 제시하지 못했습니다. QA가 직접 조사합니다."
    top, check = candidates[0], checks[0]
    have = ", ".join(s.split(" (")[0] for s in check["satisfied"])
    if check["missing"]:
        need = ", ".join(m.split(" (")[0] for m in check["missing"])
        note = f"1순위 후보 {top['cause_name']}은(는) {have} 근거가 있으나, {need}이(가) 없어 확정 전 확인이 필요합니다."
    else:
        note = f"1순위 후보 {top['cause_name']}은(는) 확정에 필요한 근거({have})를 갖췄습니다."
    if triage_result["needs_approval"]:
        reasons = ", ".join(triage_result["approval_reasons"]) or triage_result["analysis_level"]
        note += f" {reasons} 건으로 사람 승인을 요청합니다."
    else:
        note += " QA 확인 후 확정합니다."
    return note


class _ReviewNote(BaseModel):
    review_note: str


REVIEW = """QA 검토 의견을 2~3문장으로 써라.
- 1순위 원인 후보의 근거 충분 여부(갖춘 근거, 부족한 근거)와 확정 전에 필요한 확인을 쓴다.
- 2·3순위와 비교가 필요하면 짧게 덧붙인다.
- 사람 승인이 필요하면 그 사유와 함께 승인 요청을, 아니면 QA 확인 후 확정함을 쓴다."""


def note_with_llm(review_result, candidates, outcome):
    if not candidates:
        return
    names = {c["cause_code"]: c["cause_name"] for c in candidates}
    facts = {"분석 수준": review_result["analysis_level"], "사람 승인 필요": review_result["needs_approval"],
             "승인 사유": review_result["approval_reasons"],
             "후보별 검토": [{"순위": k["rank"], "원인": names[k["cause_code"]], "판정": k["evidence_status"],
                         "갖춘 근거": k["satisfied"], "부족한 근거": k["missing"]} for k in review_result["candidate_checks"]]}
    result = llm.write("review_note", REVIEW, facts, _ReviewNote, lambda r: [r["review_note"]], outcome)
    if result:
        review_result["review_note"] = result["review_note"]


def _scope_text(scope):
    if scope.get("equipment"):
        return "설비 " + ", ".join(scope["equipment"])
    if scope.get("material_lots"):
        return f"공급사 {scope.get('supplier')}의 자재 Lot " + ", ".join(scope["material_lots"])
    return None


def recurrence_check(masters, store, claim, lot_trace, candidates, review):
    """After cause analysis: is this return a real recurrence after the corrective action, or a product made before it?

    Compares the leading cause with the latest occurrence action on that cause implemented by as_of (recurrence_review
    uses the same rule: lot started after implemented_at and the same cause).
      RECURRENCE            lot started after the action, same cause
      PRE_ACTION_PRODUCT    lot started before the action: leftover product, not a recurrence
      PENDING_CAUSE_REVIEW  the symptom is not confirmed yet (INSUFFICIENT_INFO / UNCLASSIFIED), or the lot started after
                            the action but shows no production evidence of the leading cause (likely a wrong cause:
                            this halved false RECURRENCE calls, 771 -> 352, for 32 fewer true ones; results/)
      NOT_APPLICABLE        no cause candidate, a sporadic leading cause (no machine or material pinned down),
                            no implemented action on it, or no lot to date
    Whether the lot went through the action's scope (machine or material lot) does not change the verdict; it explains it
    (outside the scope = the fix was not rolled out there).
    """
    top = candidates[0] if candidates else None
    if not top:
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None,
                "reason": "원인 후보가 없어 재발 여부를 판정하지 않습니다."}
    # Recurrence is tracked for systemic causes only. The data never reviews returns of sporadic causes; at analysis time
    # the sign of a sporadic cause is a leading candidate with no machine or material pinned down.
    if not top.get("equipment_id") and top.get("cause_category") != "MATERIAL":
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None,
                "reason": f"1순위 원인 {top['cause_name']}은(는) 특정 설비·자재로 좁혀지지 않는 단발성 원인이라 재발 판정 대상이 아닙니다."}
    # only actions aimed at a machine or material lot are tracked for recurrence (quarterly "local" fixes are not)
    actions = [a for a in store.occurrence_actions(top["cause_code"])
               if a["implemented_at"] and (a["scope"].get("equipment") or a["scope"].get("material_lots"))]
    if not actions:
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None,
                "reason": f"1순위 원인 {top['cause_name']}에 대해 분석 시점까지 시행된 발생 원인 조치가 없습니다."}
    lot_id = lot_trace.get("lot_id")
    lot = store.lots([lot_id]).get(lot_id) if lot_id else None
    if not lot:
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None,
                "reason": "Lot을 알 수 없어 조치 시행일과 생산 시점을 비교할 수 없습니다."}
    equipment = {st["equipment_id"] for st in lot_trace["steps"]}
    material_lots = {m["material_lot_id"] for m in lot_trace["materials"]}

    def in_scope_of(a):
        if a["scope"].get("equipment"):
            return bool(equipment & set(a["scope"]["equipment"]))
        if a["scope"].get("material_lots"):
            return bool(material_lots & set(a["scope"]["material_lots"]))
        return None

    # Any tracked action implemented before this lot started means the cause came back after a fix (recurrence).
    # Show the action whose scope covers this lot first, then the latest.
    before = [a for a in actions if lot["started_at"] >= a["implemented_at"]]
    pool = before or actions
    action = max(pool, key=lambda a: (bool(in_scope_of(a)), a["implemented_at"], a["corrective_action_id"]))
    public_action = {k: action[k] for k in ("corrective_action_id", "investigation_id", "description", "implemented_at", "scope")}
    scope = action["scope"]
    in_scope = in_scope_of(action)
    # Pending only when the symptom itself is unknown: missing lab evidence is normal at analysis start
    # (requiring it sent 76 returns to PENDING with none right, results/20261119_recurrence_v5.md).
    symptom_known = claim.get("intake_label") not in ("INSUFFICIENT_INFO", "UNCLASSIFIED", None)
    after = bool(before)
    checks = {"cause_code": top["cause_code"], "cause_name": top["cause_name"], "cause_match": True,
              "symptom_confirmed": symptom_known, "lot_id": lot_id, "lot_started_at": lot["started_at"],
              "produced_after_action": after, "in_action_scope": in_scope}
    day, scope_text = action["implemented_at"][:10], _scope_text(scope)
    production_evidence = any(e["type"] in PRODUCTION_EVIDENCE + ("SUPPLIER",) for e in top.get("evidence", []))
    if after and symptom_known and not production_evidence:
        status = "PENDING_CAUSE_REVIEW"
        reason = (f"Lot {lot_id}은(는) 같은 원인의 조치 {action['corrective_action_id']}({day} 시행) 이후에 생산됐지만, 이 Lot에서 "
                  f"{top['cause_name']}의 생산 기록 근거(공정 신호·설비 이벤트·자재)가 없어 원인부터 다시 확인해야 합니다.")
    elif not symptom_known:
        status = "PENDING_CAUSE_REVIEW"
        reason = (f"과거 조치({action['corrective_action_id']}, {day} 시행)와 원인이 같아 보이지만, 접수 증상이 아직 확인되지 않아 "
                  "고객 회신으로 원인을 먼저 확인해야 재발 여부를 정할 수 있습니다.")
    elif after:
        status = "RECURRENCE"
        reason = f"Lot {lot_id}은(는) 조치 {action['corrective_action_id']}({day} 시행) 이후에 생산됐고 원인이 같습니다."
        if in_scope is False and scope_text:
            reason += f" 조치 범위({scope_text}) 밖에서 다시 발생해, 조치가 다른 설비·자재로 수평 전개되지 않은 것으로 보입니다."
        elif in_scope:
            reason += " 조치가 적용된 범위 안에서 다시 발생해, 조치 효과를 다시 확인해야 합니다."
    else:
        status = "PRE_ACTION_PRODUCT"
        reason = (f"Lot {lot_id}은(는) 조치 {action['corrective_action_id']}({day} 시행) 전에 생산된 제품입니다. "
                  "재발이 아니라 조치 전 생산품의 반품입니다.")
    return {"status": status, "corrective_action": public_action, "checks": checks, "reason": reason}
