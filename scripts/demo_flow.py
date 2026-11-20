"""Demo rehearsal: call a running AI server the way Spring does (Spring_API_v4 4-3, 5) and report times and texts.

    RMA_AUTH=off uvicorn server.app:create_app --factory --port 8080      # in another terminal (LLM settings from .env)
    python3 scripts/demo_flow.py [RMA-08927 RMA-07936 ...] [--base http://127.0.0.1:8080/ai/v1] [--out results/x.md]

Per return: POST /classify (intake text from the DB) -> POST /triage -> POST /analyses (no as_of, like Spring) ->
poll GET /analyses/{id} -> GET /claims/{id}/similar. A provisional triage stops before analysis (QA confirms first).
With RMA_SERVICE_JWT_SECRET set, a SYSTEM service token is signed like Spring's JwtProvider; otherwise none is sent.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT = ["RMA-08927", "RMA-08996", "RMA-07436", "RMA-07208", "RMA-08218", "RMA-07936"]


def headers():
    secret = os.environ.get("RMA_SERVICE_JWT_SECRET")
    if not secret:
        return {}
    import jwt
    now = int(time.time())
    token = jwt.encode({"sub": "spring", "role": "SYSTEM", "type": "ACCESS", "iat": now, "exp": now + 600}, secret,
                       algorithm="HS512" if len(secret.encode()) >= 64 else "HS384" if len(secret.encode()) >= 48 else "HS256")
    return {"Authorization": f"Bearer {token}"}


def intake(rma_id):
    from rma_agent.db import connect
    row = connect().execute("""select raw_text, detection_stage, reported_part_no, customer_id from rma_claim
                               where rma_id=?""", (rma_id,)).fetchone()
    return dict(row)


def timed(fn):
    t = time.perf_counter()
    res = fn()
    return res, (time.perf_counter() - t) * 1000


def run(client, rma_id):
    out = {"rma_id": rma_id, "ms": {}}
    claim = intake(rma_id)
    res, out["ms"]["classify"] = timed(lambda: client.post("/classify", json={"rma_id": rma_id, **claim}))
    res.raise_for_status()
    out["classify"] = res.json()
    res, out["ms"]["triage"] = timed(lambda: client.post("/triage", json={"rma_id": rma_id}))
    res.raise_for_status()
    out["triage"] = tri = res.json()
    if tri["provisional"]:
        out["stopped"] = "임시 판정: 품질관리자 확인 후 분석 시작 (Spring_API_v4 4-6)"
        return out
    started = time.perf_counter()
    res, out["ms"]["analyses_post"] = timed(lambda: client.post("/analyses", json={
        "rma_id": rma_id, "options": {"mode": "B", "top_k": 3, "include_drafts": True}}))
    res.raise_for_status()
    job, polls = res.json(), 0
    while True:
        time.sleep(job.get("poll_after_ms", 1000) / 1000)
        body = client.get(f"/analyses/{job['analysis_id']}").json()
        polls += 1
        if body["status"] not in ("QUEUED", "RUNNING"):
            break
        if time.perf_counter() - started > 40:  # Spring gives up after 40 s (B)
            body = {**body, "status": "SPRING_TIMEOUT"}
            break
    out["ms"]["analysis_total"] = (time.perf_counter() - started) * 1000
    out["polls"], out["analysis"] = polls, body
    res, out["ms"]["similar"] = timed(lambda: client.get(f"/claims/{rma_id}/similar", params={"limit": 5}))
    out["similar"] = res.json()
    return out


def report(results):
    lines = ["| RMA | 분류 | 판정 | 분석 상태 | 분류 ms | 판정 ms | 분석 등록 ms | 분석 완료 s | 유사 사례 ms | 경고 |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---|"]
    for r in results:
        a, ms = r.get("analysis") or {}, r["ms"]
        lines.append(f"| {r['rma_id']} | {r['classify']['status']} {r['classify'].get('minor_code') or ''} | "
                     f"{r['triage']['analysis_level']}{' (임시)' if r['triage']['provisional'] else ''} | "
                     f"{a.get('status', '분석 안 함')} | {ms['classify']:.0f} | {ms['triage']:.0f} | "
                     f"{ms.get('analyses_post', 0):.0f} | {ms.get('analysis_total', 0) / 1000:.1f} | "
                     f"{ms.get('similar', 0):.0f} | {', '.join(a.get('warnings') or r['classify'].get('warnings') or []) or '-'} |")
    for r in results:
        lines += ["", f"## {r['rma_id']}", ""]
        c = r["classify"]
        lines.append(f"- 분류: {c['status']} {c.get('minor_name') or ''} (모델 {c['model_version']}), 근거 {c['evidence_spans']}")
        if c.get("follow_up_questions"):
            lines.append(f"- 재질의: {c['follow_up_questions'][0]}")
        lines.append(f"- 판정: {r['triage']['rationale']}")
        if r.get("stopped"):
            lines.append(f"- {r['stopped']}")
            continue
        a = r["analysis"]
        res = a.get("result") or {}
        if not res:
            lines.append(f"- 분석 실패: {a.get('error')}")
            continue
        for cand in res["cause_candidates"]:
            lines.append(f"- 후보 {cand['rank']}: {cand['cause_name']} ({cand['equipment_id'] or '장비 특정 불가'}, "
                         f"점수 {cand['score']}) — {cand['explanation']}")
        lines.append(f"- QA 검토: {res['qa_review'].get('review_note')}")
        sa = res["drafts"]["service_action"]
        lines.append(f"- 처리방안: {sa['action_name']} {sa['board_qty']}매, {sa['basis']}, 예상 {sa['expected_days']}일"
                     if sa else "- 처리방안: 제안 없음")
        lines += ["- 고객 회신 초안:", "", "```", (res["drafts"]["customer_reply"] or "").strip(), "```"]
        lines.append(f"- 단계별 LLM 사용: {[t['agent'] + ('*' if t['llm_used'] else '') for t in a['trace']]}, "
                     f"모델 {a['model_versions']}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rma_ids", nargs="*", default=DEFAULT)
    ap.add_argument("--base", default="http://127.0.0.1:8080/ai/v1")
    ap.add_argument("--out")
    a = ap.parse_args()
    with httpx.Client(base_url=a.base, headers=headers(), timeout=httpx.Timeout(10, connect=2)) as client:
        health = client.get("/health").json()
        results = [run(client, rma_id) for rma_id in a.rma_ids]
    text = f"# 시연 흐름 점검: {time.strftime('%Y-%m-%d %H:%M')}\n\n- 서버 상태 {health}\n\n" + report(results)
    print(text)
    if a.out:
        (ROOT / a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
