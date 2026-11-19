"""Cause ranking: take one part of the score away at a time and compare cause@1 / cause@3 / equipment@1.

    python scripts/ablation.py [--db data/pcb_rma_v5.sqlite] [--out results/x.md]

Score = (0.40 x vote + 0.35 x recent same product + 0.25 x lot signal) x penalty (cause_analysis.py).
Each variant changes the module constants for one run; nothing else changes. Both periods are reported: the weights
were set on 2025-07~09 (tuning) and only checked on 2025-10~ (test).
"""
import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RMA_LLM", "off")

VARIANTS = [  # name, (vote, recent, signal), penalties on, recent days
    ("지금 (0.40 / 0.35 / 0.25 + 감점)", (0.40, 0.35, 0.25), True, 60),
    ("투표 빼기", (0.0, 0.35, 0.25), True, 60),
    ("최근 같은 제품 빼기", (0.40, 0.0, 0.25), True, 60),
    ("Lot 신호 빼기", (0.40, 0.35, 0.0), True, 60),
    ("감점 빼기", (0.40, 0.35, 0.25), False, 60),
    ("투표만 (B 기준선과 비슷)", (1.0, 0.0, 0.0), False, 60),
    ("세 요소 같은 비중 (1/3씩)", (1 / 3, 1 / 3, 1 / 3), True, 60),
    ("최근 범위 30일", (0.40, 0.35, 0.25), True, 30),
    ("최근 범위 90일", (0.40, 0.35, 0.25), True, 90),
]
PERIODS = [("튜닝 2025-07~09", "2025-07-01", "2025-10-01"), ("평가 2025-10~", "2025-10-01", None)]


def one(args):
    db, name, weights, penalty, days, start, end = args
    from rma_agent.agents import cause_analysis as ca
    from rma_agent.asof import CaseLibrary
    from rma_agent.db import connect
    from rma_agent.evaluate import hit_rate, known_queries, queries, run
    from rma_agent.predictors import CauseAgentC
    from rma_agent.truth import GroundTruth
    ca.W_VOTE, ca.W_RECENT, ca.W_SIGNAL = weights
    ca.RECENT_DAYS = days
    if not penalty:
        ca.PENALTY_NO_MATCH = ca.PENALTY_MATCH_NO_SIGNAL = 0.0
    conn = connect(db)
    truth, lib = GroundTruth(conn), CaseLibrary(conn)
    in_scope, _ = known_queries(conn, queries(conn, start, end), truth)
    recs = run(conn, CauseAgentC(), in_scope, lib, truth)
    other = [r for r in recs if r["group"] == "other_incident"]
    eq = [r for r in recs if r["truth"].get("equipment")]
    rate = lambda rs, f, k: hit_rate(rs, f, k)[0] if rs else None
    return (name, start, len(recs), rate(recs, "cause", 1), rate(recs, "cause", 3), rate(eq, "equipment", 1),
            rate(other, "cause", 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v5.sqlite"))
    ap.add_argument("--out")
    a = ap.parse_args()
    started = time.time()
    jobs = [(a.db, n, w, p, d, s, e) for n, w, p, d in VARIANTS for _, s, e in PERIODS]
    with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 2)) as pool:
        res = {(r[0], r[1]): r for r in pool.map(one, jobs)}
    pct = lambda v: "-" if v is None else f"{100 * v:.1f}%"
    lines = [f"# 원인 점수 요소 제거 실험: {Path(a.db).name}", "",
             "점수 = (0.40 × 투표 + 0.35 × 최근 같은 제품 + 0.25 × Lot 신호) × 감점. 한 번에 하나만 바꿨다. "
             "가중치는 튜닝 기간에서 정했고 평가 기간은 확인만 했다. 알려진 원인 반품만 센다.", ""]
    for label, s, _ in PERIODS:
        n = res[(VARIANTS[0][0], s)][2]
        lines += [f"## {label} ({n:,}건)", "",
                  "| 변형 | 원인@1 | 원인@3 | 장비@1 | 다른 사건 같은 원인 원인@1 |", "|---|---:|---:|---:|---:|"]
        base = res[(VARIANTS[0][0], s)][3]
        for name, *_ in VARIANTS:
            _, _, _, c1, c3, e1, o1 = res[(name, s)]
            diff = "" if name == VARIANTS[0][0] else f" ({100 * (c1 - base):+.1f}%p)"
            lines.append(f"| {name} | {pct(c1)}{diff} | {pct(c3)} | {pct(e1)} | {pct(o1)} |")
        lines.append("")
    lines.append(f"소요 {time.time() - started:.0f}초")
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
