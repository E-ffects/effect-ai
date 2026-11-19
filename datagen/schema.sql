PRAGMA foreign_keys=ON;

CREATE TABLE customer (
  customer_id TEXT PRIMARY KEY NOT NULL,
  customer_name TEXT NOT NULL,
  industry TEXT NOT NULL,
  region TEXT NOT NULL,
  quality_standard TEXT NOT NULL,
  ppap_required INTEGER NOT NULL
);

CREATE TABLE supplier (
  supplier_id TEXT PRIMARY KEY NOT NULL,
  supplier_name TEXT NOT NULL,
  material_type TEXT NOT NULL,
  approval_status TEXT NOT NULL
);

CREATE TABLE product_spec (
  spec_id TEXT PRIMARY KEY NOT NULL,
  part_no TEXT NOT NULL,
  revision TEXT NOT NULL,
  product_family TEXT NOT NULL,
  application TEXT NOT NULL,
  layer_count INTEGER NOT NULL,
  board_thickness_mm REAL NOT NULL,
  surface_finish TEXT NOT NULL,
  base_material TEXT NOT NULL,
  min_line_space_um INTEGER NOT NULL,
  impedance_control INTEGER NOT NULL,
  hdi INTEGER NOT NULL,
  boards_per_panel INTEGER NOT NULL,
  effective_from TEXT NOT NULL
);

CREATE TABLE process_master (
  process_id TEXT PRIMARY KEY NOT NULL,
  process_name TEXT NOT NULL,
  stage_group TEXT NOT NULL,
  stage_seq INTEGER NOT NULL,
  sequence_no INTEGER NOT NULL,
  batch_unit TEXT NOT NULL,
  is_inspection INTEGER NOT NULL
);

CREATE TABLE equipment_master (
  equipment_id TEXT PRIMARY KEY NOT NULL,
  process_id TEXT NOT NULL REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  equipment_type TEXT NOT NULL,
  line TEXT NOT NULL,
  installed_at TEXT NOT NULL
);

CREATE TABLE special_characteristic (
  characteristic_id TEXT PRIMARY KEY NOT NULL,
  characteristic_name TEXT NOT NULL,
  symbol TEXT NOT NULL,
  control_method TEXT NOT NULL,
  related_process_id TEXT REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  related_test_spec_id TEXT
);

