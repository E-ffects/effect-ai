"""Learned reordering of the top cause candidates (WBS 4.12, results/20261120_cause_ranker_v5.md). Off by default.

What it does: rank_causes() ranks candidates with the hand-set score (C). With a ranker on, the first HEAD (3)
candidates of C are reordered by a logistic regression; candidates below are untouched. C's top-3 set is kept, so
"answer in top 3" cannot drop, only the order inside it changes (test 2026-10~: cause@1 78.2% -> 85.0%, cause@3 97.8%).

Turn on:   RMA_CAUSE_RANKER=models/cause_ranker_v6.json   (path relative to effect-ai, or absolute; unset/off = C only)
Train:     python3 -m rma_agent.ranker train [--until 2026-09-01] [--out models/cause_ranker_v6.json]
Check:     python3 -m rma_agent.ranker check models/cause_ranker_v6.json [--start 2026-10-01]

The model file is JSON (feature names, scaler mean/scale, coefficients), so serving needs no scikit-learn; training
does. Every feature is computed from what was known at the analysis's as_of (AsOfStore), in training and in serving
by the same function (features()).
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAD = 3
NUM = ["vote", "recent", "recent_n", "n_cases", "signal", "fit", "with_equipment", "has_process_signal", "has_event",
       "has_material", "has_supplier", "lot_passed_past_equipment", "days_since_last", "base_score", "base_rank",
       "same_lot_n", "stage_share", "spec_share", "customer_n"]
CATS = ["PROCESS", "MATERIAL", "INSPECTION", "UNDETERMINED"]
NAMES = NUM + [f"category={c}" for c in CATS]


class Context:
    """Per-return counts the learned features need, from past approved cases known at as_of."""

    def __init__(self, store, claim, cases=None):
        past = cases if cases is not None else store.past_cases(intake_label=claim.get("intake_label"))
        self.stage = Counter(c.cause_code for c in past if c.detection_stage == claim.get("detection_stage"))
        self.spec = Counter(c.cause_code for c in past if c.spec_id == claim.get("spec_id"))
        self.customer = Counter(c.cause_code for c in past if c.customer_id == claim.get("customer_id"))
        lot = claim.get("lot_id")
        self.lot = Counter(c.cause_code for c in store.past_cases() if lot and c.lot_id == lot)


def features(candidate, base_rank, ctx):
    """Feature vector of one candidate in NAMES order. candidate: a rank_causes() item with its _features."""
    f, code = candidate["_features"], candidate["cause_code"]
    extra = {"base_score": candidate["score"], "base_rank": base_rank, "same_lot_n": ctx.lot[code],
             "stage_share": ctx.stage[code] / (sum(ctx.stage.values()) or 1),
             "spec_share": ctx.spec[code] / (sum(ctx.spec.values()) or 1), "customer_n": ctx.customer[code]}
    values = {**f, **extra}
    return [float(values[k]) for k in NUM] + [1.0 if f["category"] == c else 0.0 for c in CATS]


class Ranker:
    def __init__(self, model, path=None):
        if model.get("features") != NAMES:
            raise ValueError(f"{path}: feature list differs from this code (retrain with python3 -m rma_agent.ranker train)")
        self.model, self.path = model, path
        self.name = model["name"]

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.is_absolute():
            path = ROOT / path
        return cls(json.loads(path.read_text(encoding="utf-8")), path)

    def probability(self, x):
        m = self.model
        z = m["intercept"] + sum(w * (v - mu) / (s or 1.0) for w, v, mu, s in zip(m["coef"], x, m["mean"], m["scale"]))
        return 1 / (1 + math.exp(-z))

    def rerank(self, candidates, store, claim, cases=None):
        """Reorder candidates[:HEAD] (C order in, new order out). Their `score` becomes the model's probability."""
        head = candidates[:HEAD]
        if len(head) < 2:
            return candidates
        ctx = Context(store, claim, cases)
        scored = [(self.probability(features(c, i, ctx)), i, c) for i, c in enumerate(head, 1)]
        scored.sort(key=lambda t: (-t[0], t[1]))
        for p, _, c in scored:
            c["score"] = round(p, 2)
        return [c for _, _, c in scored] + candidates[HEAD:]


_active = {}


def active():
    """Ranker from RMA_CAUSE_RANKER (environment or .env), or None (default: off). Loaded once per path."""
    from .agents.llm import _load_dotenv
    _load_dotenv()
    setting = os.environ.get("RMA_CAUSE_RANKER", "").strip()
    if not setting or setting.lower() == "off":
        return None
    if setting not in _active:
        _active[setting] = Ranker.load(setting)
    return _active[setting]


