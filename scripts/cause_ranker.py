"""WBS 4.12: learned cause ranking vs today's hand-set weights (C), on the same time split as every other result.

    python scripts/cause_ranker.py [--db data/pcb_rma_v5.sqlite] [--out results/x.md]

Rows: one per (return, candidate cause) - the candidates rank_causes() builds from past approved cases with the same
intake label, at the return's analysis start (as_of). Label: candidate == confirmed cause. Only returns whose cause was
already known somewhere at as_of (same scope as rma_agent.evaluate).
Features: today's score parts (vote, recent same product, lot signal, penalty, ...) plus a few new ones, all as of as_of:
  same_lot_n     past approved returns from the same lot with this cause
  stage_share    share of this cause among past returns with the same label and detection stage
  spec_share     share of this cause among past returns with the same label and product
  customer_n     past approved returns of the same customer with this cause
Split by opened_at: train < 2026-09-01 <= validation < 2026-10-01 <= test. Settings picked on validation; test once.
"""
import argparse
import os
import pickle
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RMA_LLM", "off")
from rma_agent.agents import cause_analysis, manufacturing  # noqa: E402
from rma_agent.agents.runtime import load_masters  # noqa: E402
from rma_agent.asof import AsOfStore, CaseLibrary, analysis_start  # noqa: E402
from rma_agent.db import connect  # noqa: E402
from rma_agent.evaluate import known_queries, queries  # noqa: E402
from rma_agent.tools.process import ProcessBaseline  # noqa: E402
from rma_agent.truth import GroundTruth  # noqa: E402

NUM = ["vote", "recent", "recent_n", "n_cases", "signal", "fit", "with_equipment", "has_process_signal", "has_event",
       "has_material", "has_supplier", "lot_passed_past_equipment", "days_since_last", "base_score", "base_rank",
       "same_lot_n", "stage_share", "spec_share", "customer_n"]
CATS = ["PROCESS", "MATERIAL", "INSPECTION", "UNDETERMINED"]


def build(db, cache):
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    conn = connect(db)
    masters, lib, base, truth = load_masters(conn), CaseLibrary(conn), ProcessBaseline(conn), GroundTruth(conn)
    in_scope, _ = known_queries(conn, queries(conn, "2026-03-01"), truth)
    opened = dict(conn.execute("select rma_id, opened_at from rma_claim"))
    groups = []
    for rma_id, as_of, group in in_scope:
        store = AsOfStore(conn, as_of, lib, subject=rma_id)
        claim = store.claim(rma_id)
        lot = manufacturing.trace(masters, store, base, claim)
        cands = cause_analysis.rank_causes(masters, store, claim, lot, top_k=100)
        if not cands:
            continue
        past = store.past_cases(intake_label=claim["intake_label"])
        stage = Counter(c.cause_code for c in past if c.detection_stage == claim["detection_stage"])
        spec = Counter(c.cause_code for c in past if c.spec_id == claim.get("spec_id"))
        lot_n = Counter(c.cause_code for c in store.past_cases() if claim.get("lot_id") and c.lot_id == claim["lot_id"])
        cust = Counter(c.cause_code for c in past if c.customer_id == claim["customer_id"])
        ans = truth.answer(rma_id)
        rows = []
        for rank, c in enumerate(cands, 1):
            f = dict(c["_features"])
            f.update(base_score=c["score"], base_rank=rank, same_lot_n=lot_n[c["cause_code"]],
                     stage_share=stage[c["cause_code"]] / (sum(stage.values()) or 1),
                     spec_share=spec[c["cause_code"]] / (sum(spec.values()) or 1), customer_n=cust[c["cause_code"]])
            rows.append({"cause": c["cause_code"], "equipment": c["equipment_id"], "f": f})
        groups.append({"rma_id": rma_id, "opened": opened[rma_id], "group": group, "truth": ans, "rows": rows})
    cache.write_bytes(pickle.dumps(groups))
    return groups


def matrix(groups):
    X, y = [], []
    for g in groups:
        for r in g["rows"]:
            f = r["f"]
            X.append([float(f[k]) for k in NUM] + [1.0 if f["category"] == c else 0.0 for c in CATS])
            y.append(int(r["cause"] == g["truth"]["cause"]))
    return np.array(X), np.array(y)


def ranked(groups, scores):
    out, i = [], 0
    for g in groups:
        n = len(g["rows"])
        s = scores[i:i + n]
        i += n
        order = sorted(range(n), key=lambda j: (-s[j], g["rows"][j]["f"]["base_rank"]))
        out.append([g["rows"][j] for j in order])
    return out


