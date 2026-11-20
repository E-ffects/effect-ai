"""Contract: rma_agent outputs keep the fields Spring reads (Spring_API_v4 5-2) and samples/*.json match the live output.

Spring's MockAiClient serves samples/*.json, so a sample that drifts from the real output would only break when
Spring switches to ai.mode=http. These tests catch that here.
"""
import json
from pathlib import Path

import pytest

import rma_agent

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SAMPLE_IDS = [p.stem for p in sorted(SAMPLES.glob("RMA-*.json"))]

# Fields Spring reads (Spring_API_v4 5-2, 4-7)
SPRING_CLASSIFY = {"status", "major_code", "major_name", "minor_code", "minor_name", "evidence_spans",
                   "follow_up_questions", "warnings"}
SPRING_TRIAGE = {"severity", "characteristic_id", "characteristic_symbol", "customer_industry", "repeat_signal",
                 "analysis_level", "needs_approval", "approval_reasons", "provisional", "rationale", "as_of"}
SPRING_ANALYSIS = {"status", "as_of", "mode", "result", "trace", "warnings", "langsmith_run_id"}
SPRING_SERVICE_ACTION = {"action_type", "board_qty", "basis", "similar_case_count", "expected_days"}
RESULT_KEYS = {"classification", "qa_review", "trace_summary", "cause_candidates", "escape_candidates",
               "candidate_lots", "recurrence_check", "similar_cases", "drafts"}


def load(rma_id):
    return json.loads((SAMPLES / f"{rma_id}.json").read_text(encoding="utf-8"))


def test_index_lists_every_sample():
    index = json.loads((SAMPLES / "index.json").read_text(encoding="utf-8"))
    assert sorted(i["rma_id"] for i in index) == SAMPLE_IDS
    for item in index:
        s = load(item["rma_id"])
        assert item["classify_status"] == s["classify"]["status"]
        assert item["triage_level"] == s["triage"]["analysis_level"]
        assert item["triage_provisional"] == s["triage"]["provisional"]
        assert item["analysis_status"] == (s["analysis"]["status"] if "analysis" in s else None)
    assert any(i["triage_provisional"] for i in index)  # Spring needs one provisional case to test 4-6


@pytest.mark.parametrize("rma_id", SAMPLE_IDS)
def test_sample_has_spring_fields(rma_id):
    s = load(rma_id)
    assert SPRING_CLASSIFY <= set(s["classify"])
    assert SPRING_TRIAGE <= set(s["triage"])
    if "analysis" not in s:  # insufficient-info intake: re-asked, not analysed until QA confirms the triage
        assert s["classify"]["status"] != "CLASSIFIED" and s["triage"]["provisional"]
        return
    assert SPRING_ANALYSIS <= set(s["analysis"])
    assert set(s["analysis"]["result"]) == RESULT_KEYS
    action = s["analysis"]["result"]["drafts"]["service_action"]
    assert action is None or SPRING_SERVICE_ACTION <= set(action)


@pytest.mark.parametrize("rma_id", SAMPLE_IDS)
def test_sample_triage_matches_live(rma_id):
    """Triage is rule-based, so the sample must equal the live answer at the sample's as_of."""
    s = load(rma_id)
    assert rma_agent.triage(rma_id, as_of=s["triage"]["as_of"]) == s["triage"]


def test_live_classify_keeps_sample_fields():
    s = load("RMA-08927")
    live = rma_agent.classify(s["claim"]["raw_text"], detection_stage=s["claim"]["detection_stage"],
                              part_no=s["claim"]["reported_part_no"], customer_id=s["claim"]["customer_id"])
    assert set(live) == set(s["classify"])


@pytest.fixture(scope="module")
def live_analysis():
    s = load("RMA-08927")
    return s, rma_agent.run_analysis("RMA-08927", as_of=s["analysis"]["as_of"])


def _shape(value, depth=3):
    """Key structure of nested dicts (and the first item of lists), to compare outputs without their values."""
    if depth == 0:
        return None
    if isinstance(value, dict):
        return {k: _shape(v, depth - 1) for k, v in value.items()}
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return [_shape(value[0], depth - 1)]
    return None


def test_live_analysis_has_the_sample_shape(live_analysis):
    sample, live = live_analysis
    assert set(live) == set(sample["analysis"])
    for key in RESULT_KEYS - {"similar_cases"}:  # nullable parts compared only when both have them
        a, b = live["result"][key], sample["analysis"]["result"][key]
        if a is not None and b is not None:
            assert _shape(a) == _shape(b), key
    assert live["as_of"] == sample["analysis"]["as_of"]


def test_live_analysis_ranks_like_the_sample(live_analysis):
    """Ranking and evidence come from rules, not the LLM, so they must not drift from the sample."""
    sample, live = live_analysis
    codes = lambda out: [c["cause_code"] for c in out["result"]["cause_candidates"]]
    assert codes(live) == codes(sample["analysis"])
    assert live["result"]["qa_review"]["analysis_level"] == sample["analysis"]["result"]["qa_review"]["analysis_level"]
    action = lambda out: {k: out["result"]["drafts"]["service_action"][k] for k in SPRING_SERVICE_ACTION}
    assert action(live) == action(sample["analysis"])
