"""QA agent: analysis level before cause analysis, evidence check of each candidate after it.

Both are rules. Level and approval come from the QA triage record (analysis_level_rule);
the evidence check follows qa_causal_requirement (what a cause needs before it can be confirmed).
"""
from datetime import datetime, timedelta

from langsmith import traceable
from pydantic import BaseModel

from . import llm
from ..tools.names import josa

_hide = lambda inputs: {k: v for k, v in inputs.items() if k not in ("masters", "store")}
PRODUCTION_EVIDENCE = ("PROCESS_SIGNAL", "EQUIPMENT_EVENT", "MATERIAL")


@traceable(name="triage_rules", run_type="tool", process_inputs=_hide)
def triage(store, claim):
    t = store.triage(claim["rma_id"])
    if t is None:
        raise LookupError(f"{claim['rma_id']}: QA triage not decided at {store.as_of}")
    sev, symbol = t["severity"], t["characteristic_symbol"]
    l3 = t["analysis_level"] == "L3"
    # Only the L3 conditions are reasons for approval; repeat and SC are L2 conditions (they stay in rationale).
    return {"severity": sev, "characteristic_symbol": symbol, "analysis_level": t["analysis_level"],
            "needs_approval": l3, "approval_reasons": l3_reasons(sev, symbol, t["customer_industry"]) if l3 else [],
            "rationale": t["rationale"]}


def l3_reasons(severity, symbol, industry):
    """Which L3 conditions of analysis_level_rule hold (the reasons a person must approve the cause)."""
    reasons = []
    if severity is not None and severity >= 9:
        reasons.append("심각도 9 이상")
    if symbol == "CC":
        reasons.append("CC 특별특성 관련")
    if industry == "AUTOMOTIVE" and severity is not None and severity >= 8:
        reasons.append("차량 고객 + 심각도 8 이상")
    return reasons


REPEAT_DAYS, REPEAT_COUNT = 30, 3   # same customer, same symptom: the 3rd return within 30 days


def level_for(severity, symbol, industry, repeat):
    """analysis_level_rule (same rule the data was generated with)."""
    if severity >= 9 or symbol == "CC" or (industry == "AUTOMOTIVE" and severity >= 8):
        return "L3"
    if severity >= 6 or symbol == "SC" or repeat:
        return "L2"
    return "L1"


@traceable(name="triage_proposal", run_type="tool", process_inputs=_hide)
def propose_triage(masters, store, claim):
    """QA triage proposal for a new intake (no qa_triage yet): the analysis_level_rule on the intake label.

    Severity and special characteristic come from the symptom definition, the industry from the customer, the repeat
    signal from this customer's returns with the same label in the last 30 days. An unconfirmed symptom
    (INSUFFICIENT_INFO / UNCLASSIFIED) gets a provisional L2 (label criteria 2-3). A person confirms it; Spring saves it.
    """
    label, industry = claim.get("intake_label"), claim["industry"]
    base = {"rma_id": claim["rma_id"], "minor_code": label, "customer_industry": industry}
    if label in (None, "INSUFFICIENT_INFO", "UNCLASSIFIED"):
        name = {"INSUFFICIENT_INFO": "정보 부족", "UNCLASSIFIED": "분류 불가"}.get(label, "분류 전")
        rationale = f"소분류 미확정({name}) 임시 판정, 고객 {industry}"
        if industry == "AUTOMOTIVE":
            rationale += ", 차량 고객은 QA가 당일 직접 확인"
        return {**base, "severity": None, "characteristic_id": None, "characteristic_symbol": None, "repeat_signal": 0,
                "analysis_level": "L2", "needs_approval": False, "approval_reasons": [], "provisional": True,
                "rationale": rationale + " → L2"}
    symptom = masters["symptoms"][label]
    severity, char_id = symptom["severity"], symptom["characteristic_id"] or None
    char = masters["characteristics"].get(char_id, {}) if char_id else {}
    symbol = char.get("symbol")
    since = (datetime.fromisoformat(claim["opened_at"]) - timedelta(days=REPEAT_DAYS)).isoformat()
    earlier = [r for r in store.recent_same_symptom(claim["customer_id"], label, since, claim["opened_at"])
               if r["opened_at"] < claim["opened_at"] or r["rma_id"] < claim["rma_id"]]
    repeat = int(len(earlier) + 1 >= REPEAT_COUNT)
    level = level_for(severity, symbol, industry, repeat)
    rationale = f"불량 형태 {symptom['minor_name']}, 심각도 {severity}"
    if char:
        rationale += f", 관련 특성 {char['characteristic_name']}({symbol})"
    rationale += f", 고객 {industry}"
    if repeat:
        rationale += ", 동일 고객·불량 형태 30일 내 3건째 이상"
    reasons = l3_reasons(severity, symbol, industry)
    return {**base, "severity": severity, "characteristic_id": char_id, "characteristic_symbol": symbol,
            "repeat_signal": repeat, "analysis_level": level, "needs_approval": level == "L3",
            "approval_reasons": reasons if level == "L3" else [], "provisional": False,
            "rationale": f"{rationale} → {level}"}


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
        note = f"1순위 후보 {josa(top['cause_name'], '은')} {have} 근거가 있으나, {josa(need, '이')} 없어 확정 전 확인이 필요합니다."
    else:
        note = f"1순위 후보 {josa(top['cause_name'], '은')} 확정에 필요한 근거({have})를 갖췄습니다."
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