CREATE TABLE process_parameter_spec (
  param_id TEXT PRIMARY KEY NOT NULL,
  process_id TEXT NOT NULL REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  item_code TEXT NOT NULL,
  unit TEXT NOT NULL,
  target REAL NOT NULL,
  lower_limit REAL NOT NULL,
  upper_limit REAL NOT NULL,
  applies_to TEXT NOT NULL,
  characteristic_id TEXT REFERENCES special_characteristic(characteristic_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE symptom_definition (
  minor_code TEXT PRIMARY KEY NOT NULL,
  major_code TEXT NOT NULL,
  major_name TEXT NOT NULL,
  minor_name TEXT NOT NULL,
  label_version TEXT NOT NULL,
  severity INTEGER NOT NULL,
  characteristic_id TEXT REFERENCES special_characteristic(characteristic_id) DEFERRABLE INITIALLY DEFERRED,
  inclusion_rules TEXT NOT NULL,
  exclusion_rules TEXT NOT NULL
);

CREATE TABLE cause_master (
  cause_code TEXT PRIMARY KEY NOT NULL,
  cause_category TEXT NOT NULL,
  process_id TEXT REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  cause_name TEXT NOT NULL,
  mechanism TEXT NOT NULL,
  required_evidence TEXT NOT NULL,
  standard_action TEXT NOT NULL,
  action_owner_dept TEXT NOT NULL
);

CREATE TABLE escape_master (
  escape_code TEXT PRIMARY KEY NOT NULL,
  escape_name TEXT NOT NULL,
  detection_process_id TEXT REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  description TEXT NOT NULL,
  standard_action TEXT NOT NULL
);

CREATE TABLE test_spec (
  test_spec_id TEXT PRIMARY KEY NOT NULL,
  item_code TEXT NOT NULL,
  test_name TEXT NOT NULL,
  phase_group TEXT NOT NULL,
  method_version TEXT NOT NULL,
  unit TEXT NOT NULL,
  lower_limit REAL,
  upper_limit REAL,
  characteristic_id TEXT REFERENCES special_characteristic(characteristic_id) DEFERRABLE INITIALLY DEFERRED,
  valid_from TEXT NOT NULL
);

CREATE TABLE qa_causal_requirement (
  cause_code TEXT PRIMARY KEY NOT NULL REFERENCES cause_master(cause_code) DEFERRABLE INITIALLY DEFERRED,
  requirement_text TEXT NOT NULL,
  min_verified_lab_evidence INTEGER NOT NULL,
  requires_production_record INTEGER NOT NULL,
  requires_comparison_sample INTEGER NOT NULL
);

CREATE TABLE analysis_level_rule (
  rule_id TEXT PRIMARY KEY NOT NULL,
  analysis_level TEXT NOT NULL,
  condition_text TEXT NOT NULL,
  required_outputs TEXT NOT NULL,
  report_type TEXT NOT NULL
);

CREATE TABLE inspection_plan (
  plan_id TEXT PRIMARY KEY NOT NULL,
  plan_code TEXT NOT NULL,
  phase TEXT NOT NULL,
  test_spec_ids_json TEXT NOT NULL,
  applies_to TEXT NOT NULL,
  sample_rule TEXT NOT NULL,
  sample_rate REAL NOT NULL,
  revision INTEGER NOT NULL,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  change_reason TEXT NOT NULL,
  corrective_action_id TEXT REFERENCES corrective_action(corrective_action_id) DEFERRABLE INITIALLY DEFERRED,
  approval_status TEXT NOT NULL
);

CREATE TABLE material_lot (
  material_lot_id TEXT PRIMARY KEY NOT NULL,
  material_type TEXT NOT NULL,
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id) DEFERRABLE INITIALLY DEFERRED,
  grade TEXT NOT NULL,
  received_at TEXT NOT NULL,
  qty REAL NOT NULL,
  unit TEXT NOT NULL,
  coa_json TEXT NOT NULL,
  iqc_method TEXT NOT NULL,
  iqc_result TEXT NOT NULL,
  iqc_at TEXT NOT NULL,
  usage_status TEXT NOT NULL
);

CREATE TABLE production_lot (
  lot_id TEXT PRIMARY KEY NOT NULL,
  spec_id TEXT NOT NULL REFERENCES product_spec(spec_id) DEFERRABLE INITIALLY DEFERRED,
  work_order_id TEXT NOT NULL,
  date_code TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  panel_qty INTEGER NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE material_usage (
  usage_id TEXT PRIMARY KEY NOT NULL,
  lot_id TEXT NOT NULL REFERENCES production_lot(lot_id) DEFERRABLE INITIALLY DEFERRED,
  material_lot_id TEXT NOT NULL REFERENCES material_lot(material_lot_id) DEFERRABLE INITIALLY DEFERRED,
  material_type TEXT NOT NULL,
  issued_at TEXT NOT NULL
);

CREATE TABLE process_run (
  process_run_id TEXT PRIMARY KEY NOT NULL,
  lot_id TEXT NOT NULL REFERENCES production_lot(lot_id) DEFERRABLE INITIALLY DEFERRED,
  process_id TEXT NOT NULL REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  equipment_id TEXT NOT NULL REFERENCES equipment_master(equipment_id) DEFERRABLE INITIALLY DEFERRED,
  load_no INTEGER NOT NULL,
  panel_seq_from INTEGER NOT NULL,
  panel_seq_to INTEGER NOT NULL,
  recipe_version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  shift TEXT NOT NULL,
  result TEXT NOT NULL
);

CREATE TABLE process_observation (
  observation_id TEXT PRIMARY KEY NOT NULL,
  process_run_id TEXT NOT NULL REFERENCES process_run(process_run_id) DEFERRABLE INITIALLY DEFERRED,
  item_code TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  target REAL NOT NULL,
  lower_limit REAL NOT NULL,
  upper_limit REAL NOT NULL,
  measured_at TEXT NOT NULL,
  spc_flag TEXT NOT NULL
);

CREATE TABLE equipment_event (
  event_id TEXT PRIMARY KEY NOT NULL,
  equipment_id TEXT NOT NULL REFERENCES equipment_master(equipment_id) DEFERRABLE INITIALLY DEFERRED,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  metric_name TEXT,
  metric_value REAL,
  metric_unit TEXT,
  description TEXT NOT NULL,
  result TEXT NOT NULL
);

CREATE TABLE test_run (
  test_run_id TEXT PRIMARY KEY NOT NULL,
  phase TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  lot_id TEXT REFERENCES production_lot(lot_id) DEFERRABLE INITIALLY DEFERRED,
  rma_item_id TEXT REFERENCES rma_item(rma_item_id) DEFERRABLE INITIALLY DEFERRED,
  investigation_id TEXT REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  process_run_id TEXT REFERENCES process_run(process_run_id) DEFERRABLE INITIALLY DEFERRED,
  equipment_id TEXT REFERENCES equipment_master(equipment_id) DEFERRABLE INITIALLY DEFERRED,
  plan_id TEXT REFERENCES inspection_plan(plan_id) DEFERRABLE INITIALLY DEFERRED,
  sample_origin TEXT NOT NULL,
  origin_verified INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  overall_result TEXT NOT NULL
);

CREATE TABLE test_measurement (
  measurement_id TEXT PRIMARY KEY NOT NULL,
  test_run_id TEXT NOT NULL REFERENCES test_run(test_run_id) DEFERRABLE INITIALLY DEFERRED,
  test_spec_id TEXT NOT NULL REFERENCES test_spec(test_spec_id) DEFERRABLE INITIALLY DEFERRED,
  trial_index INTEGER NOT NULL,
  raw_value REAL,
  unit TEXT NOT NULL,
  condition_json TEXT NOT NULL,
  result TEXT NOT NULL
);

CREATE TABLE shipment_lot (
  shipment_lot_id TEXT PRIMARY KEY NOT NULL,
  shipment_id TEXT NOT NULL REFERENCES shipment(shipment_id) DEFERRABLE INITIALLY DEFERRED,
  lot_id TEXT NOT NULL REFERENCES production_lot(lot_id) DEFERRABLE INITIALLY DEFERRED,
  panel_seq_from INTEGER NOT NULL,
  panel_seq_to INTEGER NOT NULL,
  panel_qty INTEGER NOT NULL,
  board_qty INTEGER NOT NULL
);

CREATE TABLE rma_claim (
  rma_id TEXT PRIMARY KEY NOT NULL,
  external_claim_id TEXT NOT NULL,
  customer_id TEXT NOT NULL REFERENCES customer(customer_id) DEFERRABLE INITIALLY DEFERRED,
  opened_at TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  reported_part_no TEXT NOT NULL,
  reported_lot_no TEXT,
  reported_date_code TEXT,
  detection_stage TEXT NOT NULL,
  cs_status TEXT NOT NULL,
  qa_status TEXT NOT NULL,
  idempotency_key TEXT NOT NULL
);

CREATE TABLE customer_test_report (
  report_id TEXT PRIMARY KEY NOT NULL,
  rma_item_id TEXT NOT NULL REFERENCES rma_item(rma_item_id) DEFERRABLE INITIALLY DEFERRED,
  test_type TEXT NOT NULL,
  reported_value REAL,
  unit TEXT,
  conditions_json TEXT NOT NULL,
  fixture_info_provided INTEGER NOT NULL,
  origin_verified INTEGER NOT NULL,
  received_at TEXT NOT NULL
);

CREATE TABLE symptom_assessment (
  assessment_id TEXT PRIMARY KEY NOT NULL,
  rma_item_id TEXT NOT NULL REFERENCES rma_item(rma_item_id) DEFERRABLE INITIALLY DEFERRED,
  primary_label TEXT NOT NULL REFERENCES symptom_definition(minor_code) DEFERRABLE INITIALLY DEFERRED,
  label_version TEXT NOT NULL,
  secondary_labels_json TEXT NOT NULL,
  evidence_spans_json TEXT NOT NULL,
  proposed_by TEXT NOT NULL,
  confirmed_by TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
, intake_label text, reclassified_at text);

CREATE TABLE qa_triage (
  triage_id TEXT PRIMARY KEY NOT NULL,
  rma_id TEXT NOT NULL REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  severity INTEGER NOT NULL,
  characteristic_id TEXT REFERENCES special_characteristic(characteristic_id) DEFERRABLE INITIALLY DEFERRED,
  characteristic_symbol TEXT,
  customer_industry TEXT NOT NULL,
  repeat_signal INTEGER NOT NULL,
  analysis_level TEXT NOT NULL REFERENCES analysis_level_rule(rule_id) DEFERRABLE INITIALLY DEFERRED,
  rationale TEXT NOT NULL,
  decided_by TEXT NOT NULL,
  decided_at TEXT NOT NULL
);

CREATE TABLE investigation (
  investigation_id TEXT PRIMARY KEY NOT NULL,
  title TEXT NOT NULL,
  analysis_level TEXT NOT NULL,
  scope_definition TEXT NOT NULL,
  owner TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  status TEXT NOT NULL,
  closed_at TEXT,
  reopened_from TEXT REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE investigation_claim (
  link_id TEXT PRIMARY KEY NOT NULL,
  investigation_id TEXT NOT NULL REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  rma_id TEXT NOT NULL REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  membership_reason TEXT NOT NULL,
  linked_at TEXT NOT NULL
);

CREATE TABLE evidence (
  evidence_id TEXT PRIMARY KEY NOT NULL,
  investigation_id TEXT REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  source_type TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  available_at TEXT NOT NULL,
  author TEXT NOT NULL,
  content_summary TEXT NOT NULL,
  origin_verified INTEGER NOT NULL,
  is_synthetic INTEGER NOT NULL
);

CREATE TABLE "cause_candidate" (
  candidate_id TEXT PRIMARY KEY NOT NULL,
  investigation_id TEXT NOT NULL REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  candidate_no INTEGER NOT NULL,
  cause_code TEXT REFERENCES cause_master(cause_code) DEFERRABLE INITIALLY DEFERRED,
  review_note TEXT NOT NULL,
  commonality_dimension TEXT NOT NULL,
  review_method TEXT NOT NULL,
  review_status TEXT NOT NULL,
  review_evidence_ids_json TEXT NOT NULL,
  reviewed_at TEXT
);

CREATE TABLE cause_assessment (
  cause_assessment_id TEXT PRIMARY KEY NOT NULL,
  investigation_id TEXT NOT NULL REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  cause_code TEXT NOT NULL REFERENCES cause_master(cause_code) DEFERRABLE INITIALLY DEFERRED,
  cause_category TEXT NOT NULL,
  process_id TEXT REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  equipment_id TEXT REFERENCES equipment_master(equipment_id) DEFERRABLE INITIALLY DEFERRED,
  material_lot_ids_json TEXT NOT NULL,
  certainty_status TEXT NOT NULL,
  support_evidence_ids_json TEXT NOT NULL,
  assessed_at TEXT NOT NULL,
  approved_at TEXT
);

CREATE TABLE escape_assessment (
  escape_id TEXT PRIMARY KEY NOT NULL,
  investigation_id TEXT NOT NULL REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  escape_code TEXT NOT NULL REFERENCES escape_master(escape_code) DEFERRABLE INITIALLY DEFERRED,
  detection_process_id TEXT REFERENCES process_master(process_id) DEFERRABLE INITIALLY DEFERRED,
  test_run_id TEXT REFERENCES test_run(test_run_id) DEFERRABLE INITIALLY DEFERRED,
  evidence_ids_json TEXT NOT NULL,
  status TEXT NOT NULL,
  assessed_at TEXT NOT NULL
);

CREATE TABLE claim_cause (
  claim_cause_id TEXT PRIMARY KEY NOT NULL,
  rma_item_id TEXT NOT NULL REFERENCES rma_item(rma_item_id) DEFERRABLE INITIALLY DEFERRED,
  cause_assessment_id TEXT NOT NULL REFERENCES cause_assessment(cause_assessment_id) DEFERRABLE INITIALLY DEFERRED,
  applicability TEXT NOT NULL,
  rationale TEXT NOT NULL,
  decided_at TEXT
);

CREATE TABLE corrective_action (
  corrective_action_id TEXT PRIMARY KEY NOT NULL,
  investigation_id TEXT NOT NULL REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  action_role TEXT NOT NULL,
  action_category TEXT NOT NULL,
  description TEXT NOT NULL,
  owner_dept TEXT NOT NULL,
  cause_code TEXT REFERENCES cause_master(cause_code) DEFERRABLE INITIALLY DEFERRED,
  escape_code TEXT REFERENCES escape_master(escape_code) DEFERRABLE INITIALLY DEFERRED,
  scope_json TEXT NOT NULL,
  selected_at TEXT NOT NULL,
  due_at TEXT NOT NULL,
  implemented_at TEXT,
  verification_status TEXT NOT NULL
);

CREATE TABLE effectiveness_check (
  check_id TEXT PRIMARY KEY NOT NULL,
  corrective_action_id TEXT NOT NULL REFERENCES corrective_action(corrective_action_id) DEFERRABLE INITIALLY DEFERRED,
  metric TEXT NOT NULL,
  baseline_value REAL,
  target_value REAL,
  post_value REAL,
  window_from TEXT NOT NULL,
  window_to TEXT NOT NULL,
  result TEXT NOT NULL,
  checked_at TEXT
);

CREATE TABLE recurrence_review (
  review_id TEXT PRIMARY KEY NOT NULL,
  rma_item_id TEXT NOT NULL REFERENCES rma_item(rma_item_id) DEFERRABLE INITIALLY DEFERRED,
  corrective_action_id TEXT NOT NULL REFERENCES corrective_action(corrective_action_id) DEFERRABLE INITIALLY DEFERRED,
  lot_produced_after_action INTEGER NOT NULL,
  symptom_match INTEGER NOT NULL,
  cause_match INTEGER NOT NULL,
  verdict TEXT NOT NULL,
  reviewed_at TEXT NOT NULL
);

CREATE TABLE service_action (
  action_id TEXT PRIMARY KEY NOT NULL,
  rma_item_id TEXT NOT NULL REFERENCES rma_item(rma_item_id) DEFERRABLE INITIALLY DEFERRED,
  action_type TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  decided_at TEXT NOT NULL,
  executed_at TEXT,
  board_qty INTEGER NOT NULL,
  result TEXT NOT NULL
);

CREATE TABLE communication (
  message_id TEXT PRIMARY KEY NOT NULL,
  rma_id TEXT REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  investigation_id TEXT REFERENCES investigation(investigation_id) DEFERRABLE INITIALLY DEFERRED,
  customer_id TEXT REFERENCES customer(customer_id) DEFERRABLE INITIALLY DEFERRED,
  audience TEXT NOT NULL,
  author_role TEXT NOT NULL,
  message_text TEXT NOT NULL,
  sent_at TEXT NOT NULL
);

CREATE TABLE similar_cases (
  case_id TEXT PRIMARY KEY NOT NULL,
  query_rma_id TEXT NOT NULL REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  reference_rma_id TEXT NOT NULL REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  searched_at TEXT NOT NULL,
  similarity_score REAL NOT NULL,
  similarity_basis TEXT NOT NULL,
  applicability TEXT NOT NULL,
  search_version TEXT NOT NULL
);

CREATE TABLE claim_text_variant (
  rma_id TEXT PRIMARY KEY NOT NULL REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  variant TEXT NOT NULL,
  original_raw_text TEXT NOT NULL,
  original_evidence_spans_json TEXT NOT NULL
);

CREATE TABLE cost_rule (
        rule_id text primary key not null, rule_group text not null, value real not null, unit text not null,
        basis text not null, is_assumption integer not null);

CREATE TABLE product_cost (
        spec_id text primary key not null references product_spec(spec_id) deferrable initially deferred,
        panel_cost_krw integer not null, board_cost_krw integer not null, board_price_krw integer not null,
        basis text not null);

CREATE TABLE loss_ledger (
        ledger_id text primary key not null, cost_category text not null, cost_type text not null,
        rma_id text references rma_claim(rma_id) deferrable initially deferred,
        rma_item_id text references rma_item(rma_item_id) deferrable initially deferred,
        investigation_id text references investigation(investigation_id) deferrable initially deferred,
        customer_id text references customer(customer_id) deferrable initially deferred,
        spec_id text references product_spec(spec_id) deferrable initially deferred,
        quantity real not null, unit text not null, unit_cost_krw integer not null, amount_krw integer not null,
        basis text not null, source_table text not null, source_id text not null, occurred_at text not null);

CREATE TABLE "shipment" (
  shipment_id TEXT PRIMARY KEY NOT NULL,
  customer_id TEXT NOT NULL REFERENCES customer(customer_id) DEFERRABLE INITIALLY DEFERRED,
  order_item_id TEXT NOT NULL,
  shipped_at TEXT NOT NULL,
  delivered_at TEXT NOT NULL,
  board_qty INTEGER NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE "rma_item" (
  rma_item_id TEXT PRIMARY KEY NOT NULL,
  rma_id TEXT NOT NULL REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  panel_id TEXT,
  lot_id TEXT REFERENCES production_lot(lot_id) DEFERRABLE INITIALLY DEFERRED,
  shipment_lot_id TEXT REFERENCES shipment_lot(shipment_lot_id) DEFERRABLE INITIALLY DEFERRED,
  input_qty INTEGER NOT NULL,
  claimed_defect_qty INTEGER NOT NULL,
  returned_qty INTEGER NOT NULL,
  verified_defect_qty INTEGER,
  trace_status TEXT NOT NULL
);

CREATE INDEX ix_process_run_lot_id ON process_run(lot_id);

CREATE INDEX ix_process_run_equipment_id ON process_run(equipment_id);

CREATE INDEX ix_process_observation_process_run_id ON process_observation(process_run_id);

CREATE INDEX ix_test_run_lot_id ON test_run(lot_id);

CREATE INDEX ix_test_run_rma_item_id ON test_run(rma_item_id);

CREATE INDEX ix_test_measurement_test_run_id ON test_measurement(test_run_id);

CREATE INDEX ix_claim_cause_rma_item_id ON claim_cause(rma_item_id);

CREATE INDEX ix_investigation_claim_investigation_id ON investigation_claim(investigation_id);

CREATE INDEX ix_evidence_investigation_id ON evidence(investigation_id);

CREATE INDEX ix_shipment_lot_lot_id ON shipment_lot(lot_id);

CREATE INDEX ix_material_usage_lot_id ON material_usage(lot_id);

CREATE INDEX ix_material_usage_material_lot_id ON material_usage(material_lot_id);

CREATE INDEX ix_loss_ledger_rma on loss_ledger(rma_id);

CREATE INDEX ix_loss_ledger_occurred on loss_ledger(occurred_at);

CREATE INDEX ix_rma_item_lot_id ON rma_item(lot_id);

CREATE VIEW monthly_claims AS SELECT substr(c.opened_at,1,7) AS month, d.major_code, d.minor_code, d.minor_name, count(*) AS claims
FROM rma_claim c JOIN rma_item i USING(rma_id) JOIN symptom_assessment s USING(rma_item_id)
JOIN symptom_definition d ON d.minor_code=s.primary_label GROUP BY 1,2,3,4;

CREATE VIEW qa_case_overview AS SELECT c.rma_id, c.opened_at, c.customer_id, cu.industry, c.reported_part_no, i.lot_id, i.panel_id, c.detection_stage,
 s.primary_label, t.severity, t.characteristic_symbol, t.analysis_level, ic.investigation_id,
 ca.cause_code, ca.cause_category, ca.certainty_status, cc.applicability, c.qa_status, c.cs_status
FROM rma_claim c JOIN customer cu USING(customer_id) JOIN rma_item i USING(rma_id)
JOIN symptom_assessment s USING(rma_item_id) JOIN qa_triage t USING(rma_id)
LEFT JOIN investigation_claim ic USING(rma_id) LEFT JOIN claim_cause cc ON cc.rma_item_id=i.rma_item_id
LEFT JOIN cause_assessment ca ON ca.cause_assessment_id=cc.cause_assessment_id;

CREATE VIEW spc_out_of_spec_by_equipment_month AS SELECT r.equipment_id, r.process_id, o.item_code, substr(o.measured_at,1,7) AS month, count(*) AS observations,
 sum(o.spc_flag='OUT_OF_SPEC') AS out_of_spec
FROM process_observation o JOIN process_run r USING(process_run_id) GROUP BY 1,2,3,4;

CREATE VIEW special_characteristic_results AS SELECT sc.characteristic_id, sc.characteristic_name, sc.symbol, tr.phase, substr(tr.started_at,1,7) AS month,
 count(*) AS measurements, sum(m.result='FAIL') AS fails, sum(m.result='NOT_PERFORMED') AS not_performed
FROM test_measurement m JOIN test_spec ts USING(test_spec_id) JOIN special_characteristic sc ON sc.characteristic_id=ts.characteristic_id
JOIN test_run tr USING(test_run_id) WHERE tr.phase IN ('FQC','OQC','IQC') GROUP BY 1,2,3,4,5;

CREATE VIEW cause_confirmation_basis AS SELECT c.cause_assessment_id, c.investigation_id, c.cause_code, c.cause_category, c.certainty_status,
 sum(CASE WHEN e.source_type='test_run' AND e.origin_verified=1 THEN 1 ELSE 0 END) AS verified_lab_evidence,
 sum(CASE WHEN e.origin_verified=0 THEN 1 ELSE 0 END) AS unverified_evidence,
 sum(CASE WHEN e.source_type IN ('process_run_summary','equipment_event','material_lot','inspection_plan') THEN 1 ELSE 0 END) AS production_record_evidence
FROM cause_assessment c LEFT JOIN json_each(c.support_evidence_ids_json) j LEFT JOIN evidence e ON e.evidence_id=j.value
GROUP BY 1,2,3,4,5;

CREATE VIEW escape_point_summary AS SELECT e.escape_code, m.escape_name, m.detection_process_id, count(DISTINCT e.investigation_id) AS investigations,
 (SELECT count(*) FROM investigation_claim ic WHERE ic.investigation_id IN (SELECT investigation_id FROM escape_assessment x WHERE x.escape_code=e.escape_code)) AS claims
FROM escape_assessment e JOIN escape_master m USING(escape_code) GROUP BY 1,2,3;

CREATE VIEW qa_symptom_reproduction as
        with mapping(minor_code,item_code) as (values ('ELEC03','LAB_IST_DR'),('ELEC04','LAB_TDR_IMP'),('ELEC05','LAB_SIR'),('SOLD01','LAB_SOLDERABILITY'),('STRC01','LAB_REFLOW_REPRO'),('STRC02','LAB_XSEC_DELAM'),('STRC03','LAB_WARPAGE'),('STRC04','LAB_XSEC_CRACK'),('APPR01','LAB_SM_TAPE'),('DIM01','LAB_DIM'),('MARK01','LAB_LABEL_MATCH')),
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
        left join claim_cause cc using(rma_item_id) left join cause_assessment ca using(cause_assessment_id);

CREATE VIEW claim_loss as
        select rma_id, sum(amount_krw) total_krw,
          sum(case when cost_type='CREDIT_REFUND' then amount_krw else 0 end) refund_krw,
          sum(case when cost_type='REPLACEMENT_BOARDS' then amount_krw else 0 end) replacement_krw,
          sum(case when cost_type='CUSTOMER_CHARGEBACK' then amount_krw else 0 end) chargeback_krw,
          sum(case when cost_category='APPRAISAL' then amount_krw else 0 end) appraisal_krw,
          sum(case when cost_type in ('LOGISTICS','SORTING_SUPPORT') then amount_krw else 0 end) handling_krw
        from loss_ledger where rma_id is not null group by rma_id;

CREATE VIEW loss_by_month as
        select substr(occurred_at,1,7) month, cost_category, cost_type, count(*) entries, sum(amount_krw) amount_krw
        from loss_ledger group by 1,2,3;

CREATE VIEW loss_by_investigation as
        select l.investigation_id, i.title, i.analysis_level, sum(l.amount_krw) total_krw,
          sum(case when l.cost_category='CONTAINMENT' then l.amount_krw else 0 end) containment_krw,
          sum(case when l.cost_type='CUSTOMER_CHARGEBACK' then l.amount_krw else 0 end) chargeback_krw
        from loss_ledger l join investigation i using(investigation_id) group by 1,2,3;
