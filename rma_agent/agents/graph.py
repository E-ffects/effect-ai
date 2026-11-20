"""B-flow analysis graph (LangGraph) and the run_analysis entry point used by FastAPI.

    START -> orchestrator -> qa_triage ─┐
                          -> mfg_trace ─┴-> cause_analysis -> qa_review -> mfg_improvement -> cs_reply -> END

qa_triage and mfg_trace do not depend on each other and run in parallel.
The flow is fixed; ranking and evidence checks are rules. Sentences (explanations, review note, improvement,
reply) are written by the LLM (agents/llm.py) and fall back to templates when it is off or fails.

LangSmith: set LANGSMITH_API_KEY (and LANGSMITH_PROJECT). The graph and every tool call are then traced, and
the root run id is returned as langsmith_run_id.
"""
import operator
import os
import threading
import time
import uuid
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from . import cause_analysis, cs, llm, manufacturing, qa
from .runtime import get_runtime
from .. import ranker
from ..asof import analysis_start
from ..errors import AsOfInvalid, ClaimNotFound, InvalidInput, ServiceUnavailable, check_as_of

PROMPT_VERSION = "agents-0.4"
RULES_VERSION = "pcb-v5"
STEPS_TOTAL = 7


class AnalysisState(TypedDict, total=False):
    rma_id: str
    as_of: str
    mode: str
    top_k: int
    claim: dict
    classification: dict
    triage: dict
    lot_trace: dict
    candidates: list
    similar: list
    escapes: list
    qa_review: dict
    recurrence: dict
    candidate_lots: dict
    improvement: dict
    customer_reply: str
    service_action: dict | None
    trace: Annotated[list, operator.add]
    warnings: Annotated[list, operator.add]


def _agent(order, agent):
    """Wrap a node: time it, append one trace entry, report progress.

    The node returns (update, action, tools) or (update, action, tools, llm_used)."""
    def wrap(fn):
        def node(state, config):
            conf = config["configurable"]
            rt = conf["runtime"]
            started = time.perf_counter()
            update, action, tools, *used = fn(state, rt, rt.store(state["as_of"], state["rma_id"]), conf["llm"])
            entry = {"order": order, "agent": agent, "action": action, "tools": tools, "llm_used": bool(used and used[0]),
                     "elapsed_ms": round((time.perf_counter() - started) * 1000)}
            progress = conf.get("progress")
            if progress:
                progress(agent)
            return {**update, "trace": [entry]}
        node.__name__ = fn.__name__
        return node
    return wrap


@_agent(1, "ORCHESTRATOR")
def orchestrator(state, rt, store, outcome):
    claim = store.claim(state["rma_id"])
    classification = cs.classification(rt.masters, store, claim)
    return ({"claim": claim, "classification": classification},
            f"as_of 고정, CS 분류 결과({classification['minor_code']}) 수신", ["claim", "intake"])


@_agent(2, "QA")
def qa_triage(state, rt, store, outcome):
    t = qa.triage(store, state["claim"])
    return {"triage": t}, f"분석 수준 판정 {t['analysis_level']} ({t['rationale']})", ["triage_rules"]


@_agent(3, "MANUFACTURING")
def mfg_trace(state, rt, store, outcome):
    lot = manufacturing.trace(rt.masters, store, rt.baseline, state["claim"])
    flagged = [s["process_id"] for s in lot["steps"] if s["signal"] != "none"]
    action = (f"Lot {lot['lot_id']} 추적: 공정 {len(lot['steps'])}단계 (이상 신호 {', '.join(flagged) or '없음'}), "
              f"검사 {sum(len(v) for v in lot['inspections'].values())}대, 자재 {len(lot['materials'])}건"
              if lot["lot_id"] else "Lot 정보 없음: 공정·검사 추적 생략")
    warnings = [] if lot["lot_id"] else ["NO_LOT"]
    return ({"lot_trace": lot, "warnings": warnings}, action,
            ["lot_process_history", "process_signals", "equipment_events", "lot_tests", "material_usage"])


