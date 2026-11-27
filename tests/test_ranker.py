"""Learned cause reordering (rma_agent/ranker.py, WBS 4.12): off by default, reorders only C's top 3, uses only as_of data."""
import json

import pytest

from rma_agent import ranker, run_analysis
from rma_agent.agents import cause_analysis, manufacturing
from rma_agent.agents.runtime import get_runtime

MODEL = ranker.ROOT / "models" / "cause_ranker_v6.json"


def ranked(rma_id, use, top_k=10):
    from rma_agent.asof import analysis_start
    rt = get_runtime()
    as_of = analysis_start(rt.conn, rma_id)
    store = rt.store(as_of, rma_id)
    claim = store.claim(rma_id)
    lot = manufacturing.trace(rt.masters, store, rt.baseline, claim)
    return cause_analysis.rank_causes(rt.masters, store, claim, lot, top_k=top_k, ranker=use)


@pytest.fixture(scope="module")
def model():
    return ranker.Ranker.load(MODEL)


def claims_with_choice(n=40):
    conn = get_runtime().conn
    return [r[0] for r in conn.execute(
        "select rma_id from rma_claim where opened_at>='2026-10-01' order by rma_id limit ?", (n,))]


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("RMA_CAUSE_RANKER", raising=False)
    assert ranker.active() is None
    monkeypatch.setenv("RMA_CAUSE_RANKER", "off")
    assert ranker.active() is None
    out = run_analysis("RMA-08927")
    assert out["model_versions"]["cause_ranker"] is None


def test_model_file_matches_the_code(model):
    raw = json.loads(MODEL.read_text(encoding="utf-8"))
    assert raw["features"] == ranker.NAMES and raw["head"] == ranker.HEAD
    assert raw["trained_on"].endswith("< 2026-09-01")  # test period 2026-10~ stays unseen
    with pytest.raises(ValueError):
        ranker.Ranker({**raw, "features": raw["features"][:-1]})


def test_only_the_top_three_are_reordered(model):
    changed = 0
    for rma_id in claims_with_choice():
        before, after = ranked(rma_id, None), ranked(rma_id, model)
        assert {c["cause_code"] for c in before[:3]} == {c["cause_code"] for c in after[:3]}
        assert [c["cause_code"] for c in before[3:]] == [c["cause_code"] for c in after[3:]]
        assert [c["rank"] for c in after] == list(range(1, len(after) + 1))
        head = [c["score"] for c in after[:3]]
        assert head == sorted(head, reverse=True) and all(0 <= s <= 1 for s in head)
        changed += before[0]["cause_code"] != after[0]["cause_code"]
    assert changed > 0  # the model does change some first choices


def test_top_k_below_three_still_reorders_within_three(model):
    for rma_id in claims_with_choice(15):
        assert ranked(rma_id, model, top_k=1)[0]["cause_code"] == ranked(rma_id, model, top_k=3)[0]["cause_code"]


def test_features_use_only_cases_known_at_as_of(model):
    """The learned counts come from AsOfStore.past_cases, so moving as_of earlier can only shrink them."""
    from rma_agent.asof import analysis_start
    rt = get_runtime()
    rma_id = "RMA-08927"
    late = analysis_start(rt.conn, rma_id)
    early = rt.store("2026-06-01T00:00:00+09:00", rma_id)
    store = rt.store(late, rma_id)
    claim = store.claim(rma_id)
    now, before = ranker.Context(store, claim), ranker.Context(early, claim)
    for field in ("stage", "spec", "customer", "lot"):
        a, b = getattr(now, field), getattr(before, field)
        assert all(b[k] <= a[k] for k in b), field
    assert sum(before.stage.values()) < sum(now.stage.values())


def test_analysis_reports_the_ranker(monkeypatch):
    monkeypatch.setenv("RMA_CAUSE_RANKER", str(MODEL))
    out = run_analysis("RMA-08927")
    assert out["model_versions"]["cause_ranker"] == "cause-lr-2026-09-01"
    cause_step = next(t for t in out["trace"] if t["agent"] == "CAUSE_ANALYSIS")
    assert "학습 모델(cause-lr-2026-09-01)로 재정렬" in cause_step["action"]
    assert out["result"]["cause_candidates"][0]["cause_code"] == "OL_ETCH_RESIDUE"
