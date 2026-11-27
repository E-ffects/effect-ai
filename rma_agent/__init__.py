"""PCB RMA analysis agents. FastAPI calls only the functions below (docs/api_spec.md 1-5)."""


def run_analysis(rma_id, as_of=None, mode="B", top_k=3, on_progress=None):
    from .agents.graph import run_analysis as run
    return run(rma_id, as_of=as_of, mode=mode, top_k=top_k, on_progress=on_progress)


def analysis_as_of(rma_id, as_of=None):
    """Check that rma_id can be analysed at as_of (same checks as run_analysis) and return the as_of it would use.

    Lets the server answer POST /analyses with 404/422 right away instead of a FAILED job.
    """
    from .agents.graph import _resolve, _runtime
    return _resolve(_runtime(), rma_id, as_of)


def run_follow_up(rma_id, cause_code, as_of=None):
    """2차 회신 초안(후속 그래프): QA가 승인한 cause_code로 고객 문안과 처리방안을 만든다."""
    from .agents.graph import run_follow_up as run
    return run(rma_id, cause_code, as_of=as_of)


def similar_cases(rma_id, as_of=None, limit=5):
    from .agents.graph import similar_cases as find
    return find(rma_id, as_of=as_of, limit=limit)


def refresh_cases():
    from .agents.graph import refresh_cases as refresh
    return refresh()


def classify(raw_text, detection_stage=None, part_no=None, customer_id=None, as_of=None):
    from .agents.classifier import get_classifier
    return get_classifier().classify(raw_text, detection_stage=detection_stage, part_no=part_no,
                                     customer_id=customer_id, as_of=as_of)


def triage(rma_id, as_of=None):
    """QA triage proposal for a new intake: {severity, characteristic_symbol, analysis_level, needs_approval, rationale, ...}."""
    from .agents.graph import triage as propose
    return propose(rma_id, as_of=as_of)


def pattern_priority(as_of=None, weights=None, limit=None):
    """Quality action priority per open pattern (investigation), 0~100 with per-part basis (rma_agent/priority.py)."""
    from .priority import pattern_priority as score
    return score(as_of=as_of, weights=weights, limit=limit)


def response_priority(as_of=None, weights=None, limit=None):
    """Customer response priority per return with an open CS reply; safety (CC) returns pinned on top."""
    from .priority import response_priority as score
    return score(as_of=as_of, weights=weights, limit=limit)


def chat(request):
    """Agent panel chat (Spring AiClient.chat): request {thread_id, audience, scope{type,id}, message, history, context}.

    Stateless; answers only from the context Spring sends (already filtered by role) and the case library.
    Returns {status, intent, answer, evidence[], suggestions[], warnings[], model_version} (rma_agent/agents/chat.py).
    """
    from .agents.chat import chat as answer
    return answer(request)
