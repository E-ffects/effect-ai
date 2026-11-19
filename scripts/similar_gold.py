"""WBS 5.2: answer set for similar-case search (20 queries) and Hit@5.

    python scripts/similar_gold.py make  [--db data/pcb_rma_v4.sqlite] [--out eval/similar_cases_gold_v4.json]
    python scripts/similar_gold.py eval  [--gold eval/similar_cases_gold_v4.json] [--out results/...md]

Which past case counts as similar (only cases whose cause was approved before the query's analysis start, as_of):
  grade 2  same incident (investigation family, reopened incidents included) and same intake label
  grade 1  same cause code and same intake label
Sporadic causes are grouped by quarter in the data, so for them (and for no-defect-found) only grade 1 exists.
Hit@5: at least one of the top 5 results is grade >= 1. Strict Hit@5 (incidents only): at least one grade 2.
WBS target: Hit@5 on 16 or more of the 20 queries.

The 20 queries come from the test period (opened from 2025-10-01) and mix the situations the screen must handle:
9 incidents (one per incident, recurrence included); an incident once per group and a cause at most twice, 5 sporadic causes, 1 customer TDR notice that ended as no defect
found, 3 claims whose label's most common past cause is a different cause, and 2 claims without a Lot number.
Selection is deterministic (seed 52). A person still reviews every query and its answers before the set is final.
"""
import argparse
import json
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rma_agent.asof import CaseLibrary, analysis_start  # noqa: E402

SEED = 52
START = "2025-10-01"


def kinds(conn):
    """investigation -> (kind, family root)."""
    rows = conn.execute("select investigation_id, title, reopened_from from investigation").fetchall()
    parent = {r[0]: r[2] for r in rows}
    out = {}
    for inv, title, _ in rows:
        root = inv
        while parent.get(root):
            root = parent[root]
        kind = ("incident" if "공통점 추적" in title or parent.get(inv) else
                "tdr_ndf" if "TDR" in title else "ndf" if "미재현" in title else "sporadic")
        out[inv] = (kind, root, bool(parent.get(inv)))
    return out


def truth(conn):
    return {r[0]: {"cause": r[1], "investigation": r[2], "label": r[3]} for r in conn.execute("""
        select i.rma_id, a.cause_code, a.investigation_id, s.intake_label from rma_item i
        join claim_cause cc using(rma_item_id) join cause_assessment a using(cause_assessment_id)
        join symptom_assessment s using(rma_item_id)""")}


def answers(conn, lib, inv_kind, rma_id, t):
    as_of = analysis_start(conn, rma_id)
    kind, family, _ = inv_kind[t["investigation"]]
    gold = {}
    for c in lib.as_of(as_of, t["label"]):
        if c.rma_id == rma_id or c.cause_code != t["cause"]:
            continue
        same_incident = kind == "incident" and inv_kind[c.investigation_id][1] == family
        gold[c.rma_id] = (2 if same_incident else 1, c)
    return as_of, gold


