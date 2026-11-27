"""RAG search vs today's character search on the similar-case task (results/20261119_similar_cases_method.md).

    python3 scripts/eval_rag.py [--out results/20261127_rag_v5.md]

Query: a new return's intake text at its analysis start. Pool: approved cases known then (same intake symptom, as the
agent does; also without the symptom filter). Hit: a case with the same confirmed cause and the same symptom in the
top 5 (top 1). Weights picked on 2025-08~09, then checked once on 2025-10~12.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RMA_LLM", "off")
from rma_agent import rag  # noqa: E402
from rma_agent.agents.cause_analysis import _text_bigrams  # noqa: E402
from rma_agent.agents.runtime import get_runtime  # noqa: E402
from rma_agent.evaluate import known_queries, queries  # noqa: E402
from rma_agent.truth import GroundTruth  # noqa: E402

WEIGHTS = [0.0, 0.5, 0.7, 0.8, 0.9, 1.0]   # embedding share; the rest is the character score


def run(rt, idx, qs, truth, labelled=True):
    pos = {d.rma_id: i for i, d in enumerate(idx.docs) if d.type == "CASE"}
    claims = {r[0]: r[1] for r in rt.conn.execute("select rma_id, raw_text from rma_claim")}
    qvec = rag._normalise(idx.embedder.embed_docs([claims[q] for q, _, _ in qs]))
    res = {w: [0, 0] for w in WEIGHTS}
    res["today"] = [0, 0]
    n = 0
    for (rma_id, as_of, _), qv in zip(qs, qvec):
        store = rt.store(as_of, rma_id)
        claim = store.claim(rma_id)
        label = claim.get("intake_label")
        use_label = labelled and label not in (None, "INSUFFICIENT_INFO", "UNCLASSIFIED")
        pool = store.past_cases(intake_label=label) if use_label else store.past_cases()
        answer = truth.answer(rma_id)["cause"]
        gold = {c.rma_id for c in pool if c.cause_code == answer and c.intake_label == label}
        if not gold or not pool:
            continue
        n += 1
        ids = [c.rma_id for c in pool]
        rows = np.array([pos[i] for i in ids])
        emb = idx.matrix[rows] @ qv
        q = _text_bigrams(rma_id, claim.get("raw_text"))
        jac = np.array([len(q & _text_bigrams(c.rma_id, c.raw_text)) / (len(q | _text_bigrams(c.rma_id, c.raw_text)) or 1)
                        for c in pool], dtype=np.float32)
        order_bonus = np.arange(len(pool)) * 1e-9          # ties: most recent first, as the agent does
        for key in res:
            score = jac + order_bonus if key == "today" else key * emb + (1 - key) * jac + order_bonus
            top = [ids[i] for i in np.argsort(-score)[:5]]
            res[key][0] += top[0] in gold
            res[key][1] += any(t in gold for t in top)
    return n, {k: (v[0] / n, v[1] / n) for k, v in res.items()}


KNOWLEDGE = {"CAUSE", "ESCAPE", "SYMPTOM", "ACTION", "INVESTIGATION"}


def run_questions(idx, path):
    """Paraphrased questions (eval/rag_questions_v5.json, written by an LLM without the item's name) -> master document."""
    import json
    qs = json.loads(path.read_text(encoding="utf-8"))
    vecs = idx.embedder.embed_docs([q["question"] for q in qs])
    res = {}
    for w in WEIGHTS:
        h1 = h3 = 0
        for q, v in zip(qs, vecs):
            hits = idx.search(q["question"], types=KNOWLEDGE, k=3, query_vector=v, w_embed=w)
            got = [f"{h['type']}:{_doc_code(h)}" for h in hits]
            h1 += got[:1] == [q["gold"]]
            h3 += q["gold"] in got
        res[w] = (h1 / len(qs), h3 / len(qs))
    return len(qs), res


def _doc_code(hit):
    return hit["doc_id"].split(":", 1)[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    started = time.time()
    rt, idx, truth = get_runtime(), rag.get_index(), GroundTruth(get_runtime().conn)
    assert idx.matrix is not None, "embeddings unavailable"
    val, _ = known_queries(rt.conn, queries(rt.conn, "2025-08-01", "2025-10-01"), truth)
    test, _ = known_queries(rt.conn, queries(rt.conn, "2025-10-01"), truth)
    n_val, r_val = run(rt, idx, val, truth)
    best = max(WEIGHTS, key=lambda w: (r_val[w][1], r_val[w][0]))
    n_test, r_test = run(rt, idx, test, truth)
    n_all, r_all = run(rt, idx, test, truth, labelled=False)
    n_q, r_q = run_questions(idx, ROOT / "eval" / "rag_questions_v5.json")
    name = lambda k: "지금 방식 (글자 유사도)" if k == "today" else \
        ("글자만 (같은 계산)" if k == 0 else "임베딩만" if k == 1 else f"혼합 (임베딩 {k:.0%})")
    lines = [f"# RAG 검색 비교: {rag.EMBED_MODEL} {rag.EMBED_DIM}차원 vs 글자 유사도", "",
             "- 질의: 새 반품의 접수 원문(분석 시작 시점). 후보: 그때까지 승인된 사례. 정답: 같은 원인 + 같은 증상",
             f"- 가중치 선택 2025-08~09 ({n_val:,}건) → 확인 2025-10~ ({n_test:,}건)", "",
             "| 방식 | 검증 Hit@1 | 검증 Hit@5 | 평가 Hit@1 | 평가 Hit@5 | 증상 필터 없이 평가 Hit@5 |", "|---|---:|---:|---:|---:|---:|"]
    for k in ["today"] + WEIGHTS:
        mark = " **(채택)**" if k == best else ""
        lines.append(f"| {name(k)}{mark} | {r_val[k][0]:.1%} | {r_val[k][1]:.1%} | {r_test[k][0]:.1%} | {r_test[k][1]:.1%} | {r_all[k][1]:.1%} |")
    lines += ["", f"## 자유 질문 → 기준 문서 (원인·유출·증상, 이름을 쓰지 않고 바꿔 말한 질문 {n_q}개)", "",
              "| 방식 | Hit@1 | Hit@3 |", "|---|---:|---:|"]
    lines += [f"| {name(w)} | {r_q[w][0]:.1%} | {r_q[w][1]:.1%} |" for w in WEIGHTS]
    lines.append(f"\n소요 {time.time() - started:.0f}초")
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        (ROOT / a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
