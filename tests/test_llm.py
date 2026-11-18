"""LLM layer with a fake model: sentences replaced, made-up numbers rejected, outages fall back to templates."""
import pytest

from rma_agent import run_analysis
from rma_agent.agents import llm


def fake_ok(task, instruction, facts, schema):
    if task == "explanations":
        return {"items": [{"cause_code": c["cause_code"], "explanation": f"LLM 설명: {c['원인']}"} for c in facts["원인 후보"]]}
    if task == "review_note":
        return {"review_note": "LLM 검토 의견"}
    if task == "improvement":
        return {"process_action": "LLM 공정 개선안", "inspection_action": "LLM 검사 개선안"}
    return {"customer_reply": "LLM 회신 초안"}


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(llm, "model_name", lambda: "fake:model")
    return monkeypatch


def test_sentences_from_llm(llm_on):
    llm_on.setattr(llm, "generate", fake_ok)
    out = run_analysis("RMA-08743")
    result = out["result"]
    assert out["status"] == "SUCCEEDED" and out["model_versions"]["llm"] == "fake:model" and not out["warnings"]
    assert result["cause_candidates"][0]["explanation"] == "LLM 설명: 외층 에칭 잔동"
    assert result["qa_review"]["review_note"] == "LLM 검토 의견"
    assert result["drafts"]["customer_reply"] == "LLM 회신 초안"
    process = result["drafts"]["improvement"]["process"][0]
    assert process["action"] == "LLM 공정 개선안" and process["standard_action"] != process["action"]
    assert [s["llm_used"] for s in out["trace"]] == [False, False, False, True, True, True, True]


def test_invented_number_rejected(llm_on):
    def fake(task, instruction, facts, schema):
        if task == "customer_reply":
            return {"customer_reply": "불량률 12.7%로 확인됐습니다."}  # 12.7 is not in the facts
        return fake_ok(task, instruction, facts, schema)
    llm_on.setattr(llm, "generate", fake)
    out = run_analysis("RMA-08743")
    assert "LLM_OUTPUT_REJECTED" in out["warnings"] and out["status"] == "SUCCEEDED"
    assert out["result"]["drafts"]["customer_reply"].startswith("A01 품질 담당자님")  # template kept
    assert out["trace"][-1]["llm_used"] is False


def test_outage_keeps_ranking(llm_on):
    llm_on.setattr(llm, "generate", fake_ok)
    baseline = run_analysis("RMA-08743")

    def broken(*args):
        raise TimeoutError("LLM down")
    llm_on.setattr(llm, "generate", broken)
    out = run_analysis("RMA-08743")
    assert out["status"] == "PARTIAL" and "LLM_UNAVAILABLE" in out["warnings"]
    ranks = lambda o: [(c["cause_code"], c["equipment_id"], c["score"]) for c in o["result"]["cause_candidates"]]
    assert ranks(out) == ranks(baseline) and out["result"]["cause_candidates"][0]["explanation"]


def test_invented_numbers():
    facts = {"근거": ["OLE-02 spray_pressure_bar -3.3σ", "ET_SHORT 검출률 1.90% (130/6,833)"]}
    assert llm.invented_numbers("압력이 3.3σ 낮고 검출률 1.90%, 6833건 중 130건", facts) == []
    assert llm.invented_numbers("검출률 2.5%", facts) == ["2.5"]


def test_blocked_models(monkeypatch):
    for name in ("openai:gpt-6-sol", "openai:GPT-5.5-pro", "gpt-6.1-sol"):
        monkeypatch.setenv("RMA_LLM", name)
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        assert llm.model_name() is None
