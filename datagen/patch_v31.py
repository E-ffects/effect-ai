"""Apply v31 to a generated and finalized v30 directory (Python 3.10+)."""
from collections import defaultdict
from datetime import datetime
import csv
import heapq
import json
from pathlib import Path
import random
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text())
assert manifest["version"] == "v30", "Start from finalized v30"
asof = manifest["as_of"]
db = sqlite3.connect(root / "pcb_rma.sqlite")
db.row_factory = sqlite3.Row
db.execute("pragma foreign_keys=on")

def scalar(sql, params=()):
    return db.execute(sql, params).fetchone()[0]

def iso(value):
    return value.isoformat(timespec="seconds")

def shift(value, old, new):
    return iso(datetime.fromisoformat(value) + (datetime.fromisoformat(new) - datetime.fromisoformat(old)))

with db:
    db.execute("create temp table late_test as select test_run_id,rma_item_id from test_run where ended_at>?", (asof,))
    late = scalar("select count(*) from late_test")
    db.execute("delete from test_measurement where test_run_id in (select test_run_id from late_test)")
    db.execute("delete from test_run where test_run_id in (select test_run_id from late_test)")
    db.execute("""
        update rma_item as i
        set verified_defect_qty=(
            select case when t.overall_result='FAIL' then min(i.returned_qty, 1)
                        when t.overall_result='PASS' then 0 end
            from test_run t where t.rma_item_id=i.rma_item_id and t.phase='RMA_LAB'
            and t.subject_type='RETURNED_SAMPLE' and t.origin_verified=1
            order by t.ended_at desc,t.test_run_id desc limit 1
        )
    """)
    db.execute("""
        update investigation_claim as ic
        set linked_at=max(linked_at,(select opened_at from rma_claim where rma_id=ic.rma_id),
                                   (select opened_at from investigation where investigation_id=ic.investigation_id))
    """)
    # Evidence unavailable when a hypothesis was judged cannot support that dated judgment.
    db.execute("""
        update cause_hypothesis as h set evidence_ids_json=(
            select json_group_array(j.value)
            from json_each(h.evidence_ids_json) j
            join evidence e on e.evidence_id=j.value where e.available_at<=h.decided_at
        ) where h.decided_at is not null
    """)
    db.execute("""
        update cause_hypothesis set result='UNDER_VERIFICATION',decided_at=null
        where result in ('SUPPORTED','REJECTED') and json_array_length(evidence_ids_json)=0
    """)
    # Preserve the recorded disposition while aligning application and derived service events.
    rows = db.execute("""
        select l.*,i.rma_id,r.opened_at,a.cause_code,a.certainty_status,a.assessed_at,
               a.approved_at,t.origin_verified,t.overall_result
        from claim_cause l join rma_item i using(rma_item_id) join rma_claim r using(rma_id)
        join cause_assessment a using(cause_assessment_id)
        left join test_run t on t.rma_item_id=i.rma_item_id and t.phase='RMA_LAB' and t.subject_type='RETURNED_SAMPLE'
    """).fetchall()
    shifted = pending = unverified_ntf = 0
    for row in rows:
        old = row["decided_at"]
        defect_free = row["cause_code"] == "NO_DEFECT_FOUND"
        applies = row["applicability"] == "APPLIES"
        matches = row["origin_verified"] == 1 and row["overall_result"] == "PASS"
        invalid_ntf = defect_free and applies and not matches
        earliest = max(filter(None, (old, row["opened_at"], row["assessed_at"],
                     row["approved_at"] if row["certainty_status"] == "CONFIRMED" else None)))
        new = earliest if old and earliest <= asof and not invalid_ntf else None
        if invalid_ntf or (old is None and row["applicability"] in ("APPLIES", "APPLIES_PHOTO_ONLY")):
            unverified_ntf += int(invalid_ntf)
            db.execute("""update claim_cause set applicability='PENDING',decided_at=null,
                       rationale=? where claim_cause_id=?""",
                       ("접수 시료의 출처가 확인된 규격 내 시험이 없어 개별 판정 보류" if invalid_ntf
                        else "기준 시점까지 적용 판정이 기록되지 않아 보류", row["claim_cause_id"]))
        elif old and new != old:
            shifted += 1
            db.execute("update claim_cause set decided_at=? where claim_cause_id=?", (new,row["claim_cause_id"]))
        if new is None and (old or invalid_ntf):
            pending += 1
            db.execute("delete from service_action where rma_item_id=?", (row["rma_item_id"],))
            db.execute("""delete from communication where rma_id=? and
                       (message_text like '분석 결과 불량이 재현되지 않았습니다.%' or message_text like '분석 결과 원인이 확인되었습니다.%')""",
                       (row["rma_id"],))
            db.execute("update rma_claim set qa_status='ANALYZING',cs_status='OPEN' where rma_id=?", (row["rma_id"],))
        if new and old and new != old:
            for action in db.execute("select * from service_action where rma_item_id=?", (row["rma_item_id"],)).fetchall():
                executed = shift(action["executed_at"],old,new) if action["executed_at"] else None
                executed = executed if executed and executed <= asof else None
                db.execute("update service_action set decided_at=?,executed_at=?,result=? where action_id=?",
                           (new,executed,"EXECUTED" if executed else "SCHEDULED",action["action_id"]))
            for message in db.execute("""select * from communication where rma_id=? and
                      (message_text like '분석 결과 불량이 재현되지 않았습니다.%' or message_text like '분석 결과 원인이 확인되었습니다.%')""",
                      (row["rma_id"],)).fetchall():
                sent = shift(message["sent_at"],old,new)
                if sent <= asof:
                    db.execute("update communication set sent_at=? where message_id=?", (sent,message["message_id"]))
                else:
                    db.execute("delete from communication where message_id=?", (message["message_id"],))
    db.execute("""
        update communication as m
        set message_text='현재 확보한 자료로 원인 후보를 검토했습니다. 최종 확정 전이며 처리 방안과 추가 확인 사항을 안내드립니다.'
        where message_text like '분석 결과 원인이 확인되었습니다.%' and exists (
          select 1 from rma_item i join claim_cause l using(rma_item_id)
          join cause_assessment a using(cause_assessment_id)
          where i.rma_id=m.rma_id and (a.certainty_status<>'CONFIRMED' or l.applicability='APPLIES_PHOTO_ONLY'))
    """)
    db.execute("""
        update rma_claim as c set cs_status=case
          when exists (select 1 from rma_item i join service_action s using(rma_item_id)
                       where i.rma_id=c.rma_id and s.executed_at is not null) then 'CLOSED'
          when exists (select 1 from communication m where m.rma_id=c.rma_id
                       and (m.message_text like '분석 결과%' or m.message_text like '현재 확보한 자료로%')) then 'RESPONDED'
          else 'OPEN' end
        where exists (select 1 from rma_item i join service_action s using(rma_item_id) where i.rma_id=c.rma_id)
    """)
    db.execute("""
        update rma_claim as c set qa_status='CAUSE_DECIDED'
        where qa_status='CLOSED' and exists (
          select 1 from rma_item i join claim_cause l using(rma_item_id)
          where i.rma_id=c.rma_id and julianday(l.decided_at)+5>julianday(?))
    """, (asof,))

