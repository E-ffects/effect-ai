"""v3 last step: keep only what the multi-agent scope needs (run after finalize_v3.py).

Scope: known-cause claims, no 8D tracking, no lot-impact tracking, L3 = human approval gate.
  - drops 13 tables (8D tracking, lot impact, bottleneck, sample logistics, orders, panels) and the views on them
  - keeps shipment.order_item_id and rma_item.panel_id as plain values (foreign keys to dropped tables removed)
  - analysis_level_rule outputs rewritten for the agent flow (no 8D report types)
"""
import json
from pathlib import Path
import re
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert manifest["version"] in ("v3", "v4") and "v3_slim" not in manifest, "Run after finalize_v3.py, once"

DROP = ["qa_report", "eight_d_step", "containment_action", "document_change", "supplier_action",
        "customer_disposition_approval", "monitoring_cohort",          # 8D tracking
        "lot_impact_assessment",                                         # lot impact tracking
        "analysis_activity",                                             # investigation bottleneck
        "return_authorization", "return_receipt",                        # sample logistics
        "order_item", "panel"]                                           # order / panel detail
FK_TO_DROP = {"shipment": "order_item", "rma_item": "panel"}

db = sqlite3.connect(root / "pcb_rma.sqlite")
db.execute("pragma foreign_keys=off")
db.execute("pragma legacy_alter_table=off")


def uses(sql, table):
    return re.search(rf"\b(from|join)\s+{table}\b", sql, re.IGNORECASE) is not None


with db:
    views = db.execute("select name,sql from sqlite_master where type='view'").fetchall()
    gone, dropped_views = set(DROP), []
    while True:   # views built on dropped tables or on views already dropped
        more = [name for name, sql in views if name not in gone and any(uses(sql, t) for t in gone)]
        if not more:
            break
        gone.update(more)
        dropped_views += more
    # Drop every view during the table rebuild (SQLite re-validates views on rename), then restore the kept ones.
    kept_views = [(name, sql) for name, sql in views if name not in gone]
    for name, _ in reversed(views):
        db.execute(f'drop view if exists "{name}"')

    # Remove foreign keys that point at dropped tables, keeping the column values.
    for table, parent in FK_TO_DROP.items():
        sql = db.execute("select sql from sqlite_master where type='table' and name=?", (table,)).fetchone()[0]
        new_sql = re.sub(rf"\s+REFERENCES {parent}\([^)]*\)( DEFERRABLE INITIALLY DEFERRED)?", "", sql)
        assert new_sql != sql, table
        indexes = [r[0] for r in db.execute(
            "select sql from sqlite_master where type='index' and tbl_name=? and sql is not null", (table,))]
        db.execute(new_sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}__new", 1))
        db.execute(f"insert into {table}__new select * from {table}")
        db.execute(f"drop table {table}")
        db.execute(f"alter table {table}__new rename to {table}")
        for index_sql in indexes:
            db.execute(index_sql)

    for table in DROP:
        db.execute(f'drop table "{table}"')
    for _, sql in kept_views:
        db.execute(sql)

    # L3 now means the human approval gate of the agent flow.
    db.executemany("update analysis_level_rule set required_outputs=?,report_type=? where rule_id=?", [
        ("소분류·회신 초안, 유사 사례 표시", "REPLY", "L1"),
        ("유사 사례 기반 원인·공정·장비 후보, QA 확인 후 회신", "QA_REVIEW", "L2"),
        ("원인·공정·장비 후보와 근거, 사람 승인(원인 확정·격리·4M 변경) 후 사례 저장", "HUMAN_APPROVAL", "L3"),
    ])
    db.execute("""
        update investigation set scope_definition=json_set(scope_definition,'$.level_note',
          replace(json_extract(scope_definition,'$.level_note'),'8D 약식(L2)으로 관리','L2로 관리'))
        where json_extract(scope_definition,'$.level_note') is not null""")

db.execute("pragma foreign_keys=on")
assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
assert not db.execute("pragma foreign_key_check").fetchone()
for name, sql in db.execute("select name,sql from sqlite_master where type='view'").fetchall():
    db.execute(f'select * from "{name}" limit 1').fetchall()   # every remaining view still resolves
db.execute("vacuum")

tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' order by name")]
manifest["table_rows"] = {t: db.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
manifest["views"] = [r[0] for r in db.execute("select name from sqlite_master where type='view' order by name")]
manifest["v3_slim"] = {
    "dropped_tables": DROP, "dropped_views": dropped_views,
    "foreign_keys_removed": [f"{t}.{'order_item_id' if p == 'order_item' else 'panel_id'} -> {p}" for t, p in FK_TO_DROP.items()],
    "note": "checks above were computed on the full v3 before this step; tables listed here are no longer in the database",
    "claims_by_analysis_level": dict(db.execute("select analysis_level,count(*) from qa_triage group by 1")),
}
import csv
for name in (*tables, *manifest["views"]):   # refresh previews: this step edits rule text, scope notes and two schemas
    cur = db.execute(f'select * from "{name}" limit 100')
    with (root / "sample_csv" / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([col[0] for col in cur.description])
        writer.writerows(cur)
(root / "schema.sql").write_text(
    "PRAGMA foreign_keys=ON;\n\n" + ";\n\n".join(row[0] for row in db.execute(
        "select sql from sqlite_schema where sql is not null and name not like 'sqlite_%' order by type='view',rowid")) + ";\n",
    encoding="utf-8")
db.close()
for table in DROP:
    (root / "sample_csv" / f"{table}.csv").unlink(missing_ok=True)
for name in dropped_views:
    (root / "sample_csv" / f"{name}.csv").unlink(missing_ok=True)
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"tables": len(tables), "views": len(manifest["views"]), **manifest["v3_slim"]}, ensure_ascii=False, indent=2))
