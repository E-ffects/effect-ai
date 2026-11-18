"""Agent graph tests: output follows docs/api_spec.md 3-3, uses only data known at as_of, and is deterministic."""
import random

import pytest

from rma_agent import run_analysis, similar_cases
from rma_agent.agents.runtime import get_runtime

AGENT_ORDER = ["ORCHESTRATOR", "QA", "MANUFACTURING", "CAUSE_ANALYSIS", "QA", "MANUFACTURING", "CS"]


def sample_claims(n=25, seed=11):
    conn = get_runtime().conn
    ids = [r[0] for r in conn.execute("select rma_id from rma_claim where opened_at>='2025-06-01' order by rma_id")]
    return random.Random(seed).sample(ids, n)


@pytest.fixture(scope="module")
def example():
    progress = []
    out = run_analysis("RMA-08743", on_progress=lambda agent, done, total: progress.append((agent, done, total)))
    return out, progress


def test_response_fields(example):
    out, _ = example
    assert out["status"] == "SUCCEEDED" and out["as_of"] == "2025-11-20T14:40:46+09:00"
    result = out["result"]
    assert set(result) == {"classification", "qa_review", "trace_summary", "cause_candidates",
                           "escape_candidates", "similar_cases", "drafts"}
    for c in result["cause_candidates"]:
        assert {"rank", "status", "source", "cause_code", "process_id", "equipment_id", "score", "explanation",
                "evidence"} <= set(c)
        assert c["status"] == "PROPOSED" and c["source"] == "AI" and 0 <= c["score"] <= 1
        assert not any(k.startswith("_") for k in c)
    assert [c["rank"] for c in result["qa_review"]["candidate_checks"]] == [c["rank"] for c in result["cause_candidates"]]


def test_example_answer(example):
    """RMA-08743 (P13 follow-up): the spec's example. Outer-layer etch residue on OLE-02, E-Test fixture escape on ET-02."""
    result = example[0]["result"]
    top = result["cause_candidates"][0]
    assert (top["cause_code"], top["process_id"], top["equipment_id"]) == ("OL_ETCH_RESIDUE", "OL_ETCH", "OLE-02")
    assert result["qa_review"]["analysis_level"] == "L3" and result["qa_review"]["needs_approval"]
    assert result["escape_candidates"][0]["equipment_id"] == "ET-02"


def test_trace_and_progress(example):
    out, progress = example
    assert [s["agent"] for s in out["trace"]] == AGENT_ORDER
    assert [s["step"] for s in out["trace"]] == list(range(1, 8))
    assert sorted(done for _, done, _ in progress) == list(range(1, 8)) and all(t == 7 for _, _, t in progress)


@pytest.mark.parametrize("rma_id", sample_claims())
def test_no_future_evidence(rma_id):
    out = run_analysis(rma_id)
    as_of, result = out["as_of"], out["result"]
    evidence = [e for c in result["cause_candidates"] for e in c["evidence"]]
    evidence += [e for c in result["escape_candidates"] for e in c["evidence"]]
    for e in evidence:
        assert e["occurred_at"] is None or e["occurred_at"] <= as_of, e
    for case in result["similar_cases"]:
        assert case["opened_at"] <= as_of


def test_deterministic():
    first, second = run_analysis("RMA-08743"), run_analysis("RMA-08743")
    assert first["result"] == second["result"]


def test_mode_a_falls_back_to_b():
    out = run_analysis("RMA-08743", mode="A")
    assert "MODE_A_NOT_READY" in out["warnings"] and out["result"]["cause_candidates"]


def test_similar_cases_known_before_as_of():
    out = similar_cases("RMA-08743", limit=5)
    assert 0 < len(out["items"]) <= 5
    assert all(item["opened_at"] <= out["as_of"] for item in out["items"])


@pytest.mark.parametrize("kwargs, error", [
    ({"rma_id": "RMA-99999"}, "CLAIM_NOT_FOUND"),
    ({"rma_id": "RMA-08743", "as_of": "2025-11-01T00:00:00+09:00"}, "CLAIM_NOT_FOUND"),   # before it was opened
    ({"rma_id": "RMA-08743", "as_of": "2025-11-20T10:00:00+09:00"}, "AS_OF_INVALID"),     # opened, not triaged yet
    ({"rma_id": "RMA-08743", "as_of": "2025-11-20 14:40"}, "AS_OF_INVALID"),              # no time zone
    ({"rma_id": "RMA-08743", "as_of": "yesterday"}, "AS_OF_INVALID"),
    ({"rma_id": "RMA-08743", "mode": "C"}, "INVALID_INPUT"),
    ({"rma_id": "", "top_k": 3}, "INVALID_INPUT"),
])
def test_errors_carry_spec_codes(kwargs, error):
    from rma_agent.errors import RmaAgentError
    with pytest.raises(RmaAgentError) as info:
        run_analysis(**kwargs)
    assert info.value.code == error


def test_error_body_matches_frontend():
    from rma_agent.errors import ClaimNotFound
    assert ClaimNotFound("RMA-99999 접수를 찾을 수 없습니다").body() == {
        "detail": "RMA-99999 접수를 찾을 수 없습니다", "code": "CLAIM_NOT_FOUND"}
