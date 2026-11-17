"""Ground truth for evaluation only. Predictors must never import this module."""
from .asof import process_token


class GroundTruth:
    def __init__(self, conn):
        self.answers = {}
        for r in conn.execute("""
                select i.rma_id, a.cause_code, a.cause_category, a.process_id, a.equipment_id, a.investigation_id
                from claim_cause cc join cause_assessment a using(cause_assessment_id) join rma_item i using(rma_item_id)"""):
            self.answers[r["rma_id"]] = {
                "cause": r["cause_code"], "category": r["cause_category"],
                "process": process_token(r["process_id"], r["cause_category"]),
                "equipment": r["equipment_id"], "investigation": r["investigation_id"]}
        parents = dict(conn.execute("select investigation_id, reopened_from from investigation"))
        self._family = {}
        for inv in parents:
            root = inv
            while parents.get(root):
                root = parents[root]
            self._family[inv] = root
        self._approvals = {}
        for r in conn.execute("select cause_code, investigation_id, approved_at from cause_assessment where approved_at is not null"):
            self._approvals.setdefault(r["cause_code"], []).append((r["approved_at"], self._family[r["investigation_id"]]))

    def answer(self, rma_id):
        return self.answers[rma_id]

    def group(self, rma_id, as_of):
        """unknown: cause not approved anywhere before as_of
        same_incident: approved only within this claim's own incident (incl. reopened incidents)
        other_incident: approved in a different incident (e.g. an earlier quarter's sporadic group)"""
        ans = self.answers[rma_id]
        prior = [fam for t, fam in self._approvals.get(ans["cause"], []) if t <= as_of]
        if not prior:
            return "unknown"
        own = self._family[ans["investigation"]]
        return "other_incident" if any(fam != own for fam in prior) else "same_incident"