def make(args):
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    lib, inv_kind, tr = CaseLibrary(conn), kinds(conn), truth(conn)
    rng = random.Random(SEED)
    claims = conn.execute("select rma_id, raw_text, reported_lot_no, opened_at from rma_claim where opened_at>=? order by rma_id",
                          (START,)).fetchall()
    pool = [r for r in claims if r["rma_id"] in tr and tr[r["rma_id"]]["label"] not in ("INSUFFICIENT_INFO", "UNCLASSIFIED")]
    rng.shuffle(pool)
    chosen, used_groups = [], set()
    cache = {}

    def info(r):
        if r["rma_id"] not in cache:
            cache[r["rma_id"]] = answers(conn, lib, inv_kind, r["rma_id"], tr[r["rma_id"]])
        return cache[r["rma_id"]]

    def take(group, n, ok, why):
        for r in pool:
            if sum(1 for c in chosen if c["group"] == group) >= n:
                return
            t = tr[r["rma_id"]]
            kind, family, reopened = inv_kind[t["investigation"]]
            key = (group, family if kind == "incident" else t["cause"])  # once per group
            if (r["rma_id"] in {c["rma_id"] for c in chosen} or key in used_groups or not ok(r, t, kind, reopened)
                    or sum(tr[c["rma_id"]]["cause"] == t["cause"] for c in chosen) >= 2  # a cause at most twice
                    or any(inv_kind[tr[c["rma_id"]]["investigation"]][1] == family and tr[c["rma_id"]]["label"] == t["label"]
                           for c in chosen)):  # never the same incident and label twice
                continue
            as_of, gold = info(r)
            if not gold or (kind == "incident" and not any(g == 2 for g, _ in gold.values())):
                continue
            used_groups.add(key)
            chosen.append({"rma_id": r["rma_id"], "group": group, "why": why(r, t, kind, reopened, gold), "as_of": as_of})

    def majority_other(r, t):
        as_of, _ = info(r)
        votes = Counter(c.cause_code for c in lib.as_of(as_of, t["label"]) if c.rma_id != r["rma_id"])
        return votes and votes.most_common(1)[0][0] != t["cause"]

    take("recurrence", 1, lambda r, t, k, re: k == "incident" and re, lambda r, t, k, re, g: "재발 사건(다시 연 조사)")
    take("no_lot", 2, lambda r, t, k, re: not r["reported_lot_no"] and k in ("incident", "sporadic"),
         lambda r, t, k, re, g: "Lot 번호 없음(문장만으로 검색)")
    take("incident", 8, lambda r, t, k, re: k == "incident" and not re, lambda r, t, k, re, g: "사건성 원인")
    take("sporadic", 5, lambda r, t, k, re: k == "sporadic", lambda r, t, k, re, g: "단발성 원인")
    take("tdr_ndf", 1, lambda r, t, k, re: k == "tdr_ndf", lambda r, t, k, re, g: "고객 TDR 측정값 통보 → 불량 미재현")
    take("label_majority_other", 3, lambda r, t, k, re: k in ("incident", "sporadic") and majority_other(r, t),
         lambda r, t, k, re, g: "같은 증상의 과거 최다 원인이 다른 원인")

    names = {r[0]: r[1] for r in conn.execute("select cause_code, cause_name from cause_master")}
    labels = {r[0]: r[1] for r in conn.execute("select minor_code, minor_name from symptom_definition")}
    text = {r["rma_id"]: r["raw_text"] for r in claims}
    out = []
    for c in sorted(chosen, key=lambda c: c["rma_id"]):
        t = tr[c["rma_id"]]
        as_of, gold = info({"rma_id": c["rma_id"]})
        ranked = sorted(gold.items(), key=lambda kv: (-kv[1][0], kv[1][1].opened_at), reverse=False)
        recent = sorted(gold.values(), key=lambda gc: gc[1].opened_at, reverse=True)[:3]
        out.append({
            "rma_id": c["rma_id"], "group": c["group"], "why": c["why"], "as_of": as_of,
            "raw_text": text[c["rma_id"]], "intake_label": t["label"], "intake_name": labels.get(t["label"]),
            "cause_code": t["cause"], "cause_name": names.get(t["cause"]), "investigation_id": t["investigation"],
            "kind": inv_kind[t["investigation"]][0],
            "answers": {rid: g for rid, (g, _) in ranked},
            "n_grade2": sum(1 for g, _ in gold.values() if g == 2), "n_grade1": sum(1 for g, _ in gold.values() if g == 1),
            "examples_for_review": [{"rma_id": gc.rma_id, "grade": g, "opened_at": gc.opened_at, "raw_text": gc.raw_text}
                                    for g, gc in recent],
            "review": {"status": "PENDING", "reviewer": None, "note": None}})
    counts = Counter(q["group"] for q in out)
    meta = {"source": Path(args.db).name, "seed": SEED, "made": "2026-11-19", "queries": len(out), "groups": dict(counts),
            "definition": "grade 2 = same incident + same intake label; grade 1 = same cause + same intake label; "
                          "only cases approved before as_of", "target": "Hit@5 >= 16 of 20"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"meta": meta, "queries": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


def evaluate(args):
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    from rma_agent import similar_cases
    from rma_agent.agents.runtime import get_runtime
    conn = get_runtime().conn
    lines, hit, strict, strict_n, base_hit = [], 0, 0, 0, 0
    for q in gold["queries"]:
        ans = q["answers"]
        got = [i["rma_id"] for i in similar_cases(q["rma_id"], q["as_of"], limit=5)["items"]]
        base = [r[0] for r in conn.execute("""select reference_rma_id from similar_cases where query_rma_id=? and searched_at<=?
                                               order by similarity_score desc limit 5""", (q["rma_id"], q["as_of"]))]
        grades = [ans.get(r, 0) for r in got]
        h, b = any(grades), any(ans.get(r, 0) for r in base)
        hit += h
        base_hit += b
        if q["kind"] == "incident":
            strict_n += 1
            strict += any(g == 2 for g in grades)
        first = next((i + 1 for i, g in enumerate(grades) if g), None)
        lines.append(f"| {q['rma_id']} | {q['why']} | {q['intake_name']} | {q['cause_name']} | {q['n_grade2']} / {q['n_grade1']} "
                     f"| {''.join(str(g) for g in grades) or '-'} | {'O' if h else 'X'} {f'({first}위)' if first else ''} | {'O' if b else 'X'} |")
    n = len(gold["queries"])
    head = [f"# 유사 사례 Hit@5 (WBS 5.2): {gold['meta']['source']}\n",
            f"- 정답셋: `{args.gold}` ({n}건, 사람 검토 전)",
            f"- 정답 기준: {gold['meta']['definition']}",
            f"- **에이전트 `similar_cases()` Hit@5: {hit} / {n}** (목표 16 이상: {'충족' if hit >= 16 else '미달'})",
            f"- 같은 사건까지 맞힘(사건성 {strict_n}건): {strict} / {strict_n}",
            f"- 비교: 데이터에 저장된 글자 유사도 상위 5건만 썼을 때 {base_hit} / {n}\n",
            "| 질의 | 유형 | 증상 | 원인 | 정답 수 (같은 사건 / 같은 원인) | 상위 5건 등급 | 에이전트 | 글자 유사도만 |",
            "|---|---|---|---|---|---|---|---|"]
    text = "\n".join(head + lines) + "\n\n상위 5건 등급: 2 = 같은 사건·같은 증상, 1 = 같은 원인·같은 증상, 0 = 정답 아님.\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["make", "eval"])
    p.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v4.sqlite"))
    p.add_argument("--out")
    p.add_argument("--gold", default=str(ROOT / "eval" / "similar_cases_gold_v4.json"))
    a = p.parse_args()
    if a.cmd == "make":
        a.out = a.out or str(ROOT / "eval" / "similar_cases_gold_v4.json")
        make(a)
    else:
        evaluate(a)