# These measurements directly test the reported manifestation under the stored conditions.
# Broad visual codes and grouped open/short fixture results require a human review.
measurement_by_symptom = {
    "ELEC03": "LAB_IST_DR", "ELEC04": "LAB_TDR_IMP", "ELEC05": "LAB_SIR",
    "SOLD01": "LAB_SOLDERABILITY", "STRC01": "LAB_REFLOW_REPRO",
    "STRC02": "LAB_XSEC_DELAM", "STRC03": "LAB_WARPAGE", "STRC04": "LAB_XSEC_CRACK",
    "APPR01": "LAB_SM_TAPE", "DIM01": "LAB_DIM", "MARK01": "LAB_LABEL_MATCH",
}
values = ",".join(f"('{symptom}','{item}')" for symptom,item in measurement_by_symptom.items())
with db:
    db.execute(f"""
        create view qa_symptom_reproduction as
        with mapping(minor_code,item_code) as (values {values}),
        sample_test as (
          select * from test_run where phase='RMA_LAB' and subject_type='RETURNED_SAMPLE'
        ), matching as (
          select t.test_run_id,m.result
          from sample_test t join symptom_assessment s using(rma_item_id)
          join mapping p on p.minor_code=s.primary_label
          join test_measurement m using(test_run_id) join test_spec ts using(test_spec_id)
          where ts.test_spec_id=p.item_code and
            (s.primary_label<>'STRC01' or json_extract(m.condition_json,'$.condition')='as_received')
        ), summary as (
          select test_run_id, sum(result='FAIL') failures, sum(result='PASS') passes,
            sum(result not in ('PASS','FAIL')) incomplete from matching group by test_run_id
        )
        select i.rma_id,i.rma_item_id,s.primary_label,t.test_run_id,t.origin_verified,
          t.overall_result,coalesce(a.failures,0) as matching_failures,coalesce(a.passes,0) as matching_passes,
          case when t.test_run_id is null then 'NOT_TESTED'
               when t.origin_verified<>1 then 'UNVERIFIED_SAMPLE'
               when a.failures>0 then 'REPRODUCED'
               when a.passes>0 and a.incomplete=0 then 'NOT_REPRODUCED'
               else 'REVIEW_REQUIRED' end as reproduction_status,
          cc.applicability,ca.cause_code,ca.certainty_status
        from rma_item i join symptom_assessment s using(rma_item_id)
        left join sample_test t using(rma_item_id) left join summary a on a.test_run_id=t.test_run_id
        left join claim_cause cc using(rma_item_id) left join cause_assessment ca using(cause_assessment_id)
    """)

