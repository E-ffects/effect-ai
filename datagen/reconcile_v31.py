"""Reconcile review chronology and refresh the derived metadata."""
from pathlib import Path
import csv
import json
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_path = root / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
assert manifest["version"] == "v31"
asof = manifest["as_of"]
db = sqlite3.connect(root / "pcb_rma.sqlite")
db.execute("pragma foreign_keys=on")
with db:
    view_sql = db.execute("select sql from sqlite_schema where name='qa_symptom_reproduction'").fetchone()[0]
    if "ts.item_code=p.item_code" in view_sql:
        db.execute("drop view qa_symptom_reproduction")
        db.execute(view_sql.replace("ts.item_code=p.item_code", "ts.test_spec_id=p.item_code"))
    db.execute("""create temp table weak_cause as select a.cause_assessment_id
      from cause_assessment a join cause_confirmation_basis b using(cause_assessment_id)
      where a.certainty_status='CONFIRMED' and a.cause_category<>'UNDETERMINED'
      and b.production_record_evidence=0""")
    downgraded = db.execute("select count(*) from weak_cause").fetchone()[0]
    db.execute("""update cause_assessment set certainty_status='PROBABLE',approved_at=null
                  where cause_assessment_id in (select cause_assessment_id from weak_cause)""")
    db.execute("""update claim_cause set rationale='반송품 지표와 일치한 원인 후보. 생산·검사 기록 검증이 남아 있음.'
                  where cause_assessment_id in (select cause_assessment_id from weak_cause)
                  and applicability='APPLIES'""")
    db.execute("""update communication as m
      set message_text='반송품 시험 결과 원인 후보를 확인했습니다. 생산·검사 기록을 추가 검증하고 처리 방안을 안내드립니다.'
      where message_text like '분석 결과 원인이 확인되었습니다.%' and exists (
        select 1 from rma_item i join claim_cause c using(rma_item_id)
        join weak_cause w using(cause_assessment_id) where i.rma_id=m.rma_id)
    """)
    db.execute("delete from communication where sent_at>?", (asof,))
    db.execute("""create temp table recurrence_cause as
      select i.rma_item_id,l.decided_at,a.certainty_status,l.applicability
      from rma_item i join claim_cause l using(rma_item_id) join cause_assessment a using(cause_assessment_id)""")
    moved = db.execute("""
        update recurrence_review as r set reviewed_at=(select decided_at from recurrence_cause c
          where c.rma_item_id=r.rma_item_id)
        where exists (select 1 from recurrence_cause c where c.rma_item_id=r.rma_item_id
          and c.certainty_status='CONFIRMED' and c.applicability='APPLIES' and c.decided_at>r.reviewed_at)
    """).rowcount
    deferred = db.execute("""
        update recurrence_review as r set verdict='PENDING_CAUSE_REVIEW',cause_match=0
        where not exists (select 1 from recurrence_cause c where c.rma_item_id=r.rma_item_id
          and c.certainty_status='CONFIRMED' and c.applicability='APPLIES' and c.decided_at is not null)
    """).rowcount

checks = {
    "confirmed_manufacturing_cause_without_production_evidence":"""
        select count(*) from cause_confirmation_basis b join cause_assessment a using(cause_assessment_id)
        where a.certainty_status='CONFIRMED' and a.cause_category<>'UNDETERMINED' and b.production_record_evidence=0""",
    "recurrence_verdict_before_cause_application":"""
        select count(*) from recurrence_review r join recurrence_cause c using(rma_item_id)
        where r.verdict<>'PENDING_CAUSE_REVIEW' and (c.decided_at is null or r.reviewed_at<c.decided_at
          or c.certainty_status<>'CONFIRMED' or c.applicability<>'APPLIES')""",
    "future_customer_messages":f"select count(*) from communication where sent_at>'{asof}'",
    "service_action_before_case_decision":"""
        select count(*) from service_action s join claim_cause c using(rma_item_id)
        where c.decided_at is null or s.decided_at<c.decided_at""",
}
for name,sql in checks.items():
    value = db.execute(sql).fetchone()[0]
    assert value==0,(name,value)
    manifest["checks"][name] = {"value":value,"ok":True}
assert db.execute("pragma integrity_check").fetchone() == ("ok",)
assert not db.execute("pragma foreign_key_check").fetchone()
recurrence_n = db.execute("select count(*) from recurrence_review where verdict='RECURRENCE'").fetchone()[0]
assert recurrence_n > 0
manifest["checks"]["recurrence_reviews_flagged_RECURRENCE"] = {"value":recurrence_n,"ok":True}
manifest["v31_patch"].update(
    unsupported_manufacturing_confirmations_downgraded=downgraded,
    recurrence_review_dates_shifted=moved,
    recurrence_reviews_pending_cause=deferred,
    recurrence_verdicts=dict(db.execute("select verdict,count(*) from recurrence_review group by 1")))
for name in ("recurrence_review","communication","claim_cause","cause_assessment","qa_symptom_reproduction"):
    cur = db.execute(f'select * from "{name}" limit 100')
    with (root/"sample_csv"/f"{name}.csv").open("w",newline="",encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([col[0] for col in cur.description])
        writer.writerows(cur)
manifest["table_rows"]["communication"] = db.execute("select count(*) from communication").fetchone()[0]
reproduction = dict(db.execute("select reproduction_status,count(*) from qa_symptom_reproduction group by 1"))
assert reproduction.get("REPRODUCED", 0) and reproduction.get("NOT_REPRODUCED", 0)
assert sum(reproduction.values()) == 10610
manifest["v31_patch"]["symptom_reproduction"] = reproduction
(root/"schema.sql").write_text(
    "PRAGMA foreign_keys=ON;\n\n"+";\n\n".join(row[0] for row in db.execute(
        "select sql from sqlite_schema where sql is not null and name not like 'sqlite_%' order by type='view',rowid"))+";\n",
    encoding="utf-8")
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(manifest["v31_patch"],ensure_ascii=False,indent=2))
db.close()
