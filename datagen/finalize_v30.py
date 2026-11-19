# -*- coding: utf-8 -*-
"""v30 finalize: views, schema.sql, sample_csv, manifest, README, change log, verification."""
import csv, json, shutil, sqlite3, sys
from pathlib import Path

OUT = Path(sys.argv[1])
HERE = Path(__file__).resolve().parent
db = sqlite3.connect(OUT / "pcb_rma.sqlite")
db.execute("pragma foreign_keys=on")
q = lambda s, *a: db.execute(s, a).fetchall()

VIEWS = {
"monthly_claims": """
SELECT substr(c.opened_at,1,7) AS month, d.major_code, d.minor_code, d.minor_name, count(*) AS claims
FROM rma_claim c JOIN rma_item i USING(rma_id) JOIN symptom_assessment s USING(rma_item_id)
JOIN symptom_definition d ON d.minor_code=s.primary_label GROUP BY 1,2,3,4""",
"qa_case_overview": """
SELECT c.rma_id, c.opened_at, c.customer_id, cu.industry, c.reported_part_no, i.lot_id, i.panel_id, c.detection_stage,
 s.primary_label, t.severity, t.characteristic_symbol, t.analysis_level, ic.investigation_id,
 ca.cause_code, ca.cause_category, ca.certainty_status, cc.applicability, c.qa_status, c.cs_status
FROM rma_claim c JOIN customer cu USING(customer_id) JOIN rma_item i USING(rma_id)
JOIN symptom_assessment s USING(rma_item_id) JOIN qa_triage t USING(rma_id)
LEFT JOIN investigation_claim ic USING(rma_id) LEFT JOIN claim_cause cc ON cc.rma_item_id=i.rma_item_id
LEFT JOIN cause_assessment ca ON ca.cause_assessment_id=cc.cause_assessment_id""",
"lot_impact_overview": """
SELECT investigation_id, decision, count(*) AS lots, sum(same_lot_as_claim) AS lots_with_claims,
 sum(CASE WHEN same_lot_as_claim=0 THEN 1 ELSE 0 END) AS lots_without_claims, sum(claim_count) AS claims
FROM lot_impact_assessment GROUP BY 1,2""",
"impacted_lots_without_claims": """
SELECT li.investigation_id, li.lot_id, l.spec_id, li.same_material_json, li.same_equipment_json, li.same_condition_json,
 li.retained_sample_result, li.rationale
FROM lot_impact_assessment li JOIN production_lot l USING(lot_id)
WHERE li.decision='IMPACTED' AND li.claim_count=0""",
"investigation_activity_duration": """
SELECT a.investigation_id, i.analysis_level, a.activity_type, a.owner_dept, a.started_at, a.ended_at,
 round(julianday(coalesce(a.ended_at,'2025-12-31T23:59:00+09:00'))-julianday(a.started_at),2) AS days,
 json_array_length(a.data_sources_json) AS data_source_count, a.ended_at IS NULL AS open_at_year_end
FROM analysis_activity a JOIN investigation i USING(investigation_id)""",
"bottleneck_by_activity": """
SELECT activity_type, count(*) AS n, round(avg(days),1) AS avg_days, round(max(days),1) AS max_days,
 round(avg(data_source_count),1) AS avg_data_sources
FROM investigation_activity_duration WHERE open_at_year_end=0 GROUP BY 1 ORDER BY avg_days DESC""",
"eight_d_progress": """
SELECT r.report_id, r.investigation_id, r.report_type, r.status,
 sum(s.status='COMPLETED') AS steps_completed, count(s.step_id) AS steps_total,
 (SELECT d_step FROM eight_d_step x WHERE x.report_id=r.report_id AND x.status<>'COMPLETED' ORDER BY d_step LIMIT 1) AS current_step
FROM qa_report r LEFT JOIN eight_d_step s USING(report_id) GROUP BY 1,2,3,4""",
"spc_out_of_spec_by_equipment_month": """
SELECT r.equipment_id, r.process_id, o.item_code, substr(o.measured_at,1,7) AS month, count(*) AS observations,
 sum(o.spc_flag='OUT_OF_SPEC') AS out_of_spec
FROM process_observation o JOIN process_run r USING(process_run_id) GROUP BY 1,2,3,4""",
"special_characteristic_results": """
SELECT sc.characteristic_id, sc.characteristic_name, sc.symbol, tr.phase, substr(tr.started_at,1,7) AS month,
 count(*) AS measurements, sum(m.result='FAIL') AS fails, sum(m.result='NOT_PERFORMED') AS not_performed
FROM test_measurement m JOIN test_spec ts USING(test_spec_id) JOIN special_characteristic sc ON sc.characteristic_id=ts.characteristic_id
JOIN test_run tr USING(test_run_id) WHERE tr.phase IN ('FQC','OQC','IQC') GROUP BY 1,2,3,4,5""",
"cause_confirmation_basis": """
SELECT c.cause_assessment_id, c.investigation_id, c.cause_code, c.cause_category, c.certainty_status,
 sum(CASE WHEN e.source_type='test_run' AND e.origin_verified=1 THEN 1 ELSE 0 END) AS verified_lab_evidence,
 sum(CASE WHEN e.origin_verified=0 THEN 1 ELSE 0 END) AS unverified_evidence,
 sum(CASE WHEN e.source_type IN ('process_run_summary','equipment_event','material_lot','inspection_plan') THEN 1 ELSE 0 END) AS production_record_evidence
FROM cause_assessment c LEFT JOIN json_each(c.support_evidence_ids_json) j LEFT JOIN evidence e ON e.evidence_id=j.value
GROUP BY 1,2,3,4,5""",
"corrective_action_status": """
SELECT ca.corrective_action_id, ca.investigation_id, ca.action_role, ca.action_category, ca.owner_dept, ca.description,
 ca.selected_at, ca.implemented_at, ca.verification_status,
 (SELECT group_concat(doc_type||':'||doc_no||' r'||rev_to) FROM document_change d WHERE d.corrective_action_id=ca.corrective_action_id) AS documents,
 (SELECT group_concat(result) FROM effectiveness_check e WHERE e.corrective_action_id=ca.corrective_action_id) AS effectiveness
FROM corrective_action ca""",
"escape_point_summary": """
SELECT e.escape_code, m.escape_name, m.detection_process_id, count(DISTINCT e.investigation_id) AS investigations,
 (SELECT count(*) FROM investigation_claim ic WHERE ic.investigation_id IN (SELECT investigation_id FROM escape_assessment x WHERE x.escape_code=e.escape_code)) AS claims
FROM escape_assessment e JOIN escape_master m USING(escape_code) GROUP BY 1,2,3""",
"customer_open_dispositions": """
SELECT a.investigation_id, a.customer_id, a.proposal, a.affected_board_qty, a.submitted_at, a.status
FROM customer_disposition_approval a WHERE a.status<>'APPROVED'""",
}
for name, sql in VIEWS.items():
    db.execute(f"DROP VIEW IF EXISTS {name}")
    db.execute(f"CREATE VIEW {name} AS {sql.strip()}")
