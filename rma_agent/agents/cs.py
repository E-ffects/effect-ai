"""CS agent: classification at intake (already done by /classify) and the customer reply draft after QA review."""
from langsmith import traceable
from pydantic import BaseModel

from . import llm
from .classifier import DEFAULT_QUESTION, NOT_CLASSIFIED

UNCLASSIFIED_REQUEST = ("보내 주신 현상을 당사 QA가 반송 시료로 직접 확인하겠습니다. 불량 부위 사진과 발생 조건"
                        "(검출 단계, 상온 상시 또는 온도·진동 후)을 함께 보내 주시면 확인이 빨라집니다.")


def follow_up(classification_result):
    """What to ask the customer when the symptom is not classified yet (None when it is)."""
    code = classification_result.get("minor_code")
    if code == "INSUFFICIENT_INFO":
        return DEFAULT_QUESTION
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
        top = candidates[0]
        where = f"{top['process_name']} 공정의 " if top["process_id"] not in ("MATERIAL", "NONE") else ""
        text += f"현재 {where}{top['cause_name']}을(를) 유력 원인으로 보고 확인 중입니다. "
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
- 접수 확인(제품, Lot, 불량 형태), 현재 유력 원인(확정 아님), 시료 분석 계획, 동일 Lot 재고 조치(출하 보류·선별 검토), 결과 회신 약속.
- 내부 장비 번호, σ, 검출률, 점수 같은 내부 정보는 쓰지 않는다.
- 원인 후보가 없으면 조사 중이라고만 쓴다.
- 고객 재질의가 있으면 원인은 쓰지 말고, 재질의 내용을 빠짐없이 넣는다."""


def reply_with_llm(template, claim, classification_result, candidates, qa_review, outcome):
    question = follow_up(classification_result)
    top = candidates[0] if candidates and not question else None
    facts = {"고객": claim["customer_id"], "제품": claim.get("reported_part_no") or claim.get("spec_id"),
             "Lot": claim.get("lot_id"), "불량 형태": None if question else classification_result.get("minor_name"),
             "투입 수량": claim.get("input_qty"), "불량 수량": claim.get("claimed_defect_qty"),
             "유력 원인": {"원인": top["cause_name"], "공정": top["process_name"]} if top else None,
             "확정 전 필요한 확인": (qa_review["candidate_checks"][0]["missing"]
                                   if qa_review["candidate_checks"] and not question else []),
             "고객 재질의": question,
             "템플릿 초안": template}
    result = llm.write("customer_reply", REPLY, facts, _Reply, lambda r: [r["customer_reply"]], outcome)
    return result["customer_reply"] if result else template
