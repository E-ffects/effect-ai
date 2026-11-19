"""v4: terminology. Run after patch_v4_loss.py and before slim_v3.py.

Screens, presentations and data use one term, "원인 후보 (cause candidate)", instead of hypothesis.
A cause candidate is proposed, reviewed, then adopted (becomes the confirmed cause) or rejected.

  cause_hypothesis            -> cause_candidate
    hypothesis_id  (HYP-...)  -> candidate_id (CAN-...)
    hypothesis_no             -> candidate_no
    hypothesis_text           -> review_note      ("○○ 가설: ..." -> "○○ 검토: ...")
    verification_method       -> review_method
    evidence_ids_json         -> review_evidence_ids_json
    result SUPPORTED/REJECTED -> review_status ADOPTED/REJECTED
    decided_at                -> reviewed_at
The generation scripts (generate_v30.py, patch_v31.py) still build cause_hypothesis first; this step renames it.
"""
import json
from pathlib import Path
import sqlite3
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
manifest_file = root / "manifest.json"
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
assert manifest["version"] == "v4" and "v4_loss" in manifest and "v4_terms" not in manifest, \
    "Run after patch_v4_loss.py, once"
db = sqlite3.connect(root / "pcb_rma.sqlite")
db.execute("pragma foreign_keys=off")
db.execute("pragma legacy_alter_table=off")

RENAMES = [("hypothesis_id", "candidate_id"), ("hypothesis_no", "candidate_no"), ("hypothesis_text", "review_note"),
           ("verification_method", "review_method"), ("evidence_ids_json", "review_evidence_ids_json"),
           ("result", "review_status"), ("decided_at", "reviewed_at")]
with db:
    before = db.execute("select count(*) from cause_hypothesis").fetchone()[0]
    db.execute("alter table cause_hypothesis rename to cause_candidate")
    for old, new in RENAMES:
        db.execute(f"alter table cause_candidate rename column {old} to {new}")
    db.execute("update cause_candidate set candidate_id='CAN-'||substr(candidate_id,5) where candidate_id like 'HYP-%'")
    db.execute("update cause_candidate set review_note=replace(review_note,' 가설:',' 검토:') where review_note like '% 가설:%'")
    db.execute("update cause_candidate set review_note=replace(review_note,'가설','검토') where review_note like '%가설%'")
    db.execute("update cause_candidate set review_status='ADOPTED' where review_status='SUPPORTED'")
    db.execute("update cause_candidate set review_status='UNDER_REVIEW' where review_status='UNDER_VERIFICATION'")
    db.execute("update analysis_activity set activity_type='CANDIDATE_REVIEW' where activity_type='HYPOTHESIS_VERIFICATION'")

leftover = [n for (n, s) in db.execute("select name, sql from sqlite_master where sql is not null")
            if "hypothes" in (s or "").lower() or "hypothes" in n.lower()]
assert not leftover, leftover
assert db.execute("select count(*) from cause_candidate").fetchone()[0] == before
assert db.execute("""select count(*) from cause_candidate where review_note like '%가설%' or candidate_id like 'HYP-%'
                     or review_status not in ('ADOPTED','REJECTED','UNDER_REVIEW')""").fetchone()[0] == 0
db.execute("pragma foreign_keys=on")
assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
assert not db.execute("pragma foreign_key_check").fetchone()
status = dict(db.execute("select review_status, count(*) from cause_candidate group by 1"))
db.close()

text = json.dumps(manifest, ensure_ascii=False)
for old, new in (("cause_hypothesis", "cause_candidate"), ("hypothesis_future_evidence", "candidate_future_evidence"),
                 ("HYPOTHESIS_VERIFICATION", "CANDIDATE_REVIEW")):
    text = text.replace(old, new)
manifest = json.loads(text)
assert "hypothes" not in text.lower()
(root / "sample_csv" / "cause_hypothesis.csv").unlink(missing_ok=True)
manifest["v4_terms"] = {"table": "cause_candidate", "columns": [new for _, new in RENAMES], "review_status": status,
                        "note": "이전 이름과의 대응은 PCB_RMA_v4_수정내역.md 참고"}
manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest["v4_terms"], ensure_ascii=False, indent=2))
