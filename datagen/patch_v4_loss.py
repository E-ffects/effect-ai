"""v4: quality loss (COPQ) ledger. Run after finalize_v3.py and before slim_v3.py
(it reads containment_action, return_receipt and panel-level costs that the slim step drops).

Adds
  cost_rule      every assumption used (value, unit, basis). Change values here and re-run to recompute.
  product_cost   panel / board cost and board selling price per product, derived from product_spec
  loss_ledger    one row per cost event: what, how many, unit cost, amount, basis, source record, occurred_at
  views          claim_loss, loss_by_month, loss_by_investigation

Cost types (COPQ categories)
  EXTERNAL_FAILURE  REPLACEMENT_BOARDS, CREDIT_REFUND, CUSTOMER_CHARGEBACK, SORTING_SUPPORT, LOGISTICS
  APPRAISAL         LAB_ANALYSIS, ENGINEERING
  CONTAINMENT       CONTAINMENT_SORT, CONTAINMENT_REWORK, CONTAINMENT_SCRAP, CONTAINMENT_RETURN_REPLACE

Recognition time (occurred_at) follows the event that incurs the cost, so period totals respect the snapshot:
  service_action.executed_at, claim_cause.decided_at, test_run.ended_at, return_receipt.received_at,
  containment_action.completed_at. Scheduled (not executed) actions are not booked.
All unit costs are assumptions for a synthetic dataset, not field values.
"""
import json
from pathlib import Path
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert manifest["version"] == "v3" and "v3_slim" not in manifest and "v4_loss" not in manifest, \
    "Run after finalize_v3.py and before slim_v3.py, once"
asof = manifest["as_of"]
db = sqlite3.connect(root / "pcb_rma.sqlite")
db.row_factory = sqlite3.Row
db.execute("pragma foreign_keys=on")

