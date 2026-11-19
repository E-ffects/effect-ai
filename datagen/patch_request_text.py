"""Tidy three generated request phrases in customer text and messages. Run on a dataset folder; safe to rerun.

    python patch_request_text.py <dataset folder>      # folder with pcb_rma.sqlite, evaluation/, sample_csv/, manifest.json

The three phrases are customer requests and a QA message, not symptom text, so
labels and evidence spans do not change (checked below):
  "원인 분석 및 대책서(8D) 회신 부탁드립니다."       -> "원인 분석 및 대책서 회신 부탁드립니다."
  "긴급 봉쇄 조치(D3) 내용을 먼저 공유 부탁드립니다." -> "긴급 봉쇄 조치 내용을 먼저 공유 부탁드립니다."
  "[D3 봉쇄] 영향 Lot ..."                           -> "[봉쇄 조치] 영향 Lot ..."
The generator (pcb_config.REQUESTS, generate_v30.py) uses the new wording, so a full rebuild gives the same text.
similar_cases (character-bigram baseline on raw_text) is recomputed with finalize_v3.py's method, because the
text changed; a full rebuild of v5 with the new wording gives the same table (checked 2026-11-19).
"""
import csv
import heapq
import json
import sqlite3
import sys
from pathlib import Path

REPLACE = [("원인 분석 및 대책서(8D) 회신 부탁드립니다.", "원인 분석 및 대책서 회신 부탁드립니다."),
           ("긴급 봉쇄 조치(D3) 내용을 먼저 공유 부탁드립니다.", "긴급 봉쇄 조치 내용을 먼저 공유 부탁드립니다."),
           ("[D3 봉쇄] ", "[봉쇄 조치] ")]
COLUMNS = [("rma_claim", "raw_text", "rma_id"), ("communication", "message_text", "message_id"),
           ("claim_text_variant", "original_raw_text", "rma_id")]


def fix(text):
    if text is None:
        return text
    for old, new in REPLACE:
        text = text.replace(old, new)
    return text


def bigrams(raw):
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch == " ")
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1) if " " not in cleaned[i:i + 2]}


def recompute_similar_cases(db):
    """Same as finalize_v3.py: top 5 earlier claims by bigram Jaccard for claims opened from 2025-10-01."""
    claims = db.execute("select rma_id,raw_text,opened_at from rma_claim order by opened_at,rma_id").fetchall()
    tokens = [bigrams(r[1]) for r in claims]
    triage = dict(db.execute("select rma_id,decided_at from qa_triage"))
    matches = []
    for idx, (rma_id, _, opened_at) in enumerate(claims):
        if opened_at < "2025-10-01":
            continue
        needle = tokens[idx]

        def scores():
            for earlier in range(idx):
                if claims[earlier][2] >= opened_at:
                    break
                hit = len(needle & tokens[earlier])
                if hit:
                    yield (hit / (len(needle) + len(tokens[earlier]) - hit), -earlier)

        for score, neg in heapq.nlargest(5, scores()):
            matches.append((f"SIM-{len(matches) + 1:07}", rma_id, claims[-neg][0], triage[rma_id], round(score, 6),
                            "raw_text_char_bigram_jaccard", "SYNTHETIC_BASELINE_REQUIRES_REVIEW", "pcb-v3-baseline-1"))
    with db:
        db.execute("delete from similar_cases")
        db.executemany("insert into similar_cases values (?,?,?,?,?,?,?,?)", matches)
    return len(matches)


root = Path(sys.argv[1])
db = sqlite3.connect(root / "pcb_rma.sqlite")
tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
spans_before = db.execute("select group_concat(evidence_spans_json, '|') from symptom_assessment").fetchone()[0]
changed = {}
with db:
    for table, column, key in COLUMNS:
        if table not in tables:
            continue
        rows = db.execute(f"select {key}, {column} from {table}").fetchall()
        n = 0
        for k, v in rows:
            new = fix(v)
            if new != v:
                db.execute(f"update {table} set {column}=? where {key}=?", (new, k))
                n += 1
        changed[f"{table}.{column}"] = n
spans_after = db.execute("select group_concat(evidence_spans_json, '|') from symptom_assessment").fetchone()[0]
assert spans_before == spans_after
bad = [r for r in db.execute("""select r.raw_text, s.evidence_spans_json from rma_claim r join rma_item i using(rma_id)
                                join symptom_assessment s using(rma_item_id) where s.intake_label!='INSUFFICIENT_INFO'""")
       if not all(x in r[0] for x in json.loads(r[1] or "[]"))]
assert not bad, bad[:2]
left = sum(db.execute(f"select count(*) from {t} where {c} like '%(8D)%' or {c} like '%(D3)%' or {c} like '%[D3 %'").fetchone()[0]
           for t, c, _ in COLUMNS if t in tables)
assert left == 0, left

similar = recompute_similar_cases(db)

# evaluation inputs: raw_text must match the database
texts = dict(db.execute("select rma_id, raw_text from rma_claim"))
files = 0
for path in sorted((root / "evaluation").rglob("*_inputs.jsonl")):
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    for r in rows:
        r["raw_text"] = texts[r["rma_id"]]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    files += 1

# sample CSV previews: plain text replacement keeps the file's own format (BOM, CRLF, quoting).
# The phrases contain no comma, quote or line break, so this is safe for CSV.
for name in ("rma_claim", "communication", "claim_text_variant"):
    path = root / "sample_csv" / f"{name}.csv"
    if path.exists():
        data = path.read_bytes().decode("utf-8")
        path.write_bytes(fix(data).encode("utf-8"))
cur = db.execute('select * from "similar_cases" limit 100')  # preview, written as slim_v3.py does
with (root / "sample_csv" / "similar_cases.csv").open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow([c[0] for c in cur.description])
    writer.writerows(cur)
db.close()

manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
if sum(changed.values()) or "request_text" not in manifest:  # a rerun changes no text; keep the first counts
    manifest["request_text"] = {"date": "2026-11-19", "replaced": [{"from": a, "to": b} for a, b in REPLACE],
                             "rows_changed": changed, "evaluation_files": files, "labels_and_spans_unchanged": True}
manifest["request_text"]["similar_cases_recomputed"] = similar
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest["request_text"], ensure_ascii=False))
