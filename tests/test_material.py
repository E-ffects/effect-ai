"""Material causes: a lot using another lot of the same supplier's material is shown (SUPPLIER), not scored."""
from pathlib import Path

import pytest

from rma_agent.asof import AsOfStore, CaseLibrary
from rma_agent.db import connect
from rma_agent.evaluate import analysis_start
from rma_agent.predictors import CauseAgentC
from rma_agent.agents.cause_analysis import rank_causes
from rma_agent.agents.manufacturing import trace
from rma_agent.agents.qa import PRODUCTION_EVIDENCE

V5 = Path(__file__).resolve().parents[1] / "data" / "pcb_rma_v5.sqlite"


@pytest.mark.skipif(not V5.exists(), reason="v5 data not present")
def test_same_supplier_lot_is_evidence_not_score():
    conn = connect(V5)
    agent = CauseAgentC()
    agent._load(conn)
    rma_id = "RMA-09315"  # P09R: PSR ink from the same supplier as INV-26-0011, another lot
    store = AsOfStore(conn, analysis_start(conn, rma_id), CaseLibrary(conn), subject=rma_id)
    claim = store.claim(rma_id)
    ink = next(c for c in rank_causes(agent.masters, store, claim, trace(agent.masters, store, agent.baseline, claim), top_k=8)
               if c["cause_code"] == "SM_INK_LOT")
    supplier = [e for e in ink["evidence"] if e["type"] == "SUPPLIER"]
    assert supplier and "같은 공급사(SUP-INK-A)" in supplier[0]["summary"]
    assert "같은 공급사" in ink["explanation"]
    assert "SUPPLIER" not in PRODUCTION_EVIDENCE
    assert ink["score"] == 0.08  # x0.2 as before: 0.40 vote only, no recent case, no shared lot
