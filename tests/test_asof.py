"""Leakage tests: nothing an agent can read may postdate the analysis start of the claim."""
import random

import pytest

from rma_agent.asof import AsOfStore, CaseLibrary, FutureDataError, analysis_start
from rma_agent.db import connect


@pytest.fixture(scope="module")
def conn():
    return connect()


@pytest.fixture(scope="module")
def library(conn):
    return CaseLibrary(conn)


def sample_claims(conn, n=60, seed=7):
    ids = [r[0] for r in conn.execute("select rma_id from rma_claim order by rma_id")]
    return random.Random(seed).sample(ids, n)


def test_past_cases_known_before_as_of(conn, library):
    for rma_id in sample_claims(conn):
        store = AsOfStore(conn, analysis_start(conn, rma_id), library)
        for case in store.past_cases():
            assert case.applied_at <= store.as_of and case.approved_at <= store.as_of


def test_claim_itself_not_in_past_cases_before_its_cause_is_applied(conn, library):
    for rma_id in sample_claims(conn):
        store = AsOfStore(conn, analysis_start(conn, rma_id), library, subject=rma_id)
        assert rma_id not in {c.rma_id for c in store.past_cases()}
        assert store.past_case(rma_id) is None


def test_first_p13_claim_sees_no_p13_cause(conn, library):
    # RMA-08300 is the first P13 return (2025-10-29, v5); P13's cause was approved on 2025-11-19
    store = AsOfStore(conn, analysis_start(conn, "RMA-08300"), library)
    assert not [c for c in store.past_cases() if c.cause_code == "OL_ETCH_RESIDUE"]


def test_p13_follow_up_sees_p13_cause(conn, library):
    late = conn.execute("""select r.rma_id from rma_claim r join investigation_claim l using(rma_id)
                           where l.investigation_id='INV-25-0021' and r.opened_at>'2025-11-20' limit 1""").fetchone()[0]
    store = AsOfStore(conn, analysis_start(conn, late), library)
    cases = [c for c in store.past_cases() if c.cause_code == "OL_ETCH_RESIDUE"]
    assert cases and all(c.equipment_id == "OLE-02" for c in cases)


def test_lot_queries_known_before_as_of(conn, library):
    for rma_id in sample_claims(conn, n=25):
        store = AsOfStore(conn, analysis_start(conn, rma_id), library)
        claim = store.claim(rma_id)
        lot = claim["lot_id"]
        history = store.lot_process_history(lot)
        assert all(r["ended_at"] <= store.as_of for r in history)
        assert all(r["measured_at"] <= store.as_of for r in store.lot_observations(lot))
        events = store.equipment_events({r["equipment_id"] for r in history})
        assert all(e["recorded_at"] <= store.as_of for e in events)
        tests = store.lot_tests(lot)
        assert all(t["ended_at"] <= store.as_of for t in tests)
        assert all(m["ended_at"] <= store.as_of for m in store.test_measurements([t["test_run_id"] for t in tests[:20]]))
        assert all(u["issued_at"] <= store.as_of for u in store.material_usage(lot))
        assert all(s["searched_at"] <= store.as_of for s in store.similar_cases(rma_id))


def test_claim_from_the_future_is_refused(conn, library):
    store = AsOfStore(conn, "2025-01-01T00:00:00+09:00", library)
    with pytest.raises(FutureDataError):
        store.claim("RMA-09993")


def test_returned_sample_tests_hidden_until_finished(conn, library):
    # a returned-sample lab test is not visible at analysis start if it ended later
    rows = conn.execute("""select t.test_run_id, i.rma_id, t.lot_id from test_run t join rma_item i using(rma_item_id)
                           join qa_triage q on q.rma_id=i.rma_id
                           where t.phase='RMA_LAB' and t.ended_at>q.decided_at limit 20""").fetchall()
    assert rows, "expected returned-sample tests that finish after triage"
    for row in rows:
        store = AsOfStore(conn, analysis_start(conn, row["rma_id"]), library)
        visible = {t["test_run_id"] for t in store.lot_tests(row["lot_id"], phases=["RMA_LAB"])}
        assert row["test_run_id"] not in visible


def test_return_with_cause_applied_at_intake_is_not_its_own_case(conn, library):
    """Returns that join an already-approved incident get its cause at intake, before triage (v4: 117, v5: 209)."""
    rows = conn.execute("""select r.rma_id from rma_claim r join rma_item i using(rma_id) join qa_triage t using(rma_id)
                           join symptom_assessment s using(rma_item_id) join claim_cause cc using(rma_item_id)
                           join cause_assessment a using(cause_assessment_id)
                           where (case when cc.decided_at > a.approved_at then cc.decided_at else a.approved_at end)
                                 <= (case when t.decided_at > s.created_at then t.decided_at else s.created_at end) limit 30""").fetchall()
    assert rows
    for (rma_id,) in rows:
        store = AsOfStore(conn, analysis_start(conn, rma_id), library, subject=rma_id)
        assert rma_id not in {c.rma_id for c in store.past_cases()}
