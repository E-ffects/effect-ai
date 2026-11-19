"""WBS 4.9.1: label-stratified intake split where one symptom phrase never appears in two splits.

The official split (evaluation/, by opened_at) is kept for the "as in operation" check. It cannot be used alone for the
minor-code model: scenarios are time-bound, so some labels exist only in one period (v5: MARK01 and SOLD03 only in
test, STRC03 only in train). This split covers every label in every split, and keeps each symptom phrase (the first
evidence span found in the text) inside one split, so the test measures phrases the model has not seen.

    python scripts/make_intake_split.py data/pcb_rma_v5.sqlite --out data/splits/intake_phrase_split_v5.json

Output: {"method", "seed", "ratios", "counts": {split: {label: n}}, "split": {rma_id: split}}. Deterministic (seed).
"""
import argparse
import json
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

SPLITS = ("train", "validation", "test")
RATIOS = (0.6, 0.2, 0.2)
SEED = 7


def assign(groups, rng):
    """groups: [(key, n)]. Largest first, each to the split furthest below its target share."""
    total = sum(n for _, n in groups)
    filled = dict.fromkeys(SPLITS, 0)
    out = {}
    order = sorted(groups, key=lambda g: (-g[1], g[0]))
    rng.shuffle(order)
    order.sort(key=lambda g: -g[1])
    for i, (key, n) in enumerate(order):
        if i < len(SPLITS) and len(order) >= len(SPLITS):  # every split gets at least one group
            split = SPLITS[i]
        else:
            split = max(SPLITS, key=lambda s: RATIOS[SPLITS.index(s)] * total - filled[s])
        out[key] = split
        filled[split] += n
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--out")
    args = parser.parse_args()
    db = sqlite3.connect(args.db)
    rng = random.Random(SEED)
    rows = db.execute("""select r.rma_id, r.raw_text, s.intake_label, s.evidence_spans_json
                         from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
                         order by r.rma_id""").fetchall()
    by_label = defaultdict(lambda: defaultdict(list))
    for rma_id, text, label, spans_json in rows:
        spans = [s for s in json.loads(spans_json or "[]") if s and s in text]
        by_label[label][spans[0] if spans else f"(no phrase) {rma_id}"].append(rma_id)  # no phrase: each row alone
    split = {}
    for label in sorted(by_label):
        groups = by_label[label]
        plan = assign([(k, len(v)) for k, v in groups.items()], rng)
        for key, ids in groups.items():
            for rma_id in ids:
                split[rma_id] = plan[key]
    labels = {r[0]: r[2] for r in rows}
    counts = {s: dict(sorted(Counter(labels[i] for i, x in split.items() if x == s).items())) for s in SPLITS}
    result = {"method": "label-stratified, grouped by symptom phrase", "seed": SEED, "ratios": dict(zip(SPLITS, RATIOS)),
              "source": Path(args.db).name, "counts": counts, "split": split}
    for s in SPLITS:
        print(s, sum(counts[s].values()), "labels", len(counts[s]))
    missing = {lab for lab in by_label if any(lab not in counts[s] for s in SPLITS)}
    print("labels missing from a split:", sorted(missing) or "none")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
