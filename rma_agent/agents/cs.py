"""CS agent: classification at intake (already done by /classify) and the customer reply draft after QA review."""
from datetime import datetime

from langsmith import traceable
from pydantic import BaseModel

from . import llm
from ..tools.names import josa
from .classifier import NOT_CLASSIFIED, default_question

UNCLASSIFIED_REQUEST = ("보내 주신 현상을 당사 QA가 반송 시료로 직접 확인하겠습니다. 불량 부위 사진과 발생 조건"
                        "(검출 단계, 상온 상시 또는 온도·진동 후)을 함께 보내 주시면 확인이 빨라집니다.")


def follow_up(classification_result):
    """What to ask the customer when the symptom is not classified yet (None when it is)."""
    code = classification_result.get("minor_code")
    if code == "INSUFFICIENT_INFO":
        return default_question()
    if code == "UNCLASSIFIED":
        return UNCLASSIFIED_REQUEST
    return None


_hide = lambda inputs: {k: v for k, v in inputs.items() if k not in ("masters", "store")}


def classification(masters, store, claim):
    intake = store.intake(claim["rma_item_id"]) or {"intake_label": None, "evidence_spans": []}
    symptom = masters["symptoms"].get(intake["intake_label"], {})
    # Only phrases found in the customer's text. For INSUFFICIENT_INFO the stored spans are the QA-confirmed symptom's,
    # which the intake text does not contain (known at intake: no).
    text = claim.get("raw_text") or ""
    spans = [s for s in intake["evidence_spans"] if s and s in text]
    return {"major_code": symptom.get("major_code"), "minor_code": intake["intake_label"],
            "minor_name": symptom.get("minor_name"), "evidence_spans": spans}


# 1차 회신은 원인 이름·공정명을 쓰지 않고 범주만 알린다 (공개 범위 문구표, QA와 확정 필요).
# 값이 없는 범주(UNDETERMINED 등)는 원인 언급 없이 "조사 중"으로 쓴다.
DISCLOSURE = {"PROCESS": "제조 공정 쪽 요인", "MATERIAL": "자재 쪽 요인", "INSPECTION": "검사 단계 쪽 요인"}


def leaks(text, candidates):
    """Specific cause/process names of the candidates found in a customer text (must not reach the customer)."""
    names = {c.get(k) for c in candidates or [] for k in ("cause_name", "process_name")}
    return [n for n in names if n and n not in ("MATERIAL", "NONE") and n in (text or "")]


@traceable(name="reply_draft", run_type="tool", process_inputs=_hide)
def reply_draft(masters, claim, classification_result, candidates, qa_review):
    who = f"{claim['customer_id']} 품질 담당자님"
    what = f"{claim.get('reported_part_no') or claim.get('spec_id')} (Lot {claim.get('lot_id') or '미기재'})"
    question = follow_up(classification_result)
    if question:  # symptom unknown: ask first, no cause guess (label criteria 2-3, WBS 4.8)
        return (f"{who}, {what} 불량 통보 확인했습니다. {question} "
                "동일 Lot 재고는 출하 보류 후 선별 여부를 검토하며, 확인되는 대로 회신드리겠습니다.")
    symptom = classification_result.get("minor_name") or "불량"
    text = f"{who}, {what} {symptom} 불량 통보 확인했습니다. "
    if candidates:
        area = DISCLOSURE.get(candidates[0].get("cause_category"))
        text += (f"현재 {josa(area, '을')} 중심으로 원인을 확인 중입니다. " if area else "현재 원인을 조사 중입니다. ")
        missing = qa_review["candidate_checks"][0]["missing"] if qa_review["candidate_checks"] else []
        if any("반송품" in m for m in missing):
            text += "회송해 주시는 시료로 분석을 진행합니다. "
    else:
        text += "현재 원인을 조사 중입니다. "
    text += "동일 Lot 재고는 출하 보류 후 선별 여부를 검토하며, 결과를 회신드리겠습니다."
    return text


class _Reply(BaseModel):
    customer_reply: str


