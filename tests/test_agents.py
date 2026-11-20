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
    out = run_analysis("RMA-08927", on_progress=lambda agent, done, total: progress.append((agent, done, total)))
    return out, progress


def test_response_fields(example):
    out, _ = example
    assert out["status"] == "SUCCEEDED" and out["as_of"] == "2025-11-20T12:41:37+09:00"
    result = out["result"]
    assert set(result) == {"classification", "qa_review", "trace_summary", "cause_candidates",
                           "escape_candidates", "candidate_lots", "recurrence_check", "similar_cases", "drafts"}
    for c in result["cause_candidates"]:
        assert {"rank", "status", "source", "cause_code", "process_id", "equipment_id", "score", "explanation",
                "evidence"} <= set(c)
        assert c["status"] == "PROPOSED" and c["source"] == "AI" and 0 <= c["score"] <= 1
        assert not any(k.startswith("_") for k in c)
    assert [c["rank"] for c in result["qa_review"]["candidate_checks"]] == [c["rank"] for c in result["cause_candidates"]]


def test_example_answer(example):
    """RMA-08927 (P13 follow-up, v5; v4 example was RMA-08743). Outer-layer etch residue on OLE-02, E-Test fixture escape on ET-02."""
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
    first, second = run_analysis("RMA-08927"), run_analysis("RMA-08927")
    assert first["result"] == second["result"]


def test_mode_a_falls_back_to_b():
    out = run_analysis("RMA-08927", mode="A")
    assert "MODE_A_NOT_READY" in out["warnings"] and out["result"]["cause_candidates"]


def test_similar_cases_known_before_as_of():
    out = similar_cases("RMA-08927", limit=5)
    assert 0 < len(out["items"]) <= 5
    assert all(item["opened_at"] <= out["as_of"] for item in out["items"])