# Label-free text retrieval. The query and references use opening-time data only.
def bigrams(raw):
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch == " ")
    return {cleaned[i:i+2] for i in range(len(cleaned)-1) if " " not in cleaned[i:i+2]}

claims = db.execute("select rma_id,raw_text,opened_at from rma_claim order by opened_at,rma_id").fetchall()
tokens = [bigrams(row["raw_text"]) for row in claims]
triage = dict(db.execute("select rma_id,decided_at from qa_triage"))
matches = []
for idx,row in enumerate(claims):
    if row["opened_at"] < "2025-10-01":
        continue
    needle = tokens[idx]
    def scores():
        for earlier in range(idx):
            if claims[earlier]["opened_at"] >= row["opened_at"]:
                break
            hit = len(needle & tokens[earlier])
            if hit:
                yield (hit / (len(needle)+len(tokens[earlier])-hit), -earlier)
    for score,neg in heapq.nlargest(5, scores()):
        matches.append((f"SIM-{len(matches)+1:07}",row["rma_id"],claims[-neg]["rma_id"],triage[row["rma_id"]],
                        round(score,6),"raw_text_char_bigram_jaccard","SYNTHETIC_BASELINE_REQUIRES_REVIEW","pcb-v31-baseline-1"))
with db:
    db.execute("delete from similar_cases")
    db.executemany("insert into similar_cases values (?,?,?,?,?,?,?,?)",matches)

# Keep the chronological files as a temporal track and add a disjoint investigation track.
evaluation = root / "evaluation"
truth = {r["rma_id"]:r for r in map(json.loads,(root/"generation_truth"/"claims.jsonl").read_text().splitlines())}
parents = {r["investigation_id"]:r["reopened_from"] for r in db.execute("select investigation_id,reopened_from from investigation")}
def family(inv):
    while parents.get(inv):
        inv = parents[inv]
    return inv
groups = defaultdict(list)
for split in ("train","validation","test"):
    def load(kind):
        return {r["rma_id"]:r for r in map(json.loads,(evaluation/f"{split}_{kind}.jsonl").read_text().splitlines())}
    labels = load("reference_labels")
    for rma_id,row in load("inputs").items():
        groups[family(truth[rma_id]["investigation_id"])].append((row,labels[rma_id]))
grouped = evaluation / "investigation_holdout"
grouped.mkdir(exist_ok=True)
names = ("train","validation","test")
target = dict(zip(names,(7000,1500,1500)))
assigned = {name:[] for name in names}
rng = random.Random(31)
ordered = list(groups)
rng.shuffle(ordered)
ordered.sort(key=lambda group:-len(groups[group]))
split_of = {}
for group in ordered:
    split = max(names,key=lambda name:target[name]-len(assigned[name]))
    assigned[split].extend(groups[group])
    split_of[group] = split
for split,rows in assigned.items():
    rows.sort(key=lambda pair:(pair[0]["opened_at"],pair[0]["rma_id"]))
    for index,kind in enumerate(("inputs","reference_labels")):
        with (grouped/f"{split}_{kind}.jsonl").open("w",encoding="utf-8") as f:
            for pair in rows:
                f.write(json.dumps(pair[index],ensure_ascii=False)+"\n")
