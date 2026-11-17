"""Replay returns in time order and score predictors on cause / process / equipment.

    python -m rma_agent.evaluate -p A B                 # baselines, test period (2025-10-01 ~)
    python -m rma_agent.evaluate -p B --start 2025-07-01 --limit 300

Each return is analysed at its analysis start (QA triage done, intake label available); the predictor only
sees data known at that time through AsOfStore. Ground truth comes from truth.GroundTruth.

Scope: only returns whose cause was already approved somewhere before the analysis start (known causes).
Returns with a cause never seen before are out of scope and are not scored.
"""
import argparse
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .asof import AsOfStore, CaseLibrary, analysis_start
from .db import connect
from .predictors import PREDICTORS
from .truth import GroundTruth

RESULTS = Path(__file__).resolve().parents[1] / "results"
FIELDS = (("cause", "causes"), ("process", "processes"), ("equipment", "equipment"))
GROUP_NAMES = {"all_known": "알려진 원인 전체", "same_incident": "같은 사건 후속", "other_incident": "다른 사건 같은 원인"}


def queries(conn, start, end=None, limit=None):
    sql = "select rma_id from rma_claim where opened_at>=?"
    params = [start]
    if end:
        sql += " and opened_at<?"
        params.append(end)
    ids = [r[0] for r in conn.execute(sql + " order by opened_at, rma_id", params)]
    return ids[:limit] if limit else ids


def known_queries(conn, rma_ids, truth):
    """Keep returns in scope (known cause at analysis start). Returns (in_scope [(rma_id, as_of, group)], excluded)."""
    in_scope, excluded = [], 0
    for rma_id in rma_ids:
        as_of = analysis_start(conn, rma_id)
        group = truth.group(rma_id, as_of)
        if group == "unknown":
            excluded += 1
        else:
            in_scope.append((rma_id, as_of, group))
    return in_scope, excluded


def run(conn, predictor, in_scope, library, truth):
    records = []
    for rma_id, as_of, group in in_scope:
        store = AsOfStore(conn, as_of, library)
        claim = store.claim(rma_id)
        pred = predictor.predict(store, claim)
        records.append({"rma_id": rma_id, "as_of": as_of, "label": claim["intake_label"], "group": group,
                        "truth": truth.answer(rma_id), "pred": {k: getattr(pred, attr)[:5] for k, attr in FIELDS}})
    return records


def hit_rate(records, field, k):
    pool = [r for r in records if r["truth"][field]]
    if not pool:
        return None, 0
    hits = sum(r["truth"][field] in r["pred"][field][:k] for r in pool)
    return hits / len(pool), len(pool)


def summarize(records, excluded):
    known = records
    groups = {"all_known": known, "same_incident": [r for r in known if r["group"] == "same_incident"],
              "other_incident": [r for r in known if r["group"] == "other_incident"]}
    out = {"n_known": len(known), "n_excluded_unknown": excluded, "groups": {}}
    for gname, pool in groups.items():
        out["groups"][gname] = {"n": len(pool)}
        for field, _ in FIELDS:
            for k in (1, 3):
                rate, n = hit_rate(pool, field, k)
                out["groups"][gname][f"{field}@{k}"] = rate
                out["groups"][gname][f"{field}_n"] = n
    by_label = defaultdict(list)
    for r in known:
        by_label[r["label"]].append(r)
    out["by_label"] = {label: {"n": len(pool), "cause@3": hit_rate(pool, "cause", 3)[0],
                               "process@3": hit_rate(pool, "process", 3)[0]}
                       for label, pool in sorted(by_label.items(), key=lambda kv: str(kv[0]))}
    return out


def pct(value):
    return "-" if value is None else f"{100 * value:.1f}%"


def report(results, args, elapsed):
    lines = [f"# 평가 결과 {datetime.now():%Y-%m-%d %H:%M}", "",
             f"- 대상: {args.start} 이후 접수" + (f", {args.end} 이전" if args.end else "") +
             (f", 앞 {args.limit}건" if args.limit else ""),
             f"- 분석 시점: 접수별 분석 시작 시각(QA 판정 + 접수 소분류 확보). 그 시각 이전 데이터만 사용",
             f"- 소요 시간: {elapsed:.0f}초", ""]
    first = next(iter(results.values()))["summary"]
    lines += [f"평가 대상: 알려진 원인 {first['n_known']}건 "
              f"(같은 사건 후속 {first['groups']['same_incident']['n']}, 다른 사건 같은 원인 {first['groups']['other_incident']['n']}). "
              f"처음 보는 원인 {first['n_excluded_unknown']}건은 범위 밖이라 제외", "",
              "## 비교 (알려진 원인 전체)", "",
              "| 예측기 | 원인@1 | 원인@3 | 공정@1 | 공정@3 | 장비@1 | 장비@3 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for name, res in results.items():
        g = res["summary"]["groups"]["all_known"]
        lines.append(f"| {name} | {pct(g['cause@1'])} | {pct(g['cause@3'])} | {pct(g['process@1'])} | {pct(g['process@3'])} | "
                     f"{pct(g['equipment@1'])} | {pct(g['equipment@3'])} |")
    lines += ["", f"장비는 정답 장비가 있는 {first['groups']['all_known']['equipment_n']}건 기준이다.", ""]
    for name, res in results.items():
        s = res["summary"]
        lines += [f"## {name}", "", res["description"], "",
                  "| 구분 | 건수 | 원인@1 | 원인@3 | 공정@1 | 공정@3 | 장비@1 | 장비@3 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for gname, g in s["groups"].items():
            lines.append(f"| {GROUP_NAMES[gname]} | {g['n']} | {pct(g['cause@1'])} | {pct(g['cause@3'])} | {pct(g['process@1'])} | "
                         f"{pct(g['process@3'])} | {pct(g['equipment@1'])} | {pct(g['equipment@3'])} |")
        lines += ["", "**소분류별 (알려진 원인)**", "", "| 소분류 | 건수 | 원인@3 | 공정@3 |", "|---|---:|---:|---:|"]
        for label, v in s["by_label"].items():
            lines.append(f"| {label} | {v['n']} | {pct(v['cause@3'])} | {pct(v['process@3'])} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--predictors", nargs="+", default=["A", "B"], choices=sorted(PREDICTORS))
    parser.add_argument("--start", default="2025-10-01")
    parser.add_argument("--end")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--db")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    t0 = time.time()
    conn = connect(args.db)
    library, truth = CaseLibrary(conn), GroundTruth(conn)
    in_scope, excluded = known_queries(conn, queries(conn, args.start, args.end, args.limit), truth)
    results = {}
    for key in args.predictors:
        predictor = PREDICTORS[key]()
        records = run(conn, predictor, in_scope, library, truth)
        results[predictor.name] = {"description": predictor.description,
                                   "summary": summarize(records, excluded), "records": records}
    text = report(results, args, time.time() - t0)
    print(text)
    if not args.no_save:
        RESULTS.mkdir(exist_ok=True)
        stamp = f"{datetime.now():%Y%m%d-%H%M}_{'-'.join(args.predictors)}"
        (RESULTS / f"{stamp}.md").write_text(text + "\n", encoding="utf-8")
        (RESULTS / f"{stamp}.json").write_text(json.dumps(
            {name: {"summary": r["summary"]} for name, r in results.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nsaved: results/{stamp}.md")


if __name__ == "__main__":
    main()
