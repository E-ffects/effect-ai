"""WBS 4.3 check: does the pattern priority at a month end point at the patterns that cause the most damage next?

    python scripts/eval_priority.py [--out results/x.md]

At each month end 2026-03..2026-11 (as_of), score the open patterns, then look 60 days ahead (data the score did not
see): new returns linked to each pattern and new loss booked to it. Report, per weighting, how often the top-ranked
pattern is the one with the most future loss, and the rank correlation (Spearman) between score and future loss.
Only patterns with at least one return at as_of. Weightings: the FR-03 default with different look-back windows for count/loss, and single-part baselines.
"""
import argparse
import warnings
import sys
from datetime import datetime, timedelta
from pathlib import Path

from scipy.stats import spearmanr

warnings.filterwarnings("ignore")  # constant scores (e.g. trend only) give an undefined correlation; skipped

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rma_agent.db import connect  # noqa: E402
from rma_agent.priority import pattern_priority  # noqa: E402

AHEAD = 60
SETS = {"FR-03 기본, 최근 30일 (채택)": (None, 30),
        "FR-03 기본, 최근 60일": (None, 60),
        "FR-03 기본, 최근 90일": (None, 90),
        "FR-03 기본, 누적": (None, None),
        "건수만, 최근 30일": ({"count": 1, "loss": 0, "severity": 0, "trend": 0}, 30),
        "손실만, 최근 30일": ({"count": 0, "loss": 1, "severity": 0, "trend": 0}, 30),
        "손실만, 누적": ({"count": 0, "loss": 1, "severity": 0, "trend": 0}, None),
        "추세만": ({"count": 0, "loss": 0, "severity": 0, "trend": 1}, 30)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v5.sqlite"))
    ap.add_argument("--out")
    a = ap.parse_args()
    conn = connect(a.db)
    ends = [f"2025-{m:02d}-{d}T23:59:59+09:00" for m, d in ((3, 31), (4, 30), (5, 31), (6, 30), (7, 31), (8, 31), (9, 30), (10, 31), (11, 30))]
    stats = {k: {"top": 0, "rho": [], "n": 0} for k in SETS}
    detail = []
    for t in ends:
        later = (datetime.fromisoformat(t) + timedelta(days=AHEAD)).isoformat()
        fut_loss = dict(conn.execute("""select investigation_id, sum(amount_krw) from loss_ledger
                                        where occurred_at > ? and occurred_at <= ? group by 1""", (t, later)).fetchall())
        fut_n = dict(conn.execute("""select l.investigation_id, count(*) from investigation_claim l join rma_claim r using(rma_id)
                                     where r.opened_at > ? and r.opened_at <= ? group by 1""", (t, later)).fetchall())
        for name, (w, win) in SETS.items():
            items = [i for i in pattern_priority(as_of=t, weights=w, conn=conn, window_days=win)["items"] if i["total_claim_count"]]
            if len(items) < 2:
                continue
            fl = [fut_loss.get(i["investigation_id"], 0) for i in items]
            stats[name]["n"] += 1
            stats[name]["top"] += fl[0] == max(fl)
            stats[name]["rho"].append(spearmanr([i["score"] for i in items], fl)[0])
            if name.endswith("(채택)"):
                best = max(items, key=lambda i: fut_loss.get(i["investigation_id"], 0))
                detail.append(f"| {t[:10]} | {len(items)} | {items[0]['investigation_id']} {items[0]['title'].split(' —')[0]} ({items[0]['score']}) "
                              f"| {best['investigation_id']} {best['title'].split(' —')[0]} | {fut_loss.get(items[0]['investigation_id'], 0) / 1e8:.2f}억 / {max(fl) / 1e8:.2f}억 "
                              f"| {fut_n.get(items[0]['investigation_id'], 0)} |")
    lines = [f"# 패턴 우선순위 점검 (WBS 4.3): {Path(a.db).name}", "",
             f"매월 말(2026-03~11, 9개 시점)에 열린 패턴(조사)을 점수로 줄 세우고, 그 뒤 {AHEAD}일 동안 실제로 생긴 손실과 비교했다. "
             "점수는 그 시점까지의 데이터만 쓴다. 반품이 하나도 연결되지 않은 조사는 뺐다.", "",
             "| 가중치 | 1위 패턴이 이후 손실 1위인 비율 | 점수와 이후 손실의 순위상관 (평균) |", "|---|---:|---:|"]
    for name, s in stats.items():
        rs = [r for r in s["rho"] if r == r]
        rho = sum(rs) / len(rs) if rs else float("nan")
        lines.append(f"| {name} | {s['top']}/{s['n']} | {rho:+.2f} |")
    lines += ["", "## 시점별 1위 (기본 가중치)", "",
              f"| 시점 | 열린 패턴 | 1위 (점수) | 이후 {AHEAD}일 손실 1위 | 1위 패턴 손실 / 최대 | 1위 패턴 이후 반품 |", "|---|---:|---|---|---:|---:|"]
    lines += detail
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
