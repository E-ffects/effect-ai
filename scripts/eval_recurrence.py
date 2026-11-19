"""Recurrence verdict vs recurrence_review (the data's answer), at each return's analysis start.

    python scripts/eval_recurrence.py [--db data/pcb_rma_v5.sqlite] [--out results/x.md]

The answer uses the confirmed cause; the agent only has its leading candidate at analysis start. Every reviewed return
is checked once, against the review row of the action the agent picked (or the latest one when it picked none).
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RMA_LLM", "off")
from rma_agent.asof import AsOfStore, CaseLibrary, analysis_start  # noqa: E402
from rma_agent.db import connect  # noqa: E402
from rma_agent.agents import cause_analysis, manufacturing, qa  # noqa: E402
from rma_agent.agents.runtime import load_masters  # noqa: E402
from rma_agent.tools.process import ProcessBaseline  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v5.sqlite"))
    ap.add_argument("--out")
    a = ap.parse_args()
    conn = connect(a.db)
    masters, lib, base = load_masters(conn), CaseLibrary(conn), ProcessBaseline(conn)
    reviews = {}
    for item, ca, verdict, at in conn.execute("""select rma_item_id, corrective_action_id, verdict, reviewed_at
                                                 from recurrence_review order by reviewed_at"""):
        reviews.setdefault(item, {})[ca] = verdict
    rma_of = dict(conn.execute("select rma_item_id, rma_id from rma_item"))
    pairs, statuses = Counter(), Counter()
    for item, by_action in reviews.items():
        rma_id = rma_of[item]
        store = AsOfStore(conn, analysis_start(conn, rma_id), lib, subject=rma_id)
        claim = store.claim(rma_id)
        lot = manufacturing.trace(masters, store, base, claim)
        cands = cause_analysis.rank_causes(masters, store, claim, lot, 3)
        review = qa.review(masters, store, claim, cands, qa.triage(store, claim))
        got = qa.recurrence_check(masters, store, claim, lot, cands, review)
        ca = (got["corrective_action"] or {}).get("corrective_action_id")
        truth = by_action.get(ca) or list(by_action.values())[-1]
        pairs[(truth, got["status"])] += 1
        statuses[got["status"]] += 1
    # returns the data does not review for recurrence (e.g. sporadic causes): any verdict other than NOT_APPLICABLE there
    # is a false alarm the table above cannot show
    others = Counter()
    for (item, rma_id) in conn.execute("""select i.rma_item_id, i.rma_id from rma_item i join rma_claim r using(rma_id)
                                          where r.opened_at >= '2025-07-01' order by r.opened_at""").fetchall():
        if item in reviews:
            continue
        store = AsOfStore(conn, analysis_start(conn, rma_id), lib, subject=rma_id)
        claim = store.claim(rma_id)
        lot = manufacturing.trace(masters, store, base, claim)
        cands = cause_analysis.rank_causes(masters, store, claim, lot, 3)
        review = qa.review(masters, store, claim, cands, qa.triage(store, claim))
        others[qa.recurrence_check(masters, store, claim, lot, cands, review)["status"]] += 1
    n = sum(pairs.values())
    labels = ["RECURRENCE", "PRE_ACTION_PRODUCT", "PENDING_CAUSE_REVIEW"]
    got_labels = labels + ["NOT_APPLICABLE"]
    lines = [f"# 재발 판정 평가: {Path(a.db).name}", "",
             f"- 대상: `recurrence_review`에 있는 반품 {n:,}건, 반품별 분석 시작 시점",
             f"- 일치: **{sum(pairs[(t, t)] for t in labels) / n:.1%}** ({sum(pairs[(t, t)] for t in labels):,}건)", "",
             "| 정답 \\ 에이전트 | " + " | ".join(got_labels) + " | 합계 |", "|---|" + "---:|" * (len(got_labels) + 1)]
    for t in labels:
        row = [pairs[(t, g)] for g in got_labels]
        lines.append(f"| {t} | " + " | ".join(f"{x:,}" for x in row) + f" | {sum(row):,} |")
    for t in labels:
        tot = sum(pairs[(t, g)] for g in got_labels)
        if tot:
            lines.append(f"\n- {t} 재현율 {pairs[(t, t)] / tot:.1%}")
    m = sum(others.values())
    said = sum(pairs[(t, "RECURRENCE")] for t in labels) + others["RECURRENCE"]
    if said:
        lines.append(f"\n- **RECURRENCE 정밀도** (검토 대상이 아닌 반품까지 포함): {pairs[('RECURRENCE', 'RECURRENCE')] / said:.1%} "
                     f"({pairs[('RECURRENCE', 'RECURRENCE')]:,} / {said:,})")
    lines += ["", f"검토 대상이 아닌 반품({m:,}건, 2025-07 이후 접수): " +
              ", ".join(f"{k} {v:,}" for k, v in others.most_common()) +
              f". NOT_APPLICABLE이 아닌 판정(잘못 붙인 판정) {1 - others['NOT_APPLICABLE'] / m:.1%}" if m else ""]
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
