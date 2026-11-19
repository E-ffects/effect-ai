"""Apply v3 fixes on top of v31 (Python 3.10+).

Run order for a fresh rebuild:
  generate_v30.py -> finalize_v30.py -> patch_v31.py -> reconcile_v31.py -> patch_v3.py

v3 part 1 (structure):
  1. input_qty no larger than boards delivered to that customer from that lot
  2. lot_impact_assessment.claim_count_at_decision (claims known at decision time)
  3. sporadic claims linked to cause-named investigations only after the cause is applied
  4. claim cause applied only after its assessment is approved
  5. 8D D4 completed no earlier than cause approval, shipments after lot completion
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
import re
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert manifest["version"] == "v31", "Start from reconciled v31"
asof = manifest["as_of"]
db = sqlite3.connect(root / "pcb_rma.sqlite")
db.row_factory = sqlite3.Row
db.execute("pragma foreign_keys=on")
rng = random.Random(32)
report = {}


def scalar(sql, params=()):
    return db.execute(sql, params).fetchone()[0]


def iso(value):
    return value.isoformat(timespec="seconds")


def shift(value, old, new):
    return iso(datetime.fromisoformat(value) + (datetime.fromisoformat(new) - datetime.fromisoformat(old)))


RESULT_MESSAGES = ("분석 결과 불량이 재현되지 않았습니다.%", "분석 결과 원인이 확인되었습니다.%",
                   "현재 확보한 자료로%", "반송품 시험 결과 원인 후보를 확인했습니다.%")


def result_messages(rma_id):
    where = " or ".join("message_text like ?" for _ in RESULT_MESSAGES)
    return db.execute(f"select * from communication where rma_id=? and ({where})", (rma_id, *RESULT_MESSAGES)).fetchall()


def move_application(row, new):
    """Move one claim_cause decision and the customer-facing events that follow it."""
    old = row["decided_at"]
    db.execute("update claim_cause set decided_at=? where claim_cause_id=?", (new, row["claim_cause_id"]))
    for action in db.execute("select * from service_action where rma_item_id=?", (row["rma_item_id"],)).fetchall():
        executed = shift(action["executed_at"], old, new) if action["executed_at"] else None
        executed = executed if executed and executed <= asof else None
        db.execute("update service_action set decided_at=?,executed_at=?,result=? where action_id=?",
                   (new, executed, "EXECUTED" if executed else "SCHEDULED", action["action_id"]))
    for message in result_messages(row["rma_id"]):
        sent = shift(message["sent_at"], old, new)
        if sent <= asof:
            db.execute("update communication set sent_at=? where message_id=?", (sent, message["message_id"]))
        else:
            db.execute("delete from communication where message_id=?", (message["message_id"],))


def defer_application(row, reason):
    """Cause not approved by the snapshot: the claim waits, with no disposition or result message."""
    db.execute("update claim_cause set applicability='PENDING',decided_at=null,rationale=? where claim_cause_id=?",
               (reason, row["claim_cause_id"]))
    db.execute("delete from service_action where rma_item_id=?", (row["rma_item_id"],))
    for message in result_messages(row["rma_id"]):
        db.execute("delete from communication where message_id=?", (message["message_id"],))
    db.execute("update rma_claim set qa_status='ANALYZING',cs_status='OPEN' where rma_id=?", (row["rma_id"],))


with db:
    # 1. Reported input quantity cannot exceed what the customer received from that lot before the failure.
    rows = db.execute("""
        select i.rma_item_id,i.rma_id,i.input_qty,i.claimed_defect_qty,r.raw_text,
          (select sum(sl.board_qty) from shipment_lot sl join shipment s using(shipment_id)
           where sl.lot_id=i.lot_id and s.customer_id=r.customer_id and s.delivered_at<=r.occurred_at) delivered
        from rma_item i join rma_claim r using(rma_id) order by i.rma_item_id""").fetchall()
    capped = 0
    for row in rows:
        delivered = row["delivered"]
        assert delivered and delivered >= row["claimed_defect_qty"], row["rma_item_id"]
        if row["input_qty"] <= delivered:
            continue
        floor = -(-row["claimed_defect_qty"] // 50) * 50
        new = max(floor, int(delivered * rng.uniform(0.5, 1.0)) // 50 * 50)
        new = min(new, delivered)
        text, n = re.subn(r"투입 [\d,]+pcs", f"투입 {new:,}pcs", row["raw_text"])
        assert n == 1
        db.execute("update rma_item set input_qty=? where rma_item_id=?", (new, row["rma_item_id"]))
        db.execute("update rma_claim set raw_text=? where rma_id=?", (text, row["rma_id"]))
        capped += 1
    report["input_qty_capped_to_delivered"] = capped

    # 4. Approval before application. Unapproved E-Test retest causes get an approval one day after
    #    assessment when that falls inside the snapshot.
    db.execute("""
        update cause_assessment set approved_at=strftime('%Y-%m-%dT%H:%M:%S+09:00',
               datetime(substr(assessed_at,1,19),'+1 day'))
        where approved_at is null and datetime(substr(assessed_at,1,19),'+1 day')<=datetime(substr(?,1,19))""",
               (asof,))
    rows = db.execute("""
        select cc.*,i.rma_id,a.approved_at from claim_cause cc join rma_item i using(rma_item_id)
        join cause_assessment a using(cause_assessment_id)
        where cc.decided_at is not null and (a.approved_at is null or cc.decided_at<a.approved_at)""").fetchall()
    moved = deferred = 0
    for row in rows:
        if row["approved_at"] and row["approved_at"] <= asof:
            move_application(row, row["approved_at"])
            moved += 1
        else:
            defer_application(row, "기준 시점까지 원인 판정 승인이 없어 개별 적용 보류")
            deferred += 1
    report["claim_cause_moved_to_approval"] = moved
    report["claim_cause_deferred_unapproved"] = deferred

    # 3. Sporadic claims: the cause-named quarterly investigation is known only once the cause is applied.
    db.execute("""
        delete from investigation_claim where membership_reason='same_cause_code_quarter_group' and rma_id in (
          select i.rma_id from rma_item i join claim_cause cc using(rma_item_id) where cc.decided_at is null)""")
    report["sporadic_links_removed_pending"] = db.execute("select changes()").fetchone()[0]
    db.execute("""
        update investigation_claim as l set linked_at=(
          select max(cc.decided_at,l.linked_at) from rma_item i join claim_cause cc using(rma_item_id) where i.rma_id=l.rma_id),
          membership_reason='same_cause_code_quarter_group_after_cause_applied'
        where membership_reason='same_cause_code_quarter_group'""")
    report["sporadic_links_moved_to_cause_application"] = db.execute("select changes()").fetchone()[0]

    # 2. Claim counts follow the remaining links. claim_count = known by the snapshot,
    #    claim_count_at_decision = claims already received when QA decided the lot.
    db.execute("alter table lot_impact_assessment add column claim_count_at_decision integer")
    db.execute("""
        update lot_impact_assessment as a set
          claim_count=(select count(*) from investigation_claim l join rma_item i using(rma_id)
            where l.investigation_id=a.investigation_id and i.lot_id=a.lot_id),
          claim_count_at_decision=(select count(*) from investigation_claim l join rma_item i using(rma_id)
            join rma_claim r using(rma_id)
            where l.investigation_id=a.investigation_id and i.lot_id=a.lot_id and r.opened_at<=a.decided_at)""")
    # A per-claim lot review belongs to the investigation only once that claim is linked to it.
    db.execute("delete from lot_impact_assessment where same_lot_as_claim=1 and claim_count=0")
    report["impact_rows_removed_unlinked_claim"] = db.execute("select changes()").fetchone()[0]
    report["impact_rows_with_later_claims"] = scalar(
        "select count(*) from lot_impact_assessment where claim_count>claim_count_at_decision")
    db.execute("drop view impacted_lots_without_claims")
    db.execute("""
        create view impacted_lots_without_claims as
        select li.investigation_id, li.lot_id, l.spec_id, li.same_material_json, li.same_equipment_json,
          li.same_condition_json, li.retained_sample_result, li.rationale
        from lot_impact_assessment li join production_lot l using(lot_id)
        where li.decision='IMPACTED' and li.claim_count_at_decision=0""")

    # 5a. D4 (root cause) completes no earlier than approval of that investigation's cause.
    db.execute("""
        update eight_d_step as s set completed_at=(
          select max(a.approved_at) from qa_report r join cause_assessment a using(investigation_id)
          where r.report_id=s.report_id)
        where d_step='D4' and status='COMPLETED' and completed_at<(
          select max(a.approved_at) from qa_report r join cause_assessment a using(investigation_id)
          where r.report_id=s.report_id)""")
    report["d4_completion_moved_to_approval"] = db.execute("select changes()").fetchone()[0]

    # 5b. Shipments leave after every lot they carry is completed.
    db.execute("""
        update shipment as s set shipped_at=(
          select strftime('%Y-%m-%dT%H:%M:%S+09:00', datetime(substr(max(l.completed_at),1,19),'+1 hour'))
          from shipment_lot sl join production_lot l using(lot_id) where sl.shipment_id=s.shipment_id)
        where shipped_at<(select max(l.completed_at) from shipment_lot sl join production_lot l using(lot_id)
                          where sl.shipment_id=s.shipment_id)""")
    report["shipments_moved_after_lot_completion"] = db.execute("select changes()").fetchone()[0]

    # Customer-facing status follows the moved or deferred dispositions (same rule as v31).
    db.execute("""
        update rma_claim as c set cs_status=case
          when exists (select 1 from rma_item i join service_action s using(rma_item_id)
                       where i.rma_id=c.rma_id and s.executed_at is not null) then 'CLOSED'
          when exists (select 1 from communication m where m.rma_id=c.rma_id
                       and (m.message_text like '분석 결과%' or m.message_text like '현재 확보한 자료로%'
                            or m.message_text like '반송품 시험 결과 원인 후보%')) then 'RESPONDED'
          else 'OPEN' end
        where exists (select 1 from rma_item i join service_action s using(rma_item_id) where i.rma_id=c.rma_id)""")
    # Recurrence verdicts must not predate the (possibly moved) cause application.
    db.execute("""
        update recurrence_review as r set reviewed_at=(
          select cc.decided_at from claim_cause cc where cc.rma_item_id=r.rma_item_id)
        where verdict<>'PENDING_CAUSE_REVIEW' and exists (
          select 1 from claim_cause cc where cc.rma_item_id=r.rma_item_id and cc.decided_at>r.reviewed_at)""")
    report["recurrence_reviews_moved"] = db.execute("select changes()").fetchone()[0]
    db.execute("""
        update recurrence_review as r set verdict='PENDING_CAUSE_REVIEW',cause_match=0
        where verdict<>'PENDING_CAUSE_REVIEW' and exists (
          select 1 from claim_cause cc where cc.rma_item_id=r.rma_item_id and cc.decided_at is null)""")
    report["recurrence_reviews_pending"] = db.execute("select changes()").fetchone()[0]

checks = {
    "input_qty_above_delivered": """
        select count(*) from rma_item i join rma_claim r using(rma_id) where i.input_qty>(
          select sum(sl.board_qty) from shipment_lot sl join shipment s using(shipment_id)
          where sl.lot_id=i.lot_id and s.customer_id=r.customer_id and s.delivered_at<=r.occurred_at)""",
    "input_qty_text_mismatch": """
        select count(*) from rma_claim r join rma_item i using(rma_id)
        where instr(r.raw_text, '투입 '||printf('%,d',i.input_qty)||'pcs')=0""",
    "claimed_defect_above_input": "select count(*) from rma_item where claimed_defect_qty>input_qty",
    "claim_count_at_decision_missing": "select count(*) from lot_impact_assessment where claim_count_at_decision is null",
    "claim_application_before_approval": """
        select count(*) from claim_cause cc join cause_assessment a using(cause_assessment_id)
        where cc.decided_at is not null and (a.approved_at is null or cc.decided_at<a.approved_at)""",
    "sporadic_link_before_cause_application": """
        select count(*) from investigation_claim l join rma_item i using(rma_id) join claim_cause cc using(rma_item_id)
        where l.membership_reason like 'same_cause_code_quarter_group%' and (cc.decided_at is null or l.linked_at<cc.decided_at)""",
    "d4_completed_before_cause_approval": """
        select count(*) from eight_d_step s join qa_report r using(report_id) join cause_assessment a using(investigation_id)
        where s.d_step='D4' and s.status='COMPLETED' and s.completed_at<a.approved_at""",
    "shipment_before_lot_completion": """
        select count(*) from shipment_lot sl join shipment s using(shipment_id) join production_lot l using(lot_id)
        where s.shipped_at<l.completed_at""",
    "delivery_not_after_shipment": "select count(*) from shipment where delivered_at<=shipped_at",
    "claim_occurred_before_delivery": """
        select count(*) from rma_claim r join rma_item i using(rma_id) join shipment_lot sl using(shipment_lot_id)
        join shipment s using(shipment_id) where r.occurred_at<s.delivered_at""",
    "service_action_before_case_decision": """
        select count(*) from service_action s join claim_cause c using(rma_item_id)
        where c.decided_at is null or s.decided_at<c.decided_at""",
    "future_customer_messages": f"select count(*) from communication where sent_at>'{asof}'",
    "recurrence_verdict_before_cause_application": """
        select count(*) from recurrence_review r join claim_cause c using(rma_item_id)
        where r.verdict<>'PENDING_CAUSE_REVIEW' and (c.decided_at is null or r.reviewed_at<c.decided_at)""",
}
for name, sql in checks.items():
    value = scalar(sql)
    assert value == 0, (name, value)
    manifest["checks"][name] = {"value": value, "ok": True}
assert scalar("pragma integrity_check") == "ok"
assert not db.execute("pragma foreign_key_check").fetchone()
manifest["checks"]["impacted_lots_without_any_claim"] = {
    "value": scalar("select count(*) from lot_impact_assessment where decision='IMPACTED' and claim_count=0"), "ok": True}
manifest["checks"]["impacted_lots_without_claim_at_decision"] = {
    "value": scalar("select count(*) from impacted_lots_without_claims"), "ok": True}
manifest["v3_patch"] = report
db.commit()
db.close()
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
