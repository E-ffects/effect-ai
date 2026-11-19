"""v3 part 3: severity aligned with AIAG-VDA FMEA (2019) effects (run after patch_v3_text.py).

Severity = worst effect among own plant, customer (assembly) plant and end user; detectability is excluded.
Basis and per-code rationale: PCB_소분류_라벨_예외기준.md, section 6.

  ELEC01 7->8, ELEC02 7->8 : escaped open/short = loss of primary function
  STRC04 8->9              : same end effect as ELEC03 (intermittent open in the field)
  ELEC04 6->7              : high-speed signal degradation = degraded primary function
  DIM01  5->6              : assembly impossible -> 100% sorting, same as DIM02/STRC03

Triage levels are recomputed with the unchanged rule. Scenario investigations that now hold L3 claims
become L3 with a full 8D (D7 added as not started). Sporadic quarterly groups stay L2: QA keeps the
8D short form after confirming a single-unit defect, and the reason is written in scope_definition.
"""
import json
from pathlib import Path
import re
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert manifest["version"] == "v31" and "v3_text" in manifest and "v3_severity" not in manifest, \
    "Run after patch_v3_text.py, once"
db = sqlite3.connect(root / "pcb_rma.sqlite")
db.row_factory = sqlite3.Row
db.execute("pragma foreign_keys=on")

NEW_SEVERITY = {"ELEC01": 8, "ELEC02": 8, "STRC04": 9, "ELEC04": 7, "DIM01": 6}


def level_for(sev, char_symbol, industry, repeat):
    """Same rule as generate_v30.level_for / analysis_level_rule."""
    if sev >= 9 or char_symbol == "CC" or (industry == "AUTOMOTIVE" and sev >= 8):
        return "L3"
    if sev >= 6 or char_symbol == "SC" or repeat:
        return "L2"
    return "L1"


report = {}
with db:
    for code, sev in NEW_SEVERITY.items():
        db.execute("update symptom_definition set severity=?,label_version='pcb-v3' where minor_code=?", (sev, code))

    rows = db.execute("""
        select t.triage_id,t.rma_id,t.severity,t.characteristic_symbol,t.customer_industry,t.repeat_signal,
               t.analysis_level,t.rationale,s.primary_label,i.rma_item_id
        from qa_triage t join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
        where s.primary_label in (%s)""" % ",".join("?" * len(NEW_SEVERITY)), tuple(NEW_SEVERITY)).fetchall()
    changed_level = {}
    for row in rows:
        sev = NEW_SEVERITY[row["primary_label"]]
        level = level_for(sev, row["characteristic_symbol"], row["customer_industry"], row["repeat_signal"])
        rationale = re.sub(r"심각도 \d+", f"심각도 {sev}", row["rationale"])
        rationale = re.sub(r"→ L\d$", f"→ {level}", rationale)
        db.execute("update qa_triage set severity=?,analysis_level=?,rationale=? where triage_id=?",
                   (sev, level, rationale, row["triage_id"]))
        if level != row["analysis_level"]:
            key = f"{row['primary_label']} {row['analysis_level']}->{level}"
            changed_level[key] = changed_level.get(key, 0) + 1
            db.execute("update return_authorization set basis=? where rma_item_id=?",
                       (f"{level} 분석 대상 시료 회수(QA 접수 검토)", row["rma_item_id"]))
    report["triage_rows_updated"] = len(rows)
    report["analysis_level_changes"] = changed_level

    # Investigations now holding L3 claims.
    targets = db.execute("""
        select v.investigation_id,v.scope_definition,json_extract(v.scope_definition,'$.type') kind,
               count(*) l3_claims
        from investigation v join investigation_claim l using(investigation_id) join qa_triage t using(rma_id)
        where t.analysis_level='L3' and v.analysis_level<>'L3' group by 1""").fetchall()
    step_ids = (f"D8S-{n:06d}" for n in range(int(db.execute(
        "select max(substr(step_id,5)) from eight_d_step").fetchone()[0]) + 1, 10 ** 6))
    upgraded, kept = [], []
    for inv in targets:
        if inv["kind"] in ("SPORADIC_GROUP", "NO_DEFECT_GROUP"):
            scope = json.loads(inv["scope_definition"])
            scope["level_note"] = (f"접수 판정 L3 {inv['l3_claims']}건 포함. 공통점 추적으로 단발 결함을 확인해 "
                                   "8D 약식(L2)으로 관리")
            db.execute("update investigation set scope_definition=? where investigation_id=?",
                       (json.dumps(scope, ensure_ascii=False), inv["investigation_id"]))
            kept.append(inv["investigation_id"])
            continue
        db.execute("update investigation set analysis_level='L3' where investigation_id=?", (inv["investigation_id"],))
        for rep in db.execute("select report_id from qa_report where investigation_id=?", (inv["investigation_id"],)).fetchall():
            db.execute("update qa_report set report_type='8D_FULL' where report_id=?", (rep["report_id"],))
            if not db.execute("select 1 from eight_d_step where report_id=? and d_step='D7'", (rep["report_id"],)).fetchone():
                db.execute("insert into eight_d_step values (?,?,?,?,?,?,?,?,?)",
                           (next(step_ids), rep["report_id"], "D7", "재발 방지·예방", "품질팀", None, None,
                            "NOT_STARTED", "PFMEA·관리계획서 개정, 유사 설비·제품 수평 전개 예정"))
        upgraded.append(inv["investigation_id"])
    report["investigations_upgraded_to_L3"] = upgraded
    report["sporadic_groups_kept_L2_with_note"] = kept

    # PFMEA severity on document changes = highest symptom severity among the investigation's claims.
    db.execute("""
        update document_change as d set severity=(
          select max(sd.severity) from corrective_action ca join investigation_claim l using(investigation_id)
          join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
          join symptom_definition sd on sd.minor_code=s.primary_label
          where ca.corrective_action_id=d.corrective_action_id)
        where severity is not null""")

checks = {
    "triage_severity_not_symptom_severity": """
        select count(*) from qa_triage t join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
        join symptom_definition d on d.minor_code=s.primary_label where t.severity<>d.severity""",
    "L3_claim_in_lower_investigation_without_note": """
        select count(*) from qa_triage t join investigation_claim l using(rma_id) join investigation v using(investigation_id)
        where t.analysis_level='L3' and v.analysis_level<>'L3' and json_extract(v.scope_definition,'$.level_note') is null""",
    "full_8d_missing_any_step": """
        select count(*) from investigation i where i.analysis_level='L3' and not exists (
          select 1 from qa_report r join eight_d_step s using(report_id)
          where r.investigation_id=i.investigation_id and r.report_type='8D_FULL'
          group by r.report_id having count(distinct s.d_step)=8)""",
    "return_basis_level_mismatch": """
        select count(*) from return_authorization a join rma_item i using(rma_item_id) join qa_triage t using(rma_id)
        where a.basis not like t.analysis_level||' %'""",
}
for name, sql in checks.items():
    value = db.execute(sql).fetchone()[0]
    assert value == 0, (name, value)
    manifest["checks"][name] = {"value": value, "ok": True}
assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
assert not db.execute("pragma foreign_key_check").fetchone()
report["claims_by_analysis_level"] = dict(db.execute("select analysis_level,count(*) from qa_triage group by 1"))
manifest["claims_by_analysis_level"] = report["claims_by_analysis_level"]
manifest["v3_severity"] = report
db.close()
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
