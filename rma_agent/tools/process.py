"""Process agent tool: where did this lot go, and was anything abnormal there?

process_signals(store, baseline, lot_id) returns one entry per process step the lot passed:
  equipment, run time, out-of-spec count, shift of each condition item versus the recent norm (sigma),
  and abnormal events recorded on that equipment around the run.

The norm for an item is the same process + item over the BASELINE_DAYS before this lot's run, other lots only.
It is computed from observations measured before the run, so it never uses information after as_of.
"""
import bisect
from collections import defaultdict
from datetime import datetime, timedelta
from math import sqrt

from ..asof import FutureDataError

BASELINE_DAYS = 90
EVENT_LOOKBACK_DAYS = 60
SIGMA_ALERT = 3.0


def _shift(ts, days):
    return (datetime.fromisoformat(ts) + timedelta(days=days)).isoformat(timespec="seconds")


class ProcessBaseline:
    """All condition observations loaded once, with prefix sums for fast windowed mean / std."""

    def __init__(self, conn):
        rows = conn.execute("""select r.process_id, o.item_code, o.measured_at, o.value, r.lot_id
                               from process_observation o join process_run r using(process_run_id)
                               where o.value is not null order by o.measured_at""").fetchall()
        series = defaultdict(list)
        for r in rows:
            series[(r["process_id"], r["item_code"])].append((r["measured_at"], r["value"], r["lot_id"]))
        self._s = {}
        for key, items in series.items():
            times = [t for t, _, _ in items]
            s1, s2 = [0.0], [0.0]
            for _, v, _ in items:
                s1.append(s1[-1] + v)
                s2.append(s2[-1] + v * v)
            self._s[key] = (times, s1, s2, items)

    def norm(self, process_id, item_code, before, as_of, exclude_lot=None):
        """Mean and std of the item over [before - BASELINE_DAYS, before), excluding one lot."""
        if before > as_of:
            raise FutureDataError(f"baseline end {before} > as_of {as_of}")
        data = self._s.get((process_id, item_code))
        if not data:
            return None
        times, s1, s2, items = data
        lo = bisect.bisect_left(times, _shift(before, -BASELINE_DAYS))
        hi = bisect.bisect_left(times, before)
        n, a, b = hi - lo, s1[hi] - s1[lo], s2[hi] - s2[lo]
        if exclude_lot:
            for _, v, lot in items[lo:hi]:
                if lot == exclude_lot:
                    n, a, b = n - 1, a - v, b - v * v
        if n < 10:
            return None
        mean = a / n
        var = max(b / n - mean * mean, 0.0)
        return mean, sqrt(var)


def process_signals(store, baseline, lot_id):
    history = store.lot_process_history(lot_id)
    observations = defaultdict(list)
    for o in store.lot_observations(lot_id):
        observations[(o["process_id"], o["equipment_id"], o["load_no"])].append(o)
    equipment = {h["equipment_id"] for h in history}
    events = defaultdict(list)
    for e in store.equipment_events(equipment):
        if e["result"] == "ABNORMAL":
            events[e["equipment_id"]].append(e)

    steps = []
    for h in history:
        obs = observations.get((h["process_id"], h["equipment_id"], h["load_no"]), [])
        oos = sum(o["spc_flag"] == "OUT_OF_SPEC" for o in obs)
        z_by_item = {}
        for o in obs:
            norm = baseline.norm(h["process_id"], o["item_code"], h["started_at"], store.as_of, exclude_lot=lot_id)
            if norm and norm[1] > 0:
                z = (o["value"] - norm[0]) / norm[1]
                if abs(z) > abs(z_by_item.get(o["item_code"], 0)):
                    z_by_item[o["item_code"]] = round(z, 1)
        window_from = _shift(h["started_at"], -EVENT_LOOKBACK_DAYS)
        near = [e for e in events.get(h["equipment_id"], []) if e["occurred_at"] >= window_from]
        max_z = max((abs(z) for z in z_by_item.values()), default=0.0)
        steps.append({
            "process_id": h["process_id"], "sequence_no": h["sequence_no"], "equipment_id": h["equipment_id"],
            "load_no": h["load_no"], "started_at": h["started_at"], "ended_at": h["ended_at"],
            "recipe_version": h["recipe_version"],
            "out_of_spec": oos, "max_abs_z": max_z, "z_by_item": z_by_item,
            "abnormal_events": [{"event_type": e["event_type"], "occurred_at": e["occurred_at"],
                                 "description": e["description"]} for e in near],
            "signal": signal_level(oos, max_z, near),
        })
    return steps


def signal_level(oos, max_z, events):
    """strong: out-of-spec or >= 5 sigma; medium: >= 3 sigma or an abnormal event; none otherwise."""
    if oos > 0 or max_z >= 5:
        return "strong"
    if max_z >= SIGMA_ALERT or events:
        return "medium"
    return "none"


def abnormal_steps(steps):
    return [s for s in steps if s["signal"] != "none"]
