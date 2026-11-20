"""Service action proposal (drafts.service_action, Spring_API_v4 4-7): how often does it match what was actually done?

    python scripts/eval_service_action.py [--start 2025-10-01] [--out results/x.md]

For every return opened from --start that has a recorded service action, run the proposal at the return's analysis
start (as_of, time-controlled) with the agent's own cause ranking and similar cases, and compare with the action
actually taken. Two pools: the similar cases on screen (default) and past returns with the leading cause.
"""
import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RMA_LLM", "off")
from rma_agent.agents import cause_analysis, cs, manufacturing  # noqa: E402
from rma_agent.agents.runtime import load_masters  # noqa: E402
from rma_agent.asof import AsOfStore, CaseLibrary, analysis_start  # noqa: E402
from rma_agent.db import connect  # noqa: E402
from rma_agent.evaluate import queries  # noqa: E402
from rma_agent.tools.process import ProcessBaseline  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v5.sqlite"))
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--out")
    a = ap.parse_args()
    t0 = time.time()
    conn = connect(a.db)
    masters, lib, base = load_masters(conn), CaseLibrary(conn), ProcessBaseline(conn)
    actual = {r[0]: r[1] for r in conn.execute("""select i.rma_id, s.action_type from service_action s
                                                   join rma_item i using(rma_item_id)""")}
    res = {"similar": Counter(), "cause": Counter()}
    confusion = Counter()
    n = none = 0
    for rma_id in queries(conn, a.start):
        if rma_id not in actual:
            continue
        as_of = analysis_start(conn, rma_id)
        store = AsOfStore(conn, as_of, lib, subject=rma_id)
        claim = store.claim(rma_id)
        lot = manufacturing.trace(masters, store, base, claim)
        cands = cause_analysis.rank_causes(masters, store, claim, lot)
        similar = cause_analysis.similar_case_items(masters, store, claim)
        n += 1
        for mode in res:
            p = cs.service_action(store, claim, cands, similar, mode=mode)
            if p is None:
                none += mode == "similar"
                continue
            res[mode]["hit"] += p["action_type"] == actual[rma_id]
            if mode == "similar":
                confusion[(actual[rma_id], p["action_type"])] += 1
    lines = [f"# 처리방안 제안 점검 (drafts.service_action): {Path(a.db).name}", "",
             f"{a.start} 이후 접수 중 처리 기록이 있는 {n:,}건. 분석 시작 시점 기준으로 제안하고 실제 처리와 비교했다.", "",
             "| 근거 묶음 | 실제 처리와 일치 |", "|---|---:|",
             f"| 화면의 유사 사례 5건 (채택) | {res['similar']['hit'] / n:.1%} |",
             f"| 같은 유력 원인의 과거 반품 최근 {cs.SAME_CAUSE_CASES}건 | {res['cause']['hit'] / n:.1%} |",
             f"\n제안 없음(과거 처리 기록 없음): {none}건", "", "## 실제 → 제안 (채택 방식)", "", "| 실제 | 제안 | 건수 |", "|---|---|---:|"]
    lines += [f"| {cs.ACTION_NAMES[x]} | {cs.ACTION_NAMES[y]} | {c:,} |" for (x, y), c in confusion.most_common()]
    lines.append(f"\n소요 {time.time() - t0:.0f}초")
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
