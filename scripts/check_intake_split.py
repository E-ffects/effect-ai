"""WBS 4.9.1: check the intake-label (symptom minor code) train/validation/test split of a PCB RMA dataset.

    python scripts/check_intake_split.py <dataset folder with pcb_rma.sqlite and evaluation/> [--out results/x.md]

Checks: counts by split and label, the time split rule, overlap between splits, agreement with the database,
duplicate text inside and across splits, and label checks (codes, evidence spans, minimum counts).
"""
import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

SPLITS = ("train", "validation", "test")
SPECIAL = ("INSUFFICIENT_INFO", "UNCLASSIFIED")
MIN_TRAIN = 20  # fewer train examples than this per label is flagged


def load(folder, split):
    inputs = [json.loads(x) for x in (folder / f"{split}_inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    labels = {r["rma_id"]: r for r in map(json.loads, (folder / f"{split}_reference_labels.jsonl").read_text(encoding="utf-8").splitlines())}
    return [{**x, "label": labels[x["rma_id"]]["intake_minor_code"]} for x in inputs], len(labels)


def normalise(text, row):
    """Text without the per-claim details (customer, P/N, Lot, D/C, numbers), to find template duplicates."""
    for key in ("customer_id", "reported_part_no", "reported_lot_no", "reported_date_code"):
        if row.get(key):
            text = text.replace(row[key], "")
    return re.sub(r"\s+", " ", re.sub(r"[0-9.,%]+", "#", text)).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--out")
    args = parser.parse_args()
    root = Path(args.dataset)
    folder = root / "evaluation"
    db = sqlite3.connect(root / "pcb_rma.sqlite")
    out, problems = [], []
    p = out.append

    data, n_labels = {}, {}
    for split in SPLITS:
        data[split], n_labels[split] = load(folder, split)
    rows = [r for s in SPLITS for r in data[s]]
    p(f"# 소분류 학습·검증 데이터 점검: {root.name}\n")

    # 1. counts and split rule
    p("## 1. 분리 기준과 건수\n")
    p("| 분할 | 건수 | 비율 | 접수일 범위 | 정보 부족 | 분류 불가 |\n|---|---:|---:|---|---:|---:|")
    for s in SPLITS:
        rs = data[s]
        c = Counter(r["label"] for r in rs)
        p(f"| {s} | {len(rs):,} | {len(rs) / len(rows):.1%} | {min(r['opened_at'] for r in rs)[:10]} ~ {max(r['opened_at'] for r in rs)[:10]} "
          f"| {c['INSUFFICIENT_INFO']} | {c['UNCLASSIFIED']} |")
    p(f"| 합계 | {len(rows):,} | | | | |\n")
    rule = {"train": lambda d: d < "2025-08-01", "validation": lambda d: "2025-08-01" <= d < "2025-10-01",
            "test": lambda d: d >= "2025-10-01"}
    bad_rule = sum(not rule[s](r["opened_at"]) for s in SPLITS for r in data[s])
    ids = [r["rma_id"] for r in rows]
    overlap = len(ids) - len(set(ids))
    total_db = db.execute("select count(*) from rma_claim").fetchone()[0]
    p(f"- 기준: 접수일(`opened_at`) train < 2025-08-01 ≤ validation < 2025-10-01 ≤ test. 미래 데이터로 과거를 맞히지 않도록 시간으로 나눈다.")
    p(f"- 기준을 어긴 행: {bad_rule}건 · 분할 사이에 겹치는 반품: {overlap}건 · 입력과 정답 행 수 불일치: "
      f"{sum(len(data[s]) != n_labels[s] for s in SPLITS)}개 분할 · DB 반품 {total_db:,}건 중 누락 {total_db - len(set(ids))}건")
    if bad_rule or overlap or total_db != len(set(ids)):
        problems.append("분할 기준 또는 누락")

    # 2. agreement with the database
    dbrows = {r[0]: r[1:] for r in db.execute("""select r.rma_id, r.raw_text, s.intake_label, s.evidence_spans_json
        from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)""")}
    text_diff = sum(dbrows[r["rma_id"]][0] != r["raw_text"] for r in rows)
    label_diff = sum(dbrows[r["rma_id"]][1] != r["label"] for r in rows)
    p(f"- DB와 대조: 문장 다름 {text_diff}건, 라벨 다름 {label_diff}건\n")
    if text_diff or label_diff:
        problems.append("평가 파일과 DB 불일치")

    # 3. labels by split
    codes = [r[0] for r in db.execute("select minor_code from symptom_definition order by minor_code")]
    p("## 2. 라벨별 건수\n")
    p("| 라벨 | train | validation | test | 비고 |\n|---|---:|---:|---:|---|")
    by = {s: Counter(r["label"] for r in data[s]) for s in SPLITS}
    for code in sorted(set(codes) | {r["label"] for r in rows}):
        note = []
        if code not in codes:
            note.append("정의에 없는 코드")
        if by["train"][code] < MIN_TRAIN:
            note.append(f"train {MIN_TRAIN}건 미만")
        if not by["test"][code]:
            note.append("test 없음")
        p(f"| {code} | {by['train'][code]} | {by['validation'][code]} | {by['test'][code]} | {', '.join(note)} |")
        if "정의에 없는 코드" in note:
            problems.append(f"정의에 없는 코드 {code}")
    p("")

    # 4. duplicates
    p("## 3. 중복 점검\n")
    exact, norm, span_of = defaultdict(set), defaultdict(set), {}
    for s in SPLITS:
        for r in data[s]:
            exact[r["raw_text"]].add(s)
            norm[normalise(r["raw_text"], r)].add(s)
            spans = json.loads(dbrows[r["rma_id"]][2] or "[]")
            span_of[r["rma_id"]] = spans[0] if spans else None
    train_spans = {span_of[r["rma_id"]] for r in data["train"]} - {None}
    p("| 점검 | 결과 |\n|---|---|")
    p(f"| 같은 문장이 두 분할 이상에 있음 | {sum(len(v) > 1 for v in exact.values())}개 문장 |")
    dup_in_split = {s: len(data[s]) - len({r['raw_text'] for r in data[s]}) for s in SPLITS}
    p(f"| 분할 안에서 완전히 같은 문장 | " + ", ".join(f"{s} {n}건" for s, n in dup_in_split.items()) + " |")
    norm_test = [r for r in data["test"] if len(norm[normalise(r["raw_text"], r)]) > 1]
    p(f"| 고객·P/N·Lot·숫자를 지우면 train·validation과 같은 test 문장 | {len(norm_test):,}건 ({len(norm_test) / len(data['test']):.1%}) |")
    labelled_test = [r for r in data["test"] if span_of[r["rma_id"]]]
    seen = [r for r in labelled_test if span_of[r["rma_id"]] in train_spans]
    p(f"| test 증상 구절이 train에 그대로 있음 | {len(seen):,} / {len(labelled_test):,}건 ({len(seen) / len(labelled_test):.1%}) |")
    variant = {}
    if db.execute("select 1 from sqlite_master where name='claim_text_variant'").fetchone():
        variant = dict(db.execute("select rma_id, variant from claim_text_variant"))
        messy = [r for r in labelled_test if r["rma_id"] in variant]
        if messy:
            seen_m = sum(span_of[r["rma_id"]] in train_spans for r in messy)
            p(f"| └ 지저분한 문장(`claim_text_variant`)만 | {seen_m} / {len(messy)}건 ({seen_m / len(messy):.1%}) |")
    p("\n- 문장이 템플릿에서 만들어져 증상 구절이 분할 사이에 많이 겹친다. 그래서 이 분할의 test 점수는 실제보다 높게 나온다. "
      "독립 평가는 사람이 쓴 문장으로 만든 정답셋(4.9.2)으로 한다.\n")

    # 5. label checks
    p("## 4. 라벨 검수\n")
    no_span = [r for r in rows if r["label"] not in SPECIAL and not span_of[r["rma_id"]]]
    off_text = [r for r in rows if r["label"] != "INSUFFICIENT_INFO" and span_of[r["rma_id"]]
                and span_of[r["rma_id"]] not in r["raw_text"]]
    special_span = [r for r in rows if r["label"] == "INSUFFICIENT_INFO" and span_of[r["rma_id"]]]
    special_in_text = [r for r in special_span if span_of[r["rma_id"]] in r["raw_text"]]
    p("| 점검 | 결과 |\n|---|---|")
    p(f"| 라벨 코드가 `symptom_definition`에 있음 | {'모두 있음' if {r['label'] for r in rows} <= set(codes) else '없는 코드 있음'} |")
    p(f"| 소분류 라벨인데 근거 구절이 없음 | {len(no_span)}건 |")
    p(f"| 근거 구절이 원문에 그대로 없음 (정보 부족 제외) | {len(off_text)}건 |")
    p(f"| 정보 부족인데 저장된 근거 구절이 있음 | {len(special_span)}건 (원문에 있는 것 {len(special_in_text)}건). "
      "QA가 나중에 확인한 증상의 구절이라 접수 학습·화면에는 쓰지 않는다 |")
    if variant:
        p(f"| 지저분한 문장 수 (`claim_text_variant`) | " + ", ".join(f"{k} {v}" for k, v in Counter(variant.values()).most_common()) + " |")
    if no_span or off_text or special_in_text:
        problems.append("근거 구절")
    p("")
    p("## 판정\n")
    p("문제 없음" if not problems else "확인 필요: " + ", ".join(problems))
    text = "\n".join(out) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
