"""PCB RMA analysis agents. FastAPI calls only the functions below (docs/api_spec.md 1-5)."""


def run_analysis(rma_id, as_of=None, mode="B", top_k=3, on_progress=None):
    from .agents.graph import run_analysis as run
    return run(rma_id, as_of=as_of, mode=mode, top_k=top_k, on_progress=on_progress)


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
