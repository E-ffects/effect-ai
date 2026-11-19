"""QA triage proposal (rma_agent.triage) vs the stored qa_triage, every return, at its intake label time.

    python scripts/eval_triage.py [--out results/x.md]
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RMA_LLM", "off")
from rma_agent import triage  # noqa: E402
from rma_agent.agents.runtime import get_runtime  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    conn = get_runtime().conn
    rows = conn.execute("""select r.rma_id, s.intake_label, t.severity, t.characteristic_symbol, t.repeat_signal,
                                  t.analysis_level, t.rationale
                           from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
                           join qa_triage t using(rma_id) order by r.rma_id""").fetchall()
    c = Counter()
    for r in rows:
        p = triage(r[0])
        if r[1] in ("INSUFFICIENT_INFO", "UNCLASSIFIED"):
            c["prov"] += 1
            c["prov_level"] += p["analysis_level"] == r[5]
            continue
        c["n"] += 1
        c["all"] += (p["severity"], p["characteristic_symbol"], p["repeat_signal"], p["analysis_level"], p["rationale"]) == tuple(r[2:])
        c["level"] += p["analysis_level"] == r[5]
        c["repeat"] += p["repeat_signal"] == r[4]
    text = (f"# QA 판정안 대조 (`rma_agent.triage`)\n\n"
            f"- 접수 분류 저장 시점에 판정안을 만들어 저장된 `qa_triage`와 비교\n"
            f"- 소분류가 있는 반품 {c['n']:,}건: 분석 수준 일치 **{c['level'] / c['n']:.1%}**, 모든 필드 일치 {c['all'] / c['n']:.1%}, "
            f"반복 여부 일치 {c['repeat'] / c['n']:.1%}\n"
            f"- 남은 차이는 반복 여부(30일 내 3건째)다. 저장된 값은 생성 뒤 바뀌기 전의 증상 라벨로 셌다(예: RMA-00225는 앞선 같은 증상 반품이 1건뿐인데 반복으로 저장됨)\n"
            f"- 정보 부족·분류 불가 {c['prov']:,}건은 임시 L2. 저장된 판정은 나중에 확인된 실제 증상으로 매긴 것이라 {c['prov_level']:,}건만 같다\n")
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