@_agent(4, "CAUSE_ANALYSIS")
def cause_node(state, rt, store, outcome):
    candidates = cause_analysis.rank_causes(rt.masters, store, state["claim"], state["lot_trace"], state["top_k"])
    before = outcome.n_used
    cause_analysis.explain_with_llm(candidates, state["claim"], state["classification"], outcome)
    similar = cause_analysis.similar_case_items(rt.masters, store, state["claim"])
    warnings = [] if candidates else ["NO_CONFIRMED_CASE"]
    if state["classification"].get("minor_code") in cs.NOT_CLASSIFIED:
        warnings.append("SYMPTOM_NOT_CONFIRMED")  # candidates are for reference until the customer answers
    top = ", ".join(c["cause_code"] for c in candidates) or "없음"
    learned = ranker.active()
    how = f", 상위 3개는 학습 모델({learned.name})로 재정렬" if learned and len(candidates) > 1 else ""
    return ({"candidates": candidates, "similar": similar, "warnings": warnings},
            f"이전 사례 {len(store.past_cases(state['claim'].get('intake_label')))}건으로 원인 후보 순위: {top}{how}",
            ["past_cases", "similar_cases", "process_signals"], outcome.n_used > before)


@_agent(5, "QA")
def qa_review(state, rt, store, outcome):
    review = qa.review(rt.masters, store, state["claim"], state["candidates"], state["triage"])
    recurrence = qa.recurrence_check(rt.masters, store, state["claim"], state["lot_trace"], state["candidates"], review)
    before = outcome.n_used
    qa.note_with_llm(review, state["candidates"], outcome)
    action = ("후보별 근거 검토" + (", 사람 승인 요청 (L3)" if review["needs_approval"] else ", QA 확인 대상")
              + f", 재발 판정 {recurrence['status']}")
    return ({"qa_review": review, "recurrence": recurrence}, action,
            ["causal_requirements", "item_lab_tests", "recurrence_check"], outcome.n_used > before)


@_agent(6, "MANUFACTURING")
def mfg_improvement(state, rt, store, outcome):
    escape_list = manufacturing.escapes(rt.masters, store, state["claim"], state["lot_trace"], state["candidates"])
    lots, total = manufacturing.candidate_lots(rt.masters, store, state["claim"], state["lot_trace"], state["candidates"])
    plan = manufacturing.improvement(rt.masters, state["candidates"], escape_list)
    before = outcome.n_used
    manufacturing.improve_with_llm(plan, state["candidates"], escape_list, outcome)
    targets = [c["cause_code"] for c in state["candidates"][:1]] + [e["escape_code"] for e in escape_list]
    candidate = {"window_hours": manufacturing.SHARED_WINDOW_HOURS, "total": total, "items": lots}
    return ({"escapes": escape_list, "improvement": plan, "candidate_lots": candidate},
            f"공정·검사 개선안 초안 ({', '.join(targets) or '대상 없음'}), 공통점 있는 다른 Lot {total}개",
            ["escape_assessment", "detection_rate", "standard_actions", "candidate_lots"],
            outcome.n_used > before)


@_agent(7, "CS")
def cs_reply(state, rt, store, outcome):
    template = cs.reply_draft(rt.masters, state["claim"], state["classification"], state["candidates"], state["qa_review"])
    before = outcome.n_used
    text = cs.reply_with_llm(template, state["claim"], state["classification"], state["candidates"],
                             state["qa_review"], outcome)
    proposal = cs.service_action(store, state["claim"], state["candidates"], state["similar"])
    return ({"customer_reply": text, "service_action": proposal},
            "QA 검토 결과로 고객 회신 초안 작성" + (f", 처리방안 제안 {proposal['action_name']}" if proposal else ""),
            ["reply_template", "service_actions"],
            outcome.n_used > before)


