"""Real responses for screens and API mocks (classify, triage, run_analysis): python3 scripts/make_samples.py (LLM per .env)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rma_agent
from rma_agent.agents.runtime import get_runtime

ROOT = Path(__file__).resolve().parents[1] / "samples"
CASES = {
    "RMA-08927": "차량 L3, 공정 원인(외층 에칭 잔동)과 검사 유출 (명세 예시, v4의 RMA-08743과 같은 유형)",
    "RMA-08996": "차량 L2, 자재 원인(동박 표면 처리)",
    "RMA-07436": "반도체 L2, 공정 원인(디스미어)과 장비",
    "RMA-07208": "반도체 L1, 단발 원인(취급 스크래치), 장비 특정 불가",
    "RMA-07936": "정보 부족 접수 (외관 NG만 기재) → 재질의",
    "RMA-08218": "재발 사건(디스미어 부족 재발, 다른 장비 DSM-01)",
}


def claim_header(conn, rma_id):
    r = conn.execute("""select r.rma_id, r.customer_id, c.customer_name, c.industry, r.opened_at, r.raw_text,
                               r.reported_part_no, r.reported_lot_no, r.detection_stage, i.input_qty, i.claimed_defect_qty,
                               s.created_at label_at
                        from rma_claim r join customer c using(customer_id) join rma_item i using(rma_id)
                        join symptom_assessment s using(rma_item_id) where r.rma_id=?""", (rma_id,)).fetchone()
    return dict(r)


def main():
    ROOT.mkdir(exist_ok=True)
    conn = get_runtime().conn
    index = []
    for rma_id, note in CASES.items():
        claim = claim_header(conn, rma_id)
        classify = rma_agent.classify(claim["raw_text"], detection_stage=claim["detection_stage"],
                                      part_no=claim["reported_part_no"], customer_id=claim["customer_id"],
                                      as_of=claim.pop("label_at"))
        entry = {"rma_id": rma_id, "note": note, "claim": claim, "classify": classify, "triage": rma_agent.triage(rma_id)}
        if classify["status"] == "CLASSIFIED":
            entry["analysis"] = rma_agent.run_analysis(rma_id)
        (ROOT / f"{rma_id}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        index.append({"rma_id": rma_id, "note": note, "classify_status": classify["status"],
                      "triage_level": entry["triage"]["analysis_level"], "triage_provisional": entry["triage"]["provisional"],
                      "analysis_status": entry.get("analysis", {}).get("status")})
        print(rma_id, classify["status"], entry.get("analysis", {}).get("status"), entry.get("analysis", {}).get("warnings"))
    (ROOT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
