"""QA triage proposal for new intakes (rma_agent.triage): the analysis_level_rule on the intake label."""
import pytest

from rma_agent import triage
from rma_agent.agents.runtime import get_runtime
from rma_agent.errors import RmaAgentError


def stored(rma_id):
    return get_runtime().conn.execute("""select severity, characteristic_symbol, repeat_signal, analysis_level, rationale
                                         from qa_triage where rma_id=?""", (rma_id,)).fetchone()


def test_matches_the_stored_triage():
    p = triage("RMA-08927")
    assert (p["severity"], p["characteristic_symbol"], p["repeat_signal"], p["analysis_level"], p["rationale"]) == tuple(stored("RMA-08927"))
    assert p["needs_approval"] and "차량 고객 + 심각도 8 이상" in p["approval_reasons"] and not p["provisional"]


def test_works_before_qa_triage_exists():
    """Between the intake label (11:46) and the QA triage (12:41) there is no qa_triage yet: like a new intake."""
    p = triage("RMA-08927", as_of="2025-11-20T12:00:00+09:00")
    assert p["analysis_level"] == "L3" and p["as_of"] == "2025-11-20T12:00:00+09:00"


def test_unconfirmed_symptom_gets_provisional_l2():
    conn = get_runtime().conn
    rma_id = conn.execute("""select i.rma_id from rma_item i join symptom_assessment s using(rma_item_id)
                             where s.intake_label='INSUFFICIENT_INFO' order by i.rma_id limit 1""").fetchone()[0]
    p = triage(rma_id)
    assert p["analysis_level"] == "L2" and p["provisional"] and p["severity"] is None and not p["needs_approval"]


@pytest.mark.parametrize("kwargs,code", [
    ({"rma_id": "RMA-99999"}, "CLAIM_NOT_FOUND"),
    ({"rma_id": "RMA-08927", "as_of": "2025-11-20T10:00:00+09:00"}, "AS_OF_INVALID"),   # opened, label not saved yet
    ({"rma_id": "RMA-08927", "as_of": "2025-11-20 12:00"}, "AS_OF_INVALID"),
    ({"rma_id": ""}, "INVALID_INPUT"),
])
def test_errors(kwargs, code):
    with pytest.raises(RmaAgentError) as e:
        triage(**kwargs)
    assert e.value.code == code


def test_analysis_approval_reasons_match_the_triage():
    """Approval reasons are the L3 conditions only (repeat and SC are L2 conditions), in triage and in analysis."""
    from rma_agent import run_analysis, triage
    out = run_analysis("RMA-08927")
    review, proposal = out["result"]["qa_review"], triage("RMA-08927")
    assert review["approval_reasons"] == proposal["approval_reasons"] == ["차량 고객 + 심각도 8 이상"]
    assert proposal["repeat_signal"] == 1 and "3건째" in proposal["rationale"]
    l2 = run_analysis("RMA-08218")["result"]["qa_review"]
    assert l2["analysis_level"] == "L2" and l2["approval_reasons"] == [] and not l2["needs_approval"]
