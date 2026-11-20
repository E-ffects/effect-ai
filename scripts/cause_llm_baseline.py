"""WBS 4.12 baseline: one LLM picks the cause from the same candidates and facts, no learned or hand-set score.

    python scripts/cause_llm_baseline.py [--n 60] [--out results/x.md]

Needs scripts/cause_ranker.py to have run once (it caches the candidate rows in $TMPDIR). Uses the test period
(2025-10~), a sample stratified by incident group (seed 11). The LLM sees, per candidate: cause name/category/mechanism,
how many past approved returns with the same intake label had it, how many in the last 60 days on the same product,
and which lot evidence exists (process signal, equipment event, material, supplier) - the same facts the scores use,
in shuffled order and without any score or rank. Compared with C and the logistic ranker (top-3 reorder) on the same
sample. Costs one API call per return.
"""
import argparse
import os
import pickle
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop("RMA_LLM", None)
from rma_agent.agents import llm  # noqa: E402
import cause_ranker as cr  # noqa: E402

os.environ.pop("RMA_LLM", None)  # cause_ranker turns the LLM off; this script needs it

INSTRUCTION = """아래 반품(RMA) 한 건의 확정 원인을 후보 중에서 고른다.
- 후보마다 같은 소분류 과거 확정 건수, 같은 제품 최근 60일 건수, 이 Lot에서 찾은 근거 종류가 있다.
- 가장 유력한 원인부터 3개를 cause_codes에 순서대로 적는다. 후보에 없는 코드는 쓰지 않는다."""


class _Pick(BaseModel):
    cause_codes: List[str] = Field(description="유력한 순서의 원인 코드 3개")
    reason: str = Field(description="한두 문장")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v5.sqlite"))
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--out")
    a = ap.parse_args()
    cache = Path(os.environ.get("TMPDIR", "/tmp")) / f"cause_ranker_{Path(a.db).stem}.pkl"
    groups = pickle.loads(cache.read_bytes())
    conn = sqlite3.connect(a.db)
    master = {r[0]: {"이름": r[1], "분류": r[2], "메커니즘": r[3]}
              for r in conn.execute("select cause_code, cause_name, cause_category, mechanism from cause_master")}
    claims = {r[0]: {"원문": r[1], "검출 단계": r[2]} for r in conn.execute("select rma_id, raw_text, detection_stage from rma_claim")}
    tr = [g for g in groups if g["opened"] < "2025-09-01"]
    te = [g for g in groups if g["opened"] >= "2025-10-01"]
    Xtr, ytr = cr.matrix(tr)
    scaler = cr.StandardScaler().fit(Xtr)
    model = cr.LogisticRegression(C=0.1, max_iter=3000).fit(scaler.transform(Xtr), ytr)
    rng = random.Random(11)
    by = {}
    for g in te:
        by.setdefault(g["group"], []).append(g)
    per = max(1, a.n // len(by))
    sample = [g for k in sorted(by) for g in rng.sample(by[k], min(per, len(by[k])))]
    res = {"C": [], "로지스틱 (C 상위 3개 재정렬)": [], "LLM 단독": []}
    used, failed, t0 = 0, 0, time.time()
    for g in sample:
        rows = g["rows"][:20]
        X, _ = cr.matrix([{**g, "rows": rows}])
        s = model.predict_proba(scaler.transform(X))[:, 1]
        lr = sorted([r for r in rows if r["f"]["base_rank"] <= 3], key=lambda r: -s[rows.index(r)])
        res["C"].append([r["cause"] for r in rows])
        res["로지스틱 (C 상위 3개 재정렬)"].append([r["cause"] for r in lr] + [r["cause"] for r in rows if r["f"]["base_rank"] > 3])
        shown = rows[:]
        random.Random(g["rma_id"]).shuffle(shown)
        cands = []
        for r in shown:
            f = r["f"]
            ev = [n for k, n in (("has_process_signal", "공정 이상 신호"), ("has_event", "장비 이벤트"), ("has_material", "자재 근거"),
                                  ("has_supplier", "공급사 근거")) if f[k]]
            cands.append({"cause_code": r["cause"], **master.get(r["cause"], {}), "의심 장비": r["equipment"],
                          "같은 소분류 과거 확정 건수": f["n_cases"], "같은 제품 최근 60일 건수": f["recent_n"],
                          "이 Lot 근거": ev or ["없음"]})
        facts = {"반품": claims[g["rma_id"]], "후보": cands}
        out = llm.Outcome()
        pick = llm.write("cause_baseline", INSTRUCTION, facts, _Pick, lambda r: [], out)
        if pick:
            used += 1
            codes = [c for c in pick["cause_codes"] if c in {r["cause"] for r in rows}]
        else:
            failed += 1
            codes = []
        res["LLM 단독"].append(codes)
    dt = (time.time() - t0) / len(sample)
    lines = [f"# 원인 후보: LLM 단독 기준선 (WBS 4.12): {Path(a.db).name}", "",
             f"- 평가 구간(2025-10~)에서 사건 유형별로 뽑은 {len(sample)}건 (seed 11). 모델 {llm.model_name()}, 응답 {used}건, 실패 {failed}건, 1건당 {dt:.1f}초",
             "- LLM에는 지금 점수가 쓰는 사실(과거 건수, 최근 건수, Lot 근거)을 후보 순서를 섞어서 주고 점수·순위는 주지 않았다", "",
             "| 방식 | 원인@1 | 원인@3 |", "|---|---:|---:|"]
    for name, orders in res.items():
        h1 = sum(g["truth"]["cause"] in o[:1] for g, o in zip(sample, orders)) / len(sample)
        h3 = sum(g["truth"]["cause"] in o[:3] for g, o in zip(sample, orders)) / len(sample)
        lines.append(f"| {name} | {h1:.1%} | {h3:.1%} |")
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
