"""v5: messier customer text for returns opened from 2025-10-01 (about 15%). Run after patch_v5_noise.py.

Customers write with typos, English terms, roundabout descriptions, and sometimes mention a second symptom.
The intake label (the right answer) does not change. The symptom phrase in the text and its evidence span are
rewritten together, so evidence spans still appear verbatim in the text.
Every change is kept in claim_text_variant (variant, original text) so evaluation can split clean and messy text.
"""
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert "v5_noise" in manifest and "v5_text" not in manifest, "Run after patch_v5_noise.py, once"
db = sqlite3.connect(root / "pcb_rma.sqlite")
rng = random.Random(55)
START = "2025-10-01"
RATES = [("typo", .05), ("english", .04), ("paraphrase", .03), ("two_symptoms", .02)]  # of classified returns

TYPO = [("했습니다", "햇습니다"), ("됩니다", "되요"), ("습니다", "슴니다"), ("떨어", "떨어러"), ("보입니다", "보임"),
        ("발생", "발셍"), ("확인", "확닌"), ("납니다", "남"), ("있습니다", "있음")]
ENGLISH = [("쇼트", "short"), ("단락", "short"), ("단선", "open"), ("오픈", "open"), ("부풂", "blister"), ("부푼", "blister"),
           ("층간 박리", "delam"), ("박리", "delam"), ("크랙", "crack"), ("임피던스", "impedance"), ("절연저항", "IR"),
           ("누설 전류", "leakage"), ("스크래치", "scratch"), ("긁힘", "scratch"), ("이물", "FM"), ("라벨", "label"),
           ("휨", "warpage"), ("패드", "pad"), ("기판", "PCB"), ("간헐적", "intermittent"), ("통신 단절", "comm loss")]
PARAPHRASE = {
    "ELEC01": ["해당 기능이 아예 동작하지 않고 핀 사이가 안 통합니다", "회로가 끊긴 것처럼 신호가 전혀 안 나옵니다"],
    "ELEC02": ["전원을 넣자마자 전류가 크게 튀고 패턴끼리 붙은 것 같습니다", "인접한 선끼리 붙어서 전원이 바로 떨어집니다"],
    "ELEC03": ["전원 넣고 따뜻해지면 가끔 꺼졌다 켜집니다", "차량에서 가끔 통신이 끊겼다가 다시 붙습니다"],
    "ELEC04": ["고속 신호 파형이 찌그러지고 측정값이 기준보다 낮게 나옵니다"],
    "ELEC05": ["습한 날 시험하면 패턴 사이로 전류가 새는 것 같습니다"],
    "SOLD01": ["납이 패드에 잘 안 묻고 동그랗게 뭉칩니다", "솔더가 패드에 안 퍼지고 볼이 붙지 않습니다"],
    "SOLD02": ["BGA를 떼어 보니 패드 표면째로 분리되고 면이 검게 보입니다"],
    "SOLD03": ["부품을 다시 붙이려고 떼는데 패드까지 같이 뜯겨 나옵니다"],
    "STRC01": ["리플로 지나고 나니 기판 표면이 볼록하게 올라왔습니다", "열을 받은 뒤 기판에 물집 같은 게 생겼습니다"],
    "STRC02": ["기판 가장자리를 보면 층이 갈라져 있습니다"],
    "STRC03": ["기판이 휘어서 지그에 안 맞습니다"],
    "STRC04": ["열 시험 후 비아 저항이 올라가고 잘라 보니 홀 안이 갈라져 있습니다"],
    "APPR01": ["초록색 코팅이 벗겨져서 떨어져 나옵니다", "테이프를 붙였다 떼면 솔더레지스트가 같이 묻어 나옵니다"],
    "APPR02": ["표면이 긁혀서 구리색이 보입니다"],
    "APPR03": ["표면에 뭔가 묻어 있고 패드 색이 얼룩덜룩합니다"],
    "DIM01": ["외곽 크기가 안 맞아서 케이스에 안 들어갑니다"],
    "DIM02": ["구멍 위치가 틀어져서 부품 리드가 안 들어갑니다"],
    "MARK01": ["박스에 붙은 정보와 기판에 찍힌 정보가 다릅니다"],
}
SECOND = {  # a minor second symptom mentioned in passing (the intake label stays the main symptom)
    "ELEC": " 일부 시료는 표면 변색도 보입니다.",
    "SOLD": " 몇 장은 PSR 가장자리가 살짝 들떠 있습니다.",
    "STRC": " 일부는 외곽 치수도 약간 커 보입니다.",
    "APPR": " 한두 장은 도통 검사에서 저항이 조금 높게 나옵니다.",
    "DIM": " 표면에 미세한 긁힘도 일부 있습니다.",
}