def build_graph():
    g = StateGraph(AnalysisState)
    for name, fn in (("orchestrator", orchestrator), ("qa_triage", qa_triage), ("mfg_trace", mfg_trace),
                     ("cause_analysis", cause_node), ("qa_review", qa_review),
                     ("mfg_improvement", mfg_improvement), ("cs_reply", cs_reply)):
        g.add_node(name, fn)
    g.add_edge(START, "orchestrator")
    g.add_edge("orchestrator", "qa_triage")
    g.add_edge("orchestrator", "mfg_trace")
    g.add_edge(["qa_triage", "mfg_trace"], "cause_analysis")
    g.add_edge("cause_analysis", "qa_review")
    g.add_edge("qa_review", "mfg_improvement")
    g.add_edge("mfg_improvement", "cs_reply")
    g.add_edge("cs_reply", END)
    return g.compile()


GRAPH = build_graph()


def _tracing_on():
    if os.environ.get("LANGSMITH_API_KEY") and "LANGSMITH_TRACING" not in os.environ:
        os.environ["LANGSMITH_TRACING"] = "true"
    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true" and bool(os.environ.get("LANGSMITH_API_KEY"))


def _public(candidate):
    return {k: v for k, v in candidate.items() if not k.startswith("_")}


def _runtime():
    try:
        return get_runtime()
    except Exception as exc:  # DB file missing or unreadable
        raise ServiceUnavailable(f"DB를 열 수 없습니다: {exc}") from exc


def _resolve(rt, rma_id, as_of):
    """Check the claim and as_of before running anything. Returns the as_of to use."""
    if not rma_id or not isinstance(rma_id, str):
        raise InvalidInput("rma_id가 비어 있습니다")
    as_of = check_as_of(as_of)
    row = rt.conn.execute("select opened_at from rma_claim where rma_id=?", (rma_id,)).fetchone()
    if row is None:
        raise ClaimNotFound(f"{rma_id} 접수를 찾을 수 없습니다")
    if as_of and as_of < row["opened_at"]:
        raise ClaimNotFound(f"{rma_id}는 {as_of} 이전에 접수되지 않았습니다 (접수 {row['opened_at']})")
    try:
        start = analysis_start(rt.conn, rma_id)
    except KeyError:
        raise AsOfInvalid(f"{rma_id}는 아직 QA 접수 판정·분류가 끝나지 않아 분석할 수 없습니다")
    if as_of and as_of < start:
        raise AsOfInvalid(f"{rma_id}는 {start}부터 분석할 수 있습니다 (QA 접수 판정·분류 완료 시각)")
    return as_of or start


def triage(rma_id, as_of=None):
    """QA triage proposal for a return (docs/api_spec.md 3-6): severity, special characteristic, analysis level.

    For new intakes, which have no qa_triage until QA confirms one. Needs the intake label (classify result saved in
    symptom_assessment). as_of defaults to when that label was saved.
    """
    if not rma_id or not isinstance(rma_id, str):
        raise InvalidInput("rma_id가 비어 있습니다")
    rt = _runtime()
    as_of = check_as_of(as_of)
    row = rt.conn.execute("""select r.opened_at, s.created_at from rma_claim r join rma_item i using(rma_id)
                             left join symptom_assessment s using(rma_item_id) where r.rma_id=?""", (rma_id,)).fetchone()
    if row is None:
        raise ClaimNotFound(f"{rma_id} 접수를 찾을 수 없습니다")
    if as_of and as_of < row["opened_at"]:
        raise ClaimNotFound(f"{rma_id}는 {as_of} 이전에 접수되지 않았습니다 (접수 {row['opened_at']})")
    if row["created_at"] is None:
        raise AsOfInvalid(f"{rma_id}는 아직 접수 분류(소분류)가 저장되지 않았습니다")
    if as_of and as_of < row["created_at"]:
        raise AsOfInvalid(f"{rma_id}는 {row['created_at']}부터 판정할 수 있습니다 (접수 분류 저장 시각)")
    as_of = as_of or row["created_at"]
    store = rt.store(as_of, rma_id)
    proposal = qa.propose_triage(rt.masters, store, store.claim(rma_id))
    return {**proposal, "as_of": as_of}


