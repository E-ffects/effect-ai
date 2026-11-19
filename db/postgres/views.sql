-- PCB RMA views for PostgreSQL. Same names, columns and results as the SQLite views (v4/v5).
-- Changes from SQLite: sum(<condition>) -> sum(CASE ...), json_each/json_extract -> jsonb functions.

CREATE VIEW monthly_claims AS
SELECT substr(c.opened_at, 1, 7) AS month, d.major_code, d.minor_code, d.minor_name, count(*) AS claims
FROM rma_claim c JOIN rma_item i USING (rma_id) JOIN symptom_assessment s USING (rma_item_id)
JOIN symptom_definition d ON d.minor_code = s.primary_label
GROUP BY 1, 2, 3, 4;

CREATE VIEW qa_case_overview AS
SELECT c.rma_id, c.opened_at, c.customer_id, cu.industry, c.reported_part_no, i.lot_id, i.panel_id, c.detection_stage,
       s.primary_label, t.severity, t.characteristic_symbol, t.analysis_level, ic.investigation_id,
       ca.cause_code, ca.cause_category, ca.certainty_status, cc.applicability, c.qa_status, c.cs_status
FROM rma_claim c JOIN customer cu USING (customer_id) JOIN rma_item i USING (rma_id)
JOIN symptom_assessment s USING (rma_item_id) JOIN qa_triage t USING (rma_id)
LEFT JOIN investigation_claim ic USING (rma_id) LEFT JOIN claim_cause cc ON cc.rma_item_id = i.rma_item_id
LEFT JOIN cause_assessment ca ON ca.cause_assessment_id = cc.cause_assessment_id;

CREATE VIEW spc_out_of_spec_by_equipment_month AS
SELECT r.equipment_id, r.process_id, o.item_code, substr(o.measured_at, 1, 7) AS month, count(*) AS observations,
       sum(CASE WHEN o.spc_flag = 'OUT_OF_SPEC' THEN 1 ELSE 0 END) AS out_of_spec
FROM process_observation o JOIN process_run r USING (process_run_id)
GROUP BY 1, 2, 3, 4;

CREATE VIEW special_characteristic_results AS
SELECT sc.characteristic_id, sc.characteristic_name, sc.symbol, tr.phase, substr(tr.started_at, 1, 7) AS month,
       count(*) AS measurements,
       sum(CASE WHEN m.result = 'FAIL' THEN 1 ELSE 0 END) AS fails,
       sum(CASE WHEN m.result = 'NOT_PERFORMED' THEN 1 ELSE 0 END) AS not_performed
FROM test_measurement m JOIN test_spec ts USING (test_spec_id)
JOIN special_characteristic sc ON sc.characteristic_id = ts.characteristic_id
JOIN test_run tr USING (test_run_id)
WHERE tr.phase IN ('FQC', 'OQC', 'IQC')
GROUP BY 1, 2, 3, 4, 5;

CREATE VIEW cause_confirmation_basis AS
SELECT c.cause_assessment_id, c.investigation_id, c.cause_code, c.cause_category, c.certainty_status,
       sum(CASE WHEN e.source_type = 'test_run' AND e.origin_verified = 1 THEN 1 ELSE 0 END) AS verified_lab_evidence,
       sum(CASE WHEN e.origin_verified = 0 THEN 1 ELSE 0 END) AS unverified_evidence,
       sum(CASE WHEN e.source_type IN ('process_run_summary', 'equipment_event', 'material_lot', 'inspection_plan')
                THEN 1 ELSE 0 END) AS production_record_evidence
FROM cause_assessment c
LEFT JOIN LATERAL jsonb_array_elements_text(coalesce(c.support_evidence_ids_json, '[]')::jsonb) AS j(value) ON true
LEFT JOIN evidence e ON e.evidence_id = j.value
GROUP BY 1, 2, 3, 4, 5;

CREATE VIEW escape_point_summary AS
SELECT e.escape_code, m.escape_name, m.detection_process_id, count(DISTINCT e.investigation_id) AS investigations,
       (SELECT count(*) FROM investigation_claim ic
        WHERE ic.investigation_id IN (SELECT investigation_id FROM escape_assessment x WHERE x.escape_code = e.escape_code)) AS claims
FROM escape_assessment e JOIN escape_master m USING (escape_code)
GROUP BY 1, 2, 3;

