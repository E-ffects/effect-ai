"""Priority scores (WBS 4.3, requirement FR-03): plain arithmetic over the DB, no model, no LLM.

Two scores, both 0~100, weights are settings (pass `weights=` to override; they are rescaled to sum 1):

pattern_priority  quality action priority per repeated pattern (= investigation). Open patterns at as_of.
    count     0.30  returns and claimed defect qty in the last 30 days, each relative to the largest open pattern
                    (half/half)
    loss      0.30  estimated loss (loss_ledger) in the last 30 days, relative to the largest open pattern
    severity  0.20  highest QA severity among the pattern's returns in the last 30 days / 10
                    (30 days = WINDOW_DAYS: a pattern whose returns stopped after its action drops, scripts/eval_priority.py)
    trend     0.20  growth of all returns with the pattern's main symptom (most common intake label among its
                    returns) in the last 30 days vs the 30 days before, capped to 0~100% (+100% or more = full).
                    The symptom, not the pattern's own returns: a pattern only collects returns after it is
                    opened, so its own count always looks "new".
    Recurrence is not a part (it is monitored separately).

response_priority  customer response priority per return whose CS reply is not closed.
    severity    0.30  QA severity / 10
    elapsed     0.25  days since the return was opened, capped at 30 days
    importance  0.25  product importance by application (AUTOMOTIVE 1.0, SEMICONDUCTOR 0.8, INDUSTRIAL 0.6: assumption)
    qty         0.20  claimed defect qty, log scale relative to the largest open return
    Returns with a critical (safety) characteristic (CC) are pinned to the top regardless of score.

Only data known at as_of is used: returns opened and linked to the pattern by then, loss rows dated by then.
as_of defaults to the latest return in the data.
"""
import math
from collections import Counter
from datetime import datetime, timedelta

from .errors import InvalidInput, check_as_of

PATTERN_WEIGHTS = {"count": 0.30, "loss": 0.30, "severity": 0.20, "trend": 0.20}
RESPONSE_WEIGHTS = {"severity": 0.30, "elapsed": 0.25, "importance": 0.25, "qty": 0.20}
IMPORTANCE = {"AUTOMOTIVE": 1.0, "SEMICONDUCTOR": 0.8, "INDUSTRIAL": 0.6}
TREND_DAYS = 30
WINDOW_DAYS = 30
ELAPSED_CAP_DAYS = 30
SEVERITY_SCALE = 10
OPEN_PATTERN = {"ANALYSIS": "분석 중", "CA_IN_PROGRESS": "조치 진행", "MONITORING": "효과 확인 중"}
PAST_OPEN = "진행 중"  # open at as_of but closed later: the data keeps only the final status
PART_NAMES = {"count": "발생 건수·수량", "loss": "손실 금액", "severity": "심각도", "trend": "증가 추세",
              "elapsed": "처리 경과일", "importance": "제품 중요도", "qty": "클레임 수량"}


def _conn(conn):
    if conn is not None:
        return conn
    from .agents.runtime import get_runtime
    return get_runtime().conn


def _weights(default, given):
    w = dict(default)
    for k, v in (given or {}).items():
        if k not in w:
            raise InvalidInput(f"알 수 없는 가중치 이름: {k} (가능: {', '.join(w)})")
        w[k] = float(v)
    total = sum(w.values())
    if total <= 0 or any(v < 0 for v in w.values()):
        raise InvalidInput("가중치는 0 이상이고 합이 0보다 커야 합니다")
    return {k: v / total for k, v in w.items()}


def _as_of(conn, as_of):
    as_of = check_as_of(as_of)
    return as_of or conn.execute("select max(opened_at) from rma_claim").fetchone()[0]