(grouped/"split_manifest.json").write_text(json.dumps({
    "split_by_investigation_family":split_of,"claims_by_split":{s:len(v) for s,v in assigned.items()},
    "label_kind":"synthetic_generation_reference","seed":31
},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

checks = {
    "foreign_key_violations":"select count(*) from pragma_foreign_key_check",
    "claim_application_before_open_or_assessment_or_approval":"""
        select count(*) from claim_cause cc join rma_item i using(rma_item_id)
        join rma_claim r using(rma_id) join cause_assessment ca using(cause_assessment_id)
        where cc.decided_at<r.opened_at or cc.decided_at<ca.assessed_at or
             (ca.certainty_status='CONFIRMED' and cc.decided_at<ca.approved_at)""",
    "test_results_after_cutoff":f"select count(*) from test_run where ended_at>'{asof}'",
    "unsupported_verified_defect_quantity":"""
        select count(*) from rma_item i where verified_defect_qty is not null and
        (verified_defect_qty>1 or not exists (select 1 from test_run t where t.rma_item_id=i.rma_item_id
          and phase='RMA_LAB' and subject_type='RETURNED_SAMPLE' and origin_verified=1
          and overall_result=case i.verified_defect_qty when 0 then 'PASS' else 'FAIL' end))""",
    "hypothesis_future_evidence":"""
        select count(*) from cause_hypothesis h,json_each(h.evidence_ids_json) j
        join evidence e on e.evidence_id=j.value where e.available_at>h.decided_at""",
    "investigation_claim_premature_link":"""
        select count(*) from investigation_claim l join investigation i using(investigation_id)
        join rma_claim r using(rma_id) where l.linked_at<max(i.opened_at,r.opened_at)""",
    "search_non_historical_or_oracle_reference":"""
        select count(*) from similar_cases s join rma_claim q on q.rma_id=s.query_rma_id
        join rma_claim r on r.rma_id=s.reference_rma_id
        where r.opened_at>=q.opened_at or s.similarity_basis<>'raw_text_char_bigram_jaccard'""",
    "unverified_returned_sample_reproduction":"""
        select count(*) from qa_symptom_reproduction where reproduction_status in ('REPRODUCED','NOT_REPRODUCED')
        and (origin_verified<>1 or test_run_id is null)""",
    "no_defect_applied_without_verified_pass":"""
        select count(*) from qa_symptom_reproduction where cause_code='NO_DEFECT_FOUND' and applicability='APPLIES'
        and (coalesce(origin_verified,0)<>1 or coalesce(overall_result,'')<>'PASS')""",
    "full_8d_missing_any_step":"""
        select count(*) from investigation i where i.analysis_level='L3' and not exists (
          select 1 from qa_report r join eight_d_step s using(report_id)
          where r.investigation_id=i.investigation_id and r.report_type='8D_FULL'
          group by r.report_id having count(distinct s.d_step)=8)""",
}
for name,sql in checks.items():
    result = scalar(sql)
    assert result==0,(name,result)
    manifest["checks"][name] = {"value":result,"ok":True}
assert scalar("pragma integrity_check") == "ok"
assert scalar("select count(*) from rma_claim") == sum(map(len,assigned.values())) == 10610
assert scalar("select count(*) from qa_symptom_reproduction") == 10610
group_sets = [{family(truth[row["rma_id"]]["investigation_id"]) for row,_ in assigned[s]} for s in names]
assert all(not a & b for index,a in enumerate(group_sets) for b in group_sets[index+1:])
manifest["checks"]["investigation_holdout_overlap"] = {"value":0,"ok":True}
manifest["version"] = "v31"
manifest["v31_patch"] = {"late_tests_removed":late,"application_dates_shifted_or_deferred":shifted,
                         "service_disposition_deferred":pending,"no_defect_application_deferred":unverified_ntf,
                         "holdout_claims_by_split":{s:len(v) for s,v in assigned.items()},
                         "symptom_reproduction":dict(db.execute("select reproduction_status,count(*) from qa_symptom_reproduction group by 1"))}
manifest["table_rows"] = {t:scalar(f'select count(*) from "{t}"') for t in manifest["table_rows"]}
manifest["views"].append("qa_symptom_reproduction")
(root/"schema.sql").write_text(
    "PRAGMA foreign_keys=ON;\n\n"+";\n\n".join(row[0] for row in db.execute(
        "select sql from sqlite_schema where sql is not null and name not like 'sqlite_%' order by type='view',rowid"))+";\n",
    encoding="utf-8")
for name in (*manifest["table_rows"], "qa_symptom_reproduction"):
    cur = db.execute(f'select * from "{name}" limit 100')
    with (root/"sample_csv"/f"{name}.csv").open("w",newline="",encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([col[0] for col in cur.description])
        writer.writerows(cur)
manifest_file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
db.close()
print(json.dumps(manifest["v31_patch"],ensure_ascii=False,indent=2))