idx = [("process_run", "lot_id"), ("process_run", "equipment_id"), ("process_observation", "process_run_id"),
       ("test_run", "lot_id"), ("test_run", "rma_item_id"), ("test_measurement", "test_run_id"), ("panel", "lot_id"),
       ("rma_item", "lot_id"), ("claim_cause", "rma_item_id"), ("lot_impact_assessment", "investigation_id"),
       ("investigation_claim", "investigation_id"), ("evidence", "investigation_id"), ("shipment_lot", "lot_id"),
       ("material_usage", "lot_id"), ("material_usage", "material_lot_id")]
for t, c in idx:
    db.execute(f"CREATE INDEX IF NOT EXISTS ix_{t}_{c} ON {t}({c})")
db.commit()

# ------------------------------------------------------------- verification
checks = {}
def chk(name, sql, expect):
    v = q(sql)
    ok = v == expect if not callable(expect) else expect(v)
    checks[name] = {"value": v[0][0] if v and len(v[0]) == 1 else v, "ok": bool(ok)}

chk("integrity_check", "pragma integrity_check", [("ok",)])
chk("foreign_key_violations", "select count(*) from pragma_foreign_key_check", [(0,)])
chk("claims_total", "select count(*) from rma_claim", [(10610,)])
chk("non_manufacturing_cause_codes(logistics/customer/host)", """select count(*) from cause_master where cause_category not in ('PROCESS','MATERIAL','INSPECTION','UNDETERMINED')""", [(0,)])
chk("claims_linked_to_logistics_or_customer_fault", """select count(*) from claim_cause cc join cause_assessment c using(cause_assessment_id)
 where c.cause_category not in ('PROCESS','MATERIAL','INSPECTION','UNDETERMINED')""", [(0,)])
chk("claim_occurred_before_delivery", """select count(*) from rma_claim c join rma_item i using(rma_id) join shipment_lot sl using(shipment_lot_id)
 join shipment s using(shipment_id) where c.occurred_at < s.delivered_at""", [(0,)])
chk("returned_panel_outside_shipped_range", """select count(*) from rma_item i join shipment_lot sl using(shipment_lot_id) join panel p on p.panel_id=i.panel_id
 where p.panel_seq not between sl.panel_seq_from and sl.panel_seq_to or p.lot_id<>sl.lot_id""", [(0,)])
