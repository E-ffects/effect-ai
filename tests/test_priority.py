"""Priority scores (rma_agent.priority, WBS 4.3 / FR-03): weights, time control, CC pinning, basis."""
import pytest

from rma_agent import pattern_priority, response_priority
from rma_agent.agents.runtime import get_runtime
from rma_agent.errors import RmaAgentError

END = "2025-12-31T18:56:13+09:00"
SEP = "2025-09-30T23:59:59+09:00"


def test_pattern_score_is_the_weighted_sum_of_parts():
    res = pattern_priority(as_of=END)
    assert res["weights"] == {"count": 0.3, "loss": 0.3, "severity": 0.2, "trend": 0.2}
    items = res["items"]
    assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
    assert [i["score"] for i in items] == sorted((i["score"] for i in items), reverse=True)
    for it in items:
        assert it["score"] == pytest.approx(sum(p["points"] for p in it["parts"].values()), abs=0.2)
        assert all(0 <= p["score"] <= 1 and p["basis"] for p in it["parts"].values())
    top = items[0]
    assert top["main_symptom"] == top["parts"]["trend"]["basis"].split("(")[1].split(")")[0]
    assert top["parts"]["count"]["basis"].startswith("최근 30일")


def test_pattern_uses_only_what_was_known_at_as_of():
    conn = get_runtime().conn
    res = pattern_priority(as_of=SEP)
    ids = {i["investigation_id"] for i in res["items"]}
    assert "INV-25-0020" not in ids  # opened later
    assert "INV-25-0014" in ids  # closed later, open at SEP
    for it in res["items"]:
        n = conn.execute("""select count(*) from investigation_claim l join rma_claim r using(rma_id)
                            where l.investigation_id=? and l.linked_at <= ? and r.opened_at <= ?""",
                         (it["investigation_id"], SEP, SEP)).fetchone()[0]
        assert it["total_claim_count"] == n and it["claim_count"] <= n
    closed_later = next(i for i in res["items"] if i["investigation_id"] == "INV-25-0014")
    assert closed_later["status"] is None and closed_later["status_name"] == "진행 중"


def test_weights_are_settings():
    res = pattern_priority(as_of=END, weights={"loss": 1, "count": 0, "severity": 0, "trend": 0})
    losses = [i["loss_krw"] for i in res["items"]][:10]  # small ones tie after rounding to 0.1 point
    assert losses == sorted(losses, reverse=True)
    with pytest.raises(RmaAgentError):
        pattern_priority(weights={"recurrence": 0.2})
    with pytest.raises(RmaAgentError):
        response_priority(weights={"severity": -1})


def test_response_priority_pins_safety_returns_first():
    items = response_priority(as_of=END)["items"]
    pinned = [i["pinned"] for i in items]
    assert any(pinned) and pinned == sorted(pinned, reverse=True)
    assert all("안전 특성(CC)" in i["reason"] for i in items if i["pinned"])
    assert all(i["cs_status"] != "CLOSED" for i in items)
    rest = [i["score"] for i in items if not i["pinned"]]
    assert rest == sorted(rest, reverse=True)
    assert response_priority(as_of=END, limit=5)["items"] == items[:5]
