# -*- coding: utf-8 -*-
"""PCB RMA synthetic dataset v30 generator.

usage: python3 generate_v30.py <output_dir>
Builds pcb_rma.sqlite + schema.sql + sample_csv + evaluation + generation_truth + manifest.
All values and events are synthetic project assumptions.
"""
import csv, json, math, random, sqlite3, sys, bisect
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pcb_config as C

rng = random.Random(30)
KST = timezone(timedelta(hours=9))
ASOF = datetime.fromisoformat(C.AS_OF)
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/PCB_RMA_dataset_v30")
OUT.mkdir(parents=True, exist_ok=True)


def P(s):
    return datetime.fromisoformat(s).replace(tzinfo=KST) if len(s) == 10 else datetime.fromisoformat(s)


def I(d):
    return d.isoformat(timespec="seconds") if d is not None else None


def H(h):
    return timedelta(hours=h)


def Dd(d):
    return timedelta(days=d)


def U(a, b):
    return rng.uniform(a, b)


def wchoice(pairs):
    tot = sum(w for _, w in pairs)
    r = rng.random() * tot
    for v, w in pairs:
        r -= w
        if r <= 0:
            return v
    return pairs[-1][0]


def bday(d):
    """move to working hours 08:30~19:00"""
    if d.hour < 8:
        d = d.replace(hour=8, minute=30 + rng.randint(0, 29))
    elif d.hour >= 19:
        d = (d + Dd(1)).replace(hour=8, minute=30 + rng.randint(0, 29))
    if d.weekday() == 6:
        d = d + Dd(1)
    return d


R = defaultdict(list)   # table -> list of dict rows
CNT = Counter()


def nid(prefix, width=7):
    CNT[prefix] += 1
    return f"{prefix}-{CNT[prefix]:0{width}d}"


# ---------------------------------------------------------------- schema
SCHEMA = {}
def table(name, *cols):
    SCHEMA[name] = cols

FK = lambda t, c: f"REFERENCES {t}({c}) DEFERRABLE INITIALLY DEFERRED"
table("customer", "customer_id TEXT PRIMARY KEY NOT NULL", "customer_name TEXT NOT NULL", "industry TEXT NOT NULL",
      "region TEXT NOT NULL", "quality_standard TEXT NOT NULL", "ppap_required INTEGER NOT NULL")
table("supplier", "supplier_id TEXT PRIMARY KEY NOT NULL", "supplier_name TEXT NOT NULL", "material_type TEXT NOT NULL",
      "approval_status TEXT NOT NULL")
table("product_spec", "spec_id TEXT PRIMARY KEY NOT NULL", "part_no TEXT NOT NULL", "revision TEXT NOT NULL",
      "product_family TEXT NOT NULL", "application TEXT NOT NULL", "layer_count INTEGER NOT NULL",
      "board_thickness_mm REAL NOT NULL", "surface_finish TEXT NOT NULL", "base_material TEXT NOT NULL",
      "min_line_space_um INTEGER NOT NULL", "impedance_control INTEGER NOT NULL", "hdi INTEGER NOT NULL",
      "boards_per_panel INTEGER NOT NULL", "effective_from TEXT NOT NULL")
table("process_master", "process_id TEXT PRIMARY KEY NOT NULL", "process_name TEXT NOT NULL", "stage_group TEXT NOT NULL",
      "stage_seq INTEGER NOT NULL", "sequence_no INTEGER NOT NULL", "batch_unit TEXT NOT NULL", "is_inspection INTEGER NOT NULL")
table("equipment_master", "equipment_id TEXT PRIMARY KEY NOT NULL", f"process_id TEXT NOT NULL {FK('process_master','process_id')}",
      "equipment_type TEXT NOT NULL", "line TEXT NOT NULL", "installed_at TEXT NOT NULL")
table("special_characteristic", "characteristic_id TEXT PRIMARY KEY NOT NULL", "characteristic_name TEXT NOT NULL",
      "symbol TEXT NOT NULL", "control_method TEXT NOT NULL", f"related_process_id TEXT {FK('process_master','process_id')}",
      "related_test_spec_id TEXT")
table("process_parameter_spec", "param_id TEXT PRIMARY KEY NOT NULL", f"process_id TEXT NOT NULL {FK('process_master','process_id')}",
      "item_code TEXT NOT NULL", "unit TEXT NOT NULL", "target REAL NOT NULL", "lower_limit REAL NOT NULL",
      "upper_limit REAL NOT NULL", "applies_to TEXT NOT NULL",
      f"characteristic_id TEXT {FK('special_characteristic','characteristic_id')}")
table("symptom_definition", "minor_code TEXT PRIMARY KEY NOT NULL", "major_code TEXT NOT NULL", "major_name TEXT NOT NULL",
      "minor_name TEXT NOT NULL", "label_version TEXT NOT NULL", "severity INTEGER NOT NULL",
      f"characteristic_id TEXT {FK('special_characteristic','characteristic_id')}", "inclusion_rules TEXT NOT NULL",
      "exclusion_rules TEXT NOT NULL")
table("cause_master", "cause_code TEXT PRIMARY KEY NOT NULL", "cause_category TEXT NOT NULL",
      f"process_id TEXT {FK('process_master','process_id')}", "cause_name TEXT NOT NULL", "mechanism TEXT NOT NULL",
      "required_evidence TEXT NOT NULL", "standard_action TEXT NOT NULL", "action_owner_dept TEXT NOT NULL")
table("escape_master", "escape_code TEXT PRIMARY KEY NOT NULL", "escape_name TEXT NOT NULL",
      f"detection_process_id TEXT {FK('process_master','process_id')}", "description TEXT NOT NULL", "standard_action TEXT NOT NULL")
table("test_spec", "test_spec_id TEXT PRIMARY KEY NOT NULL", "item_code TEXT NOT NULL", "test_name TEXT NOT NULL",
      "phase_group TEXT NOT NULL", "method_version TEXT NOT NULL", "unit TEXT NOT NULL", "lower_limit REAL", "upper_limit REAL",
      f"characteristic_id TEXT {FK('special_characteristic','characteristic_id')}", "valid_from TEXT NOT NULL")
table("qa_causal_requirement", f"cause_code TEXT PRIMARY KEY NOT NULL {FK('cause_master','cause_code')}",
      "requirement_text TEXT NOT NULL", "min_verified_lab_evidence INTEGER NOT NULL", "requires_production_record INTEGER NOT NULL",
      "requires_comparison_sample INTEGER NOT NULL")
table("analysis_level_rule", "rule_id TEXT PRIMARY KEY NOT NULL", "analysis_level TEXT NOT NULL", "condition_text TEXT NOT NULL",
      "required_outputs TEXT NOT NULL", "report_type TEXT NOT NULL")
table("inspection_plan", "plan_id TEXT PRIMARY KEY NOT NULL", "plan_code TEXT NOT NULL", "phase TEXT NOT NULL",
      "test_spec_ids_json TEXT NOT NULL", "applies_to TEXT NOT NULL", "sample_rule TEXT NOT NULL", "sample_rate REAL NOT NULL",
      "revision INTEGER NOT NULL", "effective_from TEXT NOT NULL", "effective_to TEXT", "change_reason TEXT NOT NULL",
      f"corrective_action_id TEXT {FK('corrective_action','corrective_action_id')}", "approval_status TEXT NOT NULL")
table("material_lot", "material_lot_id TEXT PRIMARY KEY NOT NULL", "material_type TEXT NOT NULL",
      f"supplier_id TEXT NOT NULL {FK('supplier','supplier_id')}", "grade TEXT NOT NULL", "received_at TEXT NOT NULL",
      "qty REAL NOT NULL", "unit TEXT NOT NULL", "coa_json TEXT NOT NULL", "iqc_method TEXT NOT NULL", "iqc_result TEXT NOT NULL",
      "iqc_at TEXT NOT NULL", "usage_status TEXT NOT NULL")
table("production_lot", "lot_id TEXT PRIMARY KEY NOT NULL", f"spec_id TEXT NOT NULL {FK('product_spec','spec_id')}",
      "work_order_id TEXT NOT NULL", "date_code TEXT NOT NULL", "started_at TEXT NOT NULL", "completed_at TEXT NOT NULL",
      "panel_qty INTEGER NOT NULL", "status TEXT NOT NULL")
table("material_usage", "usage_id TEXT PRIMARY KEY NOT NULL", f"lot_id TEXT NOT NULL {FK('production_lot','lot_id')}",
      f"material_lot_id TEXT NOT NULL {FK('material_lot','material_lot_id')}", "material_type TEXT NOT NULL",
      "issued_at TEXT NOT NULL")
table("process_run", "process_run_id TEXT PRIMARY KEY NOT NULL", f"lot_id TEXT NOT NULL {FK('production_lot','lot_id')}",
      f"process_id TEXT NOT NULL {FK('process_master','process_id')}", f"equipment_id TEXT NOT NULL {FK('equipment_master','equipment_id')}",
      "load_no INTEGER NOT NULL", "panel_seq_from INTEGER NOT NULL", "panel_seq_to INTEGER NOT NULL", "recipe_version TEXT NOT NULL",
      "started_at TEXT NOT NULL", "ended_at TEXT NOT NULL", "shift TEXT NOT NULL", "result TEXT NOT NULL")
table("process_observation", "observation_id TEXT PRIMARY KEY NOT NULL",
      f"process_run_id TEXT NOT NULL {FK('process_run','process_run_id')}", "item_code TEXT NOT NULL", "value REAL NOT NULL",
      "unit TEXT NOT NULL", "target REAL NOT NULL", "lower_limit REAL NOT NULL", "upper_limit REAL NOT NULL",
      "measured_at TEXT NOT NULL", "spc_flag TEXT NOT NULL")
table("equipment_event", "event_id TEXT PRIMARY KEY NOT NULL", f"equipment_id TEXT NOT NULL {FK('equipment_master','equipment_id')}",
      "event_type TEXT NOT NULL", "occurred_at TEXT NOT NULL", "recorded_at TEXT NOT NULL", "metric_name TEXT", "metric_value REAL",
      "metric_unit TEXT", "description TEXT NOT NULL", "result TEXT NOT NULL")
table("panel", "panel_id TEXT PRIMARY KEY NOT NULL", f"lot_id TEXT NOT NULL {FK('production_lot','lot_id')}",
      "panel_seq INTEGER NOT NULL", "boards_per_panel INTEGER NOT NULL", "xout_boards INTEGER NOT NULL", "disposition TEXT NOT NULL")
table("test_run", "test_run_id TEXT PRIMARY KEY NOT NULL", "phase TEXT NOT NULL", "subject_type TEXT NOT NULL",
      "subject_id TEXT NOT NULL", f"lot_id TEXT {FK('production_lot','lot_id')}", f"rma_item_id TEXT {FK('rma_item','rma_item_id')}",
      f"investigation_id TEXT {FK('investigation','investigation_id')}", f"process_run_id TEXT {FK('process_run','process_run_id')}",
      f"equipment_id TEXT {FK('equipment_master','equipment_id')}", f"plan_id TEXT {FK('inspection_plan','plan_id')}",
      "sample_origin TEXT NOT NULL", "origin_verified INTEGER NOT NULL", "started_at TEXT NOT NULL", "ended_at TEXT NOT NULL",
      "overall_result TEXT NOT NULL")
table("test_measurement", "measurement_id TEXT PRIMARY KEY NOT NULL", f"test_run_id TEXT NOT NULL {FK('test_run','test_run_id')}",
      f"test_spec_id TEXT NOT NULL {FK('test_spec','test_spec_id')}", "trial_index INTEGER NOT NULL", "raw_value REAL",
      "unit TEXT NOT NULL", "condition_json TEXT NOT NULL", "result TEXT NOT NULL")
table("order_item", "order_item_id TEXT PRIMARY KEY NOT NULL", "order_id TEXT NOT NULL",
      f"customer_id TEXT NOT NULL {FK('customer','customer_id')}", f"spec_id TEXT NOT NULL {FK('product_spec','spec_id')}",
      "ordered_qty_boards INTEGER NOT NULL", "ordered_at TEXT NOT NULL", "requested_at TEXT NOT NULL")
table("shipment", "shipment_id TEXT PRIMARY KEY NOT NULL", f"customer_id TEXT NOT NULL {FK('customer','customer_id')}",
      f"order_item_id TEXT NOT NULL {FK('order_item','order_item_id')}", "shipped_at TEXT NOT NULL", "delivered_at TEXT NOT NULL",
      "board_qty INTEGER NOT NULL", "status TEXT NOT NULL")
table("shipment_lot", "shipment_lot_id TEXT PRIMARY KEY NOT NULL", f"shipment_id TEXT NOT NULL {FK('shipment','shipment_id')}",
      f"lot_id TEXT NOT NULL {FK('production_lot','lot_id')}", "panel_seq_from INTEGER NOT NULL", "panel_seq_to INTEGER NOT NULL",
      "panel_qty INTEGER NOT NULL", "board_qty INTEGER NOT NULL")
table("rma_claim", "rma_id TEXT PRIMARY KEY NOT NULL", "external_claim_id TEXT NOT NULL",
      f"customer_id TEXT NOT NULL {FK('customer','customer_id')}", "opened_at TEXT NOT NULL", "occurred_at TEXT NOT NULL",
      "raw_text TEXT NOT NULL", "reported_part_no TEXT NOT NULL", "reported_lot_no TEXT", "reported_date_code TEXT",
      "detection_stage TEXT NOT NULL", "cs_status TEXT NOT NULL", "qa_status TEXT NOT NULL", "idempotency_key TEXT NOT NULL")
table("rma_item", "rma_item_id TEXT PRIMARY KEY NOT NULL", f"rma_id TEXT NOT NULL {FK('rma_claim','rma_id')}",
      f"panel_id TEXT {FK('panel','panel_id')}", f"lot_id TEXT {FK('production_lot','lot_id')}",
      f"shipment_lot_id TEXT {FK('shipment_lot','shipment_lot_id')}", "input_qty INTEGER NOT NULL",
      "claimed_defect_qty INTEGER NOT NULL", "returned_qty INTEGER NOT NULL", "verified_defect_qty INTEGER",
      "trace_status TEXT NOT NULL")
table("customer_test_report", "report_id TEXT PRIMARY KEY NOT NULL", f"rma_item_id TEXT NOT NULL {FK('rma_item','rma_item_id')}",
      "test_type TEXT NOT NULL", "reported_value REAL", "unit TEXT", "conditions_json TEXT NOT NULL",
      "fixture_info_provided INTEGER NOT NULL", "origin_verified INTEGER NOT NULL", "received_at TEXT NOT NULL")
table("symptom_assessment", "assessment_id TEXT PRIMARY KEY NOT NULL", f"rma_item_id TEXT NOT NULL {FK('rma_item','rma_item_id')}",
      f"primary_label TEXT NOT NULL {FK('symptom_definition','minor_code')}", "label_version TEXT NOT NULL",
      "secondary_labels_json TEXT NOT NULL", "evidence_spans_json TEXT NOT NULL", "proposed_by TEXT NOT NULL",
      "confirmed_by TEXT", "status TEXT NOT NULL", "created_at TEXT NOT NULL")
table("qa_triage", "triage_id TEXT PRIMARY KEY NOT NULL", f"rma_id TEXT NOT NULL {FK('rma_claim','rma_id')}",
      "severity INTEGER NOT NULL", f"characteristic_id TEXT {FK('special_characteristic','characteristic_id')}",
      "characteristic_symbol TEXT", "customer_industry TEXT NOT NULL", "repeat_signal INTEGER NOT NULL",
      f"analysis_level TEXT NOT NULL {FK('analysis_level_rule','rule_id')}", "rationale TEXT NOT NULL", "decided_by TEXT NOT NULL",
      "decided_at TEXT NOT NULL")
table("return_authorization", "authorization_id TEXT PRIMARY KEY NOT NULL",
      f"rma_item_id TEXT NOT NULL {FK('rma_item','rma_item_id')}", "decided_by TEXT NOT NULL", "requested_qty INTEGER NOT NULL",
      "basis TEXT NOT NULL", "approved_at TEXT NOT NULL", "status TEXT NOT NULL")
table("return_receipt", "receipt_id TEXT PRIMARY KEY NOT NULL",
      f"authorization_id TEXT NOT NULL {FK('return_authorization','authorization_id')}", "received_at TEXT NOT NULL",
      "received_qty INTEGER NOT NULL", "package_condition TEXT NOT NULL", "identity_result TEXT NOT NULL")
table("investigation", "investigation_id TEXT PRIMARY KEY NOT NULL", "title TEXT NOT NULL", "analysis_level TEXT NOT NULL",
      "scope_definition TEXT NOT NULL", "owner TEXT NOT NULL", "opened_at TEXT NOT NULL", "status TEXT NOT NULL", "closed_at TEXT",
      f"reopened_from TEXT {FK('investigation','investigation_id')}")
table("investigation_claim", "link_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", f"rma_id TEXT NOT NULL {FK('rma_claim','rma_id')}",
      "membership_reason TEXT NOT NULL", "linked_at TEXT NOT NULL")
table("analysis_activity", "activity_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", "activity_type TEXT NOT NULL",
      "owner_dept TEXT NOT NULL", "started_at TEXT NOT NULL", "ended_at TEXT", "data_sources_json TEXT NOT NULL", "note TEXT NOT NULL")
table("evidence", "evidence_id TEXT PRIMARY KEY NOT NULL", f"investigation_id TEXT {FK('investigation','investigation_id')}",
      "source_type TEXT NOT NULL", "source_record_id TEXT NOT NULL", "occurred_at TEXT NOT NULL", "available_at TEXT NOT NULL",
      "author TEXT NOT NULL", "content_summary TEXT NOT NULL", "origin_verified INTEGER NOT NULL", "is_synthetic INTEGER NOT NULL")
table("cause_hypothesis", "hypothesis_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", "hypothesis_no INTEGER NOT NULL",
      f"cause_code TEXT {FK('cause_master','cause_code')}", "hypothesis_text TEXT NOT NULL", "commonality_dimension TEXT NOT NULL",
      "verification_method TEXT NOT NULL", "result TEXT NOT NULL", "evidence_ids_json TEXT NOT NULL", "decided_at TEXT")
