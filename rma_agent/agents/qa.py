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