def hits(groups, orders):
    def rate(sel, field, k):
        pool = [(g, o) for g, o in zip(groups, orders) if sel(g) and g["truth"].get(field)]
        if not pool:
            return None
        key = "cause" if field == "cause" else "equipment"
        return sum(g["truth"][field] in [r[key] for r in o[:k]] for g, o in pool) / len(pool)
    allg = lambda g: True
    return {"cause@1": rate(allg, "cause", 1), "cause@3": rate(allg, "cause", 3), "eq@1": rate(allg, "equipment", 1),
            "other@1": rate(lambda g: g["group"] == "other_incident", "cause", 1),
            "same@1": rate(lambda g: g["group"] == "same_incident", "cause", 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v5.sqlite"))
    ap.add_argument("--out")
    a = ap.parse_args()
    started = time.time()
    cache = Path(os.environ.get("TMPDIR", "/tmp")) / f"cause_ranker_{Path(a.db).stem}.pkl"
    groups = build(a.db, cache)
    tr = [g for g in groups if g["opened"] < "2026-09-01"]
    va = [g for g in groups if "2026-09-01" <= g["opened"] < "2026-10-01"]
    te = [g for g in groups if g["opened"] >= "2026-10-01"]
    (Xtr, ytr), (Xva, _), (Xte, _) = matrix(tr), matrix(va), matrix(te)
    base = {name: hits(gs, [g["rows"] for g in gs]) for name, gs in (("va", va), ("te", te))}
    scaler = StandardScaler().fit(Xtr)
    cands = []
    for C in (0.01, 0.1, 1.0, 10.0):
        m = LogisticRegression(C=C, max_iter=3000).fit(scaler.transform(Xtr), ytr)
        cands.append((f"로지스틱 회귀 (C={C})", lambda X, m=m: m.predict_proba(scaler.transform(X))[:, 1]))
    for depth, lr in ((3, 0.05), (None, 0.1)):
        m = HistGradientBoostingClassifier(max_depth=depth, learning_rate=lr, max_iter=300, random_state=0).fit(Xtr, ytr)
        cands.append((f"부스팅 트리 (깊이 {depth or '제한 없음'}, 학습률 {lr})", lambda X, m=m: m.predict_proba(X)[:, 1]))
    scored = []
    for name, fn in cands:
        scored.append((hits(va, ranked(va, fn(Xva)))["cause@1"], name, fn))
    scored.sort(key=lambda x: -x[0])
    best_va, best_name, best_fn = scored[0]
    test = hits(te, ranked(te, best_fn(Xte)))
    lr_name = next(n for _, n, _ in scored if n.startswith("로지스틱"))
    lr_fn = next(f for _, n, f in scored if n == lr_name)
    lr_test = hits(te, ranked(te, lr_fn(Xte)))
    # variant: reorder only inside C's top 3 (C's top-3 set kept, so cause@3 cannot drop)
    def top3_only(gs, s):
        out = []
        for g, o in zip(gs, ranked(gs, s)):
            head = [r for r in o if r["f"]["base_rank"] <= 3]
            out.append(head + [r for r in g["rows"] if r["f"]["base_rank"] > 3])
        return out
    top3_va = hits(va, top3_only(va, lr_fn(Xva)))
    top3_test = hits(te, top3_only(te, lr_fn(Xte)))
    # variant: only today's score parts (no new features), to see where the gain comes from
    keep = [i for i, k in enumerate(NUM) if k not in ("same_lot_n", "stage_share", "spec_share", "customer_n")] + \
        list(range(len(NUM), len(NUM) + len(CATS)))
    sc_old = StandardScaler().fit(Xtr[:, keep])
    m_old = LogisticRegression(C=float(lr_name.split('C=')[1].rstrip(')')), max_iter=3000).fit(sc_old.transform(Xtr[:, keep]), ytr)
    old_test = hits(te, ranked(te, m_old.predict_proba(sc_old.transform(Xte[:, keep]))[:, 1]))
    pct = lambda v: "-" if v is None else f"{v:.1%}"
    lines = [f"# 원인 후보 순위 모델 비교 (WBS 4.12): {Path(a.db).name}", "",
             f"- 학습 ~2026-08 ({len(tr):,}건), 설정 선택 2026-09 ({len(va):,}건), 평가 2026-10~ ({len(te):,}건). 알려진 원인 반품만",
             "- 후보는 지금 방식과 같다(같은 소분류의 과거 확정 원인). 바뀌는 건 순위뿐이다", "",
             "## 검증 구간(9월) 원인@1", "", "| 모델 | 원인@1 |", "|---|---:|",
             f"| 지금 방식 C (손으로 정한 가중치) | {pct(base['va']['cause@1'])} |"]
    lines += [f"| {n} | {pct(v)} |" for v, n, _ in scored]
    lines += ["", "## 평가 구간(10월~)", "",
              "| 모델 | 원인@1 | 원인@3 | 장비@1 | 같은 사건 후속 원인@1 | 다른 사건 같은 원인 원인@1 |", "|---|---:|---:|---:|---:|---:|"]
    rows_out = [("지금 방식 C", base["te"]), (f"**{best_name}** (검증 1위)", test)]
    if lr_name != best_name:
        rows_out.append((lr_name, lr_test))
    rows_out += [(f"{lr_name}, C 상위 3개 안에서만 재정렬 (검증 원인@1 {pct(top3_va['cause@1'])})", top3_test),
                 (f"{lr_name}, 새 특징 4개 뺌 (지금 점수 재료만)", old_test)]
    for name, h in rows_out:
        lines.append(f"| {name} | {pct(h['cause@1'])} | {pct(h['cause@3'])} | {pct(h['eq@1'])} | {pct(h['same@1'])} | {pct(h['other@1'])} |")
    m = LogisticRegression(C=float(lr_name.split('C=')[1].rstrip(')')), max_iter=3000).fit(scaler.transform(Xtr), ytr)
    names = NUM + [f"category={c}" for c in CATS]
    coef = sorted(zip(names, m.coef_[0]), key=lambda x: -abs(x[1]))
    lines += ["", "## 로지스틱 회귀 계수 (표준화한 특징 기준, 큰 순)", "", "| 특징 | 계수 |", "|---|---:|"]
    lines += [f"| {n} | {w:+.2f} |" for n, w in coef[:14]]
    lines.append(f"\n소요 {time.time() - started:.0f}초")
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