table("cause_assessment", "cause_assessment_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", f"cause_code TEXT NOT NULL {FK('cause_master','cause_code')}",
      "cause_category TEXT NOT NULL", f"process_id TEXT {FK('process_master','process_id')}",
      f"equipment_id TEXT {FK('equipment_master','equipment_id')}", "material_lot_ids_json TEXT NOT NULL",
      "certainty_status TEXT NOT NULL", "support_evidence_ids_json TEXT NOT NULL", "assessed_at TEXT NOT NULL", "approved_at TEXT")
table("escape_assessment", "escape_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", f"escape_code TEXT NOT NULL {FK('escape_master','escape_code')}",
      f"detection_process_id TEXT {FK('process_master','process_id')}", f"test_run_id TEXT {FK('test_run','test_run_id')}",
      "evidence_ids_json TEXT NOT NULL", "status TEXT NOT NULL", "assessed_at TEXT NOT NULL")
table("claim_cause", "claim_cause_id TEXT PRIMARY KEY NOT NULL", f"rma_item_id TEXT NOT NULL {FK('rma_item','rma_item_id')}",
      f"cause_assessment_id TEXT NOT NULL {FK('cause_assessment','cause_assessment_id')}", "applicability TEXT NOT NULL",
      "rationale TEXT NOT NULL", "decided_at TEXT")
table("lot_impact_assessment", "impact_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", f"lot_id TEXT NOT NULL {FK('production_lot','lot_id')}",
      "claim_count INTEGER NOT NULL", "same_lot_as_claim INTEGER NOT NULL", "same_material_json TEXT NOT NULL",
      "same_equipment_json TEXT NOT NULL", "same_condition_json TEXT NOT NULL", "same_time_window INTEGER NOT NULL",
      "retained_sample_result TEXT NOT NULL", "impacted_panel_from INTEGER", "impacted_panel_to INTEGER", "decision TEXT NOT NULL",
      "rationale TEXT NOT NULL", "decided_at TEXT NOT NULL")
table("containment_action", "containment_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", f"lot_id TEXT NOT NULL {FK('production_lot','lot_id')}",
      "location TEXT NOT NULL", f"customer_id TEXT {FK('customer','customer_id')}", "action_type TEXT NOT NULL",
      "qty_panels INTEGER NOT NULL", "started_at TEXT NOT NULL", "completed_at TEXT", "result TEXT NOT NULL")
table("qa_report", "report_id TEXT PRIMARY KEY NOT NULL", f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}",
      "report_type TEXT NOT NULL", "status TEXT NOT NULL", "first_submitted_at TEXT", "final_submitted_at TEXT")
table("eight_d_step", "step_id TEXT PRIMARY KEY NOT NULL", f"report_id TEXT NOT NULL {FK('qa_report','report_id')}",
      "d_step TEXT NOT NULL", "step_name TEXT NOT NULL", "owner_dept TEXT NOT NULL", "started_at TEXT", "completed_at TEXT",
      "status TEXT NOT NULL", "summary TEXT NOT NULL")
table("corrective_action", "corrective_action_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", "action_role TEXT NOT NULL",
      "action_category TEXT NOT NULL", "description TEXT NOT NULL", "owner_dept TEXT NOT NULL",
      f"cause_code TEXT {FK('cause_master','cause_code')}", f"escape_code TEXT {FK('escape_master','escape_code')}",
      "scope_json TEXT NOT NULL", "selected_at TEXT NOT NULL", "due_at TEXT NOT NULL", "implemented_at TEXT",
      "verification_status TEXT NOT NULL")
table("document_change", "change_id TEXT PRIMARY KEY NOT NULL",
      f"corrective_action_id TEXT NOT NULL {FK('corrective_action','corrective_action_id')}", "doc_type TEXT NOT NULL",
      "doc_no TEXT NOT NULL", "rev_from INTEGER NOT NULL", "rev_to INTEGER NOT NULL", "change_summary TEXT NOT NULL",
      "severity INTEGER", "occurrence_before INTEGER", "occurrence_after INTEGER", "detection_before INTEGER",
      "detection_after INTEGER", "approved_at TEXT")
table("supplier_action", "scar_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", f"supplier_id TEXT NOT NULL {FK('supplier','supplier_id')}",
      "material_lot_ids_json TEXT NOT NULL", "request_text TEXT NOT NULL", "issued_at TEXT NOT NULL", "response_due_at TEXT NOT NULL",
      "supplier_8d_received_at TEXT", "supplier_root_cause TEXT", "status TEXT NOT NULL")
table("customer_disposition_approval", "approval_id TEXT PRIMARY KEY NOT NULL",
      f"investigation_id TEXT NOT NULL {FK('investigation','investigation_id')}", f"customer_id TEXT NOT NULL {FK('customer','customer_id')}",
      "proposal TEXT NOT NULL", "affected_board_qty INTEGER NOT NULL", "submitted_at TEXT NOT NULL", "responded_at TEXT",
      "status TEXT NOT NULL")
table("effectiveness_check", "check_id TEXT PRIMARY KEY NOT NULL",
      f"corrective_action_id TEXT NOT NULL {FK('corrective_action','corrective_action_id')}", "metric TEXT NOT NULL",
      "baseline_value REAL", "target_value REAL", "post_value REAL", "window_from TEXT NOT NULL", "window_to TEXT NOT NULL",
      "result TEXT NOT NULL", "checked_at TEXT")
table("monitoring_cohort", "cohort_id TEXT PRIMARY KEY NOT NULL",
      f"corrective_action_id TEXT NOT NULL {FK('corrective_action','corrective_action_id')}", "selection_rule_json TEXT NOT NULL",
      "observation_window_days INTEGER NOT NULL", "eligible_lots INTEGER NOT NULL", "lots_with_claims INTEGER NOT NULL",
      "report_as_of TEXT NOT NULL")
table("recurrence_review", "review_id TEXT PRIMARY KEY NOT NULL", f"rma_item_id TEXT NOT NULL {FK('rma_item','rma_item_id')}",
      f"corrective_action_id TEXT NOT NULL {FK('corrective_action','corrective_action_id')}",
      "lot_produced_after_action INTEGER NOT NULL", "symptom_match INTEGER NOT NULL", "cause_match INTEGER NOT NULL",
      "verdict TEXT NOT NULL", "reviewed_at TEXT NOT NULL")
table("service_action", "action_id TEXT PRIMARY KEY NOT NULL", f"rma_item_id TEXT NOT NULL {FK('rma_item','rma_item_id')}",
      "action_type TEXT NOT NULL", "policy_version TEXT NOT NULL", "decided_at TEXT NOT NULL", "executed_at TEXT",
      "board_qty INTEGER NOT NULL", "result TEXT NOT NULL")
table("communication", "message_id TEXT PRIMARY KEY NOT NULL", f"rma_id TEXT {FK('rma_claim','rma_id')}",
      f"investigation_id TEXT {FK('investigation','investigation_id')}", f"customer_id TEXT {FK('customer','customer_id')}",
      "audience TEXT NOT NULL", "author_role TEXT NOT NULL", "message_text TEXT NOT NULL", "sent_at TEXT NOT NULL")
table("similar_cases", "case_id TEXT PRIMARY KEY NOT NULL", f"query_rma_id TEXT NOT NULL {FK('rma_claim','rma_id')}",
      f"reference_rma_id TEXT NOT NULL {FK('rma_claim','rma_id')}", "searched_at TEXT NOT NULL", "similarity_score REAL NOT NULL",
      "similarity_basis TEXT NOT NULL", "applicability TEXT NOT NULL", "search_version TEXT NOT NULL")


def add(t, **kw):
    R[t].append(kw)
    return kw


# ---------------------------------------------------------------- masters
for c in C.CUSTOMERS:
    add("customer", customer_id=c[0], customer_name=c[1], industry=c[2], region=c[3], quality_standard=c[4], ppap_required=c[5])
CUST = {c[0]: c for c in C.CUSTOMERS}
for mt, (sups, _, _, _) in C.MATERIALS.items():
    for s in sups:
        add("supplier", supplier_id=s, supplier_name=C.SUPPLIER_NAMES[s], material_type=mt, approval_status="APPROVED")
PROD = {p["spec_id"]: p for p in C.PRODUCTS}
for p in C.PRODUCTS:
    add("product_spec", spec_id=p["spec_id"], part_no=p["part_no"], revision=p["revision"], product_family=p["family"],
        application=p["application"], layer_count=p["layers"], board_thickness_mm=p["thickness"], surface_finish=p["finish"],
        base_material=p["base"], min_line_space_um=p["line_space"], impedance_control=p["imp"], hdi=p["hdi"],
        boards_per_panel=p["bpp"], effective_from="2024-06-01T00:00:00+09:00")
PROC = {}
for i, (pid, name, grp, gs, unit, insp, hours, eqs) in enumerate(C.PROCESSES, 1):
    PROC[pid] = dict(name=name, group=grp, hours=hours, eqs=eqs, unit=unit)
    add("process_master", process_id=pid, process_name=name, stage_group=grp, stage_seq=gs, sequence_no=i, batch_unit=unit,
        is_inspection=insp)
    for e in eqs:
        add("equipment_master", equipment_id=e, process_id=pid, equipment_type=C.EQUIPMENT_TYPE[e],
            line="LINE-B" if e.endswith("02") or e.endswith("04") else "LINE-A", installed_at="2021-03-01T00:00:00+09:00")
for sc in C.SPECIAL_CHARACTERISTICS:
    add("special_characteristic", characteristic_id=sc[0], characteristic_name=sc[1], symbol=sc[2], control_method=sc[3],
        related_process_id=sc[4], related_test_spec_id=sc[5])
CHAR = {sc[0]: sc for sc in C.SPECIAL_CHARACTERISTICS}
PARAM = {}
for proc, plist in C.PARAMS.items():
    for item, unit, tgt, lo, hi, sd, applies, ch in plist:
        PARAM[(proc, item)] = (unit, tgt, lo, hi, sd, applies)
        add("process_parameter_spec", param_id=f"PP-{proc}-{item}", process_id=proc, item_code=item, unit=unit, target=tgt,
            lower_limit=lo, upper_limit=hi, applies_to=applies, characteristic_id=ch)
SYM = {s[0]: s for s in C.SYMPTOMS}
for s in C.SYMPTOMS:
    add("symptom_definition", minor_code=s[0], major_code=s[1], major_name=s[2], minor_name=s[3], label_version="pcb-v30",
        severity=s[4], characteristic_id=s[5], inclusion_rules=s[6], exclusion_rules=s[7])
for code, (cat, proc, name, mech, req, ca, owner, doc) in C.CAUSES.items():
    add("cause_master", cause_code=code, cause_category=cat, process_id=proc, cause_name=name, mechanism=mech,
        required_evidence=req, standard_action=ca, action_owner_dept=owner)
SCEN_CAUSES = {s["cause"] for s in C.SCENARIOS}
for code, (cat, proc, name, mech, req, ca, owner, doc) in C.CAUSES.items():
    if cat == "UNDETERMINED":
        continue
    add("qa_causal_requirement", cause_code=code, requirement_text=req + ". 고객 측 측정값은 시료 출처·측정 조건이 확인된 경우에만 근거로 사용한다.",
        min_verified_lab_evidence=1, requires_production_record=1, requires_comparison_sample=1 if code in SCEN_CAUSES else 0)
for code, (name, proc, desc, ca) in C.ESCAPES.items():
    add("escape_master", escape_code=code, escape_name=name, detection_process_id=proc, description=desc, standard_action=ca)
TSPEC = {}
for t in C.TEST_SPECS:
    TSPEC[t[0]] = t
    add("test_spec", test_spec_id=t[0], item_code=t[1], test_name=t[2], phase_group=t[3], method_version="M1", unit=t[4],
        lower_limit=t[5], upper_limit=t[6], characteristic_id=t[7], valid_from="2024-01-01T00:00:00+09:00")
LEVEL_RULES = [
    ("L1", "심각도 5 이하이고 특별특성(CC/SC) 비해당", "반송품 확인, 분석 회신", "ANALYSIS_REPLY"),
    ("L2", "심각도 6~7, SC 특성 관련, 또는 동일 고객·불량 형태 30일 내 3건 이상 반복", "원인 분석, 영향 Lot 판정, 8D(D1~D6, D8)", "8D_SHORT"),
    ("L3", "심각도 9 이상, CC 특성 관련, 또는 차량용 고객 심각도 8 이상", "8D 전체(D1~D8), 고객 처리 승인, FMEA/CP 개정 검토", "8D_FULL"),
]
for r in LEVEL_RULES:
    add("analysis_level_rule", rule_id=r[0], analysis_level=r[0], condition_text=r[1], required_outputs=r[2], report_type=r[3])


def cmp_result(spec_id, v):
    t = TSPEC[spec_id]
    lo, hi = t[5], t[6]
    if v is None:
        return "NOT_PERFORMED"
    if lo is not None and v < lo - 1e-9:
        return "FAIL"
    if hi is not None and v > hi + 1e-9:
        return "FAIL"
    return "PASS"


# ---------------------------------------------------------------- material lots
MLOTS = defaultdict(list)   # (type, supplier, grade) -> sorted list of (received_dt, id)
MLOT = {}
GRADES = {"CCL": {"SUP-CCL-A": ["TG150", "TG170", "TG180"], "SUP-CCL-B": ["TG170", "TG180"]},
          "PREPREG": {"SUP-PP-A": ["TG150", "TG170", "TG180"], "SUP-PP-B": ["TG150", "TG170", "TG180"]}}


def coa(mt):
    if mt == "CCL":
        return {"tg_c": round(U(172, 182), 1), "resin_content_pct": round(U(42, 46), 1), "peel_n_mm": round(U(1.3, 1.5), 2)}
    if mt == "PREPREG":
        return {"resin_content_pct": round(U(55, 60), 1), "resin_flow_pct": round(U(24, 29), 1), "gel_time_s": rng.randint(110, 140)}
    if mt == "CU_FOIL":
        return {"peel_n_mm": round(U(1.25, 1.45), 2), "rz_um": round(U(4.5, 6.0), 2)}
    if mt == "PSR_INK":
        return {"viscosity_dpa_s": rng.randint(200, 240)}
    if mt == "DRY_FILM":
        return {"thickness_um": round(U(29, 31), 1)}
    return {"ni_conc_g_l": round(U(5.6, 6.2), 2)}


for mt, (sups, gaware, interval, unit) in C.MATERIALS.items():
    for s in sups:
        grades = GRADES.get(mt, {}).get(s, ["STD"]) if gaware else ["STD"]
        for g in grades:
            d = P("2024-09-23") + Dd(rng.randint(0, interval - 1))
            n = 0
            while d < P("2025-12-20"):
                n += 1
                mid = f"ML-{mt}-{s[-1]}{g[-3:] if g != 'STD' else ''}-{len(MLOT) + 1:05d}"
                rec = bday(d + H(rng.randint(9, 15)))
                MLOT[mid] = dict(id=mid, type=mt, sup=s, grade=g, received=rec)
                MLOTS[(mt, s, g)].append((rec, mid))
                d += Dd(interval + rng.randint(-1, 2))
for k in MLOTS:
    MLOTS[k].sort()


def pick_mlot(mt, sup, grade, when):
    lst = MLOTS[(mt, sup, grade)]
    keys = [x[0] for x in lst]
    i = bisect.bisect_right(keys, when - Dd(1)) - 1
    return lst[max(i, 0)][1]


def material_plan(p):
    g = p["grade"]
    plan = {}
    plan["CCL"] = ("SUP-CCL-A", g) if g == "TG150" else (("SUP-CCL-A" if rng.random() < .55 else "SUP-CCL-B"), g)
    plan["PREPREG"] = ("SUP-PP-A" if rng.random() < .5 else "SUP-PP-B", g)
    if p["spec_id"] == "AUTO-BMS4H":
        plan["CU_FOIL"] = ("SUP-CU-B" if rng.random() < .7 else "SUP-CU-A", "STD")
    else:
        plan["CU_FOIL"] = ("SUP-CU-A" if rng.random() < .6 else "SUP-CU-B", "STD")
    plan["DRY_FILM"] = ("SUP-DF-A", "STD")
    plan["PSR_INK"] = ("SUP-INK-A" if rng.random() < .6 else "SUP-INK-B", "STD")
    if p["finish"] == "ENIG":
        plan["SF_CHEM"] = ("SUP-CHEM-A", "STD")
    return plan


# ---------------------------------------------------------------- production
LOAD_SIZE = {"LAM": 80, "DRILL": 100, "PLATE": 60, "SF": 60, "PACK": 50}
LOTS = {}
RUNS = {}
RUNS_BY_LOT = defaultdict(list)
RUNS_BY_EQ = defaultdict(list)
day = P("2024-11-04")
lotn = 0
while day <= P("2025-12-12"):
    if day.weekday() != 6:
        for p in C.PRODUCTS:
            x = p["weight"] * 5.4
            n = int(x) + (1 if rng.random() < x - int(x) else 0)
            for _ in range(n):
                lotn += 1
                lid = f"LT-{lotn:05d}"
                qty = rng.randrange(120, 250, 10)
                st = day + H(rng.choice([7, 9, 13, 15, 19]) + U(0, 1.5))
                LOTS[lid] = dict(id=lid, spec=p["spec_id"], start=st, qty=qty, mat={})
    day += Dd(1)

for lid, L in LOTS.items():
    p = PROD[L["spec"]]
    for mt, (sup, g) in material_plan(p).items():
        L["mat"][mt] = pick_mlot(mt, sup, g, L["start"])
    t = L["start"]
    for pid, name, grp, gs, unit, insp, hours, eqs in C.PROCESSES:
        if pid == "IQC":
            continue
        t = t + H(U(1.5, 9))
        cands = C.SF_BY_FINISH[p["finish"]] if pid == "SF" else eqs
        shift = lambda d: "A" if 6 <= d.hour < 14 else ("B" if 14 <= d.hour < 22 else "C")
        if unit == "LOAD":
            size = LOAD_SIZE[pid]
            nload = math.ceil(L["qty"] / size)
            ends = []
            for k in range(nload):
                a, b = k * size + 1, min(L["qty"], (k + 1) * size)
                eq = rng.choice(cands)
                s0 = t + H(k * U(0.3, 1.2))
                e0 = s0 + H(hours * U(0.8, 1.2))
                rid = nid("RUN", 8)
                RUNS[rid] = dict(id=rid, lot=lid, proc=pid, eq=eq, load=k + 1, a=a, b=b, recipe=f"{pid}-R4", s=s0, e=e0,
                                 shift=shift(s0), result="COMPLETED")
                ends.append(e0)
            t = max(ends)
        else:
            eq = rng.choice(cands)
            e0 = t + H(hours * U(0.8, 1.2) * (1 + L["qty"] / 400))
            rid = nid("RUN", 8)
            RUNS[rid] = dict(id=rid, lot=lid, proc=pid, eq=eq, load=1, a=1, b=L["qty"], recipe=f"{pid}-R4", s=t, e=e0,
                             shift=shift(t), result="COMPLETED")
            t = e0
    L["complete"] = t
    for rid in [r for r in RUNS if False]:
        pass