chk("impacted_lot_shipped_after_impact_decision", """select count(*) from lot_impact_assessment li join shipment_lot sl using(lot_id)
 join shipment s using(shipment_id) where li.decision='IMPACTED' and s.shipped_at > li.decided_at""", [(0,)])
chk("confirmed_cause_without_verified_lab_evidence", """select count(*) from cause_confirmation_basis where certainty_status='CONFIRMED' and verified_lab_evidence=0""", [(0,)])
chk("confirmed_scenario_cause_without_production_record", """select count(*) from cause_confirmation_basis b join investigation i using(investigation_id)
 where b.certainty_status='CONFIRMED' and i.scope_definition not like '%_GROUP%' and b.production_record_evidence=0""", [(0,)])
chk("impacted_lots_without_any_claim", "select count(*) from impacted_lots_without_claims", lambda v: v[0][0] > 0)
chk("return_authorization_decided_by_non_QA", "select count(*) from return_authorization where decided_by<>'QA'", [(0,)])
chk("recurrence_reviews_flagged_RECURRENCE", "select count(*) from recurrence_review where verdict='RECURRENCE'", lambda v: v[0][0] > 0)
chk("L3_investigations_without_full_8D", """select count(*) from investigation i left join qa_report r using(investigation_id)
 where i.analysis_level='L3' and i.scope_definition not like '%_GROUP%' and r.report_type<>'8D_FULL'""", [(0,)])
db.commit()

# ------------------------------------------------------------- schema.sql + csv
schema = "PRAGMA foreign_keys=ON;\n\n" + ";\n\n".join(s for s, in q(
    "select sql from sqlite_schema where sql is not null and name not like 'sqlite_%' order by type='view', rowid")) + ";\n"
(OUT / "schema.sql").write_text(schema, encoding="utf-8")
sc = OUT / "sample_csv"
if sc.exists():
    shutil.rmtree(sc)
sc.mkdir()
tables = [t for t, in q("select name from sqlite_schema where type='table' and name not like 'sqlite_%' order by name")]
for t in tables:
    cur = db.execute(f'select * from "{t}" limit 100')
    with open(sc / f"{t}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur.fetchall())

# ------------------------------------------------------------- manifest
scen = json.load(open(OUT / "generation_truth" / "scenarios.json", encoding="utf-8"))
manifest = {
    "version": "v30",
    "domain": "PCB 제조(다층 PCB 원판) RMA",
    "based_on": "SSD_RMA_dataset_v28 구조·규칙(명명, 시험 출처 확인 규칙)을 유지하고 도메인 재생성",
    "as_of": "2025-12-31T23:59:00+09:00",
    "basis_mail": "RE: [SKALA 프로젝트 문의] RMA AI 프로젝트 관련 현업 기준 (SK AX 이재민, 2026-11-16)",
    "table_rows": {t: q(f'select count(*) from "{t}"')[0][0] for t in tables},
    "views": sorted(VIEWS),
    "claims_by_major": dict(q("select major_code,count(*) from qa_case_overview o join symptom_definition d on d.minor_code=o.primary_label group by 1")),
    "claims_by_cause_category": dict(q("select cause_category,count(*) from qa_case_overview group by 1")),
    "claims_by_analysis_level": dict(q("select analysis_level,count(*) from qa_triage group by 1")),
    "monthly_claims": dict(q("select substr(opened_at,1,7),count(*) from rma_claim group by 1")),
    "scenarios": [{"id": s["id"], "title": s["title"], "cause_code": s["cause"], "escape_code": s["escape"],
                   "claims": s["claims_generated"], "impacted_lots": s["injected_lot_count"], "investigation_id": s["investigation_id"]}
                  for s in scen],
    "lot_impact_decisions": dict(q("select decision,count(*) from lot_impact_assessment group by 1")),
    "bottleneck_by_activity": [dict(zip(["activity_type", "n", "avg_days", "max_days", "avg_data_sources"], r)) for r in q("select * from bottleneck_by_activity")],
    "checks": checks,
}
(OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# ------------------------------------------------------------- generation config + scripts
import importlib.util
spec = importlib.util.spec_from_file_location("pcb_config", HERE / "pcb_config.py")
cfg = importlib.util.module_from_spec(spec); spec.loader.exec_module(cfg)
config = {k: getattr(cfg, k) for k in dir(cfg) if k.isupper()}
(OUT / "generation_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
for f in ("pcb_config.py", "generate_v30.py", "finalize_v30.py"):
    shutil.copy(HERE / f, OUT / f)

failed = [k for k, v in checks.items() if not v["ok"]]
print(json.dumps(checks, ensure_ascii=False, indent=1))
print("FAILED:", failed)
db.close()