REPLY = """고객사 품질 담당자에게 보낼 회신 초안을 3~5문장으로 써라.
- 접수 확인(제품, Lot, 불량 형태), 원인 범주(확정 아님, 범주 표현 그대로만 쓰고 구체적인 원인·공정·자재·설비 이름은 쓰지 않는다), 시료 분석 계획, 동일 Lot 재고 조치(출하 보류·선별 검토), 결과 회신 약속.
- 내부 장비 번호, σ, 검출률, 점수, 내부 시험 항목(보관 시료, 골든 샘플, 출처 확인 등) 같은 내부 정보는 쓰지 않는다.
- '회송 시료 분석'이 참이면 회송해 주시는 시료로 분석한다고 쓰고, 거짓이면 시료 분석을 언급하지 않는다.
- 원인 범주가 없으면 조사 중이라고만 쓴다.
- 고객 재질의가 있으면 원인은 쓰지 말고, 재질의 내용을 빠짐없이 넣는다."""


def reply_with_llm(template, claim, classification_result, candidates, qa_review, outcome):
    question = follow_up(classification_result)
    top = candidates[0] if candidates and not question else None
    facts = {"고객": claim["customer_id"], "제품": claim.get("reported_part_no") or claim.get("spec_id"),
             "Lot": claim.get("lot_id"), "불량 형태": None if question else classification_result.get("minor_name"),
             "투입 수량": claim.get("input_qty"), "불량 수량": claim.get("claimed_defect_qty"),
             "원인 범주": DISCLOSURE.get(top.get("cause_category")) if top else None,
             # QA's internal check list (sourced return test, retained samples, ...) is not for the customer;
             # only whether the returned samples will be analysed.
             "회송 시료 분석": bool(top and qa_review["candidate_checks"] and
                                any("반송품" in m for m in qa_review["candidate_checks"][0]["missing"])),
             "고객 재질의": question,
             "템플릿 초안": template}
    result = llm.write("customer_reply", REPLY, facts, _Reply, lambda r: [r["customer_reply"]], outcome)
    if not result:
        return template
    if leaks(result["customer_reply"], candidates):  # 구체적 원인이 새면 템플릿으로
        outcome.rejected = True
        return template
    return result["customer_reply"]


ACTION_NAMES = {"REPLACEMENT_SHIPMENT": "대체품 출하", "CREDIT_NOTE": "대금 공제",
                "SORTING_SUPPORT_AND_CREDIT": "선별 지원·대금 공제", "NO_CHARGE_SAMPLE_RETURN": "무상 시료 회수"}
SAME_CAUSE_CASES = 30   # mode "cause": most recent past returns with the leading cause whose handling is looked at
MIN_POOL = 3            # mode "cause": fewer handled returns than this -> the similar cases instead