# ---------------------------------------------------------------- assumptions
RULES = [
    # board cost model (18x24 inch production panel)
    ("BOARD", "PANEL_COST_PER_LAYER", 8000, "KRW/panel/layer", "표준 FR-4 다층 패널의 층당 자재·공정 변동비 가정"),
    ("BOARD", "FACTOR_HDI", 1.6, "x", "레이저 비아·순차 적층 추가 공정"),
    ("BOARD", "FACTOR_LOW_LOSS", 1.3, "x", "저손실(고주파) 원판 자재 단가"),
    ("BOARD", "FACTOR_HEAVY_COPPER", 1.5, "x", "3oz 후동 도금·에칭 추가 비용 (BMS)"),
    ("BOARD", "FINISH_ENIG", 10000, "KRW/panel", "무전해 니켈·금 표면처리"),
    ("BOARD", "FINISH_IMSN", 6000, "KRW/panel", "무전해 주석 표면처리"),
    ("BOARD", "FINISH_OSP", 3000, "KRW/panel", "유기 보호막 표면처리"),
    ("BOARD", "YIELD", 0.95, "ratio", "최종 수율. 기판 원가 = 패널 원가 / 패널당 기판 수 / 수율"),
    ("BOARD", "MARGIN_AUTOMOTIVE", 0.30, "ratio", "판매가 = 원가 x (1 + 마진). 차량 등급 관리 비용 반영"),
    ("BOARD", "MARGIN_OTHER", 0.25, "ratio", "반도체·산업 고객 마진"),
    # customer-side loss by detection stage (multiplier on board selling price)
    ("CHARGEBACK", "STAGE_CUSTOMER_IQC", 1, "x board price", "수입검사 발견: 기판만 폐기"),
    ("CHARGEBACK", "STAGE_SMT_REFLOW", 5, "x board price", "리플로 발견: 기판 + 실장 부품 일부 손실"),
    ("CHARGEBACK", "STAGE_ICT_FUNCTION", 10, "x board price", "ICT·기능검사 발견: 실장 완료 보드 폐기·재작업"),
    ("CHARGEBACK", "STAGE_CUSTOMER_RELIABILITY", 15, "x board price", "신뢰성 시험 발견: 완성 보드 + 시험 비용"),
    ("CHARGEBACK", "STAGE_FIELD", 50, "x board price", "필드 발견: 완성품 회수·교체, 현장 대응 (1:10:100 원리)"),
    ("CHARGEBACK", "CAP_AUTOMOTIVE", 30000000, "KRW/claim", "공급 계약상 반품 1건 배상 상한 가정 (차량)"),
    ("CHARGEBACK", "CAP_SEMICONDUCTOR", 15000000, "KRW/claim", "배상 상한 가정 (반도체)"),
    ("CHARGEBACK", "CAP_INDUSTRIAL", 5000000, "KRW/claim", "배상 상한 가정 (산업)"),
    # handling
    ("HANDLING", "LOGISTICS_DOMESTIC", 50000, "KRW/shipment", "국내 긴급 택배·화물"),
    ("HANDLING", "LOGISTICS_OVERSEAS", 300000, "KRW/shipment", "해외 항공 특송"),
    ("HANDLING", "SORTING_SUPPORT_DOMESTIC", 400000, "KRW/claim", "고객사 선별 지원 인력 1인·1일"),
    ("HANDLING", "SORTING_SUPPORT_OVERSEAS", 1500000, "KRW/claim", "해외 고객사 선별 지원 출장 (인건비·체재비)"),
    ("HANDLING", "ENGINEER_RATE", 60000, "KRW/hour", "품질 엔지니어 시간당 인건비(부대비 포함)"),
    ("HANDLING", "HOURS_L1", 1, "hour/claim", "L1: 반송품 확인·회신"),
    ("HANDLING", "HOURS_L2", 3, "hour/claim", "L2: 접수 검토·원인 적용·회신 (조사 공통 분석은 시험비로 반영)"),
    ("HANDLING", "HOURS_L3", 8, "hour/claim", "L3: 원인 검토·승인·고객 대응"),
    # containment (per panel)
    ("CONTAINMENT", "SORT_ET_PER_PANEL", 3000, "KRW/panel", "100% E-Test 재검 선별"),
    ("CONTAINMENT", "SORT_VISUAL_ET_PER_PANEL", 2500, "KRW/panel", "외관 + E-Test 선별"),
    ("CONTAINMENT", "SORT_COUPON_TDR_PER_PANEL", 2000, "KRW/panel", "패널 쿠폰 TDR 선별"),
    ("CONTAINMENT", "SORT_SIMPLE_PER_PANEL", 1000, "KRW/panel", "치수·휨 선별"),
    ("CONTAINMENT", "REBAKE_PER_PANEL", 500, "KRW/panel", "125℃ 4시간 재베이킹"),
    ("CONTAINMENT", "REPACK_PER_PANEL", 300, "KRW/panel", "재포장"),
    ("CONTAINMENT", "RELABEL_PER_PANEL", 300, "KRW/panel", "라벨 재부착·마킹 확인"),
]
LAB = {   # per test item in one returned / retained sample analysis
    "LAB_XSEC_VOID": 200000, "LAB_XSEC_DELAM": 200000, "LAB_XSEC_ICD": 200000, "LAB_XSEC_CRACK": 200000,
    "LAB_XSEC_HOLE_CU": 200000, "LAB_SEM_NI_CORR": 400000, "LAB_EDS_P": 400000, "LAB_IST_DR": 500000,
    "LAB_SIR": 300000, "LAB_REFLOW_REPRO": 150000, "LAB_DSC_DTG": 150000, "LAB_XRAY": 100000,
    "LAB_REG_OFFSET": 100000, "LAB_TDR_IMP": 100000, "LAB_WARPAGE": 100000, "LAB_HOLE_WALL_ROUGH": 100000,
    "LAB_IONIC": 80000, "LAB_SOLDERABILITY": 80000, "LAB_PEEL": 80000, "LAB_MOISTURE": 50000,
    "LAB_ET_RETEST": 50000, "LAB_XRF_AU": 50000, "LAB_SN_THK": 50000, "LAB_MICROSCOPE": 30000,
    "LAB_LINE_WIDTH": 30000, "LAB_HOLE_POS": 30000, "LAB_DIM": 30000, "LAB_SM_TAPE": 20000,
    "LAB_LABEL_MATCH": 10000, "IQC_CCL_VOID": 100000, "IQC_PP_FLOW": 100000, "IQC_CU_PEEL": 100000,
    "IQC_INK_VISC": 100000,
}
RULES += [("LAB", k, v, "KRW/test item", "반품·보관 시료 분석 외주·내부 시험 단가 가정") for k, v in LAB.items()]
R = {key: value for _, key, value, _, _ in RULES}

