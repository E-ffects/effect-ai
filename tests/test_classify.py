"""CS classification (POST /classify): retrieval fallback, LLM output checks, spec 3-1 fields."""
import pytest

from rma_agent import classify
from rma_agent.agents import llm
from rma_agent.errors import RmaAgentError

AS_OF = "2025-11-20T14:40:46+09:00"
SHORT = ("A01 수입검사 담당입니다. P/N PCB-ECU6-A1 (Lot LT-01603) SMT 리플로 공정에서 미세 패턴 사이 브리지 형태의 쇼트가 "
         "보입니다. 투입 1,750pcs 중 26pcs 불량입니다.")
VAGUE = "A03 품질팀입니다. P/N PCB-BMS4H-C2 ICT·기능검사에서 기능 검사에서 NG가 납니다. 투입 500pcs 중 7pcs 불량입니다."
FIELDS = {"status", "major_code", "major_name", "minor_code", "minor_name", "confidence", "evidence_spans", "severity",
          "characteristic_symbol", "alternatives", "follow_up_questions", "model_version", "elapsed_ms", "warnings"}


def test_classified_without_llm():
    out = classify(SHORT, detection_stage="SMT_REFLOW", as_of=AS_OF)
    assert set(out) == FIELDS
    assert (out["status"], out["minor_code"], out["major_code"], out["severity"]) == ("CLASSIFIED", "ELEC02", "ELEC", 8)
    assert out["evidence_spans"] and all(s in SHORT for s in out["evidence_spans"])


def test_insufficient_asks_again():
    out = classify(VAGUE, as_of=AS_OF)
    assert out["status"] == "INSUFFICIENT_INFO" and out["minor_code"] is None and out["follow_up_questions"]


@pytest.mark.parametrize("kwargs, code", [({"raw_text": "  "}, "INVALID_INPUT"),
                                          ({"raw_text": SHORT, "as_of": "2025-11-20"}, "AS_OF_INVALID")])
def test_invalid_input(kwargs, code):
    with pytest.raises(RmaAgentError) as info:
        classify(**kwargs)
    assert info.value.code == code


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(llm, "model_name", lambda: "fake:model")
    return monkeypatch


def test_llm_decision_checked(llm_on):
    llm_on.setattr(llm, "generate", lambda *a: {
        "minor_code": "ELEC02", "confidence": 0.9, "follow_up_question": None,
        "evidence_spans": ["미세 패턴 사이 브리지 형태의 쇼트가 보입니다", "원문에 없는 구절"],
        "alternatives": [{"minor_code": "ELEC05", "confidence": 0.05}, {"minor_code": "NOPE", "confidence": 0.05}]})
    out = classify(SHORT, as_of=AS_OF)
    assert out["minor_code"] == "ELEC02" and out["model_version"].endswith("+llm")
    assert out["evidence_spans"] == ["미세 패턴 사이 브리지 형태의 쇼트가 보입니다"]   # made-up span dropped
    assert [a["minor_code"] for a in out["alternatives"]] == ["ELEC05"]               # unknown code dropped


def test_llm_unknown_code_falls_back(llm_on):
    llm_on.setattr(llm, "generate", lambda *a: {"minor_code": "ELEC99", "confidence": 0.9, "evidence_spans": [],
                                                "alternatives": [], "follow_up_question": None})
    out = classify(SHORT, as_of=AS_OF)
    assert out["minor_code"] == "ELEC02" and "LLM_OUTPUT_REJECTED" in out["warnings"]


def test_llm_outage_falls_back(llm_on):
    def broken(*a):
        raise TimeoutError("down")
    llm_on.setattr(llm, "generate", broken)
    out = classify(SHORT, as_of=AS_OF)
    assert out["minor_code"] == "ELEC02" and out["warnings"] == ["LLM_UNAVAILABLE"]