def _recurrence_check(masters, store, claim, lot_trace, candidates, review):
    """After cause analysis: is this return a real recurrence after the corrective action, or a product made before it?

    Compares the leading cause with the latest occurrence action on that cause implemented by as_of (recurrence_review
    uses the same rule: lot started after implemented_at and the same cause).
      RECURRENCE            lot started after the action, same cause
      PRE_ACTION_PRODUCT    lot started before the action: leftover product, not a recurrence
      PENDING_CAUSE_REVIEW  the symptom is not confirmed yet (INSUFFICIENT_INFO / UNCLASSIFIED), or the lot started after
                            the action but shows no evidence the leading cause hit it (likely a wrong cause; results/)
      NOT_APPLICABLE        no cause candidate, a sporadic leading cause (no machine or material pinned down),
                            no implemented action on it, or no lot to date
    Whether the lot went through the action's scope (machine or material lot) does not change the verdict; it explains it
    (outside the scope = the fix was not rolled out there).
    """
    top = candidates[0] if candidates else None
    if not top:
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None, "_why": "no_candidate",
                "reason": "원인 후보가 없어 재발 여부를 판정하지 않습니다."}
    # Recurrence is tracked for systemic causes only. The data never reviews returns of sporadic causes; at analysis time
    # the sign of a sporadic cause is a leading candidate with no machine or material pinned down.
    if not top.get("equipment_id") and top.get("cause_category") != "MATERIAL":
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None, "_why": "sporadic",
                "reason": f"1순위 원인 {josa(top['cause_name'], '은')} 특정 설비·자재로 좁혀지지 않는 단발성 원인이라 재발 판정 대상이 아닙니다."}
    # only actions aimed at a machine or material lot are tracked for recurrence (quarterly "local" fixes are not)
    actions = [a for a in store.occurrence_actions(top["cause_code"])
               if a["implemented_at"] and (a["scope"].get("equipment") or a["scope"].get("material_lots"))]
    if not actions:
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None, "_why": "no_action",
                "reason": f"1순위 원인 {top['cause_name']}에 대해 분석 시점까지 시행된 발생 원인 조치가 없습니다."}
    lot_id = lot_trace.get("lot_id")
    lot = store.lots([lot_id]).get(lot_id) if lot_id else None
    if not lot:
        return {"status": "NOT_APPLICABLE", "corrective_action": None, "checks": None, "_why": "no_lot",
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
    # Evidence that the leading cause really hit this lot: a condition drift at its process, a material cause, or an
    # equipment event on a machine the earlier fix did not cover. An event alone on the machine that was fixed is
    # usually routine (chosen on 2025-07~09: precision 36% -> 66%; on 2025-10~: 72% -> 75% with no true recurrence lost).
    types = {e["type"] for e in top.get("evidence", [])}
    production_evidence = ("PROCESS_SIGNAL" in types or top.get("cause_category") == "MATERIAL"
                           or ("EQUIPMENT_EVENT" in types and in_scope is False))
    if after and symptom_known and not production_evidence:
        status = "PENDING_CAUSE_REVIEW"
        reason = (f"Lot {josa(lot_id, '은')} 같은 원인의 조치 {action['corrective_action_id']}({day} 시행) 이후에 생산됐지만, 이 Lot에서 "
                  f"{top['cause_name']}의 근거(원인 공정의 조건 이탈, 조치하지 않은 설비의 이상, 자재)가 없어 원인부터 다시 확인해야 합니다.")
    elif not symptom_known:
        status = "PENDING_CAUSE_REVIEW"
        reason = (f"과거 조치({action['corrective_action_id']}, {day} 시행)와 원인이 같아 보이지만, 접수 증상이 아직 확인되지 않아 "
                  "고객 회신으로 원인을 먼저 확인해야 재발 여부를 정할 수 있습니다.")
    elif after:
        status = "RECURRENCE"
        reason = f"Lot {josa(lot_id, '은')} 조치 {action['corrective_action_id']}({day} 시행) 이후에 생산됐고 원인이 같습니다."
        if in_scope is False and scope_text:
            reason += f" 조치 범위({scope_text}) 밖에서 다시 발생해, 조치가 다른 설비·자재로 수평 전개되지 않은 것으로 보입니다."
        elif in_scope:
            reason += " 조치가 적용된 범위 안에서 다시 발생해, 조치 효과를 다시 확인해야 합니다."
    else:
        status = "PRE_ACTION_PRODUCT"
        reason = (f"Lot {josa(lot_id, '은')} 조치 {action['corrective_action_id']}({day} 시행) 전에 생산된 제품입니다. "
                  "재발이 아니라 조치 전 생산품의 반품입니다.")
    return {"status": status, "corrective_action": public_action, "checks": checks, "reason": reason}


# The screen's recurrence decision uses three words (재발 / 확산형 재발 / 신규). PRE_ACTION_PRODUCT has no button there yet.
def _suggestion(result):
    status, why = result["status"], result.pop("_why", None)
    if status == "RECURRENCE":
        return "확산형 재발" if result["checks"]["in_action_scope"] is False else "재발"
    if status == "PRE_ACTION_PRODUCT":
        return "조치 전 생산품"
    if status == "NOT_APPLICABLE" and why in ("sporadic", "no_action"):
        return "신규"
    return None  # PENDING_CAUSE_REVIEW, no candidate, no lot: no suggestion until the cause or lot is known


def recurrence_check(masters, store, claim, lot_trace, candidates, review):
    """See _recurrence_check. Adds `suggestion`, the screen's word for the verdict (or None)."""
    result = _recurrence_check(masters, store, claim, lot_trace, candidates, review)
    result["suggestion"] = _suggestion(result)
    return result