# ---------- training and checking (needs scikit-learn and the dataset)
def _examples(conn, start, end):
    """[(rma_id, opened_at, truth, [(x, cause, base_rank)])] for returns with a known cause, features at their as_of."""
    from .agents import cause_analysis, manufacturing
    from .agents.runtime import load_masters
    from .asof import AsOfStore, CaseLibrary
    from .evaluate import known_queries, queries
    from .tools.process import ProcessBaseline
    from .truth import GroundTruth
    masters, lib, base, truth = load_masters(conn), CaseLibrary(conn), ProcessBaseline(conn), GroundTruth(conn)
    opened = dict(conn.execute("select rma_id, opened_at from rma_claim"))
    in_scope, _ = known_queries(conn, queries(conn, start, end), truth)
    out = []
    for rma_id, as_of, group in in_scope:
        store = AsOfStore(conn, as_of, lib, subject=rma_id)
        claim = store.claim(rma_id)
        lot = manufacturing.trace(masters, store, base, claim)
        cands = cause_analysis.rank_causes(masters, store, claim, lot, top_k=100, ranker=None)
        if not cands:
            continue
        ctx = Context(store, claim)
        out.append((rma_id, opened[rma_id], truth.answer(rma_id),
                    [(features(c, i, ctx), c["cause_code"], i) for i, c in enumerate(cands, 1)]))
    return out


def train(conn, until="2026-09-01", start="2026-03-01", C=0.1):
    """Fit on returns opened in [start, until). C=0.1 was the best setting on 2026-09 (results/20261120_cause_ranker_v5.md)."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    data = _examples(conn, start, until)
    X = np.array([x for *_, rows in data for x, _, _ in rows])
    y = np.array([int(cause == t["cause"]) for _, _, t, rows in data for _, cause, _ in rows])
    scaler = StandardScaler().fit(X)
    lr = LogisticRegression(C=C, max_iter=3000).fit(scaler.transform(X), y)
    return {"name": f"cause-lr-{until}", "features": NAMES, "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(), "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]),
            "C": C, "head": HEAD, "trained_on": f"{start} <= opened_at < {until}", "n_returns": len(data),
            "n_rows": int(len(y))}


def check(conn, ranker, start="2026-10-01", end=None):
    """cause@1/@3 with and without the ranker, through rank_causes() itself (the serving path)."""
    from .agents import cause_analysis, manufacturing
    from .agents.runtime import load_masters
    from .asof import AsOfStore, CaseLibrary
    from .evaluate import known_queries, queries
    from .tools.process import ProcessBaseline
    from .truth import GroundTruth
    masters, lib, base, truth = load_masters(conn), CaseLibrary(conn), ProcessBaseline(conn), GroundTruth(conn)
    in_scope, _ = known_queries(conn, queries(conn, start, end), truth)
    hits = {"C": [0, 0], ranker.name: [0, 0]}
    n = 0
    for rma_id, as_of, _ in in_scope:
        store = AsOfStore(conn, as_of, lib, subject=rma_id)
        claim = store.claim(rma_id)
        lot = manufacturing.trace(masters, store, base, claim)
        answer = truth.answer(rma_id)["cause"]
        n += 1
        for name, r in (("C", None), (ranker.name, ranker)):
            codes = [c["cause_code"] for c in cause_analysis.rank_causes(masters, store, claim, lot, top_k=3, ranker=r)]
            hits[name][0] += codes[:1] == [answer]
            hits[name][1] += answer in codes
    return n, {name: (h[0] / n, h[1] / n) for name, h in hits.items()}


def main(argv=None):
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("RMA_LLM", "off")
    from .db import connect
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--until", default="2026-09-01")
    t.add_argument("--out", default="models/cause_ranker_v6.json")
    t.add_argument("--db")
    c = sub.add_parser("check")
    c.add_argument("model")
    c.add_argument("--start", default="2026-10-01")
    c.add_argument("--end")
    c.add_argument("--db")
    a = ap.parse_args(argv)
    started = time.time()
    conn = connect(a.db)
    if a.cmd == "train":
        model = train(conn, until=a.until)
        out = ROOT / a.out
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(model, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"saved {a.out}: {model['n_returns']:,} returns, {model['n_rows']:,} rows ({time.time() - started:.0f}s)")
    else:
        n, res = check(conn, Ranker.load(a.model), a.start, a.end)
        print(f"{n:,} returns from {a.start}")
        for name, (h1, h3) in res.items():
            print(f"  {name:28s} cause@1 {h1:.1%}  cause@3 {h3:.1%}")
        print(f"({time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
