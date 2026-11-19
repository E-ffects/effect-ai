"""Score CS classification against the stored intake labels.

    python -m rma_agent.evaluate_classify                     # retrieval only (no LLM), test period
    python -m rma_agent.evaluate_classify --llm 120           # also LLM on a stratified sample of 120

Each text is classified as of its own labelling time: only intakes labelled before it are used as examples.
Truth is symptom_assessment.intake_label (the label CS gave at intake). INSUFFICIENT_INFO and UNCLASSIFIED count
as labels: the classifier must say "ask again" / "outside the 18" for them.
"""
import argparse
import os
import random
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .db import connect

RESULTS = Path(__file__).resolve().parents[1] / "results"


def claims(conn, start, end=None):
    sql = """select r.rma_id, r.raw_text, r.detection_stage, s.intake_label, s.created_at
             from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
             where r.opened_at>=?"""
    params = [start]
    if end:
        sql += " and r.opened_at<?"
        params.append(end)
    return [dict(r) for r in conn.execute(sql + " order by r.opened_at, r.rma_id", params)]


def predicted(out):
    return out["minor_code"] or out["status"]


def score(rows, preds):
    n = len(rows)
    hit = sum(preds[r["rma_id"]] == r["intake_label"] for r in rows)
    by_label = defaultdict(lambda: [0, 0])
    for r in rows:
        by_label[r["intake_label"]][0] += 1
        by_label[r["intake_label"]][1] += preds[r["rma_id"]] == r["intake_label"]
    asked = [r for r in rows if preds[r["rma_id"]] == "INSUFFICIENT_INFO"]
    truly = [r for r in rows if r["intake_label"] == "INSUFFICIENT_INFO"]
    precision = sum(r["intake_label"] == "INSUFFICIENT_INFO" for r in asked) / len(asked) if asked else None
    recall = sum(preds[r["rma_id"]] == "INSUFFICIENT_INFO" for r in truly) / len(truly) if truly else None
    confusion = Counter((r["intake_label"], preds[r["rma_id"]]) for r in rows if preds[r["rma_id"]] != r["intake_label"])
    return {"n": n, "accuracy": hit / n if n else None, "by_label": dict(by_label),
            "insufficient_precision": precision, "insufficient_recall": recall, "confusion": confusion.most_common(8)}


def pct(v):
    return "-" if v is None else f"{100 * v:.1f}%"


def report(name, s):
    lines = [f"## {name}", "", f"- 건수 {s['n']}, 정확도 **{pct(s['accuracy'])}**",
             f"- 정보 부족 판정: 정밀도 {pct(s['insufficient_precision'])}, 재현율 {pct(s['insufficient_recall'])}", "",
             "| 소분류 | 건수 | 정확도 |", "|---|---:|---:|"]
    for label, (n, h) in sorted(s["by_label"].items()):
        lines.append(f"| {label} | {n} | {pct(h / n)} |")
    lines += ["", "자주 틀린 것 (정답 → 예측): " + ", ".join(f"{a}→{b} {c}건" for (a, b), c in s["confusion"]) or "-", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2025-10-01")
    parser.add_argument("--end")
    parser.add_argument("--llm", type=int, default=0, help="also run the LLM on a stratified sample of this size")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    os.environ.setdefault("RMA_LLM", "off")
    from .agents import classifier as cls, llm
    from .agents.runtime import load_masters
    conn = connect()
    model = cls.Classifier(conn, load_masters(conn))
    rows = claims(conn, args.start, args.end)
    text = [f"# CS 분류 평가 ({datetime.now():%Y-%m-%d %H:%M})", "",
            f"기간 {args.start} ~ {args.end or '끝'}, 정답은 접수 시 CS 소분류(intake_label)", ""]

    t0 = time.time()
    saved, os.environ["RMA_LLM"] = os.environ.get("RMA_LLM"), "off"
    preds = {r["rma_id"]: predicted(model.classify(r["raw_text"], r["detection_stage"], as_of=r["created_at"],
                                                   exclude=r["rma_id"])) for r in rows}
    os.environ["RMA_LLM"] = saved
    text.append(report(f"검색만 (LLM 없음, {time.time() - t0:.0f}초)", score(rows, preds)))

    if args.llm:
        if llm.model_name() is None:
            raise SystemExit("LLM is off or has no key: set RMA_LLM and the key in .env")
        by_label = defaultdict(list)
        for r in rows:
            by_label[r["intake_label"]].append(r)
        per = max(1, args.llm // len(by_label))
        rng = random.Random(7)
        sample = [r for label in sorted(by_label) for r in rng.sample(by_label[label], min(per, len(by_label[label])))]
        t0 = time.time()
        llm_preds = {}
        for r in sample:
            llm_preds[r["rma_id"]] = predicted(model.classify(r["raw_text"], r["detection_stage"], as_of=r["created_at"],
                                                              exclude=r["rma_id"]))
        text.append(report(f"LLM {llm.model_name()} (소분류별 최대 {per}건 표본, {time.time() - t0:.0f}초)",
                           score(sample, llm_preds)))
        text.append(report("같은 표본, 검색만", score(sample, preds)))

    out = "\n".join(text)
    print(out)
    if not args.no_save:
        RESULTS.mkdir(exist_ok=True)
        path = RESULTS / f"{datetime.now():%Y%m%d-%H%M}_classify.md"
        path.write_text(out + "\n", encoding="utf-8")
        print(f"\nsaved: results/{path.name}")


if __name__ == "__main__":
    main()