@pytest.mark.parametrize("kwargs, error", [
    ({"rma_id": "RMA-99999"}, "CLAIM_NOT_FOUND"),
    ({"rma_id": "RMA-08927", "as_of": "2025-11-01T00:00:00+09:00"}, "CLAIM_NOT_FOUND"),   # before it was opened
    ({"rma_id": "RMA-08927", "as_of": "2025-11-20T10:00:00+09:00"}, "AS_OF_INVALID"),     # opened 08:56, triaged 12:41
    ({"rma_id": "RMA-08927", "as_of": "2025-11-20 14:40"}, "AS_OF_INVALID"),              # no time zone
    ({"rma_id": "RMA-08927", "as_of": "yesterday"}, "AS_OF_INVALID"),
    ({"rma_id": "RMA-08927", "mode": "C"}, "INVALID_INPUT"),
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


def test_evidence_spans_come_from_the_customer_text():
    """INSUFFICIENT_INFO claims store the QA-confirmed symptom's phrase; it must not show up as intake evidence."""
    conn = get_runtime().conn
    rma_id = conn.execute("""select i.rma_id from rma_item i join symptom_assessment s using(rma_item_id)
                             where s.intake_label='INSUFFICIENT_INFO' order by i.rma_id limit 1""").fetchone()[0]
    out = run_analysis(rma_id)
    text = conn.execute("select raw_text from rma_claim where rma_id=?", (rma_id,)).fetchone()[0]
    assert all(s in text for s in out["result"]["classification"]["evidence_spans"])


def test_reply_asks_the_customer_when_symptom_is_not_classified():
    """INSUFFICIENT_INFO: the reply asks what happened and names no cause; candidates are flagged as provisional."""
    from rma_agent.agents.classifier import DEFAULT_QUESTION
    conn = get_runtime().conn
    rma_id = conn.execute("""select i.rma_id from rma_item i join symptom_assessment s using(rma_item_id)
                             where s.intake_label='INSUFFICIENT_INFO' order by i.rma_id limit 1""").fetchone()[0]
    out = run_analysis(rma_id)
    reply = out["result"]["drafts"]["customer_reply"]
    assert DEFAULT_QUESTION in reply
    assert not any(c["cause_name"] in reply for c in out["result"]["cause_candidates"])
    assert "SYMPTOM_NOT_CONFIRMED" in out["warnings"]


def test_reply_names_the_leading_cause_when_classified(example):
    out, _ = example
    assert out["result"]["cause_candidates"][0]["cause_name"] in out["result"]["drafts"]["customer_reply"]
    assert "SYMPTOM_NOT_CONFIRMED" not in out["warnings"]


def test_candidate_lots_share_something_known_by_as_of(example):
    out, _ = example
    lots = out["result"]["candidate_lots"]
    assert lots["window_hours"] == 24 and lots["total"] >= len(lots["items"]) > 0 and len(lots["items"]) <= 20
    first = lots["items"][0]
    assert first["matches_top_cause"] and first["shared"][0]["equipment_id"] == "OLE-02"  # leading cause's machine first
    for lot in lots["items"]:
        assert lot["lot_id"] != out["result"]["trace_summary"]["lot_id"] and lot["shared_count"] == len(lot["shared"])
        assert lot["started_at"] <= out["as_of"]
        assert lot["first_shipped_at"] is None or lot["first_shipped_at"] <= out["as_of"]
        for x in lot["shared"]:
            assert x["type"] in ("EQUIPMENT", "MATERIAL")
            if x["type"] == "EQUIPMENT":
                assert x["gap_hours"] <= 24


def test_recurrence_outside_the_action_scope():
    """P04R (v5): desmear fix applied on DSM-02 only; the cause came back on DSM-01."""
    rc = run_analysis("RMA-08218")["result"]["recurrence_check"]
    assert rc["status"] == "RECURRENCE"
    assert rc["corrective_action"]["scope"] == {"equipment": ["DSM-02"]}
    assert rc["checks"]["produced_after_action"] and rc["checks"]["in_action_scope"] is False
    assert "수평 전개" in rc["reason"]


def test_no_recurrence_verdict_without_a_tracked_action(example):
    out, _ = example  # outer-layer etch residue had no implemented action at this as_of
    assert out["result"]["recurrence_check"]["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("rma_id,status,suggestion", [
    ("RMA-08218", "RECURRENCE", "확산형 재발"),        # recurred outside the fixed machine
    ("RMA-07085", "PRE_ACTION_PRODUCT", "조치 전 생산품"),
    ("RMA-08927", "NOT_APPLICABLE", "신규"),           # no action on the cause yet
    ("RMA-07936", "PENDING_CAUSE_REVIEW", None),       # symptom not confirmed
])
def test_recurrence_suggestion_uses_the_screen_words(rma_id, status, suggestion):
    rc = run_analysis(rma_id)["result"]["recurrence_check"]
    assert (rc["status"], rc["suggestion"]) == (status, suggestion)


def test_similar_case_items_carry_screen_fields():
    out = similar_cases("RMA-08927", limit=5)
    for item in out["items"]:
        assert item["claim_date"] == item["opened_at"][:10] and item["opened_at"] <= out["as_of"]
        assert item["basis"].endswith("문장 유사") and item["basis"].startswith("같은 증상")
        assert isinstance(item["recurrence"], bool) and item["days_to_resolve"] >= 0
        assert item["cause_name"] and item["minor_name"]
    assert any("같은 Lot" in i["basis"] for i in out["items"])  # earlier returns from LT-01603


def test_service_action_from_similar_cases_known_by_as_of(example):
    """drafts.service_action (Spring_API_v4 4-7): from the handling of the similar cases on screen, decided by as_of."""
    out, _ = example
    res = out["result"]
    p = res["drafts"]["service_action"]
    assert set(p) >= {"action_type", "board_qty", "basis", "similar_case_count", "expected_days"}
    assert p["action_type"] in ("REPLACEMENT_SHIPMENT", "CREDIT_NOTE", "SORTING_SUPPORT_AND_CREDIT", "NO_CHARGE_SAMPLE_RETURN")
    conn = get_runtime().conn
    ids = [s["rma_id"] for s in res["similar_cases"]]
    rows = conn.execute(f"""select s.action_type, s.decided_at from service_action s join rma_item i using(rma_item_id)
                            where i.rma_id in ({','.join('?' * len(ids))})""", ids).fetchall()
    known = [r[0] for r in rows if r[1] <= out["as_of"]]
    assert p["similar_case_count"] == len(known) and known.count(p["action_type"]) == max(map(known.count, known))
    qty = conn.execute("select claimed_defect_qty from rma_item where rma_id='RMA-08927'").fetchone()[0]
    assert p["board_qty"] == (0 if p["action_type"] == "NO_CHARGE_SAMPLE_RETURN" else qty)
    assert all(c["cause_name"] not in p["basis"] for c in res["cause_candidates"])
    assert isinstance(p["expected_days"], int) and p["expected_days"] > 0
