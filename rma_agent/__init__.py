"""PCB RMA analysis agents. FastAPI calls only the four functions below (docs/api_spec.md 1-5)."""


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
