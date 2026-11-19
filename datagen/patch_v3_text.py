"""v3 part 2: claim text (run after patch_v3.py).

  a. paraphrases: about half of the claims use a new wording of the same symptom, 5% with spacing noise
  b. INSUFFICIENT_INFO (3%): vague intake text, CS question and customer reply before QA triage
  c. UNCLASSIFIED (1%): symptom outside the 18 codes on no-defect-found claims
  d. ELEC04: half of the genuine impedance claims carry customer measurement conditions
  e. SOLD01/STRC03 boundary: "BGA 미접합" with wetting evidence stays SOLD01
Labels follow the text rules in PCB_소분류_라벨_예외기준.md.
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
assert manifest["version"] == "v31" and "v3_patch" in manifest and "v3_text" not in manifest, \
    "Run after patch_v3.py, once"
asof = manifest["as_of"]
sys.path.insert(0, str(root))
import pcb_config as C  # noqa: E402

db = sqlite3.connect(root / "pcb_rma.sqlite")
db.row_factory = sqlite3.Row
db.execute("pragma foreign_keys=on")
rng = random.Random(3202)
report = {}


def iso(value):
    return value.isoformat(timespec="seconds")


def dt(value):
    return datetime.fromisoformat(value)


def H(hours):
    return timedelta(hours=hours)


# Same stage rule as generation: test words only for reliability/field, returned-unit words only for field.
def allowed(phrase, stage):
    if ("시험" in phrase or "HAST" in phrase) and stage not in ("CUSTOMER_RELIABILITY", "FIELD"):
        return False
    if ("회수품" in phrase or "필드" in phrase) and stage != "FIELD":
        return False
    return True


PARAPHRASE = {
    "ELEC01": ["일부 회로에서 open 판정이 나옵니다", "도통 검사에서 단선으로 나옵니다", "패턴이 끊어져 신호가 전달되지 않습니다",
               "오픈 NG가 발생합니다", "특정 핀 간 저항이 무한대로 측정됩니다"],
    "ELEC02": ["인접 회로끼리 붙어 short 판정됩니다", "숏트 불량이 발생했습니다", "전원과 GND 사이 저항이 0Ω에 가깝습니다",
               "미세 피치 구간에서 패턴 간 단락이 발생합니다"],
    "ELEC03": ["냉열 사이클 후 저항이 튀는 현상이 있습니다", "가열하면 끊겼다가 식으면 다시 정상입니다",
               "열을 가하면 간헐적으로 단선됩니다", "진동이 있을 때만 신호가 끊깁니다"],
    "ELEC04": ["차동 임피던스가 목표값보다 낮게 나옵니다", "TDR 파형에서 임피던스가 규격 하한 아래로 떨어집니다",
               "임피던스 관리 라인이 규격을 만족하지 못합니다"],
    "ELEC05": ["습도가 높으면 누설이 생깁니다", "패턴 사이 절연이 떨어져 리크 전류가 측정됩니다", "절연 저항이 낮아 간헐 오동작이 납니다"],
    "SOLD01": ["솔더 젖음성이 나빠 납이 올라가지 않습니다", "납이 패드에 묻지 않고 튕겨 나갑니다",
               "솔더링 후 패드 일부가 드러나 납땜이 덜 됩니다"],
    "SOLD02": ["BGA가 패드 표면에서 통째로 떨어지고 파단면이 거뭇합니다", "접합부가 니켈층 계면에서 깨집니다",
               "충격 후 BGA 볼이 패드에서 분리되며 패드가 검게 보입니다"],
    "SOLD03": ["부품을 떼어낼 때 패드까지 같이 뜯깁니다", "리워크 시 동박 패드가 들립니다", "패드가 기판에서 분리되어 떠 있습니다"],
    "STRC01": ["리플로 후 기판에 물집처럼 부푼 곳이 생깁니다", "블리스터가 발생했습니다", "실장 후 기판 표면이 볼록하게 솟아 있습니다"],
    "STRC02": ["기판 층이 갈라져 있습니다", "층간이 분리되어 벌어져 있습니다", "단면에서 레이어 사이 틈이 보입니다", "디라미 불량이 발생했습니다"],
    "STRC03": ["기판이 휘어 있어 실장 지그에 안착되지 않습니다", "보우·트위스트가 규격을 초과합니다", "기판이 바나나처럼 휘었습니다"],
    "STRC04": ["비아 내벽 도금에 금이 가 있습니다", "홀 도금층 크랙이 보입니다", "스루홀 배럴 균열이 확인됩니다"],
    "APPR01": ["PSR이 들떠서 떨어집니다", "솔더마스크가 벗겨집니다", "녹색 잉크가 일어나 있습니다"],
    "APPR02": ["기판에 긁힌 자국이 있고 동이 보입니다", "표면에 찍힘·긁힘이 있습니다", "회로 위 PSR이 긁혀 구리가 드러났습니다"],
    "APPR03": ["기판에 먼지·이물이 붙어 있습니다", "패드가 얼룩지고 변색되어 있습니다", "표면에 오염 자국이 있습니다"],
    "DIM01": ["외곽 치수가 도면 공차를 벗어납니다", "기판이 커서 하우징에 들어가지 않습니다", "V-cut 위치가 틀어져 있습니다"],
    "DIM02": ["홀이 막혀 핀이 들어가지 않습니다", "홀 위치가 어긋나 부품 삽입이 안 됩니다", "드릴 홀 위치 편차가 큽니다"],
    "MARK01": ["라벨 정보와 기판 인쇄가 다릅니다", "박스 라벨과 실물 Date code가 안 맞습니다", "다른 품번 라벨이 붙어 있습니다"],
}
# Boundary wording: "미접합" with wetting evidence is a wetting defect, not warpage.
SOLD01_BGA = "BGA 볼 일부가 패드에 젖지 않아 미접합이 발생합니다"
VAGUE = {
    "ELEC": ["전기적 불량이 발생했습니다", "기능 검사에서 NG가 납니다", "제품이 동작하지 않습니다"],
    "ELEC04": ["고속 신호 품질이 나쁩니다"],
    "SOLD": ["BGA 미접합이 발생합니다", "실장 불량이 발생했습니다"],
    "STRC": ["기판 구조 불량이 의심됩니다", "실장 불량이 발생했습니다"],
    "APPR": ["외관 불량이 있습니다", "외관 NG가 발생했습니다"],
    "DIM": ["조립이 되지 않습니다"],
    "MARK": ["라벨 관련 문제가 있습니다"],
    "ANY": ["불량이 발생했습니다", "NG 다수 발생했습니다"],
}
OUT_OF_SCOPE = ["실크 인쇄가 번져 문자가 잘 보이지 않습니다", "기판 색상이 이전 Lot과 다르게 보입니다",
                "표면 광택이 Lot마다 차이 납니다", "기판에서 평소와 다른 냄새가 납니다",
                "실크 문자 위치가 도면과 다릅니다"]
CS_QUESTION = ("증상 확인을 위해 문의드립니다. 불량 현상(오픈·쇼트·간헐·외관·실장 등), 발생 위치, "
               "재현 조건(상온 상시 또는 온도·진동 후)을 알려 주십시오.")

truth_file = root / "generation_truth" / "claims.jsonl"
truth = {r["rma_id"]: r for r in map(json.loads, truth_file.read_text(encoding="utf-8").splitlines())}
rows = db.execute("""
    select r.rma_id,r.customer_id,r.opened_at,r.raw_text,r.detection_stage,i.rma_item_id,
           s.assessment_id,s.primary_label,s.created_at,s.evidence_spans_json,t.decided_at triage_at
    from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
    join qa_triage t using(rma_id) order by r.rma_id""").fetchall()
major = dict(db.execute("select minor_code,major_code from symptom_definition"))


def current_span(row):
    for phrase in C.SYMPTOM_TEXT[row["primary_label"]]:
        if phrase in row["raw_text"]:
            return phrase
    raise AssertionError(row["rma_id"])


def next_id(table, column, prefix, width):
    last = db.execute(f"select max({column}) from {table}").fetchone()[0]
    number = int(last.split("-")[1]) if last else 0
    while True:
        number += 1
        yield f"{prefix}-{number:0{width}d}"


msg_ids = next_id("communication", "message_id", "MSG", 7)
ctr_ids = next_id("customer_test_report", "report_id", "CTR", 6)

with db:
    db.execute("alter table symptom_assessment add column intake_label text")
    db.execute("alter table symptom_assessment add column reclassified_at text")
    db.executemany("insert into symptom_definition values (?,?,?,?,?,?,?,?,?)", [
        ("INSUFFICIENT_INFO", "NA", "미확정", "정보 부족", "pcb-v3", 0, None,
         "증상 서술이 없거나 결과만 서술(기능 불량, NG), 또는 소분류 후보 셋 이상",
         "Lot·D/C 누락은 추적 정보 부족이며 해당 없음. CS 재질의 후 재분류, 회신 전 임시 L2"),
        ("UNCLASSIFIED", "NA", "미확정", "분류 불가", "pcb-v3", 0, None,
         "증상 서술은 충분하나 18종 어디에도 해당하지 않음",
         "QA 확인 전 임시 L2. 같은 유형 30일 내 3건 이상이면 신규 소분류 후보"),
    ])
    db.execute("""update analysis_level_rule set condition_text=condition_text||', 또는 소분류 미확정(분류 불가) 임시 판정'
                  where rule_id='L2'""")

    used = set()
    by_kind = {}
    for row in rows:
        by_kind.setdefault(truth[row["rma_id"]]["kind"], []).append(row)

    # c. UNCLASSIFIED: cosmetic or out-of-list complaints that QA finds within spec.
    pool = [r for r in by_kind["NTF"]]
    rng.shuffle(pool)
    unclassified = pool[:100]
    for row in unclassified:
        span = current_span(row)
        phrase = rng.choice(OUT_OF_SCOPE)
        text = row["raw_text"].replace(span, phrase, 1)
        db.execute("update rma_claim set raw_text=? where rma_id=?", (text, row["rma_id"]))
        db.execute("""update symptom_assessment set primary_label='UNCLASSIFIED',intake_label='UNCLASSIFIED',
                      secondary_labels_json='[]',evidence_spans_json=? where assessment_id=?""",
                   (json.dumps([phrase], ensure_ascii=False), row["assessment_id"]))
        cust = db.execute("select industry from customer where customer_id=?", (row["customer_id"],)).fetchone()[0]
        db.execute("""update qa_triage set severity=0,characteristic_id=null,characteristic_symbol=null,analysis_level='L2',
                      rationale=? where rma_id=?""",
                   (f"불량 형태 분류 불가(18종 외 증상), 고객 {cust} → QA 확인 전 임시 L2", row["rma_id"]))
        db.execute("""update return_authorization set basis='L2 분석 대상 시료 회수(QA 접수 검토)'
                      where rma_item_id=?""", (row["rma_item_id"],))
        truth[row["rma_id"]]["intake_symptom"] = "UNCLASSIFIED"
        used.add(row["rma_id"])
    report["unclassified"] = len(unclassified)

    # b. INSUFFICIENT_INFO: the customer reply arrives before QA triage, so triage uses the clarified symptom.
    pool = [r for r in rows if r["rma_id"] not in used and truth[r["rma_id"]]["kind"] != "NTF_MEAS"
            and (dt(r["triage_at"]) - dt(r["opened_at"])) >= H(3)]
    rng.shuffle(pool)
    insufficient = pool[:300]
    for row in insufficient:
        label = row["primary_label"]
        span = current_span(row)
        options = VAGUE["ELEC04"] if label == "ELEC04" else \
            VAGUE["SOLD"] if label in ("SOLD01", "SOLD02", "STRC03") else VAGUE[major[label]]
        phrase = rng.choice(options + VAGUE["ANY"])
        text = row["raw_text"].replace(span, phrase, 1)
        opened, triaged = dt(row["opened_at"]), dt(row["triage_at"])
        asked = opened + H(rng.uniform(0.3, 0.8))
        replied = triaged - H(rng.uniform(0.3, 0.8))
        assert asked < replied
        db.execute("update rma_claim set raw_text=? where rma_id=?", (text, row["rma_id"]))
        db.executemany("insert into communication values (?,?,?,?,?,?,?,?)", [
            (next(msg_ids), row["rma_id"], None, row["customer_id"], "customer", "CS", CS_QUESTION, iso(asked)),
            (next(msg_ids), row["rma_id"], None, row["customer_id"], "CS", "customer",
             f"[고객 회신] {span}.", iso(replied)),
        ])
        db.execute("""update symptom_assessment set intake_label='INSUFFICIENT_INFO',reclassified_at=?,
                      created_at=max(created_at,?),evidence_spans_json=? where assessment_id=?""",
                   (iso(replied), iso(replied), json.dumps([span], ensure_ascii=False), row["assessment_id"]))
        truth[row["rma_id"]]["intake_symptom"] = "INSUFFICIENT_INFO"
        used.add(row["rma_id"])
    report["insufficient_info"] = len(insufficient)

    # a + e. Paraphrases on the remaining claims (NTF_MEAS keeps its fixed measurement wording).
    paraphrased = boundary = noisy = 0
    for row in rows:
        if row["rma_id"] in used or truth[row["rma_id"]]["kind"] == "NTF_MEAS":
            continue
        label = row["primary_label"]
        span = current_span(row)
        phrase = span
        if label == "SOLD01" and rng.random() < 0.25:
            phrase, boundary = SOLD01_BGA, boundary + 1
        elif rng.random() < 0.5:
            options = [p for p in PARAPHRASE[label] if allowed(p, row["detection_stage"])]
            if options:
                phrase, paraphrased = rng.choice(options), paraphrased + 1
        if rng.random() < 0.05:
            phrase, noisy = phrase.replace(" ", ""), noisy + 1
        if phrase != span:
            db.execute("update rma_claim set raw_text=? where rma_id=?",
                       (row["raw_text"].replace(span, phrase, 1), row["rma_id"]))
            spans = json.loads(row["evidence_spans_json"])
            spans = [phrase if s == span else s for s in spans] or [phrase]
            db.execute("update symptom_assessment set evidence_spans_json=? where assessment_id=?",
                       (json.dumps(spans, ensure_ascii=False), row["assessment_id"]))
    report["paraphrased"] = paraphrased
    report["sold01_bga_boundary"] = boundary
    report["spacing_noise"] = noisy

    # d. Genuine impedance claims: half carry the customer's measurement conditions.
    elec04 = [r for r in rows if r["rma_id"] not in used and r["primary_label"] == "ELEC04"
              and truth[r["rma_id"]]["cause_code"] == "IL_ETCH_OVER"]
    rng.shuffle(elec04)
    full, partial = elec04[:170], elec04[170:210]
    for kind, group in (("full", full), ("partial", partial)):
        for row in group:
            value = round(rng.uniform(41.0, 44.5), 1)
            fixture = rng.choice(["Polar CITS 프로브", "SMA 픽스처", "고객 자체 픽스처"])
            if kind == "full":
                rise = rng.choice([28, 35, 40])
                sentence = f"고객 측정 조건: 임피던스 쿠폰, {fixture}, 상승시간 {rise}ps, 측정값 {value}Ω입니다."
                cond = {"fixture": fixture, "coupon": "impedance_coupon", "rise_time_ps": rise, "cable_deskew": True}
                provided, verified = 1, 1
            else:
                sentence = f"고객 측정값 {value}Ω입니다({fixture} 사용, 측정 위치는 확인 중)."
                cond = {"fixture": fixture, "coupon": "unknown", "rise_time_ps": None, "cable_deskew": None}
                provided, verified = 1, 0
            text = db.execute("select raw_text from rma_claim where rma_id=?", (row["rma_id"],)).fetchone()[0]
            text = re.sub(r"(투입 [\d,]+pcs)", sentence + r" \1", text, count=1)
            db.execute("update rma_claim set raw_text=? where rma_id=?", (text, row["rma_id"]))
            db.execute("insert into customer_test_report values (?,?,?,?,?,?,?,?,?)",
                       (next(ctr_ids), row["rma_item_id"], "TDR_IMPEDANCE", value, "ohm",
                        json.dumps(cond, ensure_ascii=False), provided, verified,
                        iso(dt(row["opened_at"]) + H(rng.uniform(0.5, 6)))))
    report["elec04_conditions_full"] = len(full)
    report["elec04_conditions_partial"] = len(partial)

    db.execute("update symptom_assessment set intake_label=primary_label where intake_label is null")
    db.execute("update symptom_assessment set label_version='pcb-v3'")

# Generation truth keeps the generated symptom and records the intake wording class.
truth_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in truth.values()), encoding="utf-8")

checks = {
    "symptom_span_not_in_text": """
        select count(*) from symptom_assessment s join rma_item i using(rma_item_id) join rma_claim r using(rma_id)
        where s.intake_label<>'INSUFFICIENT_INFO' and instr(r.raw_text, json_extract(s.evidence_spans_json,'$[0]'))=0""",
    "insufficient_reply_after_triage": """
        select count(*) from symptom_assessment s join rma_item i using(rma_item_id) join qa_triage t using(rma_id)
        where s.intake_label='INSUFFICIENT_INFO' and s.reclassified_at>t.decided_at""",
    "future_customer_messages": f"select count(*) from communication where sent_at>'{asof}'",
    "customer_report_before_claim": """
        select count(*) from customer_test_report c join rma_item i using(rma_item_id) join rma_claim r using(rma_id)
        where c.received_at<r.opened_at""",
}
for name, sql in checks.items():
    value = db.execute(sql).fetchone()[0]
    assert value == 0, (name, value)
    manifest["checks"][name] = {"value": value, "ok": True}
assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
assert not db.execute("pragma foreign_key_check").fetchone()
report["intake_labels"] = dict(db.execute(
    "select intake_label,count(*) from symptom_assessment where intake_label in ('INSUFFICIENT_INFO','UNCLASSIFIED') group by 1"))
manifest["v3_text"] = report
db.close()
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