CREATE VIEW qa_symptom_reproduction AS
WITH mapping(minor_code, item_code) AS (
  VALUES ('ELEC03', 'LAB_IST_DR'), ('ELEC04', 'LAB_TDR_IMP'), ('ELEC05', 'LAB_SIR'), ('SOLD01', 'LAB_SOLDERABILITY'),
         ('STRC01', 'LAB_REFLOW_REPRO'), ('STRC02', 'LAB_XSEC_DELAM'), ('STRC03', 'LAB_WARPAGE'), ('STRC04', 'LAB_XSEC_CRACK'),
         ('APPR01', 'LAB_SM_TAPE'), ('DIM01', 'LAB_DIM'), ('MARK01', 'LAB_LABEL_MATCH')
), sample_test AS (
  SELECT * FROM test_run WHERE phase = 'RMA_LAB' AND subject_type = 'RETURNED_SAMPLE'
), matching AS (
  SELECT t.test_run_id, m.result
  FROM sample_test t JOIN symptom_assessment s USING (rma_item_id)
  JOIN mapping p ON p.minor_code = s.primary_label
  JOIN test_measurement m USING (test_run_id) JOIN test_spec ts USING (test_spec_id)
  WHERE ts.test_spec_id = p.item_code
    AND (s.primary_label <> 'STRC01' OR (m.condition_json::jsonb ->> 'condition') = 'as_received')
), summary AS (
  SELECT test_run_id,
         sum(CASE WHEN result = 'FAIL' THEN 1 ELSE 0 END) AS failures,
         sum(CASE WHEN result = 'PASS' THEN 1 ELSE 0 END) AS passes,
         sum(CASE WHEN result NOT IN ('PASS', 'FAIL') THEN 1 ELSE 0 END) AS incomplete
  FROM matching GROUP BY test_run_id
)
SELECT i.rma_id, i.rma_item_id, s.primary_label, t.test_run_id, t.origin_verified,
       t.overall_result, coalesce(a.failures, 0) AS matching_failures, coalesce(a.passes, 0) AS matching_passes,
       CASE WHEN t.test_run_id IS NULL THEN 'NOT_TESTED'
            WHEN t.origin_verified <> 1 THEN 'UNVERIFIED_SAMPLE'
            WHEN a.failures > 0 THEN 'REPRODUCED'
            WHEN a.passes > 0 AND a.incomplete = 0 THEN 'NOT_REPRODUCED'
            ELSE 'REVIEW_REQUIRED' END AS reproduction_status,
       cc.applicability, ca.cause_code, ca.certainty_status
FROM rma_item i JOIN symptom_assessment s USING (rma_item_id)
LEFT JOIN sample_test t USING (rma_item_id) LEFT JOIN summary a ON a.test_run_id = t.test_run_id
LEFT JOIN claim_cause cc USING (rma_item_id) LEFT JOIN cause_assessment ca USING (cause_assessment_id);

CREATE VIEW claim_loss AS
SELECT rma_id, sum(amount_krw) AS total_krw,
       sum(CASE WHEN cost_type = 'CREDIT_REFUND' THEN amount_krw ELSE 0 END) AS refund_krw,
       sum(CASE WHEN cost_type = 'REPLACEMENT_BOARDS' THEN amount_krw ELSE 0 END) AS replacement_krw,
       sum(CASE WHEN cost_type = 'CUSTOMER_CHARGEBACK' THEN amount_krw ELSE 0 END) AS chargeback_krw,
       sum(CASE WHEN cost_category = 'APPRAISAL' THEN amount_krw ELSE 0 END) AS appraisal_krw,
       sum(CASE WHEN cost_type IN ('LOGISTICS', 'SORTING_SUPPORT') THEN amount_krw ELSE 0 END) AS handling_krw
FROM loss_ledger WHERE rma_id IS NOT NULL GROUP BY rma_id;

CREATE VIEW loss_by_month AS
SELECT substr(occurred_at, 1, 7) AS month, cost_category, cost_type, count(*) AS entries, sum(amount_krw) AS amount_krw
FROM loss_ledger GROUP BY 1, 2, 3;

CREATE VIEW loss_by_investigation AS
SELECT l.investigation_id, i.title, i.analysis_level, sum(l.amount_krw) AS total_krw,
       sum(CASE WHEN l.cost_category = 'CONTAINMENT' THEN l.amount_krw ELSE 0 END) AS containment_krw,
       sum(CASE WHEN l.cost_type = 'CUSTOMER_CHARGEBACK' THEN l.amount_krw ELSE 0 END) AS chargeback_krw
FROM loss_ledger l JOIN investigation i USING (investigation_id) GROUP BY 1, 2, 3;
