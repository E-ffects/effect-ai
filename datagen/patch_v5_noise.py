"""v5: process signals that are NOT the cause of any return ("red herrings"). Run after patch_v3_severity.py.

Real lines show condition drifts and equipment alarms that never reach a customer. Without them, any signal on a
lot points at the cause. Each episode below either shifts one condition item (inside spec) on one machine for a
week, or records an abnormal equipment event. No return is attached to them.
"""
import json
import math
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert "v3_severity" in manifest and "v5_noise" not in manifest, "Run after patch_v3_severity.py, once"
db = sqlite3.connect(root / "pcb_rma.sqlite")
rng = random.Random(51)

SHIFTS = [  # equipment, process, item, from, to, shift in sigma, note
    ("PLT-01", "PLATE", "current_density_asd", "2025-10-08", "2025-10-15", -3.5, "정류기 출력 일시 저하(규격 안, 재작업 없이 출하)"),
    ("DSM-02", "DESMEAR", "desmear_rate_mg_cm2", "2025-11-12", "2025-11-19", +3.2, "약품조 교체 직후 제거량 상승(규격 안)"),
]
EVENTS = [  # equipment, type, occurred, recorded delay h, metric, value, unit, description, result
    ("OLE-01", "NOZZLE_CHECK", "2025-11-05T10:20:00+09:00", 2, "clogged_nozzles", 2, "ea", "정기 점검에서 스프레이 노즐 2개 막힘 확인·즉시 교체", "ABNORMAL"),
    ("SMR-01", "EQUIPMENT_FAULT", "2025-10-21T14:05:00+09:00", 1, "zone2_heater_alarm", 1, "ea", "경화로 2존 히터 경보(센서 접촉 불량, 당일 복구)", "ABNORMAL"),
    ("SMR-01", "REPAIR", "2025-10-21T18:30:00+09:00", 1, None, None, None, "2존 온도 센서 커넥터 재체결", "COMPLETED"),
    ("DSM-02", "BATH_CHANGE", "2025-11-11T22:00:00+09:00", 2, "permanganate_age_m2", 1480, "m2", "정기 교체(처리 면적 1,480㎡)", "COMPLETED"),
    ("PLT-01", "EQUIPMENT_FAULT", "2025-10-08T07:40:00+09:00", 3, "rectifier_output_pct", 93, "%", "정류기 1번 채널 출력 93%(규격 안), 10-15 조정", "ABNORMAL"),
]


def iso(d):
    return d.isoformat(timespec="seconds")


report = {"shifts": [], "events": []}
with db:
    for eq, proc, item, start, end, k, note in SHIFTS:
        base = [r[0] for r in db.execute(
            """select o.value from process_observation o join process_run r using(process_run_id)
               where r.process_id=? and o.item_code=? and o.measured_at<? and o.measured_at>=?""",
            (proc, item, start, iso(datetime.fromisoformat(start + "T00:00:00+09:00") - timedelta(days=90))))]
        mean = sum(base) / len(base)
        sigma = math.sqrt(sum((v - mean) ** 2 for v in base) / len(base))
        rows = db.execute("""select o.observation_id, o.value, o.lower_limit, o.upper_limit
                             from process_observation o join process_run r using(process_run_id)
                             where r.equipment_id=? and r.process_id=? and o.item_code=? and r.started_at>=? and r.started_at<?""",
                          (eq, proc, item, start, end)).fetchall()
        margin = 0.1 * sigma
        for oid, v, lo, hi in rows:
            nv = min(max(v + k * sigma + rng.gauss(0, 0.3 * sigma), lo + margin), hi - margin)  # stays inside spec
            db.execute("update process_observation set value=?, spc_flag='NORMAL' where observation_id=?", (round(nv, 4), oid))
        report["shifts"].append({"equipment": eq, "item": item, "from": start, "to": end, "sigma": round(sigma, 4),
                                 "shift_sigma": k, "observations": len(rows), "note": note})
    last = max(int(r[0].split("-")[1]) for r in db.execute("select event_id from equipment_event"))
    for i, (eq, et, occ, delay, mname, mval, munit, desc, result) in enumerate(EVENTS, 1):
        rec = iso(datetime.fromisoformat(occ) + timedelta(hours=delay))
        db.execute("""insert into equipment_event (event_id, equipment_id, event_type, occurred_at, recorded_at, metric_name,
                      metric_value, metric_unit, description, result) values (?,?,?,?,?,?,?,?,?,?)""",
                   (f"EVT-{last + i:06d}", eq, et, occ, rec, mname, mval, munit, desc, result))
        report["events"].append({"event_id": f"EVT-{last + i:06d}", "equipment": eq, "type": et, "occurred_at": occ})

assert db.execute("select count(*) from process_observation where (value<lower_limit or value>upper_limit) and spc_flag='NORMAL'").fetchone()[0] == 0
db.close()
manifest["v5_noise"] = report
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=1))