def _days(a, b):
    return (datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds() / 86400


def _won(v):
    return f"{v / 1e8:.2f}억 원" if v >= 1e8 else f"{v / 1e4:,.0f}만 원"


def _score(parts, weights):
    for name, p in parts.items():
        p["weight"] = round(weights[name], 4)
        p["points"] = round(100 * weights[name] * p["score"], 1)
        p["score"] = round(p["score"], 4)
        p["name"] = PART_NAMES[name]
    return round(sum(p["points"] for p in parts.values()), 1)


def pattern_priority(as_of=None, weights=None, limit=None, conn=None, window_days=WINDOW_DAYS):
    """Open patterns (investigations) at as_of, highest quality action priority first.

    count and loss look at the last `window_days` (None = since the pattern opened)."""
    conn = _conn(conn)
    as_of = _as_of(conn, as_of)
    w = _weights(PATTERN_WEIGHTS, weights)
    pats = {r[0]: {"investigation_id": r[0], "title": r[1], "analysis_level": r[2], "opened_at": r[3], "status": r[4]}
            for r in conn.execute("""select investigation_id, title, analysis_level, opened_at, status from investigation
                                     where opened_at <= ? and (closed_at is null or closed_at > ?)""", (as_of, as_of))}
    if not pats:
        return {"as_of": as_of, "weights": w, "items": []}
    for p in pats.values():
        p.update(n=0, qty=0, loss=0.0, sev=0, cc=0, labels=Counter(), rmas=[])
    cut1 = datetime.fromisoformat(as_of).timestamp() - TREND_DAYS * 86400
    cut0 = cut1 - TREND_DAYS * 86400
    since = (datetime.fromisoformat(as_of) - timedelta(days=window_days)).isoformat() if window_days else ""
    by_label = {}
    for opened, label in conn.execute("""select r.opened_at, s.intake_label from rma_claim r join rma_item i using(rma_id)
                                         join symptom_assessment s using(rma_item_id)
                                         where r.opened_at <= ? and s.created_at <= ?""", (as_of, as_of)):
        ts = datetime.fromisoformat(opened).timestamp()
        if ts > cut0:
            n = by_label.setdefault(label, [0, 0])
            n[0 if ts > cut1 else 1] += 1
    rows = conn.execute("""select l.investigation_id, r.rma_id, r.opened_at, t.severity, t.characteristic_symbol,
                                  i.claimed_defect_qty, s.intake_label
                           from investigation_claim l join rma_claim r using(rma_id) join rma_item i using(rma_id)
                           left join qa_triage t using(rma_id) left join symptom_assessment s using(rma_item_id)
                           where l.linked_at <= ? and r.opened_at <= ?""", (as_of, as_of))
    for inv, rma_id, opened, sev, symbol, qty, label in rows:
        p = pats.get(inv)
        if p is None:
            continue
        p["rmas"].append((opened, rma_id))
        if label:
            p["labels"][label] += 1
        if opened <= since:
            continue
        p["n"] += 1
        p["qty"] += qty or 0
        p["sev"] = max(p["sev"], sev or 0)
        p["cc"] += symbol == "CC"
    for inv, amount in conn.execute("""select investigation_id, sum(amount_krw) from loss_ledger
                                       where occurred_at <= ? and occurred_at > ? and investigation_id is not null
                                       group by 1""", (as_of, since)):
        if inv in pats:
            pats[inv]["loss"] = float(amount or 0)
    span = f"최근 {window_days}일 " if window_days else "누적 "
    top_n = max(p["n"] for p in pats.values()) or 1
    top_q = max(p["qty"] for p in pats.values()) or 1
    top_l = max(p["loss"] for p in pats.values()) or 1
    items = []
    for p in pats.values():
        label = p["labels"].most_common(1)[0][0] if p["labels"] else None
        recent, before = by_label.get(label, [0, 0]) if label else (0, 0)
        growth = (recent - before) / before if before else (1.0 if recent else 0.0)
        parts = {
            "count": {"score": 0.5 * p["n"] / top_n + 0.5 * p["qty"] / top_q,
                      "basis": f"{span}반품 {p['n']:,}건 · 불량 {p['qty']:,}pcs (가장 큰 패턴 대비 {p['n'] / top_n:.0%} · {p['qty'] / top_q:.0%})"},
            "loss": {"score": p["loss"] / top_l, "basis": f"{span}추정 손실 {_won(p['loss'])} (가장 큰 패턴 대비 {p['loss'] / top_l:.0%})"},
            "severity": {"score": p["sev"] / SEVERITY_SCALE,
                         "basis": f"{span}최고 심각도 {p['sev']}/{SEVERITY_SCALE}" + (f" · 안전 특성(CC) 반품 {p['cc']:,}건" if p["cc"] else "")},
            "trend": {"score": min(max(growth, 0.0), 1.0),
                      "basis": (f"같은 증상({label}) 반품 최근 {TREND_DAYS}일 {recent:,}건 / 직전 {TREND_DAYS}일 {before:,}건"
                                + (f" ({growth:+.0%})" if before else (" (새로 생김)" if recent else ""))) if label else "연결된 반품 없음"},
        }
        score = _score(parts, w)
        recent = [r for _, r in sorted(p["rmas"], reverse=True)[:5]]
        items.append({"investigation_id": p["investigation_id"], "title": p["title"], "analysis_level": p["analysis_level"],
                      "status": p["status"] if p["status"] in OPEN_PATTERN else None,
                      "status_name": OPEN_PATTERN.get(p["status"], PAST_OPEN), "opened_at": p["opened_at"],
                      "main_symptom": label, "claim_count": p["n"], "total_claim_count": len(p["rmas"]), "defect_qty": p["qty"], "loss_krw": round(p["loss"]),
                      "score": score, "parts": parts, "recent_rma_ids": recent,
                      "reason": " · ".join(f"{v['name']} {v['points']:.1f}점" for v in
                                           sorted(parts.values(), key=lambda v: -v["points"]))})
    items.sort(key=lambda x: (-x["score"], x["investigation_id"]))
    for i, it in enumerate(items, 1):
        it["rank"] = i
    return {"as_of": as_of, "weights": w, "items": items[:limit] if limit else items}


def response_priority(as_of=None, weights=None, limit=None, conn=None):
    """Returns whose CS reply is not closed, highest customer response priority first (CC pinned on top).

    CS status is the stored one (the data has no CS status history); as_of only sets the elapsed days and
    which returns exist.
    """
    conn = _conn(conn)
    as_of = _as_of(conn, as_of)
    w = _weights(RESPONSE_WEIGHTS, weights)
    app = dict(conn.execute("select part_no, max(application) from product_spec group by part_no"))
    rows = conn.execute("""select r.rma_id, r.customer_id, r.opened_at, r.cs_status, r.reported_part_no, t.severity,
                                  t.characteristic_symbol, sum(i.claimed_defect_qty)
                           from rma_claim r join rma_item i using(rma_id) left join qa_triage t using(rma_id)
                           where r.cs_status <> 'CLOSED' and r.opened_at <= ?
                           group by r.rma_id, r.customer_id, r.opened_at, r.cs_status, r.reported_part_no,
                                    t.severity, t.characteristic_symbol""", (as_of,)).fetchall()
    if not rows:
        return {"as_of": as_of, "weights": w, "items": []}
    top_q = math.log1p(max(r[7] or 0 for r in rows)) or 1
    items = []
    for rma_id, cust, opened, cs, part_no, sev, symbol, qty in rows:
        days = max(_days(as_of, opened), 0.0)
        application = app.get(part_no)
        imp = IMPORTANCE.get(application, min(IMPORTANCE.values()))
        sev = sev or 0
        qty = qty or 0
        parts = {
            "severity": {"score": sev / SEVERITY_SCALE, "basis": f"심각도 {sev}/{SEVERITY_SCALE}" + (" · 안전 특성(CC)" if symbol == "CC" else "")},
            "elapsed": {"score": min(days / ELAPSED_CAP_DAYS, 1.0), "basis": f"접수 후 {days:.0f}일 ({ELAPSED_CAP_DAYS}일 이상은 만점)"},
            "importance": {"score": imp, "basis": f"{application or '용도 미상'} 제품 (중요도 {imp:.1f})"},
            "qty": {"score": math.log1p(qty) / top_q, "basis": f"클레임 불량 {qty:,}pcs"},
        }
        score = _score(parts, w)
        items.append({"rma_id": rma_id, "customer_id": cust, "opened_at": opened, "cs_status": cs,
                      "part_no": part_no, "pinned": symbol == "CC", "score": score, "parts": parts,
                      "reason": ("안전 특성(CC) 반품이라 맨 위 고정 · " if symbol == "CC" else "")
                                + " · ".join(f"{v['name']} {v['points']:.1f}점" for v in sorted(parts.values(), key=lambda v: -v["points"]))})
    items.sort(key=lambda x: (not x["pinned"], -x["score"], x["opened_at"]))
    for i, it in enumerate(items, 1):
        it["rank"] = i
    return {"as_of": as_of, "weights": w, "items": items[:limit] if limit else items}