for rid, r in RUNS.items():
    RUNS_BY_LOT[r["lot"]].append(rid)
    RUNS_BY_EQ[r["eq"]].append(rid)


def lot_runs(lid, proc):
    return [RUNS[x] for x in RUNS_BY_LOT[lid] if RUNS[x]["proc"] == proc]


# panels (scrap determined here)
PANEL_SCRAP = {}
for lid, L in LOTS.items():
    bpp = PROD[L["spec"]]["bpp"]
    L["scrap"] = set(i for i in range(1, L["qty"] + 1) if rng.random() < 0.008)


def date_code(d):
    y, w, _ = d.isocalendar()
    return f"{y % 100:02d}{w:02d}"


# ---------------------------------------------------------------- shipments
SHIPLOTS = {}   # id -> dict
for lid, L in LOTS.items():
    oqc = lot_runs(lid, "OQC")[0]
    ship_at = bday(oqc["e"] + H(U(4, 60)))
    L["ship_at"] = ship_at
    custs = PROD[L["spec"]]["customers"]
    if ship_at > ASOF:
        continue
    n = 1 if L["qty"] < 160 or rng.random() < .5 else 2
    cut = [1, L["qty"] + 1] if n == 1 else [1, rng.randint(L["qty"] // 3, 2 * L["qty"] // 3), L["qty"] + 1]
    chosen = rng.sample(custs, n)
    for k in range(n):
        a, b = cut[k], cut[k + 1] - 1
        good = [i for i in range(a, b + 1) if i not in L["scrap"]]
        cu = chosen[k]
        overseas = CUST[cu][3] == "해외"
        sid = nid("SL", 7)
        SHIPLOTS[sid] = dict(id=sid, lot=lid, cust=cu, a=a, b=b, panels=len(good), ship=ship_at + H(k * 2),
                             deliv=bday(ship_at + Dd(U(5, 10) if overseas else U(0.6, 2.2))))
ACTIVE_SHIP = dict(SHIPLOTS)

# ---------------------------------------------------------------- scenario injection
SCN = {s["id"]: s for s in C.SCENARIOS}
INJ = {}       # scenario id -> {lot_id: (a,b)}
INJ_RUNS = {}  # scenario id -> set(run ids) carrying the shifted condition


def select_injection(s, end_override=None):
    sel = s["selector"]
    start = P(sel["start"])
    end = P(sel["end"]) + Dd(1)
    if end_override is not None:
        end = min(end, end_override)
    lots, runs = {}, set()
    if sel["kind"] in ("equipment", "condition"):
        for eq in sel["equipment"]:
            for rid in RUNS_BY_EQ[eq]:
                r = RUNS[rid]
                if not (start <= r["s"] < end):
                    continue
                L = LOTS[r["lot"]]
                if sel.get("products") and L["spec"] not in sel["products"]:
                    continue
                if sel["kind"] == "condition" or sel.get("fraction"):
                    hrng = random.Random(hash((s["id"], rid)) & 0xffffffff)
                    if hrng.random() > sel.get("fraction", 1.0):
                        continue
                runs.add(rid)
                a, b = lots.get(r["lot"], (r["a"], r["b"]))
                lots[r["lot"]] = (min(a, r["a"]), max(b, r["b"]))
    else:
        bad = set()
        for (mt, sup, g), lst in MLOTS.items():
            if mt == sel["material"] and sup == sel["supplier"]:
                for rec, mid in lst:
                    if start <= rec < end:
                        bad.add(mid)
        s["_bad_mlots"] = sorted(bad)
        for lid, L in LOTS.items():
            if L["mat"].get(sel["material"]) in bad:
                if end_override is not None and L["start"] >= end_override:
                    continue
                lots[lid] = (1, L["qty"])
    return lots, runs


def claim_weight_factor(s, lid):
    sel = s["selector"]
    if sel.get("escape_equipment"):
        et = lot_runs(lid, "ET")[0]["eq"]
        return 1.0 if et == sel["escape_equipment"] else 0.12
    return 1.0


CLAIMS = []   # dicts
TIMELINE = {}


def detection_lag(stage):
    lo, hi = C.DETECTION[stage][1]
    return U(lo, hi)


def make_claim_candidates(s, lots, n, avoid=None):
    """sample claim candidates from active shipments of impacted lots"""
    pool = []
    for sid, sl in ACTIVE_SHIP.items():
        if sl["lot"] in lots and LOTS[sl["lot"]]["spec"] in s["claim_products"]:
            a, b = lots[sl["lot"]]
            lo, hi = max(a, sl["a"]), min(b, sl["b"])
            if lo > hi:
                continue
            w = (hi - lo + 1) * PROD[LOTS[sl["lot"]]["spec"]]["bpp"] * claim_weight_factor(s, sl["lot"])
            pool.append((sid, lo, hi, w))
    if not pool:
        return []
    tot = sum(x[3] for x in pool)
    cum = []
    acc = 0
    for x in pool:
        acc += x[3]
        cum.append(acc)
    out = []
    tries = 0
    while len(out) < n and tries < n * 30:
        tries += 1
        i = bisect.bisect_left(cum, rng.random() * tot)
        sid, lo, hi, _ = pool[i]
        sl = ACTIVE_SHIP[sid]
        stage = wchoice(s["detection"])
        occ = sl["deliv"] + Dd(detection_lag(stage)) + H(U(0, 10))
        opened = bday(occ + H(U(4, 60)))
        if opened > ASOF - H(2):
            continue
        L = LOTS[sl["lot"]]
        seqs = [q for q in range(lo, hi + 1) if q not in L["scrap"]]
        if not seqs:
            continue
        out.append(dict(ship=sid, lot=sl["lot"], cust=sl["cust"], panel_seq=rng.choice(seqs), stage=stage, occ=occ,
                        opened=opened, symptom=wchoice(s["symptoms"])))
    return out


def plan_timeline(s, t0, lots):
    """returns investigation milestone dates"""
    kind = s["selector"]["kind"]
    fieldish = any(st in ("FIELD", "CUSTOMER_RELIABILITY") for st, w in s["detection"] if w >= .3)
    sources = {"equipment": ["MES 공정 이력", "설비 로그", "SPC"], "condition": ["MES 공정 이력", "SPC", "약품·욕 분석 기록"],
               "material": ["MES 자재 투입 이력", "IQC 기록", "공급사 성적서(CoA)", "구매 입고 기록"]}[kind][:]
    if fieldish:
        sources.append("고객 시험·필드 회수 결과")
    if any(v for v in lots.values() if v[0] != 1):
        sources.append("로드별 작업 기록")
    dc = 1.5 + 1.3 * len(sources) + U(0, 3)
    lt = 2 + 0.8 * len(sources) + U(0, 3) + (2 if kind == "material" else 0)
    hv = U(6, 16) + (5 if fieldish else 0)
    T = dict(t0=t0, sources=sources)
    T["dc_start"] = t0 + H(U(3, 12))
    T["dc_end"] = T["dc_start"] + Dd(dc)
    T["decision"] = T["dc_end"] + Dd(lt)
    T["rc"] = T["decision"] + Dd(hv)
    T["ca_select"] = T["rc"] + Dd(U(2, 5))
    T["impl"] = T["ca_select"] + Dd(U(6, 18))
    T["esc_impl"] = T["ca_select"] + Dd(U(4, 14))
    T["d7"] = T["impl"] + Dd(U(10, 25))
    T["eff_end"] = T["impl"] + Dd(U(35, 60))
    T["d8"] = max(T["eff_end"], T["d7"]) + Dd(U(1, 4))
    return T


scen_order = sorted(C.SCENARIOS, key=lambda s: P(s["selector"]["start"]))
HELD = defaultdict(list)   # lot -> list of (scenario, shiplot dict, decision)
for s in scen_order:
    lots, runs = select_injection(s)
    cand = make_claim_candidates(s, lots, int(s["claims"] * 3))
    if not cand:
        INJ[s["id"]], INJ_RUNS[s["id"]] = lots, runs
        continue
    t0 = min(c["opened"] for c in cand)
    T = plan_timeline(s, t0, lots)
    # issue persists until the natural fix (config end) or root cause confirmation, whichever first
    lots, runs = select_injection(s, end_override=T["rc"])
    # lots not yet shipped at the lot-impact decision are held
    for sid, sl in list(ACTIVE_SHIP.items()):
        if sl["lot"] in lots and sl["ship"] > T["decision"]:
            HELD[sl["lot"]].append((s["id"], sl, T["decision"]))
            del ACTIVE_SHIP[sid]
    cand = [c for c in cand if c["lot"] in lots and c["ship"] in ACTIVE_SHIP]
    first = min(cand, key=lambda c: c["opened"])
    rest = [c for c in cand if c is not first]
    rng.shuffle(rest)
    chosen = [first] + rest[: s["claims"] - 1]
    T["t0"] = first["opened"]
    INJ[s["id"]], INJ_RUNS[s["id"]] = lots, runs
    TIMELINE[s["id"]] = T
    for c in chosen:
        c.update(kind="SCENARIO", scenario=s["id"], cause=s["cause"])
        CLAIMS.append(c)

INJECTED_LOTS = set()
for v in INJ.values():
    INJECTED_LOTS |= set(v)

# background claims
n_scen = len(CLAIMS)
n_spor = C.TOTAL_CLAIMS - n_scen - C.N_NTF - C.N_NTF_MEAS
clean_ship = [sl for sl in ACTIVE_SHIP.values() if sl["lot"] not in INJECTED_LOTS and sl["panels"] > 0]
used_lots = Counter()


def background(kind, n, symptom_fn, spec_filter=None):
    pool = [sl for sl in clean_ship if (spec_filter is None or LOTS[sl["lot"]]["spec"] in spec_filter)]
    made = 0
    while made < n:
        sl = rng.choice(pool)
        if used_lots[sl["lot"]] >= 1 and rng.random() < .85:
            continue
        sym, cause = symptom_fn()
        stage = rng.choice(["CUSTOMER_IQC", "SMT_REFLOW", "ICT_FUNCTION"]) if kind != "NTF_MEAS" else rng.choice(["CUSTOMER_IQC", "ICT_FUNCTION"])
        occ = sl["deliv"] + Dd(detection_lag(stage)) + H(U(0, 10))
        opened = bday(occ + H(U(4, 60)))
        if opened > ASOF - H(2) or opened < P("2025-01-06"):
            continue
        L = LOTS[sl["lot"]]
        seqs = [q for q in range(sl["a"], sl["b"] + 1) if q not in L["scrap"]]
        used_lots[sl["lot"]] += 1
        CLAIMS.append(dict(ship=sl["id"], lot=sl["lot"], cust=sl["cust"], panel_seq=rng.choice(seqs), stage=stage, occ=occ,
                           opened=opened, symptom=sym, kind=kind, scenario=None, cause=cause))
        made += 1


def spor():
    sym = wchoice(list(C.SPORADIC_WEIGHTS.items()))
    return sym, wchoice(C.SPORADIC[sym])


def ntf():
    sym = rng.choice(["ELEC01", "ELEC02", "APPR03", "SOLD01", "STRC01", "DIM01", "ELEC04", "APPR01"])
    return sym, "NO_DEFECT_FOUND"


background("SPORADIC", n_spor, spor)
background("NTF", C.N_NTF, ntf)
background("NTF_MEAS", C.N_NTF_MEAS, lambda: ("ELEC04", "NO_DEFECT_FOUND"), spec_filter=["AUTO-RADAR8", "SEMI-MEM10"])
CLAIMS.sort(key=lambda c: c["opened"])
assert len(CLAIMS) == C.TOTAL_CLAIMS, len(CLAIMS)

# ---------------------------------------------------------------- observations & equipment events
RUN_SHIFT = defaultdict(dict)   # run -> {item: (delta, sd)}
for sid_, s in SCN.items():
    sel = s["selector"]
    T = TIMELINE.get(sid_)
    endv = min(P(sel["end"]) + Dd(1), T["rc"]) if T else P(sel["end"]) + Dd(1)
    s["_eff_end"] = endv
    if sel["kind"] in ("equipment", "condition"):
        for rid in INJ_RUNS[sid_]:
            for item, (d, sd) in s["shifts"].items():
                RUN_SHIFT[rid][item] = (d, sd)
            if sel.get("recipe"):
                RUNS[rid]["recipe"] = sel["recipe"]
    if sel.get("aoi_recipe"):
        for eq in PROC["OL_AOI"]["eqs"]:
            for rid in RUNS_BY_EQ[eq]:
                if P(sel["start"]) <= RUNS[rid]["s"] < endv:
                    RUN_SHIFT[rid]["aoi_sensitivity_level"] = (-2, 0)
                    RUN_SHIFT[rid]["aoi_defects_per_panel"] = (-0.55, 0.05)
                    RUNS[rid]["recipe"] = "OL_AOI-R5-LOWSENS"

for rid, r in RUNS.items():
    plist = C.PARAMS.get(r["proc"], [])
    fin = PROD[LOTS[r["lot"]]["spec"]]["finish"]
    for item, unit, tgt, lo, hi, sd, applies, ch in plist:
        if applies != "ALL" and applies != fin:
            continue
        v = rng.gauss(tgt, sd) if sd else tgt
        if item in RUN_SHIFT[rid]:
            d, sd2 = RUN_SHIFT[rid][item]
            v = tgt + d + (rng.gauss(0, sd2) if sd2 else 0)
        else:
            v = min(max(v, lo + (hi - lo) * 0.03), hi - (hi - lo) * 0.03) if hi > lo else v
        if item == "aoi_defects_per_panel":
            v = max(v, 0.05)
        v = round(v, 4)
        flag = "OUT_OF_SPEC" if (v < lo or v > hi) else "NORMAL"
        add("process_observation", observation_id=nid("OBS", 8), process_run_id=rid, item_code=item, value=v, unit=unit,
            target=tgt, lower_limit=lo, upper_limit=hi, measured_at=I(r["s"] + (r["e"] - r["s"]) * 0.6), spc_flag=flag)

# baseline equipment events
for proc, info in PROC.items():
    for eq in info["eqs"]:
        d = P("2024-11-11") + Dd(rng.randint(0, 20))
        while d < P("2025-12-20"):
            add("equipment_event", event_id=nid("EVT", 6), equipment_id=eq, event_type="PREVENTIVE_MAINTENANCE",
                occurred_at=I(d + H(8)), recorded_at=I(d + H(10)), metric_name=None, metric_value=None, metric_unit=None,
                description="정기 예방보전", result="COMPLETED")
            d += Dd(28 + rng.randint(-3, 3))
        if proc in ("LAM", "ROUT", "ET", "DRILL"):
            d = P("2024-12-02") + Dd(rng.randint(0, 30))
            while d < P("2025-12-20"):
                metric = {"LAM": ("thermocouple_offset_c", round(rng.gauss(0, 0.6), 2), "C"),
                          "ROUT": ("origin_offset_um", round(rng.gauss(0, 6), 1), "um"),
                          "ET": ("golden_board_result", 1, "boolean"),
                          "DRILL": ("spindle_runout_um", round(U(2, 6), 1), "um")}[proc]
                add("equipment_event", event_id=nid("EVT", 6), equipment_id=eq, event_type="CALIBRATION",
                    occurred_at=I(d + H(9)), recorded_at=I(d + H(11)), metric_name=metric[0], metric_value=metric[1],
                    metric_unit=metric[2], description="정기 교정", result="WITHIN_TOLERANCE")
                d += Dd(91 if proc == "LAM" else 60)
SCEN_EVENTS = defaultdict(list)
for sid_, s in SCN.items():
    T = TIMELINE.get(sid_)
    for eq, et, when, rec, mname, mval, munit, desc in s.get("events", []):
        occ = P(s["selector"]["start"]) + H(7) if when == "start" else s["_eff_end"] - H(U(4, 10))
        recd = occ + H(2) if rec == "same" else (T["decision"] + Dd(U(1, 4)) if T else occ + Dd(30))
        ev = add("equipment_event", event_id=nid("EVT", 6), equipment_id=eq, event_type=et, occurred_at=I(occ),
                 recorded_at=I(recd), metric_name=mname, metric_value=mval, metric_unit=munit, description=desc,
                 result="ABNORMAL" if et not in ("REPAIR", "PREVENTIVE_MAINTENANCE") else "COMPLETED")
        SCEN_EVENTS[sid_].append(ev)

# ---------------------------------------------------------------- inspection plans
BASE_PLANS = [
    ("PLAN-ET", "ET", ["ET_OPEN", "ET_SHORT"], "ALL", "PANEL_100PCT", 1.0),
    ("PLAN-IAOI", "IN_PROCESS", ["IAOI_DEFECT"], "ALL", "LOT_100PCT", 1.0),
    ("PLAN-OAOI", "IN_PROCESS", ["OAOI_DEFECT"], "ALL", "LOT_100PCT", 1.0),
    ("PLAN-FQC-XSEC", "FQC", ["FQC_XSEC_HOLE_CU"], "ALL", "PER_LOT_COUPON_3HOLES", 1.0),
    ("PLAN-FQC-XRF", "FQC", ["FQC_XRF_NI", "FQC_XRF_AU"], "ENIG", "PER_LOT_5POINTS", 1.0),
    ("PLAN-FQC-TDR", "FQC", ["FQC_TDR_IMP"], "IMPEDANCE", "PER_LOT_EDGE_COUPON", 1.0),
    ("PLAN-FQC-FLOAT", "FQC", ["FQC_SOLDER_FLOAT"], "ALL", "PER_LOT_1COUPON", 1.0),
    ("PLAN-FQC-WARP", "FQC", ["FQC_WARPAGE"], "ALL", "PER_LOT_3PANELS_ROOM_TEMP", 1.0),
    ("PLAN-FQC-SM", "FQC", ["FQC_SM_TAPE"], "ALL", "PER_LOT_1PANEL", 1.0),
    ("PLAN-FQC-DIM", "FQC", ["FQC_DIM"], "ALL", "PER_LOT_2PANELS", 1.0),
    ("PLAN-FQC-SOLDER", "FQC", ["FQC_SOLDERABILITY"], "ALL", "PER_LOT_1COUPON", 1.0),
    ("PLAN-FQC-SN", "FQC", ["FQC_SN_THK"], "ImSn", "WEEKLY_FIRST_LOT", 1.0),
    ("PLAN-FQC-IONIC", "FQC", ["FQC_IONIC"], "ALL", "WEEKLY_FIRST_LOT", 1.0),
    ("PLAN-FQC-PEEL", "FQC", ["FQC_PEEL"], "ALL", "WEEKLY_FIRST_LOT", 1.0),
    ("PLAN-FQC-MOIST", "FQC", ["FQC_MOISTURE"], "ALL", "PER_LOT_PRE_PACK", 1.0),
    ("PLAN-OQC-LABEL", "OQC", ["OQC_LABEL_MATCH"], "ALL", "PER_LOT_SCAN", 1.0),
]
# escape corrective actions -> plan revisions/additions
ESC_PLAN = {
    "P01": ("PLAN-FQC-IST", "FQC", ["FQC_IST"], "AUTOMOTIVE", "PER_LOT_IST_COUPON", 1.0, "차량용 제품 IST 열사이클 샘플 시험 신설"),
    "P02": ("PLAN-FQC-FLOAT", "FQC", ["FQC_SOLDER_FLOAT"], "ALL", "PER_LOAD_1COUPON", 1.0, "솔더 플로트 쿠폰 로드별 채취"),
    "P04": ("PLAN-FQC-XSEC", "FQC", ["FQC_XSEC_HOLE_CU"], "ALL", "PER_LOT_COUPON_3HOLES", 1.0, "단면 검사 Lot별 실시 환원"),
    "P05": ("PLAN-FQC-XSEC", "FQC", ["FQC_XSEC_HOLE_CU"], "ALL", "PER_LOT_COUPON_RACK_POSITIONS_3", 1.0, "도금 랙 상·중·하 위치별 단면 쿠폰 채취"),
    "P06": ("PLAN-FQC-NICORR", "FQC", ["FQC_NI_CORR"], "ENIG", "PER_LOT_SEM", 1.0, "ENIG Ni 부식 단면 SEM Lot별 시험 신설"),
    "P07": ("PLAN-IQC-CCL", "IQC", ["IQC_CCL_VOID"], "CCL", "PER_MATERIAL_LOT", 1.0, "CCL 수입 단면 보이드 실측 신설"),
    "P08": ("PLAN-IQC-PP", "IQC", ["IQC_PP_FLOW"], "PREPREG", "PER_MATERIAL_LOT", 1.0, "프리프레그 resin flow 실측 신설"),
    "P09": ("PLAN-FQC-SM", "FQC", ["FQC_SM_TAPE"], "ALL", "PER_LOT_3PANELS_3POSITIONS", 1.0, "PSR 테이프 시험 시료 확대"),
    "P10": ("PLAN-IQC-CU", "IQC", ["IQC_CU_PEEL"], "CU_FOIL", "PER_MATERIAL_LOT", 1.0, "동박 박리 강도 실측 신설"),
    "P12": ("PLAN-FQC-TDR", "FQC", ["FQC_TDR_IMP"], "IMPEDANCE", "PER_LOT_EDGE_AND_CENTER_COUPON", 1.0, "TDR 쿠폰 중앙부 추가"),
    "P13": ("PLAN-ET-GOLDEN", "ET", ["ET_SHORT"], "ALL", "DAILY_GOLDEN_BOARD", 1.0, "픽스처 일일 골든 보드 검증 신설"),
    "P16": ("PLAN-FQC-DIM", "FQC", ["FQC_DIM"], "ALL", "FIRST_ARTICLE_AFTER_MAINTENANCE_PLUS_PER_LOT", 1.0, "보전 후 초품 치수 승인 추가"),
    "P17": ("PLAN-FQC-IONIC", "FQC", ["FQC_IONIC"], "ALL", "PER_LOT", 1.0, "이온 오염 시험 Lot별 실시"),
    "P18": ("PLAN-OQC-BAG", "OQC", ["OQC_BAG_LEAK"], "ALL", "PER_LOT_ALL_BAGS", 1.0, "출하검사 진공 유지·HIC 확인 신설"),
    "P19": ("PLAN-OQC-LABEL", "OQC", ["OQC_LABEL_MATCH"], "ALL", "PER_LOT_SCAN_INTERLOCK", 1.0, "스캔 미완료 시 출하 불가 인터록"),
    "P20": ("PLAN-FQC-WARPHOT", "FQC", ["FQC_WARPAGE_HOT"], "THIN", "PER_LOT_SHADOW_MOIRE", 1.0, "박판 제품 고온 휨 측정 신설"),
    "P21": ("PLAN-FQC-SN", "FQC", ["FQC_SN_THK"], "ImSn", "PER_LOT", 1.0, "주석 두께 Lot별 측정"),
}
PLANS = defaultdict(list)    # code -> list of plan dicts sorted by eff
for code, phase, specs, applies, rule, rate in BASE_PLANS:
    PLANS[code].append(dict(plan_id=f"{code}-REV1", plan_code=code, phase=phase, specs=specs, applies=applies, rule=rule, rate=rate,
                            rev=1, eff=P("2024-01-01"), to=None, reason="기준 검사 계획", ca=None))


def revise_plan(code, phase, specs, applies, rule, rate, eff, reason, ca=None):
    lst = PLANS[code]
    if lst:
        lst[-1]["to"] = eff
    rev = len(lst) + 1
    lst.append(dict(plan_id=f"{code}-REV{rev}", plan_code=code, phase=phase, specs=specs, applies=applies, rule=rule, rate=rate,
                    rev=rev, eff=eff, to=None, reason=reason, ca=ca))


revise_plan("PLAN-FQC-XSEC", "FQC", ["FQC_XSEC_HOLE_CU"], "ALL", "EVERY_5TH_LOT", 0.2, P("2025-05-26"),
            "원가 절감 목적 단면 검사 5 Lot당 1회로 축소")
ESC_PLAN_EFF = {}
for sid_, T in sorted(TIMELINE.items(), key=lambda x: x[1]["esc_impl"]):
    if sid_ in ESC_PLAN and T["esc_impl"] <= ASOF:
        code, phase, specs, applies, rule, rate, reason = ESC_PLAN[sid_]
        ESC_PLAN_EFF[sid_] = T["esc_impl"]
        revise_plan(code, phase, specs, applies, rule, rate, T["esc_impl"], reason, ca=f"__ESC__{sid_}")


def active_plans(when, spec):
    p = PROD[spec]
    out = []
    for code, lst in PLANS.items():
        for pl in lst:
            if pl["eff"] <= when and (pl["to"] is None or when < pl["to"]):
                a = pl["applies"]
                ok = (a == "ALL" or a == p["finish"] or (a == "IMPEDANCE" and p["imp"]) or (a == "AUTOMOTIVE" and p["application"] == "AUTOMOTIVE")
                      or (a == "THIN" and p["thickness"] <= 1.0))
                if ok and pl["phase"] != "IQC":
                    out.append(pl)
    return out


# ---------------------------------------------------------------- production tests
FQC_NORMAL = {"FQC_XSEC_HOLE_CU": (25, 1.6), "FQC_XRF_NI": (4.6, .3), "FQC_XRF_AU": (.072, .005), "FQC_TDR_IMP": (50, 1.3),
              "FQC_SOLDER_FLOAT": (0, 0), "FQC_IONIC": (.6, .18), "FQC_WARPAGE": (.42, .07), "FQC_WARPAGE_HOT": (.5, .07),
              "FQC_PEEL": (1.35, .07), "FQC_SM_TAPE": (0, 0), "FQC_DIM": (0, 22), "FQC_SOLDERABILITY": (98.5, .5),
              "FQC_SN_THK": (1.05, .05), "FQC_IST": (3.5, 1.3), "FQC_NI_CORR": (0.4, .3), "FQC_MOISTURE": (.04, .01),
              "OQC_LABEL_MATCH": (1, 0), "OQC_BAG_LEAK": (0, 0), "ET_SHORT": (0, 0)}
LOT_SCEN = defaultdict(set)
for sid_, lots in INJ.items():
    for lid in lots:
        LOT_SCEN[lid].add(sid_)


def clipnorm(spec, mu, sd):
    t = TSPEC[spec]
    v = rng.gauss(mu, sd) if sd else mu
    lo, hi = t[5], t[6]
    if lo is not None and hi is not None and lo < hi:
        v = min(max(v, lo + (hi - lo) * .05), hi - (hi - lo) * .05)
    elif lo is not None:
        v = max(v, lo * 1.03 if lo > 0 else lo + .1)
    elif hi is not None and hi > 0:
        v = min(v, hi * .9)
    return round(v, 4)


def fqc_value(spec, lid):
    sc = LOT_SCEN.get(lid, set())
    mu, sd = FQC_NORMAL[spec]
    if spec == "FQC_TDR_IMP" and "P12" in sc:
        mu = 46.8
    if spec == "FQC_WARPAGE" and "P20" in sc:
        mu, sd = .63, .04
    if spec == "FQC_WARPAGE_HOT" and "P20" in sc:
        return round(U(.9, 1.3), 3)
    if spec == "FQC_IONIC" and "P17" in sc:
        mu, sd = 1.25, .12
    return clipnorm(spec, mu, sd)


def new_test(phase, subject_type, subject_id, lot, start, dur_h, specs_vals, equipment=None, plan=None, run=None,
             rma_item=None, inv=None, origin="FACTORY_RECORD", verified=1, override=None):
    tid = nid("TEST", 8)
    results = []
    for spec, trial, val, cond in specs_vals:
        res = cmp_result(spec, val)
        if override and spec in override:
            res = override[spec]
        results.append(res)
        add("test_measurement", measurement_id=nid("MEAS", 8), test_run_id=tid, test_spec_id=spec, trial_index=trial,
            raw_value=val, unit=TSPEC[spec][4], condition_json=json.dumps(cond, ensure_ascii=False), result=res)
    overall = "FAIL" if "FAIL" in results else ("NOT_PERFORMED" if results and all(r == "NOT_PERFORMED" for r in results)
                                                 else ("INCOMPLETE" if "NOT_PERFORMED" in results else "PASS"))
    add("test_run", test_run_id=tid, phase=phase, subject_type=subject_type, subject_id=subject_id, lot_id=lot,
        rma_item_id=rma_item, investigation_id=inv, process_run_id=run, equipment_id=equipment, plan_id=plan,
        sample_origin=origin, origin_verified=verified, started_at=I(start), ended_at=I(start + H(dur_h)), overall_result=overall)
    return tid, overall


ET_OVERRIDE_PANELS = {}
for c in CLAIMS:
    if c["cause"] == "ET_RETEST_MISJUDGE":
        ET_OVERRIDE_PANELS[(c["lot"], c["panel_seq"])] = c["symptom"]

LOT_TESTS = defaultdict(dict)   # lot -> {plan_code: test_run_id}
week_seen = set()
PANELS = {}
for lid, L in sorted(LOTS.items(), key=lambda x: x[1]["start"]):
    spec = L["spec"]
    p = PROD[spec]
    bpp = p["bpp"]
    sc = LOT_SCEN.get(lid, set())
    # AOI
    for proc, ts in (("IL_AOI", "IAOI_DEFECT"), ("OL_AOI", "OAOI_DEFECT")):
        r = lot_runs(lid, proc)[0]
        base = 0.8 if proc == "IL_AOI" else 0.9
        d = RUN_SHIFT[r["id"]].get("aoi_defects_per_panel")
        v = round(max(.05, base + (d[0] if d else 0) + rng.gauss(0, .2)), 3)
        new_test("IN_PROCESS", "LOT", lid, lid, r["s"] + H(.5), 2, [(ts, 1, v, {"recipe": r["recipe"]})], equipment=r["eq"],
                 plan=PLANS["PLAN-IAOI" if proc == "IL_AOI" else "PLAN-OAOI"][0]["plan_id"], run=r["id"])
    # ET per panel
    etr = lot_runs(lid, "ET")[0]
    et_eq = etr["eq"]
    p13 = "P13" in sc
    a13, b13 = INJ["P13"].get(lid, (0, -1)) if p13 else (0, -1)
    for q in range(1, L["qty"] + 1):
        pid_ = f"{lid}-P{q:03d}"
        scrap = q in L["scrap"]
        op = 1 if rng.random() < .012 else 0
        sh = 1 if rng.random() < .009 else 0
        if p13 and a13 <= q <= b13:
            real_short = rng.random() < .11
            if real_short:
                sh = 1 if (et_eq != "ET-02" or rng.random() < .1) else 0
        if scrap:
            op, sh = rng.randint(3, 9), rng.randint(0, 3)
        t_at = etr["s"] + H(8 * q / L["qty"])
        meas = [("ET_OPEN", 1, float(op), {"program": f"{p['part_no']}-ET-R3"}),
                ("ET_SHORT", 1, float(sh), {"program": f"{p['part_no']}-ET-R3"})]
        ov = None
        if (lid, q) in ET_OVERRIDE_PANELS:
            sym = ET_OVERRIDE_PANELS[(lid, q)]
            meas = [("ET_OPEN", 1, 1.0 if sym == "ELEC01" else 0.0, {"program": f"{p['part_no']}-ET-R3"}),
                    ("ET_SHORT", 1, 1.0 if sym == "ELEC02" else 0.0, {"program": f"{p['part_no']}-ET-R3"}),
                    ("ET_OPEN", 2, 0.0, {"retest": True, "judgement": "MANUAL_OVERRIDE"}),
                    ("ET_SHORT", 2, 0.0, {"retest": True, "judgement": "MANUAL_OVERRIDE"})]
            ov = None
        tid, res = new_test("ET", "PANEL", pid_, lid, t_at, 0.03, meas, equipment=et_eq, plan=PLANS["PLAN-ET"][0]["plan_id"], run=etr["id"])
        if (lid, q) in ET_OVERRIDE_PANELS:
            R["test_run"][-1]["overall_result"] = "PASS_BY_OVERRIDE"
        xo = 0 if scrap else min(bpp - 1, op + sh) if (op + sh) else 0
        PANELS[pid_] = dict(panel_id=pid_, lot_id=lid, panel_seq=q, boards_per_panel=bpp, xout_boards=bpp if scrap else xo,
                            disposition="SCRAPPED" if scrap else "SHIPPED")
    # FQC / OQC
    fq = lot_runs(lid, "FQC")[0]
    oq = lot_runs(lid, "OQC")[0]
    wk = (spec, fq["s"].isocalendar()[:2])
    first_of_week = wk not in week_seen
    week_seen.add(wk)
    for pl in active_plans(fq["s"], spec):
        if pl["phase"] == "ET":
            continue
        if pl["phase"] == "IN_PROCESS":
            continue
        rule = pl["rule"]
        if rule.startswith("WEEKLY") and not first_of_week:
            continue
        if rule == "EVERY_5TH_LOT" and int(lid[-5:]) % 5 != 0:
            continue
        if rule == "DAILY_GOLDEN_BOARD":
            continue
        vals = []
        for spec_id in pl["specs"]:
            n = 3 if spec_id in ("FQC_XSEC_HOLE_CU", "FQC_WARPAGE") else (5 if spec_id.startswith("FQC_XRF") else (2 if spec_id in ("FQC_TDR_IMP", "FQC_DIM") else 1))
            for k in range(1, n + 1):
                if pl["phase"] == "OQC" and spec_id == "OQC_LABEL_MATCH" and "P19" in sc and rule == "PER_LOT_SCAN":
                    vals.append((spec_id, k, None, {"note": "교대 중 스캔 미실시"}))
                else:
                    vals.append((spec_id, k, fqc_value(spec_id, lid), {"rule": rule}))
        run = oq if pl["phase"] == "OQC" else fq
        tid, res = new_test(pl["phase"], "COUPON" if pl["phase"] == "FQC" else "LOT", lid, lid, run["s"] + H(U(.2, 2)), 1.5, vals,
                            equipment=run["eq"], plan=pl["plan_id"], run=run["id"])
        LOT_TESTS[lid][pl["plan_code"]] = tid

# IQC tests on material lots after IQC plan revisions
IQC_SPEC = {"PLAN-IQC-CCL": ("CCL", "IQC_CCL_VOID", (0, 0)), "PLAN-IQC-PP": ("PREPREG", "IQC_PP_FLOW", (26, 1.5)),
            "PLAN-IQC-CU": ("CU_FOIL", "IQC_CU_PEEL", (1.33, .06))}
IQC_METHOD_FROM = {}
for code, (mt, spec_id, (mu, sd)) in IQC_SPEC.items():
    if code in PLANS:
        pl = PLANS[code][0]
        IQC_METHOD_FROM[mt] = pl["eff"]
        for mid, m in MLOT.items():
            if m["type"] == mt and m["received"] >= pl["eff"]:
                new_test("IQC", "MATERIAL_LOT", mid, None, m["received"] + H(3), 2, [(spec_id, 1, clipnorm(spec_id, mu, sd), {"sample": "incoming"})],
                         equipment="IQC-01", plan=pl["plan_id"])
BAD_MLOT = {}
for sid_, s in SCN.items():
    for mid in s.get("_bad_mlots", []):
        BAD_MLOT[mid] = sid_
for mid, m in MLOT.items():
    meth = "COA_REVIEW+INCOMING_TEST" if m["type"] in IQC_METHOD_FROM and m["received"] >= IQC_METHOD_FROM[m["type"]] else "COA_REVIEW"
    status = "IN_USE_OR_CONSUMED"
    if mid in BAD_MLOT:
        T = TIMELINE.get(BAD_MLOT[mid])
        status = "QUARANTINED_RETURNED_TO_SUPPLIER" if T and T["decision"] <= ASOF else status
    add("material_lot", material_lot_id=mid, material_type=m["type"], supplier_id=m["sup"], grade=m["grade"],
        received_at=I(m["received"]), qty=rng.randint(20, 60) * (50 if m["type"] in ("CCL",) else 1), unit=C.MATERIALS[m["type"]][3],
        coa_json=json.dumps(coa(m["type"]), ensure_ascii=False), iqc_method=meth, iqc_result="ACCEPTED", iqc_at=I(m["received"] + H(4)),
        usage_status=status)

# ---------------------------------------------------------------- write production tables
for lid, L in LOTS.items():
    status = "SHIPPED" if L["ship_at"] <= ASOF else ("FG_STOCK" if L["complete"] <= ASOF else "WIP")
    add("production_lot", lot_id=lid, spec_id=L["spec"], work_order_id=f"WO-{lid[3:]}", date_code=date_code(L["complete"]),
        started_at=I(L["start"]), completed_at=I(L["complete"]), panel_qty=L["qty"], status=status)
    for mt, mid in L["mat"].items():
        add("material_usage", usage_id=nid("MU", 7), lot_id=lid, material_lot_id=mid, material_type=mt,
            issued_at=I(L["start"] - H(U(2, 20))))
for rid, r in RUNS.items():
    add("process_run", process_run_id=rid, lot_id=r["lot"], process_id=r["proc"], equipment_id=r["eq"], load_no=r["load"],
        panel_seq_from=r["a"], panel_seq_to=r["b"], recipe_version=r["recipe"], started_at=I(r["s"]), ended_at=I(r["e"]),
        shift=r["shift"], result=r["result"])

# shipments + orders
ship_groups = defaultdict(list)
for sid, sl in ACTIVE_SHIP.items():
    ship_groups[(sl["cust"], sl["ship"].date(), LOTS[sl["lot"]]["spec"], sl["deliv"].date())].append(sl)
orders = {}
SHIP_OF = {}
for (cu, d, spec, _dd), sls in sorted(ship_groups.items()):
    okey = (cu, spec, d.year, d.month)
    if okey not in orders:
        oi = nid("OI", 6)
        orders[okey] = oi
        add("order_item", order_item_id=oi, order_id=f"PO-{cu}-{d.year % 100:02d}{d.month:02d}", customer_id=cu, spec_id=spec,
            ordered_qty_boards=0, ordered_at=I(datetime(d.year, d.month, 1, 10, tzinfo=KST) - Dd(21)),
            requested_at=I(datetime(d.year, d.month, 1, 10, tzinfo=KST) + Dd(14)))
    shid = nid("SHIP", 6)
    bq = 0
    for sl in sls:
        boards = sl["panels"] * PROD[spec]["bpp"] - sum(PANELS[f"{sl['lot']}-P{q:03d}"]["xout_boards"] for q in range(sl["a"], sl["b"] + 1)
                                                         if q not in LOTS[sl["lot"]]["scrap"])
        bq += boards
        SHIP_OF[sl["id"]] = shid
        add("shipment_lot", shipment_lot_id=sl["id"], shipment_id=shid, lot_id=sl["lot"], panel_seq_from=sl["a"], panel_seq_to=sl["b"],
            panel_qty=sl["panels"], board_qty=boards)
    add("shipment", shipment_id=shid, customer_id=cu, order_item_id=orders[okey], shipped_at=I(min(x["ship"] for x in sls)),
        delivered_at=I(max(x["deliv"] for x in sls)), board_qty=bq, status="DELIVERED" if max(x["deliv"] for x in sls) <= ASOF else "IN_TRANSIT")
oq_sum = defaultdict(int)
for r in R["shipment"]:
    oq_sum[r["order_item_id"]] += r["board_qty"]
for r in R["order_item"]:
    r["ordered_qty_boards"] = int(math.ceil(oq_sum[r["order_item_id"]] * U(1.0, 1.08) / 100) * 100)
# unshipped lots
shipped_ranges = defaultdict(list)
for sl in ACTIVE_SHIP.values():
    shipped_ranges[sl["lot"]].append((sl["a"], sl["b"]))
for pid_, pn in PANELS.items():
    if pn["disposition"] == "SCRAPPED":
        continue
    rngs = shipped_ranges.get(pn["lot_id"], [])
    if not any(a <= pn["panel_seq"] <= b for a, b in rngs):
        pn["disposition"] = "HOLD" if pn["lot_id"] in HELD else ("FG_STOCK" if LOTS[pn["lot_id"]]["complete"] <= ASOF else "WIP")

print("production", len(LOTS), "lots", len(PANELS), "panels", len(RUNS), "runs", len(ACTIVE_SHIP), "shiplots", flush=True)

# ---------------------------------------------------------------- RMA intake
def level_for(sev, char_sym, industry, repeat):
    if sev >= 9 or char_sym == "CC" or (industry == "AUTOMOTIVE" and sev >= 8):
        return "L3"
    if sev >= 6 or char_sym == "SC" or repeat:
        return "L2"
    return "L1"


recent = defaultdict(list)
for n, c in enumerate(CLAIMS, 1):
    c["rma_id"] = f"RMA-{n:05d}"
    c["item_id"] = f"ITEM-{n:05d}"
for c in CLAIMS:
    cu = c["cust"]
    L = LOTS[c["lot"]]
    p = PROD[L["spec"]]
    sym = c["symptom"]
    sev = SYM[sym][4]
    ch = SYM[sym][5]
    chs = CHAR[ch][2] if ch else None
    key = (cu, sym)
    rep = int(sum(1 for t in recent[key] if (c["opened"] - t) <= Dd(30)) >= 2)
    c["repeat"] = rep
    recent[key].append(c["opened"])
    lvl = level_for(sev, chs, CUST[cu][2], rep)
    c["level"] = lvl
    c["sev"] = sev
    # text
    lot_no, dc = c["lot"], date_code(L["complete"])
    r = rng.random()
    rep_lot = lot_no if r < .8 else None
    rep_dc = dc if r > .1 else None
    inp = rng.randrange(200, 6000, 50)
    if c["kind"] == "SCENARIO":
        dq = max(1, int(inp * U(.002, .03)))
    else:
        dq = rng.randint(1, 8)
    ret = 0
    if lvl in ("L2", "L3") or rng.random() < .45:
        ret = min(dq, rng.randint(2, 8))
    c.update(input=inp, defect=dq, returned=ret, rep_lot=rep_lot, rep_dc=dc if rep_dc else None)
    stage_txt = C.DETECTION[c["stage"]][0]
    parts = [rng.choice(C.OPENERS).format(cust=cu)]
    ref = f"P/N {p['part_no']}"
    if rep_lot and rep_dc:
        ref += f" (Lot {rep_lot}, D/C {rep_dc})"
    elif rep_lot:
        ref += f" (Lot {rep_lot})"
    else:
        ref += f" (D/C {rep_dc}, 라벨 훼손으로 Lot 번호 확인 불가)"
    phr = C.SYMPTOM_TEXT[sym]
    ok = [x for x in phr if not ((("시험" in x or "HAST" in x) and c["stage"] not in ("CUSTOMER_RELIABILITY", "FIELD"))
                                 or (("회수품" in x or "필드" in x) and c["stage"] != "FIELD"))]
    parts.append(f"{ref} {stage_txt} {rng.choice(ok or phr)}.")
    if c["stage"] == "SMT_REFLOW" and rng.random() < .5:
        parts.append(rng.choice(["리플로 2회 통과 후 확인되었습니다.", "피크 온도 250℃ 조건입니다.", "무연 솔더 프로파일 적용 중입니다."]))
    if c["kind"] == "NTF_MEAS":
        parts.append("당사 TDR 측정 결과 43.8Ω입니다.")
    parts.append(f"투입 {inp:,}pcs 중 {dq:,}pcs 불량입니다.")
    parts.append(f"불량 샘플 {ret}pcs 회송 예정입니다." if ret else rng.choice(["불량 사진 첨부드립니다.", "샘플은 당사 보관 중입니다."]))
    parts.append(rng.choice(C.REQUESTS))
    c["text"] = " ".join(parts)

RMA_ITEMS = {}
SYMPTOM_SECOND = {"STRC01": "STRC02", "ELEC03": "STRC04", "STRC04": "ELEC03", "ELEC01": "APPR02", "SOLD02": "SOLD01"}
for c in CLAIMS:
    L = LOTS[c["lot"]]
    p = PROD[L["spec"]]
    panel_id = f"{c['lot']}-P{c['panel_seq']:03d}"
    trace = "LOT_LABEL" if c["rep_lot"] else "DATE_CODE_TRACED"
    add("rma_claim", rma_id=c["rma_id"], external_claim_id=f"EXT-{c['cust']}-{c['rma_id'][4:]}", customer_id=c["cust"],
        opened_at=I(c["opened"]), occurred_at=I(c["occ"]), raw_text=c["text"], reported_part_no=p["part_no"],
        reported_lot_no=c["rep_lot"], reported_date_code=c["rep_dc"], detection_stage=c["stage"], cs_status="OPEN",
        qa_status="TRIAGED", idempotency_key=f"IMPORT-{c['rma_id']}")
    item = add("rma_item", rma_item_id=c["item_id"], rma_id=c["rma_id"], panel_id=None, lot_id=c["lot"], shipment_lot_id=c["ship"],
               input_qty=c["input"], claimed_defect_qty=c["defect"], returned_qty=c["returned"], verified_defect_qty=None,
               trace_status=trace)
    RMA_ITEMS[c["item_id"]] = item
    sec = []
    if c["symptom"] in SYMPTOM_SECOND and rng.random() < .15:
        sec = [SYMPTOM_SECOND[c["symptom"]]]
    span = next((x for x in C.SYMPTOM_TEXT[c["symptom"]] if x in c["text"]), c["text"])
    add("symptom_assessment", assessment_id=nid("SYM", 7), rma_item_id=c["item_id"], primary_label=c["symptom"], label_version="pcb-v30",
        secondary_labels_json=json.dumps(sec), evidence_spans_json=json.dumps([span], ensure_ascii=False),
        proposed_by="synthetic_annotation", confirmed_by="synthetic_QA", status="SYNTHETIC_REFERENCE", created_at=I(c["opened"] + H(U(1, 6))))
    tri_at = c["opened"] + H(U(1.5, 9))
    c["triage_at"] = tri_at
    ch = SYM[c["symptom"]][5]
    rationale = f"불량 형태 {SYM[c['symptom']][3]}, 심각도 {c['sev']}"
    if ch:
        rationale += f", 관련 특성 {CHAR[ch][1]}({CHAR[ch][2]})"
    rationale += f", 고객 {CUST[c['cust']][2]}"
    if c["repeat"]:
        rationale += ", 동일 고객·불량 형태 30일 내 3건째 이상"
    add("qa_triage", triage_id=nid("TRI", 7), rma_id=c["rma_id"], severity=c["sev"], characteristic_id=ch,
        characteristic_symbol=CHAR[ch][2] if ch else None, customer_industry=CUST[c["cust"]][2],
        repeat_signal=c["repeat"],
        analysis_level=c["level"], rationale=rationale + f" → {c['level']}", decided_by="QA", decided_at=I(tri_at))
    # sample return requested by QA (not by CS guide)
    c["receipt_at"] = None
    c["verified_origin"] = 0
    if c["returned"] > 0:
        auth_at = tri_at + H(U(.5, 4))
        rec_at = bday(auth_at + Dd(U(2.5, 9) if CUST[c["cust"]][3] == "국내" else U(6, 14)))
        aid = nid("AUTH", 7)
        received = rec_at <= ASOF
        add("return_authorization", authorization_id=aid, rma_item_id=c["item_id"], decided_by="QA", requested_qty=c["returned"],
            basis=f"{c['level']} 분석 대상 시료 회수(QA 접수 검토)", approved_at=I(auth_at), status="RECEIVED" if received else "AWAITING_SAMPLE")
        if received:
            ident = "PANEL_ID_MATCH" if rng.random() < .93 else "LOT_MARKING_ONLY"
            add("return_receipt", receipt_id=nid("RCPT", 7), authorization_id=aid, received_at=I(rec_at), received_qty=c["returned"],
                package_condition="ESD_BAG_SEALED", identity_result=ident)
            c["receipt_at"] = rec_at
            c["verified_origin"] = 1 if ident == "PANEL_ID_MATCH" else 0
            if ident == "PANEL_ID_MATCH":
                item["panel_id"] = panel_id
                item["trace_status"] = "PANEL_ID_VERIFIED"
    if c["kind"] == "NTF_MEAS":
        add("customer_test_report", report_id=nid("CTR", 6), rma_item_id=c["item_id"], test_type="TDR_IMPEDANCE",
            reported_value=round(U(43.2, 44.6), 1), unit="ohm", conditions_json=json.dumps({"fixture": None, "coupon": "unknown", "cable_deskew": None}),
            fixture_info_provided=0, origin_verified=0, received_at=I(c["opened"]))
    elif c["stage"] in ("CUSTOMER_RELIABILITY", "FIELD") and rng.random() < .5:
        ver = 1 if rng.random() < .7 else 0
        add("customer_test_report", report_id=nid("CTR", 6), rma_item_id=c["item_id"],
            test_type="THERMAL_SHOCK" if c["stage"] == "CUSTOMER_RELIABILITY" else "FIELD_RETURN_ANALYSIS",
            reported_value=None, unit=None, conditions_json=json.dumps({"cycles": rng.choice([500, 1000]), "range": "-40~125C"} if c["stage"] == "CUSTOMER_RELIABILITY" else {"mileage_km": rng.randint(3000, 40000)}),
            fixture_info_provided=ver, origin_verified=ver, received_at=I(c["opened"] + H(U(2, 30))))
    elif c["symptom"] == "STRC01" and c["cause"] == "PACK_VACUUM_SEAL":
        add("customer_test_report", report_id=nid("CTR", 6), rma_item_id=c["item_id"], test_type="HIC_ON_RECEIPT",
            reported_value=round(U(30, 60)), unit="%RH", conditions_json=json.dumps({"bag_opened": "at_line", "photo": True}),
            fixture_info_provided=1, origin_verified=1, received_at=I(c["opened"] + H(U(1, 12))))

# ---------------------------------------------------------------- lab analysis of returned samples
EVID = {}


def evidence(inv, stype, srec, occ, avail, author, summary, verified):
    eid = nid("EVID", 7)
    add("evidence", evidence_id=eid, investigation_id=inv, source_type=stype, source_record_id=srec, occurred_at=I(occ),
        available_at=I(avail), author=author, content_summary=summary, origin_verified=verified, is_synthetic=1)
    return eid


def lab_values(sym_tests, cause, abnormal):
    vals = []
    ab = C.CAUSE_LAB.get(cause, {}) if abnormal else {}
    for t in sym_tests:
        if t == "LAB_REFLOW_REPRO":
            if t in ab:
                v1 = float(rng.randint(int(ab[t][0]), int(ab[t][1])))
                v2 = 0.0 if cause == "PACK_VACUUM_SEAL" else float(max(1, int(v1) - rng.randint(0, 1)))
            else:
                v1 = v2 = 0.0
            vals.append((t, 1, v1, {"condition": "as_received", "profile": "260C_x3"}))
            vals.append((t, 2, v2, {"condition": "after_bake_125C_4h", "profile": "260C_x3"}))
            continue
        if t in ab:
            lo, hi = ab[t]
            v = float(rng.randint(int(lo), int(hi))) if TSPEC[t][4] in ("count", "grade", "boolean") else round(U(lo, hi), 3)
        else:
            mu, sd = C.LAB_NORMAL[t]
            v = clipnorm(t, mu, sd) if sd else float(mu)
        vals.append((t, 1, v, {}))
    return vals


def tests_for(sym, cause):
    ts = list(C.SYMPTOM_LAB[sym])
    for t in C.CAUSE_LAB.get(cause, {}):
        if t not in ts and t in C.LAB_NORMAL:
            ts.append(t)
    return ts


INV_OF = {}
for c in CLAIMS:
    c["lab_test"] = None
    if not c["receipt_at"]:
        continue
    t_at = bday(c["receipt_at"] + Dd(U(.8, 4)))
    if t_at > ASOF:
        continue
    abnormal = c["cause"] not in ("NO_DEFECT_FOUND",)
    vals = lab_values(tests_for(c["symptom"], c["cause"]), c["cause"], abnormal)
    tid, res = new_test("RMA_LAB", "RETURNED_SAMPLE", f"{c['item_id']}-S1", c["lot"], t_at, U(3, 30), vals, equipment="LAB-01",
                        rma_item=c["item_id"], origin="RETURNED_FROM_CUSTOMER", verified=c["verified_origin"])
    c["lab_test"] = tid
    c["lab_at"] = t_at + H(30)
    c["lab_res"] = res
    RMA_ITEMS[c["item_id"]]["verified_defect_qty"] = c["returned"] if res == "FAIL" else 0

# ---------------------------------------------------------------- investigations
ORDER_DEPTS = ["품질팀", "공정기술팀", "생산팀", "설비팀", "구매팀"]
inv_n = 0


def new_inv(title, level, scope, opened, status, closed=None, reopened=None):
    global inv_n
    inv_n += 1
    iid = f"INV-25-{inv_n:04d}"
    add("investigation", investigation_id=iid, title=title, analysis_level=level, scope_definition=json.dumps(scope, ensure_ascii=False),
        owner="QA", opened_at=I(opened), status=status, closed_at=I(closed) if closed else None, reopened_from=reopened)
    return iid


def link_claims(iid, claims, reason):
    for c in claims:
        add("investigation_claim", link_id=nid("LINK", 7), investigation_id=iid, rma_id=c["rma_id"], membership_reason=reason,
            linked_at=I(max(c["triage_at"], P(R["investigation"][-1]["opened_at"]) if False else c["triage_at"])))
        INV_OF[c["rma_id"]] = iid


def clip(d):
    return d if d is not None and d <= ASOF else None


def step_status(s, e):
    if s is None or s > ASOF:
        return None, None, "NOT_STARTED"
    if e is None or e > ASOF:
        return s, None, "IN_PROGRESS"
    return s, e, "COMPLETED"


DOC_NO = Counter()


def doc_change(ca_id, doc_type, proc, summary, when, fmea=None):
    key = (doc_type, proc)
    DOC_NO[key] += 1
    short = {"PFMEA": "PFMEA", "CONTROL_PLAN": "CP", "WORK_INSTRUCTION": "WI", "INSPECTION_STANDARD": "IS", "IQC_STANDARD": "IQC"}[doc_type]
    rev_from = 3 + DOC_NO[key] - 1
    s_, o1, o2, d1, d2 = fmea if fmea else (None, None, None, None, None)
    add("document_change", change_id=nid("DOC", 6), corrective_action_id=ca_id, doc_type=doc_type, doc_no=f"{short}-{proc}-01",
        rev_from=rev_from, rev_to=rev_from + 1, change_summary=summary, severity=s_, occurrence_before=o1, occurrence_after=o2,
        detection_before=d1, detection_after=d2, approved_at=I(clip(when + Dd(1))))


CONT_MODE = {  # cause -> (customer action, factory action, proposal)
    "OL_ETCH_RESIDUE": ("SORT_100PCT_ET_RETEST", "RETEST_ON_ET-01_THEN_RELEASE", "SORT_AT_CUSTOMER_THEN_USE"),
    "HANDLING_SCRATCH": ("SORT_VISUAL_AND_ET", "SORT_THEN_RELEASE", "SORT_AT_CUSTOMER_THEN_USE"),
    "ROUT_OFFSET": ("SORT_DIMENSION", "SCRAP", "SORT_AT_CUSTOMER_THEN_USE"),
    "LABEL_PRINT_MIX": ("RELABEL_AFTER_MARKING_CHECK", "RELABEL_AFTER_MARKING_CHECK", "SORT_AT_CUSTOMER_THEN_USE"),
    "PACK_VACUUM_SEAL": ("REBAKE_125C_4H_BEFORE_USE", "REBAKE_AND_REPACK", "REBAKE_THEN_USE_DEVIATION"),
    "IL_ETCH_OVER": ("SORT_BY_PANEL_COUPON_TDR", "SORT_BY_PANEL_COUPON_TDR", "SORT_AT_CUSTOMER_THEN_USE"),
    "LAM_COOLING_RECIPE": ("SORT_WARPAGE", "SORT_WARPAGE", "SORT_AT_CUSTOMER_THEN_USE"),
}
DEFAULT_CONT = ("RETURN_AND_REPLACE", "SCRAP", "RETURN_AND_REPLACE")
PROC_OF_KIND = {}

SCEN_INV = {}
for s in scen_order:
    sid_ = s["id"]
    if sid_ not in TIMELINE:
        continue
    T = TIMELINE[sid_]
    claims = [c for c in CLAIMS if c.get("scenario") == sid_]
    level = "L3" if any(c["level"] == "L3" for c in claims) else ("L2" if any(c["level"] == "L2" for c in claims) else "L1")
    cause = s["cause"]
    cat, proc, cname = C.CAUSES[cause][0], C.CAUSES[cause][1], C.CAUSES[cause][2]
    sel = s["selector"]
    rc_ok = T["rc"] <= ASOF
    closed = T["d8"] if T["d8"] <= ASOF else None
    status = "CLOSED" if closed else ("MONITORING" if T["impl"] <= ASOF else ("CA_IN_PROGRESS" if rc_ok else "ANALYSIS"))
    reopened = SCEN_INV.get(s.get("recurrence_of"))
    prim = Counter(c["symptom"] for c in claims).most_common(1)[0][0]
    scope = {"product_group": sorted({LOTS[c["lot"]]["spec"] for c in claims}), "primary_symptom": prim,
             "trace_dimensions": s["key_dims"], "note": "영향 Lot은 공통점(Lot·자재·설비·조건·시간대) 기준으로 판정, 건수·비율 기준 아님"}
    iid = new_inv(f"{SYM[prim][3]} — 공통점 추적", level, scope, T["t0"] + H(U(1, 4)), status, closed, reopened)
    SCEN_INV[sid_] = iid
    T["iid"] = iid
    link_claims(iid, claims, "same_symptom_and_trace_commonality")
    if reopened:
        for r in R["investigation"]:
            if r["investigation_id"] == reopened and r["status"] == "CLOSED":
                r["status"] = "REOPENED_LINKED"
    # ---- activities
    lab_tests = sorted([c for c in claims if c["lab_test"]], key=lambda c: c["lab_at"])
    lab_s = lab_tests[0]["lab_at"] - H(30) if lab_tests else None
    lab_e = lab_tests[min(9, len(lab_tests) - 1)]["lab_at"] if lab_tests else None
    acts = [("DATA_COLLECTION", "품질팀", T["dc_start"], T["dc_end"], T["sources"], "분산된 생산·품질·자재 이력 수집 및 기간 특정"),
            ("LOT_TRACE", "품질팀", T["dc_end"], T["decision"], ["MES 공정 이력", "출하 이력"], "공통점 기준 영향 Lot 후보 선정 및 선후행 추적"),
            ("LAB_ANALYSIS", "품질팀", lab_s, lab_e, ["분석실 단면·SEM·X-ray·재현 시험"], "반송품·보관 시료·양품 비교 분석"),
            ("HYPOTHESIS_VERIFICATION", "공정기술팀", T["decision"], T["rc"], ["가설별 검증 시험", "설비·약품 기록"], "원인 가설 선정 및 검증"),
            ("CA_IMPLEMENTATION", C.CAUSES[cause][6], T["ca_select"], T["impl"], ["작업표준·관리계획서"], "시정조치 실행"),
            ("EFFECTIVENESS_VERIFICATION", "품질팀", T["impl"], T["eff_end"], ["조치 후 생산 Lot 품질 실적", "클레임 이력"], "시정조치 효과성 검증")]
    for at, dept, s0, e0, src, note in acts:
        if s0 is None or s0 > ASOF:
            continue
        add("analysis_activity", activity_id=nid("ACT", 6), investigation_id=iid, activity_type=at, owner_dept=dept, started_at=I(s0),
            ended_at=I(clip(e0)), data_sources_json=json.dumps(src, ensure_ascii=False), note=note)
    # ---- evidence: returned sample lab tests
    ev_lab = []
    for c in lab_tests:
        if c["lab_at"] <= ASOF:
            e = evidence(iid, "test_run", c["lab_test"], c["lab_at"] - H(30), c["lab_at"], "synthetic_lab_engineer",
                         f"반송품 분석({SYM[c['symptom']][3]}): {', '.join(TSPEC[t][2] for t in C.CAUSE_LAB.get(cause, {}))} 이상",
                         c["verified_origin"])
            if c["verified_origin"]:
                ev_lab.append(e)
    # production record evidence
    ev_prod = []
    lots = INJ[sid_]
    if sel["kind"] in ("equipment", "condition"):
        runs = sorted(INJ_RUNS[sid_], key=lambda x: RUNS[x]["s"])
        if runs:
            items = list(s["shifts"].keys()) or ["recipe/calibration"]
            ev_prod.append(evidence(iid, "process_run_summary", runs[0], RUNS[runs[0]]["s"], T["dc_end"], "synthetic_quality_engineer",
                                    f"{','.join(sel['equipment'])} {sel['start']}~ 처리 로드 {len(runs)}건, 관리 항목 {', '.join(items)} 기록 검토", 1))
        for ev in SCEN_EVENTS[sid_]:
            ev_prod.append(evidence(iid, "equipment_event", ev["event_id"], P(ev["occurred_at"]), max(P(ev["recorded_at"]), T["dc_end"]),
                                    "synthetic_equipment_engineer", ev["description"], 1))
    else:
        for mid in s.get("_bad_mlots", []):
            ev_prod.append(evidence(iid, "material_lot", mid, MLOT[mid]["received"], T["dc_end"], "synthetic_quality_engineer",
                                    f"{sel['material']} {mid} 투입 Lot 추적, 수입검사는 성적서 확인만 실시", 1))
    if sid_ == "P04":
        ev_prod.append(evidence(iid, "inspection_plan", "PLAN-FQC-XSEC-REV2", P("2025-05-26"), T["dc_end"], "synthetic_quality_engineer",
                                "단면 검사 5 Lot당 1회로 축소(2025-05-26)", 1))
    # ---- lot commonality
    claim_lots = Counter(c["lot"] for c in claims)
    start, endw = P(sel["start"]), s["_eff_end"]
    key_proc = sel.get("process")
    candidates = set(lots)
    ctrl = []
    if sel["kind"] in ("equipment", "condition"):
        for lid, L in LOTS.items():
            if lid in lots:
                continue
            rr = lot_runs(lid, key_proc)
            if any(start - Dd(7) <= r["s"] < endw + Dd(7) for r in rr) and (L["spec"] in s["claim_products"] or any(r["eq"] in sel["equipment"] for r in rr)):
                ctrl.append(lid)
    else:
        bad = set(s.get("_bad_mlots", []))
        for lid, L in LOTS.items():
            if lid in lots:
                continue
            m = L["mat"].get(sel["material"])
            if m and MLOT[m]["sup"] == sel["supplier"] and abs((MLOT[m]["received"] - start).days) <= 25:
                ctrl.append(lid)
    rng.shuffle(ctrl)
    ctrl = ctrl[:40]
    candidates |= set(ctrl)
    ret_imp = [l for l in sorted(lots, key=lambda x: -claim_lots.get(x, 0))][:5]
    ret_clr = ctrl[:5]
    retained = {}
    for lid in ret_imp + ret_clr:
        when = T["dc_end"] + Dd(U(.5, (T["decision"] - T["dc_end"]).total_seconds() / 86400 + .2))
        if when > ASOF:
            continue
        sym_t = tests_for(prim, cause)
        vals = lab_values(sym_t, cause, lid in lots)
        a, b = lots.get(lid, (1, LOTS[lid]["qty"]))
        tid, res = new_test("RMA_LAB", "RETAINED_SAMPLE", f"{lid}-P{a:03d}-RET", lid, when, U(4, 30), vals, equipment="LAB-01",
                            inv=iid, origin="RETAINED_STOCK", verified=1)
        retained[lid] = res
        ev_prod.append(evidence(iid, "test_run", tid, when, when + H(30), "synthetic_lab_engineer",
                                f"{lid} 보관 시료 비교 시험 {res}", 1))
    if sel["kind"] == "material":
        for mid in s.get("_bad_mlots", [])[:2]:
            when = T["dc_end"] + Dd(1)
            if when > ASOF:
                continue
            spec_id = {"CCL": "IQC_CCL_VOID", "PREPREG": "IQC_PP_FLOW", "CU_FOIL": "IQC_CU_PEEL", "PSR_INK": "IQC_INK_VISC"}[sel["material"]]
            v = {"IQC_CCL_VOID": float(rng.randint(3, 7)), "IQC_PP_FLOW": round(U(14, 18), 1), "IQC_CU_PEEL": round(U(.6, .9), 2),
                 "IQC_INK_VISC": float(rng.randint(285, 320))}[spec_id]
            tid, res = new_test("RMA_LAB", "MATERIAL_STOCK", mid, None, when, 6, [(spec_id, 1, v, {"sample": "unused_stock"})],
                                equipment="LAB-01", inv=iid, origin="RETAINED_STOCK", verified=1)
            ev_prod.append(evidence(iid, "test_run", tid, when, when + H(8), "synthetic_lab_engineer",
                                    f"미사용 재고 {mid} {TSPEC[spec_id][2]} {v} → {res}", 1))
    # golden reference
    gw = T["dc_end"] + Dd(1)
    if gw <= ASOF:
        vals = lab_values(tests_for(prim, cause), cause, False)
        tid, _ = new_test("RMA_LAB", "GOLDEN_SAMPLE", f"GOLD-{PROD[LOTS[claims[0]['lot']]['spec']]['part_no']}", None, gw, 8, vals,
                          equipment="LAB-01", inv=iid, origin="REFERENCE_STOCK", verified=1)
        ev_prod.append(evidence(iid, "test_run", tid, gw, gw + H(10), "synthetic_lab_engineer", "양품(골든) 시료 비교 기준값 확보", 1))
    dec_at = T["decision"]
    if dec_at <= ASOF:
        for lid in sorted(candidates):
            L = LOTS[lid]
            mats = {mt: m for mt, m in L["mat"].items() if sel["kind"] == "material" and mt == sel["material"] and MLOT[m]["sup"] == sel["supplier"]}
            eqs = {}
            cond = {}
            if sel["kind"] != "material":
                rr = lot_runs(lid, key_proc)
                eqs = {r["eq"]: [r["load_no"] if False else r["load"] for r in rr if r["eq"] in sel["equipment"]] for r in rr if r["eq"] in sel["equipment"]}
                for r in rr:
                    for o_item in s["shifts"]:
                        sh = RUN_SHIFT[r["id"]].get(o_item)
                        if sh:
                            cond[f"load{r['load']}:{o_item}"] = "OUT_OF_WINDOW" if abs(sh[0]) > 0 else "NORMAL"
            in_window = any(start <= r["s"] < endw for r in lot_runs(lid, key_proc)) if key_proc else (L["start"] >= start - Dd(5))
            impacted = lid in lots
            ret = retained.get(lid, "NOT_TESTED")
            if impacted:
                decision = "IMPACTED"
                why = []
                if claim_lots.get(lid):
                    why.append(f"클레임 {claim_lots[lid]}건")
                else:
                    why.append("클레임 없음")
                why.append(f"공통점 {'/'.join(s['key_dims'])} 일치")
                if ret != "NOT_TESTED":
                    why.append(f"보관 시료 {ret}")
                rationale = ", ".join(why) + " → 영향 Lot"
            else:
                decision = "SUSPECT_MONITOR" if rng.random() < .07 else "CLEARED"
                rationale = ("시간대·제품 공통이나 핵심 공통점(" + "/".join(s["key_dims"][:1]) + ") 불일치" +
                             (f", 보관 시료 {ret}" if ret != "NOT_TESTED" else "") +
                             (" → 출하 후 클레임 모니터링" if decision == "SUSPECT_MONITOR" else " → 비영향"))
            a, b = lots.get(lid, (None, None))
            add("lot_impact_assessment", impact_id=nid("IMP", 6), investigation_id=iid, lot_id=lid, claim_count=claim_lots.get(lid, 0),
                same_lot_as_claim=int(lid in claim_lots), same_material_json=json.dumps(mats), same_equipment_json=json.dumps(eqs),
                same_condition_json=json.dumps(cond), same_time_window=int(in_window), retained_sample_result=ret,
                impacted_panel_from=a, impacted_panel_to=b, decision=decision, rationale=rationale, decided_at=I(dec_at))
        # containment
        cust_mode, fac_mode, proposal = CONT_MODE.get(cause, DEFAULT_CONT)
        affected_by_cust = defaultdict(int)
        for lid in sorted(lots):
            a, b = lots[lid]
            for sl in ACTIVE_SHIP.values():
                if sl["lot"] == lid and sl["ship"] <= dec_at:
                    n = max(0, min(b, sl["b"]) - max(a, sl["a"]) + 1)
                    if n:
                        affected_by_cust[sl["cust"]] += n * PROD[LOTS[lid]["spec"]]["bpp"]
                        s1 = dec_at + H(U(4, 20))
                        e1 = s1 + Dd(U(2, 9))
                        add("containment_action", containment_id=nid("CNT", 6), investigation_id=iid, lot_id=lid, location="CUSTOMER",
                            customer_id=sl["cust"], action_type=cust_mode, qty_panels=n, started_at=I(clip(s1) or ASOF),
                            completed_at=I(clip(e1)), result="COMPLETED" if clip(e1) else "IN_PROGRESS")
            held = [x for x in HELD.get(lid, []) if x[0] == sid_]
            npan = sum(max(0, min(b, x[1]["b"]) - max(a, x[1]["a"]) + 1) for x in held)
            if not held:
                # not shipped yet at decision (WIP / FG)
                if LOTS[lid]["ship_at"] > dec_at:
                    npan = b - a + 1
            if npan:
                s1 = dec_at + H(U(1, 6))
                e1 = s1 + Dd(U(1, 6))
                add("containment_action", containment_id=nid("CNT", 6), investigation_id=iid, lot_id=lid,
                    location="FACTORY_WIP_FG", customer_id=None, action_type=f"HOLD_THEN_{fac_mode}", qty_panels=npan,
                    started_at=I(s1), completed_at=I(clip(e1)), result="COMPLETED" if clip(e1) else "IN_PROGRESS")
                for q in range(a, b + 1):
                    pn = PANELS.get(f"{lid}-P{q:03d}")
                    if pn and pn["disposition"] in ("HOLD", "FG_STOCK", "WIP"):
                        pn["disposition"] = {"SCRAP": "SCRAPPED_CONTAINMENT", "REBAKE_AND_REPACK": "REWORKED_RELEASED"}.get(fac_mode, "SORTED_RELEASED") if clip(e1) else "HOLD"
        if level == "L3":
            for cu, nb in sorted(affected_by_cust.items()):
                sub = dec_at + Dd(U(.5, 2))
                resp = sub + Dd(U(1, 6))
                if sub > ASOF:
                    continue
                add("customer_disposition_approval", approval_id=nid("CDA", 5), investigation_id=iid, customer_id=cu, proposal=proposal,
                    affected_board_qty=nb, submitted_at=I(sub), responded_at=I(clip(resp)),
                    status=("APPROVED" if rng.random() < .88 else "CONDITIONALLY_APPROVED") if clip(resp) else "PENDING")
                add("communication", message_id=nid("MSG", 7), rma_id=None, investigation_id=iid, customer_id=cu, audience="customer",
                    author_role="QA", message_text=f"[봉쇄 조치] 영향 Lot {len(lots)}개 판정. 귀사 재고 {nb:,}pcs에 대해 '{proposal}' 처리 승인을 요청드립니다.",
                    sent_at=I(sub))
    # ---- hypotheses
    fam = [x for x in C.HYPOTHESIS_FAMILY.get(prim, []) if x != cause]
    rng.shuffle(fam)
    hyps = [(cause, True)] + [(x, False) for x in fam[:2]]
    rng.shuffle(hyps)
    for k, (hc, truth) in enumerate(hyps, 1):
        hcat = C.CAUSES[hc][0]
        dim = s["key_dims"][0] if truth else {"MATERIAL": "MATERIAL_LOT", "PROCESS": "EQUIPMENT/CONDITION", "INSPECTION": "INSPECTION_RECORD"}[hcat]
        dt_ = T["rc"] if truth else T["decision"] + Dd(U(1, max(1.5, (T["rc"] - T["decision"]).days)))
        if T["decision"] > ASOF:
            res, dt_ = "OPEN", None
        elif truth:
            res = "SUPPORTED" if rc_ok else "UNDER_VERIFICATION"
            dt_ = clip(dt_)
        else:
            res = "REJECTED" if dt_ <= ASOF else "UNDER_VERIFICATION"
            dt_ = clip(dt_)
        evs = (ev_lab[:3] + ev_prod[:3]) if truth else ev_prod[-1:]
        txt = C.CAUSES[hc][3] if truth else f"{C.CAUSES[hc][2]} 가설: 영향 Lot·비영향 Lot 간 해당 공통점 차이 없음, 반송품 해당 지표 정상"
        add("cause_hypothesis", hypothesis_id=nid("HYP", 6), investigation_id=iid, hypothesis_no=k, cause_code=hc,
            hypothesis_text=txt if res != "OPEN" else f"{C.CAUSES[hc][2]} 가설 검증 예정", commonality_dimension=dim,
            verification_method=C.CAUSES[hc][4], result=res, evidence_ids_json=json.dumps(evs if res != "OPEN" else []), decided_at=I(dt_))
    # ---- cause & escape
    cert = "CONFIRMED" if rc_ok and ev_lab and ev_prod else ("PROBABLE" if T["decision"] <= ASOF else "UNCONFIRMED")
    eq = sel.get("equipment", [None])[0] if sel["kind"] != "material" else None
    ca_row = add("cause_assessment", cause_assessment_id=nid("CAUSE", 6), investigation_id=iid, cause_code=cause, cause_category=cat,
                 process_id=proc if proc else None, equipment_id=eq, material_lot_ids_json=json.dumps(s.get("_bad_mlots", [])),
                 certainty_status=cert, support_evidence_ids_json=json.dumps((ev_lab[:5] + ev_prod[:6]) if cert != "UNCONFIRMED" else ev_lab[:3]),
                 assessed_at=I(clip(T["rc"]) or min(ASOF, T["decision"])), approved_at=I(clip(T["rc"] + H(4))) if cert == "CONFIRMED" else None)
    T["cause_row"] = ca_row
    esc = s["escape"]
    esc_proc = C.ESCAPES[esc][1]
    esc_test = None
    for lid in sorted(lots):
        lt = LOT_TESTS.get(lid, {})
        cand_code = {"FQC": ["PLAN-FQC-XSEC", "PLAN-FQC-FLOAT", "PLAN-FQC-WARP", "PLAN-FQC-TDR", "PLAN-FQC-SM", "PLAN-FQC-DIM", "PLAN-FQC-IONIC"],
                     "OQC": ["PLAN-OQC-LABEL"]}.get(esc_proc, [])
        for cc in cand_code:
            if cc in lt:
                esc_test = lt[cc]
                break
        if esc_test:
            break
    if esc == "ESC_ET_FIXTURE":
        for lid in sorted(lots):
            if lot_runs(lid, "ET")[0]["eq"] == "ET-02":
                esc_test = next((r["test_run_id"] for r in R["test_run"] if r["lot_id"] == lid and r["phase"] == "ET"), None)
                break
    if T["decision"] <= ASOF:
        add("escape_assessment", escape_id=nid("ESC", 6), investigation_id=iid, escape_code=esc, detection_process_id=esc_proc,
            test_run_id=esc_test, evidence_ids_json=json.dumps(ev_prod[-2:]), status="CONFIRMED" if rc_ok else "UNDER_REVIEW",
            assessed_at=I(clip(T["rc"]) or T["decision"]))
    for c in claims:
        dec = max(T["rc"] + H(4), c.get("lab_at") or T["rc"]) if cert == "CONFIRMED" else None
        add("claim_cause", claim_cause_id=nid("CC", 7), rma_item_id=c["item_id"], cause_assessment_id=ca_row["cause_assessment_id"],
            applicability="APPLIES" if cert == "CONFIRMED" else "PENDING",
            rationale=f"클레임 Lot {c['lot']}가 영향 Lot으로 판정됨({'/'.join(s['key_dims'])} 공통)" + ("" if c["lab_test"] else ", 반송품 미분석 — Lot 판정으로 적용"),
            decided_at=I(clip(dec)))
        c["cause_decided"] = clip(dec)
        c["inv"] = iid
    # ---- 8D report & actions
    rtype = {"L3": "8D_FULL", "L2": "8D_SHORT", "L1": "ANALYSIS_REPLY"}[level]
    first_sub = clip(T["ca_select"])
    final_sub = clip(T["d8"])
    rep = add("qa_report", report_id=nid("RPT", 5), investigation_id=iid, report_type=rtype,
              status="FINAL_SUBMITTED" if final_sub else ("INTERIM_SUBMITTED" if first_sub else "DRAFT"),
              first_submitted_at=I(first_sub), final_submitted_at=I(final_sub))
    steps = [("D1", "팀 구성", "품질팀", T["t0"], T["t0"] + H(6), "품질·공정기술·생산·설비·구매 CFT 구성"),
             ("D2", "문제 기술", "품질팀", T["t0"] + H(6), T["t0"] + Dd(1), f"{SYM[prim][3]}, 클레임 {len(claims)}건, 제품 {', '.join(scope['product_group'])}"),
             ("D3", "봉쇄 조치", "품질팀", T["t0"] + Dd(1), T["decision"] + Dd(3), "영향 Lot 판정 후 사내 재고 보류, 고객 재고 처리 승인 요청"),
             ("D4", "근본 원인(발생·유출)", "공정기술팀", T["t0"] + Dd(1), T["rc"], f"발생: {cname} / 유출: {C.ESCAPES[esc][0]}"),
             ("D5", "시정조치 선정", "품질팀", T["rc"], T["ca_select"], "발생·유출 원인별 시정조치 선정"),
             ("D6", "실행 및 유효성 검증", C.CAUSES[cause][6], T["ca_select"], T["eff_end"], "조치 실행 후 생산 Lot 실적으로 효과성 검증"),
             ("D7", "재발 방지·예방", "품질팀", T["impl"], T["d7"], "PFMEA·관리계획서 개정, 유사 설비·제품 수평 전개"),
             ("D8", "종결", "품질팀", max(T["eff_end"], T["d7"]), T["d8"], "효과성 확인 후 종결 및 고객 최종 회신")]
    if rtype != "ANALYSIS_REPLY":
        for d, nm, dept, s0, e0, summ in steps:
            if rtype == "8D_SHORT" and d == "D7":
                continue
            ss, ee, st = step_status(s0, e0)
            add("eight_d_step", step_id=nid("D8S", 6), report_id=rep["report_id"], d_step=d, step_name=nm, owner_dept=dept, started_at=I(ss),
                completed_at=I(ee), status=st, summary=summ if st != "NOT_STARTED" else "-")
    T["cas"] = []
    if rc_ok:
        occ_desc = C.CAUSES[cause][5]
        scope_ca = {"equipment": sel.get("equipment"), "products": "ALL"} if sel["kind"] != "material" else {"supplier": sel["supplier"], "material_lots": s.get("_bad_mlots", [])}
        if sid_ == "P01":
            occ_desc = "DRL-03 비트 수명 카운터 인터록 적용 및 카운터 리셋 로직 수정"
            scope_ca = {"equipment": ["DRL-03"]}
        elif s.get("occ_ca"):  # v5: the original action that later proves incomplete
            occ_desc, scope_ca = s["occ_ca"]
        impl = clip(T["impl"])
        ca1 = add("corrective_action", corrective_action_id=nid("CA", 6), investigation_id=iid, action_role="OCCURRENCE",
                  action_category={"MATERIAL": "SUPPLIER_SCAR", "PROCESS": "PROCESS_EQUIPMENT", "INSPECTION": "INSPECTION"}[cat],
                  description=occ_desc, owner_dept=C.CAUSES[cause][6], cause_code=cause, escape_code=None, scope_json=json.dumps(scope_ca, ensure_ascii=False),
                  selected_at=I(T["ca_select"]), due_at=I(T["ca_select"] + Dd(21)), implemented_at=I(impl),
                  verification_status="IMPLEMENTING" if not impl else "MONITORING")
        T["cas"].append(ca1)
        if C.CAUSES[cause][7] and impl:
            doc_change(ca1["corrective_action_id"], C.CAUSES[cause][7], proc or sel.get("material", "IQC"), occ_desc, impl)
        eimpl = clip(T["esc_impl"])
        ca2 = add("corrective_action", corrective_action_id=nid("CA", 6), investigation_id=iid, action_role="ESCAPE",
                  action_category="INSPECTION", description=C.ESCAPES[esc][3], owner_dept="품질팀", cause_code=None, escape_code=esc,
                  scope_json=json.dumps({"plan": ESC_PLAN.get(sid_, ("CP",))[0]}), selected_at=I(T["ca_select"]),
                  due_at=I(T["ca_select"] + Dd(14)), implemented_at=I(eimpl), verification_status="IMPLEMENTING" if not eimpl else "MONITORING")
        T["cas"].append(ca2)
        if eimpl:
            dt_map = {"ESC_IQC_COA_ONLY": "IQC_STANDARD", "ESC_SPC_REACTION": "CONTROL_PLAN", "ESC_OQC_SCAN_SKIPPED": "INSPECTION_STANDARD",
                      "ESC_OQC_LEAK_CHECK_ABSENT": "INSPECTION_STANDARD", "ESC_ET_FIXTURE": "INSPECTION_STANDARD", "ESC_AOI_THRESHOLD": "CONTROL_PLAN"}
            doc_change(ca2["corrective_action_id"], dt_map.get(esc, "CONTROL_PLAN"), esc_proc or proc or "FQC", C.ESCAPES[esc][3], eimpl)
            for code, lst in PLANS.items():
                for pl in lst:
                    if pl["ca"] == f"__ESC__{sid_}":
                        pl["ca"] = ca2["corrective_action_id"]
        if sel["kind"] == "material":
            iss = T["decision"]
            rcv = iss + Dd(U(9, 20))
            add("supplier_action", scar_id=nid("SCAR", 4), investigation_id=iid, supplier_id=sel["supplier"],
                material_lot_ids_json=json.dumps(s.get("_bad_mlots", [])), request_text=f"{C.CAUSES[cause][2]} 원인 분석 및 시정조치(8D) 요청, 해당 Lot 반품",
                issued_at=I(iss), response_due_at=I(iss + Dd(14)), supplier_8d_received_at=I(clip(rcv)),
                supplier_root_cause={"CCL": "함침기 수지 점도 관리 이탈", "PREPREG": "B-stage 건조 온도 과다", "PSR_INK": "희석제 배합 비율 오류",
                                     "CU_FOIL": "조화 처리 전류 저하"}[sel["material"]] if clip(rcv) else None,
                status="CLOSED" if clip(rcv + Dd(20)) else ("RESPONSE_RECEIVED" if clip(rcv) else "OPEN"))
        if level == "L3":
            d7 = clip(T["d7"])
            if sid_ == "P01R":
                desc = "전 드릴 설비(DRL-01~04) 비트 수명 인터록·카운터 로직 수평 전개 및 설비 변경 시 수평 전개 체크 의무화"
                role = "HORIZONTAL"
            elif s.get("recurrence_of"):  # v5 recurrences
                desc = s["recur_ca"]
                role = "HORIZONTAL"
            elif sid_ == "P01":
                desc = f"PFMEA '{cname}' 발생도·검출도 재평가(적용 범위: DRL-03 한정, 타 드릴 설비 수평 전개 미포함)"
                role = "PREVENTION"
            else:
                desc = f"PFMEA '{cname}' 발생도·검출도 재평가, 유사 설비·제품 수평 전개"
                role = "PREVENTION"
            ca3 = add("corrective_action", corrective_action_id=nid("CA", 6), investigation_id=iid, action_role=role, action_category="SYSTEM",
                      description=desc, owner_dept="품질팀", cause_code=cause, escape_code=esc, scope_json=json.dumps({"scope": "similar_process_and_products"}),
                      selected_at=I(T["ca_select"]), due_at=I(T["ca_select"] + Dd(40)), implemented_at=I(d7),
                      verification_status="IMPLEMENTING" if not d7 else "MONITORING")
            T["cas"].append(ca3)
            if d7:
                sev = max(SYM[c["symptom"]][4] for c in claims)
                o1 = rng.randint(4, 6)
                d1 = rng.randint(6, 8)
                doc_change(ca3["corrective_action_id"], "PFMEA", proc or "IQC", f"고장형태 '{SYM[prim][3]}' / 원인 '{cname}' 재평가", d7,
                           fmea=(sev, o1, max(2, o1 - 3), d1, max(2, d1 - 4)))
                doc_change(ca3["corrective_action_id"], "CONTROL_PLAN", proc or "IQC", "PFMEA 개정 반영 관리 항목·주기 갱신", d7)
    for c in claims:
        add("communication", message_id=nid("MSG", 7), rma_id=c["rma_id"], investigation_id=None, customer_id=c["cust"], audience="customer",
            author_role="QA", message_text="불량 통보를 접수했습니다. 시료 회수 후 분석 결과와 처리 계획을 회신드리겠습니다.", sent_at=I(c["triage_at"] + H(1)))
    for ca in T["cas"]:
        add("communication", message_id=nid("MSG", 7), rma_id=None, investigation_id=iid, customer_id=None, audience=ca["owner_dept"],
            author_role="QA", message_text=f"[시정조치 요청] {ca['description']} / 기한 {ca['due_at'][:10]}", sent_at=ca["selected_at"])

# ---------------------------------------------------------------- sporadic / NTF investigations (grouped by cause and quarter)
groups = defaultdict(list)
for c in CLAIMS:
    if c["kind"] == "SCENARIO":
        continue
    q = (c["opened"].month - 1) // 3 + 1
    key = ("NTF_MEAS" if c["kind"] == "NTF_MEAS" else c["cause"], q)
    groups[key].append(c)
for (gkey, q), claims in sorted(groups.items()):
    t0 = min(c["opened"] for c in claims)
    level = "L3" if any(c["level"] == "L3" for c in claims) else ("L2" if any(c["level"] == "L2" for c in claims) else "L1")
    cause = "NO_DEFECT_FOUND" if gkey == "NTF_MEAS" else gkey
    title = {"NTF_MEAS": "고객 TDR 측정값 이탈 통보 — 측정 조건 확인", "NO_DEFECT_FOUND": "불량 미재현 건"}.get(gkey, f"{C.CAUSES[cause][2]} 단발 불량")
    last = max(c["opened"] for c in claims)
    endq = datetime(2025, q * 3, 28, 18, tzinfo=KST) + Dd(20)
    closed = clip(endq)
    iid = new_inv(f"2025 Q{q} {title}", level, {"type": "SPORADIC_GROUP" if gkey not in ("NTF_MEAS", "NO_DEFECT_FOUND") else "NO_DEFECT_GROUP",
                                                "quarter": q, "note": "단일 Lot 단발 건 묶음. 공통점 추적 결과 동일 설비·자재·조건 공통점 없음"},
                  t0 + H(2), "CLOSED" if closed else "ANALYSIS", closed)
    link_claims(iid, claims, "same_cause_code_quarter_group")
    cat = C.CAUSES[cause][0]
    evs = []
    for c in claims:
        if c["lab_test"] and c["lab_at"] <= ASOF:
            e = evidence(iid, "test_run", c["lab_test"], c["lab_at"] - H(30), c["lab_at"], "synthetic_lab_engineer",
                         f"반송품 분석 {c['lab_res']}", c["verified_origin"])
            c["ev"] = e
            if c["verified_origin"]:
                evs.append(e)
    # lot-level single trace for sporadic lots
    for lid in sorted({c["lot"] for c in claims})[:60]:
        add("lot_impact_assessment", impact_id=nid("IMP", 6), investigation_id=iid, lot_id=lid,
            claim_count=sum(1 for c in claims if c["lot"] == lid), same_lot_as_claim=1, same_material_json="{}", same_equipment_json="{}",
            same_condition_json="{}", same_time_window=0, retained_sample_result="NOT_TESTED", impacted_panel_from=None, impacted_panel_to=None,
            decision="ISOLATED_UNIT" if cause != "NO_DEFECT_FOUND" else "NOT_IMPACTED",
            rationale="동일 Lot 내 다른 클레임·공정 이상 기록 없음 → 단발 결함" if cause != "NO_DEFECT_FOUND" else "반송품 규격 내 → 영향 없음",
            decided_at=I(min(ASOF, min(c["opened"] for c in claims if c["lot"] == lid) + Dd(5))))
    if gkey == "NTF_MEAS":
        e_ctr = []
        for c in claims[:5]:
            rid_ = next((r["report_id"] for r in R["customer_test_report"] if r["rma_item_id"] == c["item_id"]), None)
            if rid_:
                e_ctr.append(evidence(iid, "customer_test_report", rid_, c["opened"], c["opened"], "customer",
                                      "고객 TDR 측정값(픽스처·쿠폰 정보 없음) — 출처·조건 미확인으로 판정 근거 제외", 0))
        for k, (hc, txt, res) in enumerate([("IL_ETCH_OVER", "내층 과에칭 가설: 반송품 선폭·TDR 규격 내, 해당 Lot 에칭 기록 정상", "REJECTED"),
                                            (None, "측정 조건 차이 가설: 고객 측정은 쿠폰·픽스처·디스큐 정보 없음, 당사 동일 시료 재측정 규격 내", "SUPPORTED")], 1):
            add("cause_hypothesis", hypothesis_id=nid("HYP", 6), investigation_id=iid, hypothesis_no=k, cause_code=hc, hypothesis_text=txt,
                commonality_dimension="MEASUREMENT_CONDITION" if hc is None else "CONDITION",
                verification_method="반송품 TDR 재측정(표준 쿠폰·픽스처) 및 보관 시료 비교", result=res,
                evidence_ids_json=json.dumps(evs[:3] + e_ctr[:2]), decided_at=I(clip(t0 + Dd(12))))
    cert = "CONFIRMED" if evs and cat == "UNDETERMINED" else ("PROBABLE" if evs else "UNCONFIRMED")
    if cause == "ET_RETEST_MISJUDGE" and evs:
        cert = "CONFIRMED"
    car = add("cause_assessment", cause_assessment_id=nid("CAUSE", 6), investigation_id=iid, cause_code=cause, cause_category=cat,
              process_id=C.CAUSES[cause][1], equipment_id=None, material_lot_ids_json="[]", certainty_status=cert,
              support_evidence_ids_json=json.dumps(evs[:8]), assessed_at=I(min(ASOF, last + Dd(6))),
              approved_at=I(min(ASOF, last + Dd(7))) if cert != "UNCONFIRMED" else None)
    if cause in C.SPORADIC_ESCAPE:
        add("escape_assessment", escape_id=nid("ESC", 6), investigation_id=iid, escape_code=C.SPORADIC_ESCAPE[cause], detection_process_id="ET",
            test_run_id=None, evidence_ids_json=json.dumps(evs[:2]), status="CONFIRMED", assessed_at=I(min(ASOF, last + Dd(6))))
    for c in claims:
        if c["lab_test"] and c["lab_at"] <= ASOF:
            dec = c["lab_at"] + H(U(4, 30))
            appl = "APPLIES"
            why = "반송품 분석 결과 해당 원인 지표 확인" if cause != "NO_DEFECT_FOUND" else "반송품 재시험 규격 내, 불량 미재현"
            if gkey == "NTF_MEAS":
                why = "고객 측정값은 측정 조건·픽스처 정보가 없어 근거 제외. 당사 표준 조건 재측정 규격 내"
        elif c["receipt_at"] is None and c["returned"] == 0:
            dec = c["triage_at"] + Dd(U(1, 4))
            appl = "APPLIES_PHOTO_ONLY" if cause != "NO_DEFECT_FOUND" else "NOT_DETERMINED_NO_SAMPLE"
            why = "사진·고객 정보로 판단(시료 미회수)" if cause != "NO_DEFECT_FOUND" else "시료 미회수로 판정 불가, 사진상 이상 없음"
        else:
            dec, appl, why = None, "PENDING", "시료 분석 대기"
        add("claim_cause", claim_cause_id=nid("CC", 7), rma_item_id=c["item_id"], cause_assessment_id=car["cause_assessment_id"],
            applicability=appl, rationale=why, decided_at=I(clip(dec)))
        c["cause_decided"] = clip(dec)
        c["inv"] = iid
        add("communication", message_id=nid("MSG", 7), rma_id=c["rma_id"], investigation_id=None, customer_id=c["cust"], audience="customer",
            author_role="QA", message_text="불량 통보를 접수했습니다. 분석 결과를 회신드리겠습니다.", sent_at=I(c["triage_at"] + H(1)))
    rtype = {"L3": "8D_SHORT", "L2": "8D_SHORT", "L1": "ANALYSIS_REPLY"}[level] if cause != "NO_DEFECT_FOUND" else "ANALYSIS_REPLY"
    add("qa_report", report_id=nid("RPT", 5), investigation_id=iid, report_type=rtype, status="FINAL_SUBMITTED" if closed else "INTERIM_SUBMITTED",
        first_submitted_at=I(min(ASOF, t0 + Dd(7))), final_submitted_at=I(closed))
    if cause != "NO_DEFECT_FOUND" and closed:
        ca = add("corrective_action", corrective_action_id=nid("CA", 6), investigation_id=iid, action_role="OCCURRENCE",
                 action_category="PROCESS_LOCAL" if cat == "PROCESS" else "INSPECTION", description=C.CAUSES[cause][5],
                 owner_dept=C.CAUSES[cause][6], cause_code=cause, escape_code=C.SPORADIC_ESCAPE.get(cause), scope_json=json.dumps({"scope": "local"}),
                 selected_at=I(min(ASOF, last + Dd(7))), due_at=I(min(ASOF, last + Dd(21))), implemented_at=I(min(ASOF, last + Dd(14))),
                 verification_status="EFFECTIVE")
        doc_change(ca["corrective_action_id"], C.CAUSES[cause][7] or "WORK_INSTRUCTION", C.CAUSES[cause][1] or "FQC", C.CAUSES[cause][5], P(ca["implemented_at"]))

# ---------------------------------------------------------------- effectiveness, monitoring, recurrence
cause_claims_by_lot = defaultdict(list)
for c in CLAIMS:
    cause_claims_by_lot[(c["cause"], c["lot"])].append(c)
for sid_, T in TIMELINE.items():
    s = SCN[sid_]
    for ca in T.get("cas", []):
        if not ca["implemented_at"]:
            continue
        impl = P(ca["implemented_at"])
        wto = impl + Dd(120)
        if ca["action_role"] in ("OCCURRENCE", "HORIZONTAL", "PREVENTION"):
            post_lots = [lid for lid, L in LOTS.items() if impl <= L["start"] < wto]
            sel_ = s["selector"]

            def in_scope(c):
                if ca["action_role"] != "OCCURRENCE":
                    return True
                if sel_["kind"] == "material":
                    m = LOTS[c["lot"]]["mat"].get(sel_["material"])
                    return bool(m) and MLOT[m]["sup"] == sel_["supplier"]
                return any(r["eq"] in sel_["equipment"] and r["a"] <= c["panel_seq"] <= r["b"] for r in lot_runs(c["lot"], sel_["process"]))
            post = sum(1 for lid in post_lots for c in cause_claims_by_lot.get((s["cause"], lid), [])
                       if c["opened"] <= ASOF and c["kind"] == "SCENARIO" and in_scope(c))
            base = sum(1 for c in CLAIMS if c.get("scenario") == sid_)
            done = wto + Dd(30) <= ASOF
            result = "NOT_EFFECTIVE" if post > 0 else ("EFFECTIVE" if done else "MONITORING")
            add("effectiveness_check", check_id=nid("EFF", 5), corrective_action_id=ca["corrective_action_id"],
                metric=("조치 후 120일 생산 Lot 중 조치 범위(동일 설비·자재) 동일 원인 클레임 건수" if ca["action_role"] == "OCCURRENCE"
                        else "조치 후 120일 생산 Lot 전체(타 설비 포함) 동일 원인 클레임 건수"), baseline_value=base, target_value=0, post_value=post,
                window_from=I(impl), window_to=I(wto), result=result, checked_at=I(min(ASOF, wto + Dd(30))))
            ca["verification_status"] = result
            if ca["action_role"] == "OCCURRENCE" and s["shifts"]:
                item = list(s["shifts"])[0]
                proc = s["selector"]["process"]
                eqs = s["selector"]["equipment"]
                inj_set = set(INJ_RUNS[sid_])
                pre_vals = [o for o in R["process_observation"] if o["process_run_id"] in inj_set and o["item_code"] == item]
                n_pre, n_pre_oos = len(pre_vals), sum(1 for o in pre_vals if o["spc_flag"] == "OUT_OF_SPEC")
                post_runs = [rid for eq in eqs for rid in RUNS_BY_EQ[eq] if impl <= RUNS[rid]["s"] < min(ASOF, impl + Dd(60))]
                post_set = set(post_runs)
                vals = [o for o in R["process_observation"] if o["process_run_id"] in post_set and o["item_code"] == item] if post_set else []
                n_post = len(vals)
                n_post_oos = sum(1 for o in vals if o["spc_flag"] == "OUT_OF_SPEC")
                if n_post:
                    add("effectiveness_check", check_id=nid("EFF", 5), corrective_action_id=ca["corrective_action_id"],
                        metric=f"{item} 규격 이탈률(조치 후 60일)", baseline_value=round(100.0 * n_pre_oos / max(1, n_pre), 1), target_value=0,
                        post_value=round(100.0 * n_post_oos / n_post, 2), window_from=I(impl), window_to=I(min(ASOF, impl + Dd(60))),
                        result="EFFECTIVE" if n_post_oos == 0 else "NOT_EFFECTIVE", checked_at=I(min(ASOF, impl + Dd(61))))
            if ca["action_role"] != "OCCURRENCE":
                continue
            elig = [lid for lid, L in LOTS.items() if impl <= L["start"] < impl + Dd(60)]
            withc = sum(1 for lid in elig if cause_claims_by_lot.get((s["cause"], lid)))
            add("monitoring_cohort", cohort_id=nid("COH", 5), corrective_action_id=ca["corrective_action_id"],
                selection_rule_json=json.dumps({"lots_started_after": I(impl), "days": 60, "cause": s["cause"]}), observation_window_days=60,
                eligible_lots=len(elig), lots_with_claims=withc, report_as_of=I(ASOF))
        else:
            add("effectiveness_check", check_id=nid("EFF", 5), corrective_action_id=ca["corrective_action_id"],
                metric="개정 검사·관리 기준 적용 Lot 비율(%)", baseline_value=0, target_value=100, post_value=100,
                window_from=I(impl), window_to=I(min(ASOF, impl + Dd(30))), result="EFFECTIVE" if impl + Dd(30) <= ASOF else "MONITORING",
                checked_at=I(min(ASOF, impl + Dd(30))))
            ca["verification_status"] = "EFFECTIVE" if impl + Dd(30) <= ASOF else "MONITORING"
# an original incident's closure must reflect its recurrence (P01 -> P01R, v5: P04R, P05R, P12R, P09R)
for rs in C.SCENARIOS:
    orig = rs.get("recurrence_of")
    if orig in TIMELINE and rs["id"] in TIMELINE and TIMELINE[rs["id"]]["t0"] <= ASOF:
        for r in R["investigation"]:
            if r["investigation_id"] == TIMELINE[orig]["iid"]:
                r["status"] = "REOPENED_LINKED"
for sid_, T in TIMELINE.items():
    s = SCN[sid_]
    occ = [ca for ca in T.get("cas", []) if ca["action_role"] == "OCCURRENCE" and ca["implemented_at"]]
    targets = [(sid_, occ)]
    for tgt_sid, cas in targets:
        if not cas:
            continue
        ca = cas[0]
        impl = P(ca["implemented_at"])
        recurs = {x["id"] for x in C.SCENARIOS if x.get("recurrence_of") == sid_}
        rel = [c for c in CLAIMS if c.get("scenario") == sid_ or c.get("scenario") in recurs]
        for c in rel:
            if c["opened"] <= impl:
                continue
            after = int(LOTS[c["lot"]]["start"] >= impl)
            verdict = "RECURRENCE" if after else "PRE_ACTION_PRODUCT"
            add("recurrence_review", review_id=nid("REV", 6), rma_item_id=c["item_id"], corrective_action_id=ca["corrective_action_id"],
                lot_produced_after_action=after, symptom_match=1, cause_match=1, verdict=verdict,
                reviewed_at=I(min(ASOF, c["triage_at"] + Dd(U(1, 5)))))

# ---------------------------------------------------------------- service actions, statuses
for c in CLAIMS:
    dec = c.get("cause_decided")
    qa = "TRIAGED"
    if c["receipt_at"]:
        qa = "ANALYZING"
    if dec:
        qa = "CAUSE_DECIDED"
    if dec and P(next(r for r in R["investigation"] if r["investigation_id"] == c["inv"])["opened_at"]) and \
            next(r for r in R["investigation"] if r["investigation_id"] == c["inv"])["status"] in ("CLOSED", "REOPENED_LINKED", "MONITORING"):
        qa = "CLOSED" if dec + Dd(5) <= ASOF else qa
    cs = "OPEN"
    if dec:
        nd = c["cause"] == "NO_DEFECT_FOUND"
        if nd:
            at = "NO_CHARGE_SAMPLE_RETURN"
        elif c["kind"] == "SCENARIO" and CONT_MODE.get(c["cause"], DEFAULT_CONT)[0] == "RETURN_AND_REPLACE":
            at = "REPLACEMENT_SHIPMENT"
        elif c["kind"] == "SCENARIO":
            at = "SORTING_SUPPORT_AND_CREDIT"
        else:
            at = "CREDIT_NOTE"
        ex = dec + Dd(U(2, 10))
        add("service_action", action_id=nid("SVC", 7), rma_item_id=c["item_id"], action_type=at, policy_version="B2B-QA-2025",
            decided_at=I(dec), executed_at=I(clip(ex)), board_qty=0 if nd else c["defect"], result="EXECUTED" if clip(ex) else "SCHEDULED")
        add("communication", message_id=nid("MSG", 7), rma_id=c["rma_id"], investigation_id=None, customer_id=c["cust"], audience="customer",
            author_role="QA", message_text=("분석 결과 불량이 재현되지 않았습니다. 측정 조건 공유를 요청드립니다." if nd else
                                            f"분석 결과 원인이 확인되었습니다. 처리 방안: {at}. 상세 내용은 대책서로 회신드립니다."),
            sent_at=I(dec + H(2)))
        cs = "CLOSED" if clip(ex) else "RESPONDED"
    for r in R["rma_claim"]:
        pass
    c["qa_status"], c["cs_status"] = qa, cs
claim_row = {r["rma_id"]: r for r in R["rma_claim"]}
for c in CLAIMS:
    claim_row[c["rma_id"]]["qa_status"] = c["qa_status"]
    claim_row[c["rma_id"]]["cs_status"] = c["cs_status"]
for pn in PANELS.values():
    R["panel"].append(pn)
for code, lst in PLANS.items():
    for pl in lst:
        ca = pl["ca"] if pl["ca"] and not str(pl["ca"]).startswith("__") else None
        add("inspection_plan", plan_id=pl["plan_id"], plan_code=code, phase=pl["phase"], test_spec_ids_json=json.dumps(pl["specs"]),
            applies_to=pl["applies"], sample_rule=pl["rule"], sample_rate=pl["rate"], revision=pl["rev"], effective_from=I(pl["eff"]),
            effective_to=I(pl["to"]), change_reason=pl["reason"], corrective_action_id=ca, approval_status="APPROVED")

# ---------------------------------------------------------------- similar cases + evaluation
def toks(t):
    t = "".join(ch for ch in t if ch.isalnum() or ch == " ")
    return {t[i:i + 2] for i in range(len(t) - 1) if " " not in t[i:i + 2]}


TRAIN_END, VAL_END = P("2025-08-01"), P("2025-10-01")
split = lambda c: "train" if c["opened"] < TRAIN_END else ("validation" if c["opened"] < VAL_END else "test")
train_by_label = defaultdict(list)
for c in CLAIMS:
    c["toks"] = toks(c["text"].split(".", 1)[-1])
    if split(c) == "train":
        train_by_label[c["symptom"]].append(c)
for c in CLAIMS:
    if split(c) != "test":
        continue
    cands = train_by_label[c["symptom"]]
    sc = sorted(((len(c["toks"] & o["toks"]) / max(1, len(c["toks"] | o["toks"])), o) for o in cands), key=lambda x: -x[0])[:5]
    for score, o in sc:
        add("similar_cases", case_id=nid("SIM", 7), query_rma_id=c["rma_id"], reference_rma_id=o["rma_id"], searched_at=I(c["triage_at"]),
            similarity_score=round(score, 6), similarity_basis="reference_label_filter_then_char_bigram_jaccard",
            applicability="SYNTHETIC_BASELINE_REQUIRES_REVIEW", search_version="pcb-v30-baseline-1")

ev = OUT / "evaluation"
ev.mkdir(exist_ok=True)
cause_of_inv = {r["investigation_id"]: r for r in R["cause_assessment"]}
inv_cause = {}
for r in R["cause_assessment"]:
    inv_cause[r["investigation_id"]] = r
files = {k: (open(ev / f"{k}_inputs.jsonl", "w", encoding="utf-8"), open(ev / f"{k}_reference_labels.jsonl", "w", encoding="utf-8"))
         for k in ("train", "validation", "test")}
for c in CLAIMS:
    fi, fl = files[split(c)]
    fi.write(json.dumps({"rma_id": c["rma_id"], "customer_id": c["cust"], "opened_at": I(c["opened"]), "detection_stage": c["stage"],
                         "reported_part_no": PROD[LOTS[c["lot"]]["spec"]]["part_no"], "reported_lot_no": c["rep_lot"],
                         "reported_date_code": c["rep_dc"], "raw_text": c["text"]}, ensure_ascii=False) + "\n")
    fl.write(json.dumps({"rma_id": c["rma_id"], "minor_code": c["symptom"], "severity": c["sev"], "analysis_level": c["level"],
                         "cause_code": c["cause"], "cause_category": C.CAUSES[c["cause"]][0], "traced_lot_id": c["lot"],
                         "synthetic_reference": True}, ensure_ascii=False) + "\n")
for a, b in files.values():
    a.close(); b.close()
with open(ev / "lot_impact_reference.jsonl", "w", encoding="utf-8") as f:
    for sid_, T in TIMELINE.items():
        s = SCN[sid_]
        f.write(json.dumps({"investigation_id": T["iid"], "cause_code": s["cause"], "escape_code": s["escape"],
                            "key_dimensions": s["key_dims"], "impacted_lots": sorted(INJ[sid_]),
                            "impacted_lots_without_claims": sorted(l for l in INJ[sid_] if not any(c["lot"] == l and c.get("scenario") == sid_ for c in CLAIMS)),
                            "synthetic_reference": True}, ensure_ascii=False) + "\n")
gt = OUT / "generation_truth"
gt.mkdir(exist_ok=True)
with open(gt / "claims.jsonl", "w", encoding="utf-8") as f:
    for c in CLAIMS:
        f.write(json.dumps({"rma_id": c["rma_id"], "kind": c["kind"], "scenario": c["scenario"], "cause_code": c["cause"],
                            "symptom": c["symptom"], "lot_id": c["lot"], "panel_seq": c["panel_seq"], "investigation_id": c.get("inv")},
                           ensure_ascii=False) + "\n")
with open(gt / "scenarios.json", "w", encoding="utf-8") as f:
    out = []
    for s in C.SCENARIOS:
        T = TIMELINE.get(s["id"], {})
        out.append({k: v for k, v in s.items() if not k.startswith("_")} | {
            "injected_lot_count": len(INJ.get(s["id"], {})), "claims_generated": sum(1 for c in CLAIMS if c.get("scenario") == s["id"]),
            "effective_window_end": I(s.get("_eff_end")), "bad_material_lots": s.get("_bad_mlots", []),
            "milestones": {k: I(v) for k, v in T.items() if isinstance(v, datetime)}, "investigation_id": T.get("iid")})
    json.dump(out, f, ensure_ascii=False, indent=1)

# ---------------------------------------------------------------- write SQLite
dbp = OUT / "pcb_rma.sqlite"
if dbp.exists():
    dbp.unlink()
db = sqlite3.connect(dbp)
db.execute("pragma journal_mode=off")
db.execute("pragma synchronous=off")
for t, cols in SCHEMA.items():
    db.execute(f"CREATE TABLE {t} (\n  " + ",\n  ".join(cols) + "\n)")
for t, cols in SCHEMA.items():
    names = [c.split()[0] for c in cols]
    rows = R.get(t, [])
    db.executemany(f"INSERT INTO {t} ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                   [tuple(r.get(n) for n in names) for r in rows])
    print(t, len(rows), flush=True)
db.commit()
db.close()
print("claims", len(CLAIMS), "scenario", n_scen, "sporadic", n_spor)