CONTAINMENT_MAP = {   # action_type -> (cost_type, per-panel rule keys, scrap?, logistics?)
    "SORT_100PCT_ET_RETEST": ("CONTAINMENT_SORT", ["SORT_ET_PER_PANEL"], False, False),
    "SORT_VISUAL_AND_ET": ("CONTAINMENT_SORT", ["SORT_VISUAL_ET_PER_PANEL"], False, False),
    "SORT_BY_PANEL_COUPON_TDR": ("CONTAINMENT_SORT", ["SORT_COUPON_TDR_PER_PANEL"], False, False),
    "SORT_DIMENSION": ("CONTAINMENT_SORT", ["SORT_SIMPLE_PER_PANEL"], False, False),
    "SORT_WARPAGE": ("CONTAINMENT_SORT", ["SORT_SIMPLE_PER_PANEL"], False, False),
    "HOLD_THEN_SORT_THEN_RELEASE": ("CONTAINMENT_SORT", ["SORT_VISUAL_ET_PER_PANEL"], False, False),
    "HOLD_THEN_SORT_WARPAGE": ("CONTAINMENT_SORT", ["SORT_SIMPLE_PER_PANEL"], False, False),
    "REBAKE_125C_4H_BEFORE_USE": ("CONTAINMENT_REWORK", ["REBAKE_PER_PANEL"], False, False),
    "HOLD_THEN_REBAKE_AND_REPACK": ("CONTAINMENT_REWORK", ["REBAKE_PER_PANEL", "REPACK_PER_PANEL"], False, False),
    "RELABEL_AFTER_MARKING_CHECK": ("CONTAINMENT_REWORK", ["RELABEL_PER_PANEL"], False, False),
    "HOLD_THEN_SCRAP": ("CONTAINMENT_SCRAP", [], True, False),
    "RETURN_AND_REPLACE": ("CONTAINMENT_RETURN_REPLACE", [], True, True),
}
CATEGORY = {"REPLACEMENT_BOARDS": "EXTERNAL_FAILURE", "CREDIT_REFUND": "EXTERNAL_FAILURE",
            "CUSTOMER_CHARGEBACK": "EXTERNAL_FAILURE", "SORTING_SUPPORT": "EXTERNAL_FAILURE",
            "LOGISTICS": "EXTERNAL_FAILURE", "LAB_ANALYSIS": "APPRAISAL", "ENGINEERING": "APPRAISAL",
            "CONTAINMENT_SORT": "CONTAINMENT", "CONTAINMENT_REWORK": "CONTAINMENT",
            "CONTAINMENT_SCRAP": "CONTAINMENT", "CONTAINMENT_RETURN_REPLACE": "CONTAINMENT"}

ledger = []


def book(cost_type, amount, quantity, unit, unit_cost, basis, source_table, source_id, occurred_at,
         rma_id=None, rma_item_id=None, investigation_id=None, customer_id=None, spec_id=None):
    if occurred_at is None or occurred_at > asof or amount <= 0:
        return
    ledger.append((f"LOSS-{len(ledger) + 1:07d}", CATEGORY[cost_type], cost_type, rma_id, rma_item_id, investigation_id,
                   customer_id, spec_id, quantity, unit, round(unit_cost), round(amount), basis, source_table,
                   source_id, occurred_at))


