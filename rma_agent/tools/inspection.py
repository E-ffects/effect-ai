"""Inspection tool: which inspection equipment checked this lot, and does it catch defects as often as its peers?

A detection rate is fails / tests per equipment over the DETECTION_DAYS before as_of.
An equipment whose rate is well below its peers (LOW_RATIO) is a lead for an escape (why the defect got out).
"""
from collections import defaultdict
from datetime import datetime, timedelta

DETECTION_DAYS = 30
LOW_RATIO = 0.6

# Inspection process in cause/escape masters -> test_run.phase
PHASE_OF = {"ET": "ET", "FQC": "FQC", "OQC": "OQC", "IQC": "IQC", "OL_AOI": "IN_PROCESS", "IL_AOI": "IN_PROCESS"}


def _since(as_of, days):
    return (datetime.fromisoformat(as_of) - timedelta(days=days)).isoformat(timespec="seconds")


def lot_inspections(store, lot_id):
    """Inspection equipment that tested this lot, by phase: {phase: {equipment_id: (fails, tests, last_ended_at)}}."""
    out = defaultdict(dict)
    for t in store.lot_tests(lot_id):
        if t["phase"] == "RMA_LAB" or not t["equipment_id"]:
            continue
        fails, n, last = out[t["phase"]].get(t["equipment_id"], (0, 0, ""))
        out[t["phase"]][t["equipment_id"]] = (fails + (t["overall_result"] == "FAIL"), n + 1, max(last, t["ended_at"]))
    return dict(out)


def detection_rates(store, phase, test_spec_id=None):
    """{equipment_id: {"fails", "tests", "rate"}} over the last DETECTION_DAYS, overall or for one test item."""
    since = _since(store.as_of, DETECTION_DAYS)
    return since, {eq: {"fails": f, "tests": n, "rate": f / n if n else 0.0}
                   for eq, (f, n) in store.detection_counts(phase, since, test_spec_id).items()}


def test_item_for(minor_name):
    """E-Test item that should catch this symptom (None: use the overall fail rate)."""
    if "쇼트" in (minor_name or ""):
        return "ET_SHORT"
    if any(word in (minor_name or "") for word in ("오픈", "단선")):
        return "ET_OPEN"
    return None


def low_detection(rates, equipment_id):
    """True when this equipment detects clearly less than the mean of its peers."""
    peers = [r["rate"] for eq, r in rates.items() if eq != equipment_id and r["tests"] >= 100]
    mine = rates.get(equipment_id)
    if not peers or not mine or mine["tests"] < 100:
        return False
    return mine["rate"] < LOW_RATIO * (sum(peers) / len(peers))


def tested_at(lot_trace, process_id, equipment_process):
    """Inspection equipment of this lot that belongs to process_id (e.g. OL_AOI keeps OAOI-*, drops IAOI-*)."""
    tested = lot_trace["inspections"].get(PHASE_OF.get(process_id, process_id), {})
    return {eq: v for eq, v in tested.items() if equipment_process.get(eq, process_id) == process_id}