def typo(span):
    out = span
    for a, b in rng.sample(TYPO, len(TYPO)):
        if a in out:
            out = out.replace(a, b, 1)
            break
    if out == span and " " in out:
        i = rng.choice([m.start() for m in re.finditer(" ", out)])
        out = out[:i] + out[i + 1:]
    return out if out != span else None


def english(span):
    out, n = span, 0
    for a, b in ENGLISH:
        if a in out and n < 2:
            out = out.replace(a, b, 1)
            n += 1
    return out if out != span else None


rows = db.execute("""select r.rma_id, r.raw_text, s.assessment_id, s.intake_label, s.evidence_spans_json
                     from rma_claim r join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
                     where r.opened_at>=? order by r.rma_id""", (START,)).fetchall()
db.execute("""create table if not exists claim_text_variant (
  rma_id TEXT PRIMARY KEY NOT NULL REFERENCES rma_claim(rma_id) DEFERRABLE INITIALLY DEFERRED,
  variant TEXT NOT NULL,
  original_raw_text TEXT NOT NULL,
  original_evidence_spans_json TEXT NOT NULL
)""")
done, skipped = {}, 0
with db:
    for rma_id, text, aid, label, spans_json in rows:
        spans = json.loads(spans_json or "[]")
        if label == "INSUFFICIENT_INFO":
            if rng.random() < .3:  # vague text gets vaguer, still nothing about the symptom
                new = text.replace("습니다.", "습니다. 육안상으로는 특이점이 없습니다.", 1)
                if new != text:
                    db.execute("update rma_claim set raw_text=? where rma_id=?", (new, rma_id))
                    db.execute("insert into claim_text_variant values (?,?,?,?)", (rma_id, "vague", text, spans_json))
                    done["vague"] = done.get("vague", 0) + 1
            continue
        if label == "UNCLASSIFIED" or not spans or spans[0] not in text:
            continue
        x, acc, variant = rng.random(), 0.0, None
        for name, rate in RATES:
            acc += rate
            if x < acc:
                variant = name
                break
        if not variant:
            continue
        span = spans[0]
        new_span, new_text = None, None
        if variant == "typo":
            new_span = typo(span)
        elif variant == "english":
            new_span = english(span) or typo(span)
        elif variant == "paraphrase" and label in PARAPHRASE:
            new_span = rng.choice(PARAPHRASE[label])
        elif variant == "two_symptoms":
            extra = SECOND.get(label[:4].rstrip("0123456789"), "")
            end = text.find(span) + len(span)
            dot = text.find(".", end)
            if extra and dot >= 0:
                new_text = text[:dot + 1] + extra + text[dot + 1:]
                new_span = span
        if not new_span:
            skipped += 1
            continue
        new_text = new_text or text.replace(span, new_span, 1)
        db.execute("update rma_claim set raw_text=? where rma_id=?", (new_text, rma_id))
        db.execute("update symptom_assessment set evidence_spans_json=? where assessment_id=?",
                   (json.dumps([new_span] + spans[1:], ensure_ascii=False), aid))
        db.execute("insert into claim_text_variant values (?,?,?,?)", (rma_id, variant, text, spans_json))
        done[variant] = done.get(variant, 0) + 1

bad = [r for r in db.execute("""select r.raw_text, s.evidence_spans_json from claim_text_variant v join rma_claim r using(rma_id)
                                 join rma_item i using(rma_id) join symptom_assessment s using(rma_item_id)
                                 where v.variant!='vague'""") if json.loads(r[1])[0] not in r[0]]
assert not bad, bad[:3]
total = len(rows)
db.close()
manifest["v5_text"] = {"from": START, "returns_in_period": total, "changed": done, "changed_total": sum(done.values()),
                       "share": round(sum(done.values()) / total, 3), "table": "claim_text_variant"}
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest["v5_text"], ensure_ascii=False))