with db:
    db.execute("""create table cost_rule (
        rule_id text primary key not null, rule_group text not null, value real not null, unit text not null,
        basis text not null, is_assumption integer not null)""")
    db.executemany("insert into cost_rule values (?,?,?,?,?,1)",
                   [(key, group, value, unit, basis) for group, key, value, unit, basis in RULES])

    db.execute("""create table product_cost (
        spec_id text primary key not null references product_spec(spec_id) deferrable initially deferred,
        panel_cost_krw integer not null, board_cost_krw integer not null, board_price_krw integer not null,
        basis text not null)""")
    product = {}
    for p in db.execute("select * from product_spec").fetchall():
        factors, notes = 1.0, []
        if p["hdi"]:
            factors *= R["FACTOR_HDI"]; notes.append("HDI")
        if "저손실" in p["base_material"]:
            factors *= R["FACTOR_LOW_LOSS"]; notes.append("저손실 자재")
        if "후동" in p["product_family"] or "3oz" in p["product_family"]:
            factors *= R["FACTOR_HEAVY_COPPER"]; notes.append("후동")
        finish = {"ENIG": "FINISH_ENIG", "ImSn": "FINISH_IMSN", "OSP": "FINISH_OSP"}[p["surface_finish"]]
        panel = R["PANEL_COST_PER_LAYER"] * p["layer_count"] * factors + R[finish]
        board = panel / p["boards_per_panel"] / R["YIELD"]
        margin = R["MARGIN_AUTOMOTIVE"] if p["application"] == "AUTOMOTIVE" else R["MARGIN_OTHER"]
        price = board * (1 + margin)
        basis = (f"패널 = {R['PANEL_COST_PER_LAYER']:,.0f} x {p['layer_count']}층"
                 + (f" x {factors:g}({', '.join(notes)})" if notes else "") + f" + {p['surface_finish']} {R[finish]:,.0f}; "
                 f"기판 = 패널 / {p['boards_per_panel']}개 / 수율 {R['YIELD']}; 판매가 = 원가 x {1 + margin:.2f}")
        product[p["spec_id"]] = (panel, board, price)
        db.execute("insert into product_cost values (?,?,?,?,?)", (p["spec_id"], round(panel), round(board), round(price), basis))

    # claim context
    claims = {r["rma_item_id"]: dict(r) for r in db.execute("""
        select i.rma_item_id, i.rma_id, i.claimed_defect_qty, r.customer_id, r.detection_stage, cu.industry, cu.region,
               l.spec_id, t.analysis_level, cc.applicability, cc.decided_at, a.investigation_id, a.cause_category
        from rma_item i join rma_claim r using(rma_id) join customer cu using(customer_id)
        join production_lot l on l.lot_id=i.lot_id join qa_triage t using(rma_id)
        join claim_cause cc using(rma_item_id) join cause_assessment a using(cause_assessment_id)""")}

    def logistics_rate(region):
        return R["LOGISTICS_OVERSEAS"] if region == "해외" else R["LOGISTICS_DOMESTIC"]

    # 1) service actions: replacement boards, credit/refund, sorting support, replacement logistics
    for s in db.execute("select * from service_action where executed_at is not null").fetchall():
        c = claims[s["rma_item_id"]]
        _, board, price = product[c["spec_id"]]
        ctx = dict(rma_id=c["rma_id"], rma_item_id=s["rma_item_id"], investigation_id=c["investigation_id"],
                   customer_id=c["customer_id"], spec_id=c["spec_id"])
        if s["action_type"] == "REPLACEMENT_SHIPMENT":
            book("REPLACEMENT_BOARDS", s["board_qty"] * board, s["board_qty"], "board", board,
                 "교체 기판 수량 x 기판 원가", "service_action", s["action_id"], s["executed_at"], **ctx)
            rate = logistics_rate(c["region"])
            book("LOGISTICS", rate, 1, "shipment", rate, f"교체품 발송 ({c['region']})",
                 "service_action", s["action_id"], s["executed_at"], **ctx)
        if s["action_type"] in ("CREDIT_NOTE", "SORTING_SUPPORT_AND_CREDIT"):
            book("CREDIT_REFUND", s["board_qty"] * price, s["board_qty"], "board", price,
                 "크레딧(대금 차감) 수량 x 판매가", "service_action", s["action_id"], s["executed_at"], **ctx)
        if s["action_type"] == "SORTING_SUPPORT_AND_CREDIT":
            rate = R["SORTING_SUPPORT_OVERSEAS"] if c["region"] == "해외" else R["SORTING_SUPPORT_DOMESTIC"]
            book("SORTING_SUPPORT", rate, 1, "claim", rate, f"고객사 선별 지원 ({c['region']})",
                 "service_action", s["action_id"], s["executed_at"], **ctx)

    # 2) customer charge-back: only when a manufacturing cause is applied to the claim
    capped = 0
    for item_id, c in claims.items():
        if c["decided_at"] is None or c["applicability"] not in ("APPLIES", "APPLIES_PHOTO_ONLY") \
                or c["cause_category"] not in ("PROCESS", "MATERIAL", "INSPECTION"):
            continue
        _, _, price = product[c["spec_id"]]
        multiple = R[f"STAGE_{c['detection_stage']}"]
        raw = c["claimed_defect_qty"] * price * multiple
        cap = R[f"CAP_{c['industry']}"]
        amount = min(raw, cap)
        capped += raw > cap
        basis = f"불량 {c['claimed_defect_qty']}장 x 판매가 x 발견 단계 배수 {multiple:g} ({c['detection_stage']})"
        if raw > cap:
            basis += f", 배상 상한 {cap:,.0f}원 적용"
        book("CUSTOMER_CHARGEBACK", amount, c["claimed_defect_qty"], "board", amount / c["claimed_defect_qty"], basis,
             "claim_cause", item_id, c["decided_at"], rma_id=c["rma_id"], rma_item_id=item_id,
             investigation_id=c["investigation_id"], customer_id=c["customer_id"], spec_id=c["spec_id"])

    # 3) engineering hours: when the cause is applied to the claim
    for item_id, c in claims.items():
        if c["decided_at"] is None:
            continue
        hours = R[f"HOURS_{c['analysis_level']}"]
        book("ENGINEERING", hours * R["ENGINEER_RATE"], hours, "hour", R["ENGINEER_RATE"],
             f"분석 수준 {c['analysis_level']} {hours:g}시간 x 시간당 인건비", "claim_cause", item_id, c["decided_at"],
             rma_id=c["rma_id"], rma_item_id=item_id, investigation_id=c["investigation_id"],
             customer_id=c["customer_id"], spec_id=c["spec_id"])

    # 4) inbound sample logistics
    for r in db.execute("""select rr.receipt_id, rr.received_at, a.rma_item_id from return_receipt rr
                           join return_authorization a using(authorization_id)""").fetchall():
        c = claims[r["rma_item_id"]]
        rate = logistics_rate(c["region"])
        book("LOGISTICS", rate, 1, "shipment", rate, f"반품 시료 회수 ({c['region']})", "return_receipt", r["receipt_id"],
             r["received_at"], rma_id=c["rma_id"], rma_item_id=r["rma_item_id"], investigation_id=c["investigation_id"],
             customer_id=c["customer_id"], spec_id=c["spec_id"])

    # 5) lab analysis: each test item run on a returned / retained / reference / stock sample
    lots = dict(db.execute("select lot_id, spec_id from production_lot").fetchall())
    for t in db.execute("""select t.test_run_id, t.rma_item_id, t.investigation_id, t.lot_id, t.ended_at,
                                  group_concat(distinct m.test_spec_id) specs
                           from test_run t join test_measurement m using(test_run_id)
                           where t.phase='RMA_LAB' group by t.test_run_id""").fetchall():
        specs = t["specs"].split(",")
        amount = sum(LAB.get(s, 0) for s in specs)
        c = claims.get(t["rma_item_id"]) if t["rma_item_id"] else None
        book("LAB_ANALYSIS", amount, len(specs), "test item", amount / len(specs), "시험 항목별 단가 합: " + ", ".join(specs),
             "test_run", t["test_run_id"], t["ended_at"],
             rma_id=c["rma_id"] if c else None, rma_item_id=t["rma_item_id"],
             investigation_id=(c["investigation_id"] if c else t["investigation_id"]),
             customer_id=c["customer_id"] if c else None, spec_id=c["spec_id"] if c else lots.get(t["lot_id"]))

    # 6) containment (investigation level)
    regions = dict(db.execute("select customer_id, region from customer").fetchall())
    for a in db.execute("select * from containment_action where completed_at is not null").fetchall():
        kind = a["action_type"]
        if kind not in CONTAINMENT_MAP and kind.startswith("HOLD_THEN_") and kind[10:] in CONTAINMENT_MAP:
            kind = kind[10:]  # factory hold followed by the same sorting as at the customer
        cost_type, keys, scrap, ship = CONTAINMENT_MAP[kind]
        spec = lots[a["lot_id"]]
        panel = product[spec][0]
        unit = sum(R[k] for k in keys) + (panel if scrap else 0)
        basis = (f"{a['qty_panels']}패널 x " + (" + ".join(keys + (["패널 원가"] if scrap else []))) + f" ({a['action_type']})")
        book(cost_type, a["qty_panels"] * unit, a["qty_panels"], "panel", unit, basis, "containment_action",
             a["containment_id"], a["completed_at"], investigation_id=a["investigation_id"], customer_id=a["customer_id"],
             spec_id=spec)
        if ship and a["customer_id"]:
            rate = logistics_rate(regions[a["customer_id"]])
            book("LOGISTICS", rate * 2, 2, "shipment", rate, "고객 재고 회수·교체품 발송 (왕복)", "containment_action",
                 a["containment_id"], a["completed_at"], investigation_id=a["investigation_id"],
                 customer_id=a["customer_id"], spec_id=spec)

    db.execute("""create table loss_ledger (
        ledger_id text primary key not null, cost_category text not null, cost_type text not null,
        rma_id text references rma_claim(rma_id) deferrable initially deferred,
        rma_item_id text references rma_item(rma_item_id) deferrable initially deferred,
        investigation_id text references investigation(investigation_id) deferrable initially deferred,
        customer_id text references customer(customer_id) deferrable initially deferred,
        spec_id text references product_spec(spec_id) deferrable initially deferred,
        quantity real not null, unit text not null, unit_cost_krw integer not null, amount_krw integer not null,
        basis text not null, source_table text not null, source_id text not null, occurred_at text not null)""")
    db.executemany("insert into loss_ledger values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ledger)
    db.execute("create index ix_loss_ledger_rma on loss_ledger(rma_id)")
    db.execute("create index ix_loss_ledger_occurred on loss_ledger(occurred_at)")
    db.execute("""create view claim_loss as
        select rma_id, sum(amount_krw) total_krw,
          sum(case when cost_type='CREDIT_REFUND' then amount_krw else 0 end) refund_krw,
          sum(case when cost_type='REPLACEMENT_BOARDS' then amount_krw else 0 end) replacement_krw,
          sum(case when cost_type='CUSTOMER_CHARGEBACK' then amount_krw else 0 end) chargeback_krw,
          sum(case when cost_category='APPRAISAL' then amount_krw else 0 end) appraisal_krw,
          sum(case when cost_type in ('LOGISTICS','SORTING_SUPPORT') then amount_krw else 0 end) handling_krw
        from loss_ledger where rma_id is not null group by rma_id""")
    db.execute("""create view loss_by_month as
        select substr(occurred_at,1,7) month, cost_category, cost_type, count(*) entries, sum(amount_krw) amount_krw
        from loss_ledger group by 1,2,3""")
    db.execute("""create view loss_by_investigation as
        select l.investigation_id, i.title, i.analysis_level, sum(l.amount_krw) total_krw,
          sum(case when l.cost_category='CONTAINMENT' then l.amount_krw else 0 end) containment_krw,
          sum(case when l.cost_type='CUSTOMER_CHARGEBACK' then l.amount_krw else 0 end) chargeback_krw
        from loss_ledger l join investigation i using(investigation_id) group by 1,2,3""")

# ---------------------------------------------------------------- checks
def scalar(sql):
    return db.execute(sql).fetchone()[0]


checks = {
    "loss_after_snapshot": f"select count(*) from loss_ledger where occurred_at>'{asof}'",
    "loss_non_positive": "select count(*) from loss_ledger where amount_krw<=0",
    "executed_service_action_without_loss": """
        select count(*) from service_action s where s.executed_at is not null and s.action_type<>'NO_CHARGE_SAMPLE_RETURN'
        and not exists (select 1 from loss_ledger l where l.source_table='service_action' and l.source_id=s.action_id)""",
    "scheduled_service_action_booked": """
        select count(*) from loss_ledger l join service_action s on l.source_id=s.action_id
        where l.source_table='service_action' and s.executed_at is null""",
    "chargeback_on_non_manufacturing_cause": """
        select count(*) from loss_ledger l join claim_cause cc on cc.rma_item_id=l.rma_item_id
        join cause_assessment a using(cause_assessment_id)
        where l.cost_type='CUSTOMER_CHARGEBACK' and a.cause_category not in ('PROCESS','MATERIAL','INSPECTION')""",
    "chargeback_above_cap": """
        select count(*) from loss_ledger l join customer cu using(customer_id) join cost_rule r on r.rule_id='CAP_'||cu.industry
        where l.cost_type='CUSTOMER_CHARGEBACK' and l.amount_krw>r.value""",
    "completed_containment_without_loss": """
        select count(*) from containment_action c where c.completed_at is not null and not exists
        (select 1 from loss_ledger l where l.source_table='containment_action' and l.source_id=c.containment_id)""",
}
for name, sql in checks.items():
    value = scalar(sql)
    assert value == 0, (name, value)
    manifest["checks"][name] = {"value": value, "ok": True}
assert scalar("pragma integrity_check") == "ok"
assert not db.execute("pragma foreign_key_check").fetchone()

amounts = sorted(r[0] for r in db.execute("select total_krw from claim_loss"))
manifest["version"] = "v4"
manifest["based_on"] = "PCB_RMA_dataset_v31 + v3 patches + patch_v4_loss.py (+ slim_v3.py)"
manifest["v4_loss"] = {
    "ledger_rows": len(ledger),
    "total_krw": scalar("select sum(amount_krw) from loss_ledger"),
    "by_type_krw": dict(db.execute("select cost_type, sum(amount_krw) from loss_ledger group by 1 order by 2 desc")),
    "by_category_krw": dict(db.execute("select cost_category, sum(amount_krw) from loss_ledger group by 1 order by 2 desc")),
    "claims_with_loss": len(amounts),
    "claim_loss_median_krw": amounts[len(amounts) // 2] if amounts else 0,
    "claim_loss_mean_krw": round(sum(amounts) / len(amounts)) if amounts else 0,
    "chargeback_capped_claims": capped,
}
manifest["table_rows"].update({t: scalar(f"select count(*) from {t}") for t in ("cost_rule", "product_cost", "loss_ledger")})
manifest["views"] = sorted(set(manifest["views"]) | {"claim_loss", "loss_by_month", "loss_by_investigation"})
db.close()
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest["v4_loss"], ensure_ascii=False, indent=2))
