"""v3 part 3: refresh derived files after patch_v3.py and patch_v3_text.py.

  - similar_cases recomputed on the new claim text (opening-time data only, as in v31)
  - evaluation inputs/labels rebuilt from the database with the same split membership;
    labels gain intake_minor_code (label at intake, the target for intake classification)
  - lot_impact_reference gains impacted_lots_without_claims_at_decision
  - sample CSV, schema.sql, manifest
"""
import csv
import heapq
import json
from pathlib import Path
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert manifest["version"] == "v31" and "v3_severity" in manifest, "Run after patch_v3_severity.py"
db = sqlite3.connect(root / "pcb_rma.sqlite")
db.row_factory = sqlite3.Row
db.execute("pragma foreign_keys=on")


def scalar(sql):
    return db.execute(sql).fetchone()[0]


# Similar cases: same label-free baseline as v31 on the rewritten text.
def bigrams(raw):
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch == " ")
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1) if " " not in cleaned[i:i + 2]}


claims = db.execute("select rma_id,raw_text,opened_at from rma_claim order by opened_at,rma_id").fetchall()
tokens = [bigrams(row["raw_text"]) for row in claims]
triage = dict(db.execute("select rma_id,decided_at from qa_triage"))
matches = []
for idx, row in enumerate(claims):
    if row["opened_at"] < "2025-10-01":
        continue
    needle = tokens[idx]

    def scores():
        for earlier in range(idx):
            if claims[earlier]["opened_at"] >= row["opened_at"]:
                break
            hit = len(needle & tokens[earlier])
            if hit:
                yield (hit / (len(needle) + len(tokens[earlier]) - hit), -earlier)

    for score, neg in heapq.nlargest(5, scores()):
        matches.append((f"SIM-{len(matches) + 1:07}", row["rma_id"], claims[-neg]["rma_id"], triage[row["rma_id"]],
                        round(score, 6), "raw_text_char_bigram_jaccard", "SYNTHETIC_BASELINE_REQUIRES_REVIEW",
                        "pcb-v3-baseline-1"))
with db:
    db.execute("delete from similar_cases")
    db.executemany("insert into similar_cases values (?,?,?,?,?,?,?,?)", matches)

# Evaluation files: same membership, fields refreshed from the database.
inputs = {r["rma_id"]: dict(r) for r in db.execute("""
    select rma_id,customer_id,opened_at,detection_stage,reported_part_no,reported_lot_no,reported_date_code,raw_text
    from rma_claim""")}
labels = {r["rma_id"]: dict(r) for r in db.execute("""
    select r.rma_id,s.primary_label minor_code,s.intake_label intake_minor_code,t.severity,t.analysis_level,i.lot_id
    from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id) join qa_triage t using(rma_id)""")}
evaluation = root / "evaluation"
for folder in (evaluation, evaluation / "investigation_holdout"):
    for split in ("train", "validation", "test"):
        old = [json.loads(x) for x in (folder / f"{split}_reference_labels.jsonl").read_text(encoding="utf-8").splitlines()]
        with (folder / f"{split}_inputs.jsonl").open("w", encoding="utf-8") as fi, \
             (folder / f"{split}_reference_labels.jsonl").open("w", encoding="utf-8") as fl:
            for ref in old:
                db_label = labels[ref["rma_id"]]
                fi.write(json.dumps(inputs[ref["rma_id"]], ensure_ascii=False) + "\n")
                fl.write(json.dumps({
                    "rma_id": ref["rma_id"], "minor_code": db_label["minor_code"],
                    "intake_minor_code": db_label["intake_minor_code"], "severity": db_label["severity"],
                    "analysis_level": db_label["analysis_level"], "cause_code": ref["cause_code"],
                    "cause_category": ref["cause_category"], "traced_lot_id": db_label["lot_id"],
                    "synthetic_reference": True}, ensure_ascii=False) + "\n")

impact_file = evaluation / "lot_impact_reference.jsonl"
refs = [json.loads(x) for x in impact_file.read_text(encoding="utf-8").splitlines()]
for ref in refs:
    ref["impacted_lots_without_claims_at_decision"] = sorted(r[0] for r in db.execute(
        "select lot_id from impacted_lots_without_claims where investigation_id=?", (ref["investigation_id"],)))
impact_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in refs), encoding="utf-8")

# Manifest, schema, sample CSV.
assert scalar("pragma integrity_check") == "ok"
assert not db.execute("pragma foreign_key_check").fetchone()
assert scalar("select count(*) from rma_claim") == 10610
manifest["version"] = "v3"
manifest["based_on"] = "PCB_RMA_dataset_v31 + patch_v3.py, patch_v3_text.py, finalize_v3.py"
manifest["table_rows"] = {t: scalar(f'select count(*) from "{t}"') for t in manifest["table_rows"]}
manifest["intake_labels"] = dict(db.execute("select intake_label,count(*) from symptom_assessment group by 1"))
manifest["checks"]["search_non_historical_or_oracle_reference"] = {"value": scalar("""
    select count(*) from similar_cases s join rma_claim q on q.rma_id=s.query_rma_id
    join rma_claim r on r.rma_id=s.reference_rma_id where r.opened_at>=q.opened_at"""), "ok": True}
assert manifest["checks"]["search_non_historical_or_oracle_reference"]["value"] == 0
(root / "schema.sql").write_text(
    "PRAGMA foreign_keys=ON;\n\n" + ";\n\n".join(row[0] for row in db.execute(
        "select sql from sqlite_schema where sql is not null and name not like 'sqlite_%' order by type='view',rowid")) + ";\n",
    encoding="utf-8")
for name in (*manifest["table_rows"], "qa_symptom_reproduction"):
    cur = db.execute(f'select * from "{name}" limit 100')
    with (root / "sample_csv" / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([col[0] for col in cur.description])
        writer.writerows(cur)
manifest["v3_patch"]["symptom_reproduction"] = dict(
    db.execute("select reproduction_status,count(*) from qa_symptom_reproduction group by 1"))
db.close()
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("v3 finalized:", manifest["intake_labels"])