def run_analysis(rma_id, as_of=None, mode="B", top_k=3, on_progress=None):
    """Analyse one return. Returns the 3-3 response fields: result, trace, warnings, ... (spec docs/api_spec.md).

    Raises rma_agent.errors.* (InvalidInput, ClaimNotFound, AsOfInvalid, ServiceUnavailable) with the spec's codes.
    """
    if mode not in ("A", "B"):
        raise InvalidInput(f"mode는 A 또는 B입니다: {mode!r}")
    if not isinstance(top_k, int) or not 1 <= top_k <= 10:
        raise InvalidInput(f"top_k는 1~10입니다: {top_k!r}")
    rt = _runtime()
    as_of = _resolve(rt, rma_id, as_of)
    warnings = []
    if mode == "A":
        warnings.append("MODE_A_NOT_READY")  # A (autonomous cause analysis) is not built yet; runs B
    done, lock = [0], threading.Lock()

    def progress(agent):
        with lock:
            done[0] += 1
            n = done[0]
        if on_progress:
            on_progress(agent, n, STEPS_TOTAL)

    tracing = _tracing_on()
    outcome = llm.Outcome()
    run_id = uuid.uuid4()
    started = time.perf_counter()
    state = GRAPH.invoke(
        {"rma_id": rma_id, "as_of": as_of, "mode": mode, "top_k": top_k, "trace": [], "warnings": warnings},
        config={"configurable": {"runtime": rt, "progress": progress, "llm": outcome}, "run_id": run_id,
                "run_name": f"rma_analysis {rma_id}", "tags": [f"mode:{mode}", RULES_VERSION],
                "metadata": {"rma_id": rma_id, "as_of": as_of, "mode": mode}})
    claim = state["claim"]
    trace = sorted(state["trace"], key=lambda e: e["order"])
    warnings = list(dict.fromkeys(state["warnings"]))
    if outcome.failed:
        warnings.append("LLM_UNAVAILABLE")      # some sentences are templates; ranking and evidence unchanged
    if outcome.rejected:
        warnings.append("LLM_OUTPUT_REJECTED")  # an LLM sentence had a number not in the facts; template kept
    return {
        "rma_id": rma_id, "as_of": as_of, "mode": mode, "status": "PARTIAL" if outcome.failed else "SUCCEEDED",
        "result": {
            "classification": state["classification"],
            "qa_review": state["qa_review"],
            "trace_summary": {"lot_id": claim.get("lot_id"), "spec_id": claim.get("spec_id"),
                              "part_no": claim.get("reported_part_no")},
            "cause_candidates": [_public(c) for c in state["candidates"]],
            "escape_candidates": state["escapes"],
            "candidate_lots": state["candidate_lots"],
            "recurrence_check": state["recurrence"],
            "similar_cases": state["similar"],
            "drafts": {"customer_reply": state["customer_reply"], "improvement": state["improvement"],
                       "service_action": state["service_action"]},
        },
        "trace": [{"step": i, **{k: v for k, v in e.items() if k != "order"}} for i, e in enumerate(trace, 1)],
        "warnings": warnings,
        "langsmith_run_id": str(run_id) if tracing else None,
        "model_versions": {"llm": llm.model_name(), "prompt": PROMPT_VERSION, "rules": RULES_VERSION,
                           "cause_ranker": ranker.active().name if ranker.active() else None},
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def similar_cases(rma_id, as_of=None, limit=5):
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise InvalidInput(f"limit는 1~20입니다: {limit!r}")
    rt = _runtime()
    as_of = _resolve(rt, rma_id, as_of)
    store = rt.store(as_of, rma_id)
    claim = store.claim(rma_id)
    return {"rma_id": rma_id, "as_of": as_of,
            "items": cause_analysis.similar_case_items(rt.masters, store, claim, limit)}


def refresh_cases():
    from . import classifier
    count = _runtime().refresh()
    classifier._classifier = None  # rebuilt with the newly labelled intakes on next classify()
    return count
