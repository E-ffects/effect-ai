"""WBS 4.10.1: compare intake (symptom subcategory) models on the phrase split, settings chosen on validation only.

    python scripts/compare_intake_models.py [--db data/pcb_rma_v5.sqlite] [--split data/splits/intake_phrase_split_v5.json]
                                            [--llm 0] [--out results/x.md]

Models (all see only the train split):
  knn       today's retrieval: character-bigram TF-IDF (digits removed), k nearest, similarity-weighted vote;
            below a confidence threshold -> INSUFFICIENT_INFO
  logreg    character 1-3 gram TF-IDF + logistic regression (one model over the 20 labels)
  knn+llm   today's full classify() (retrieval examples + subcategory definitions -> LLM), on a stratified sample
            of the test split (--llm N per label; costs API calls)
Settings (k, threshold, C) are picked on validation by macro-F1; test is scored once.
Also reported on the official time split (train < 2025-08 <= validation < 2025-10 <= test) for reference.
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CODE = re.compile(r"\S*\d\S*")  # same as the classifier: tokens with digits (P/N, Lot, D/C, quantities) removed
INSUFFICIENT, UNCLASSIFIED = "INSUFFICIENT_INFO", "UNCLASSIFIED"


def clean(text):
    return re.sub(r"\s+", " ", CODE.sub(" ", text)).strip()


def load(db, split_file):
    conn = sqlite3.connect(db)
    rows = conn.execute("""select r.rma_id, r.raw_text, s.intake_label, r.opened_at, r.detection_stage
                           from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
                           order by r.rma_id""").fetchall()
    messy = {r[0]: r[1] for r in conn.execute("select rma_id, variant from claim_text_variant")} \
        if conn.execute("select 1 from sqlite_master where name='claim_text_variant'").fetchone() else {}
    phrase = json.loads(Path(split_file).read_text())["split"]
    time_split = {r[0]: ("train" if r[3] < "2025-08-01" else "validation" if r[3] < "2025-10-01" else "test") for r in rows}
    return rows, messy, {"구절 분할": phrase, "시간 분할": time_split}


def metrics(y_true, y_pred):
    labels = sorted(set(y_true))
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, labels=[INSUFFICIENT], zero_division=0)
    return {"acc": accuracy_score(y_true, y_pred), "macro_f1": f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0),
            "ins_p": p[0], "ins_r": r[0]}


class KNN:
    def __init__(self, k=15, threshold=0.45):
        self.k, self.threshold = k, threshold

    def fit(self, texts, labels):
        self.vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 2), sublinear_tf=False, norm="l2")
        self.X = self.vec.fit_transform([clean(t) for t in texts])
        self.y = np.array(labels)
        return self

    def scores(self, texts):
        sims = (self.vec.transform([clean(t) for t in texts]) @ self.X.T).toarray()
        out = []
        for row in sims:
            idx = np.argpartition(-row, self.k)[:self.k]
            votes = Counter()
            for i in idx:
                votes[self.y[i]] += row[i]
            total = sum(votes.values()) or 1.0
            out.append(sorted(((l, v / total) for l, v in votes.items()), key=lambda x: -x[1]))
        return out

    def predict(self, texts, scores=None):
        res = []
        for votes in scores if scores is not None else self.scores(texts):
            top, conf = votes[0]
            res.append(INSUFFICIENT if conf < self.threshold and top not in (INSUFFICIENT, UNCLASSIFIED) else top)
        return res


class LogReg:
    def __init__(self, C=10.0):
        self.C = C

    def fit(self, texts, labels):
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3), sublinear_tf=True, min_df=2)
        X = self.vec.fit_transform([clean(t) for t in texts])
        self.model = LogisticRegression(C=self.C, max_iter=2000)
        self.model.fit(X, labels)
        return self

    def predict(self, texts):
        return list(self.model.predict(self.vec.transform([clean(t) for t in texts])))


def run_split(name, split, rows, messy, out):
    part = {s: [r for r in rows if split.get(r[0]) == s] for s in ("train", "validation", "test")}
    T = {s: ([r[1] for r in rs], [r[2] for r in rs]) for s, rs in part.items()}
    out.append(f"## {name}\n\n학습 {len(part['train']):,} / 검증 {len(part['validation']):,} / 평가 {len(part['test']):,}건\n")
    results = {}
    # kNN: pick k and threshold on validation
    best = None
    for k in (5, 10, 15, 25):
        m = KNN(k=k).fit(*T["train"])
        sc = m.scores(T["validation"][0])
        for th in (0.0, 0.3, 0.45, 0.6):
            m.threshold = th
            f = metrics(T["validation"][1], m.predict(None, sc))["macro_f1"]
            if best is None or f > best[0]:
                best = (f, k, th)
    knn = KNN(k=best[1], threshold=best[2]).fit(*T["train"])
    t0 = time.time(); pred = knn.predict(T["test"][0]); dt = (time.time() - t0) / len(pred) * 1000
    results["knn"] = (f"kNN 검색 (k={best[1]}, 정보 부족 기준 {best[2]})", pred, dt, knn)
    # logistic regression: pick C on validation
    best = None
    for C in (1.0, 10.0, 50.0):
        m = LogReg(C=C).fit(*T["train"])
        f = metrics(T["validation"][1], m.predict(T["validation"][0]))["macro_f1"]
        if best is None or f > best[0]:
            best = (f, C)
    lr = LogReg(C=best[1]).fit(*T["train"])
    t0 = time.time(); pred = lr.predict(T["test"][0]); dt = (time.time() - t0) / len(pred) * 1000
    results["logreg"] = (f"TF-IDF + 로지스틱 회귀 (C={best[1]})", pred, dt, lr)
    y = T["test"][1]
    test_ids = [r[0] for r in part["test"]]
    msy = [i for i, rid in enumerate(test_ids) if rid in messy]
    out.append("| 모델 | 정확도 | Macro-F1 | 정보 부족 정밀도 / 재현율 | 지저분한 문장 정확도 | 1건당 |\n|---|---:|---:|---:|---:|---:|")
    for key, (label, pred, dt, _) in results.items():
        m = metrics(y, pred)
        ma = f"{sum(pred[i] == y[i] for i in msy) / len(msy):.1%} ({len(msy)})" if msy else "-"
        out.append(f"| {label} | {m['acc']:.1%} | **{m['macro_f1']:.3f}** | {m['ins_p']:.1%} / {m['ins_r']:.1%} | {ma} | {dt:.1f}ms |")
    labels = sorted(set(y))
    f_knn = f1_score(y, results["knn"][1], labels=labels, average=None, zero_division=0)
    f_lr = f1_score(y, results["logreg"][1], labels=labels, average=None, zero_division=0)
    n = Counter(y)
    out.append("\n<details><summary>라벨별 F1</summary>\n\n| 라벨 | 평가 건수 | kNN | 로지스틱 회귀 |\n|---|---:|---:|---:|")
    for l, a, b in zip(labels, f_knn, f_lr):
        out.append(f"| {l} | {n[l]} | {a:.3f} | {b:.3f} |")
    out.append("\n</details>\n")
    return part, results


def llm_sample(rows, split, messy, per_label, out):
    os.environ.pop("RMA_LLM", None)
    from rma_agent.agents import classifier as cl
    from rma_agent.agents.runtime import get_runtime
    rt = get_runtime()
    c = cl.Classifier(rt.conn, rt.masters)
    keep = [i for i, it in enumerate(c.index.items) if split.get(it["rma_id"]) == "train"]
    c.index.items = [c.index.items[i] for i in keep]
    c.index.times = [c.index.times[i] for i in keep]
    rng = random.Random(4)
    test = [r for r in rows if split.get(r[0]) == "test"]
    by = {}
    for r in test:
        by.setdefault(r[2], []).append(r)
    sample = [x for l in sorted(by) for x in rng.sample(by[l], min(per_label, len(by[l])))]
    y, pred, used, t0 = [], [], 0, time.time()
    for rid, text, label, _, stage in sample:
        res = c.classify(text, detection_stage=stage)
        y.append(label)
        pred.append(res["minor_code"] or res["status"])
        used += res["model_version"].endswith("+llm")
    dt = (time.time() - t0) / len(sample) * 1000
    m = metrics(y, pred)
    msy = [i for i, r in enumerate(sample) if r[0] in messy]
    ma = f"{sum(pred[i] == y[i] for i in msy) / len(msy):.1%} ({len(msy)})" if msy else "-"
    out.append(f"\n**검색 + LLM** (구절 분할 평가에서 라벨별 {per_label}건, 총 {len(sample)}건, LLM 사용 {used}건): "
               f"정확도 {m['acc']:.1%}, Macro-F1 **{m['macro_f1']:.3f}**, 정보 부족 정밀도 {m['ins_p']:.1%} / 재현율 {m['ins_r']:.1%}, "
               f"지저분한 문장 {ma}, 1건당 {dt / 1000:.1f}초\n")
    return sample, pred


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "pcb_rma_v5.sqlite"))
    ap.add_argument("--split", default=str(ROOT / "data" / "splits" / "intake_phrase_split_v5.json"))
    ap.add_argument("--llm", type=int, default=0, help="test samples per label for knn+llm (0 = skip)")
    ap.add_argument("--out")
    a = ap.parse_args()
    rows, messy, splits = load(a.db, a.split)
    out = [f"# 소분류 모델 비교 (WBS 4.10.1): {Path(a.db).name}", "",
           "설정(k, 정보 부족 기준, C)은 검증 분할의 Macro-F1로 고르고, 평가 분할은 한 번만 쟀다. 정답은 접수 소분류(`intake_label`, 20종).", ""]
    for name, split in splits.items():
        run_split(name, split, rows, messy, out)
    if a.llm:
        llm_sample(rows, splits["구절 분할"], messy, a.llm, out)
    text = "\n".join(out) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