def service_action(store, claim, candidates, similar, mode="similar"):
    """Proposal for how to settle the return with the customer (Spring_API_v4 4-7), from how past returns were handled.

    Pool: the similar cases shown on screen (mode "similar", default), or the most recent past returns whose approved
    cause is the leading candidate (mode "cause"). Matched the action actually taken in 68.0% vs 67.2% of returns
    opened 2026-10~ (scripts/eval_service_action.py). The proposal is the pool's most common action (ties: the most
    recent); board_qty is this return's claimed defect qty (0 for a sample return); expected_days is the median days
    from decision to execution for that action in the pool (if none was executed by as_of: all past actions of that
    type). The basis names no cause or equipment (the CS screen shows it). None when no past handling is known.
    """
    if mode == "cause" and candidates:
        top = candidates[0]["cause_code"]
        same = [c for c in store.past_cases(intake_label=claim.get("intake_label")) if c.cause_code == top]
        acts, source = store.service_actions([c.rma_id for c in same[-SAME_CAUSE_CASES:]]), "같은 유력 원인의 과거 반품"
        if len(acts) < MIN_POOL:
            acts = []
    else:
        acts = []
    if not acts:
        acts, source = store.service_actions([s["rma_id"] for s in similar]), "유사 사례"
    if not acts:
        return None
    counts = {}
    for a in acts:
        counts[a["action_type"]] = counts.get(a["action_type"], 0) + 1
    last = {a["action_type"]: i for i, a in enumerate(acts)}
    action = max(counts, key=lambda k: (counts[k], last[k]))
    days = sorted((datetime.fromisoformat(a["executed_at"]) - datetime.fromisoformat(a["decided_at"])).total_seconds() / 86400
                  for a in acts if a["action_type"] == action and a["executed_at"])
    days_basis = "유사 사례"
    if not days:  # none of the pool's actions was executed by as_of: all past actions of that type
        days, days_basis = sorted(store.service_action_days(action)), f"과거 {ACTION_NAMES.get(action, action)} 전체"
    expected = max(1, round(days[len(days) // 2])) if days else None
    qty = 0 if action == "NO_CHARGE_SAMPLE_RETURN" else claim.get("claimed_defect_qty") or 0
    return {"action_type": action, "action_name": ACTION_NAMES.get(action, action), "board_qty": qty,
            "basis": f"{source} {len(acts)}건 중 {counts[action]}건이 {ACTION_NAMES.get(action, action)}",
            "similar_case_count": len(acts), "expected_days": expected,
            "expected_days_basis": f"{days_basis} 처리 결정→실행 {len(days)}건의 중앙값" if days else None,
            "alternatives": [{"action_type": k, "action_name": ACTION_NAMES.get(k, k), "count": v}
                             for k, v in sorted(counts.items(), key=lambda x: -x[1]) if k != action]}


# ---------- 2차 회신: QA가 원인을 승인·확인한 뒤 CS가 쓴다 (후속 그래프, agents/graph.py run_follow_up)
# 원인은 1차와 같은 범주 문구표(DISCLOSURE)로만 알린다. 처리방안은 한글명으로만 쓰고 코드명은 쓰지 않는다.

def follow_up_draft(claim, cause, proposal):
    who = f"{claim['customer_id']} 품질 담당자님"
    what = f"{claim.get('reported_part_no') or claim.get('spec_id')} (Lot {claim.get('lot_id') or '미기재'})"
    area = DISCLOSURE.get(cause.get("cause_category"))
    text = f"{who}, {what} 불량 건의 원인 분석이 끝났습니다. "
    text += (f"{josa(area, '이')} 원인으로 확인되었습니다. " if area else "원인 확인을 마쳤습니다. ")
    if proposal:
        text += f"처리는 {proposal['action_name']}로 진행하려 합니다. "
        if proposal.get("expected_days"):
            text += f"예상 소요 기간은 약 {proposal['expected_days']}일입니다. "
    else:
        text += "처리 방안은 확정되는 대로 안내드리겠습니다. "
    return text + "동일 원인이 다시 발생하지 않도록 개선 조치를 진행하고, 진행 상황을 회신드리겠습니다."


FOLLOW_UP = """고객사 품질 담당자에게 보낼 2차 회신 초안을 3~5문장으로 써라. 원인 분석이 끝난 뒤의 회신이다.
- 접수 건 확인(제품, Lot), 원인 확인 사실(원인 범주 표현 그대로), 처리 계획(처리방안 이름 그대로), 예상 일정, 재발 방지 조치 약속.
- 구체적인 원인·공정·자재·설비 이름, 내부 코드, σ, 검출률, 점수, 손실 금액은 쓰지 않는다.
- 원인 범주가 없으면 원인 확인을 마쳤다고만 쓴다. 처리방안이 없으면 확정되는 대로 안내한다고 쓴다.
- 예상 일정은 facts의 값만 쓰고 다른 숫자를 만들지 않는다."""


def follow_up_with_llm(template, claim, cause, proposal, candidates, outcome):
    facts = {"고객": claim["customer_id"], "제품": claim.get("reported_part_no") or claim.get("spec_id"),
             "Lot": claim.get("lot_id"), "원인 범주": DISCLOSURE.get(cause.get("cause_category")),
             "처리방안": proposal["action_name"] if proposal else None,
             "예상 소요 일수": proposal.get("expected_days") if proposal else None,
             "템플릿 초안": template}
    result = llm.write("follow_up_reply", FOLLOW_UP, facts, _Reply, lambda r: [r["customer_reply"]], outcome)
    if not result:
        return template
    text = result["customer_reply"]
    if leaks(text, candidates) or any(code in text for code in ACTION_NAMES):
        outcome.rejected = True
        return template
    return text
