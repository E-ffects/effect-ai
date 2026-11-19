"""Time-bounded data access.

Every query an agent makes goes through AsOfStore, which only returns what was known at `as_of`.
Timestamps are ISO-8601 strings in the same +09:00 offset, so string comparison is time comparison.

Which column decides "known at":
  rma_claim            opened_at
  process_run          ended_at
  process_observation  measured_at
  equipment_event      recorded_at   (not occurred_at: an event is known once it is recorded)
  test_run             ended_at
  material_usage       issued_at     (material IQC result only after iqc_at)
  similar_cases        searched_at
  qa_triage            decided_at
  escape_assessment    assessed_at
  corrective_action    selected_at   (implemented_at hidden until known)
  past cases           claim_cause.decided_at and cause_assessment.approved_at
Reference tables (process_master, equipment_master, cause_master, ...) have no time and are always available.
"""
import bisect
import json
from collections import defaultdict
from dataclasses import dataclass


class FutureDataError(RuntimeError):
    """Raised when a query would expose data recorded after as_of."""


def analysis_start(conn, rma_id):
    """When cause analysis can start: QA triage done and the intake label available."""
    row = conn.execute("""
        select case when t.decided_at is null or s.created_at is null then null when t.decided_at > s.created_at then t.decided_at else s.created_at end from qa_triage t join rma_item i using(rma_id)
        join symptom_assessment s using(rma_item_id) where t.rma_id=?""", (rma_id,)).fetchone()
    if row is None or row[0] is None:
        raise KeyError(rma_id)
    return row[0]


def process_token(process_id, category):
    """Answer key for 'which process': material causes map to MATERIAL, undetermined ones to NONE."""
    if process_id:
        return process_id
    return "MATERIAL" if category == "MATERIAL" else "NONE"


@dataclass(frozen=True)
class Case:
    """One past return whose cause was approved and applied: the unit of the case library."""
    rma_id: str
    applied_at: str
    approved_at: str
    opened_at: str
    customer_id: str
    detection_stage: str
    raw_text: str
    intake_label: str
    primary_label: str
    spec_id: str
    lot_id: str
    cause_code: str
    cause_category: str
    process: str
    equipment_id: str | None
    material_lot_ids: tuple
    investigation_id: str
    certainty: str


class CaseLibrary:
    """All applied cases loaded once, sliced by time on demand (overall and per intake label)."""

    def __init__(self, conn):
        rows = conn.execute("""
            select r.rma_id, case when cc.decided_at is null or a.approved_at is null then null when cc.decided_at > a.approved_at then cc.decided_at else a.approved_at end known_at, a.approved_at, r.opened_at, r.customer_id,
                   r.detection_stage, r.raw_text, s.intake_label, s.primary_label, l.spec_id, i.lot_id,
                   a.cause_code, a.cause_category, a.process_id, a.equipment_id, a.material_lot_ids_json,
                   a.investigation_id, a.certainty_status
            from claim_cause cc join cause_assessment a using(cause_assessment_id)
            join rma_item i using(rma_item_id) join rma_claim r using(rma_id)
            join symptom_assessment s using(rma_item_id) left join production_lot l on l.lot_id=i.lot_id
            where cc.decided_at is not null and a.approved_at is not null""").fetchall()
        self._cases = sorted((Case(
            rma_id=r["rma_id"], applied_at=r["known_at"], approved_at=r["approved_at"], opened_at=r["opened_at"],
            customer_id=r["customer_id"], detection_stage=r["detection_stage"], raw_text=r["raw_text"],
            intake_label=r["intake_label"], primary_label=r["primary_label"], spec_id=r["spec_id"], lot_id=r["lot_id"],
            cause_code=r["cause_code"], cause_category=r["cause_category"],
            process=process_token(r["process_id"], r["cause_category"]), equipment_id=r["equipment_id"],
            material_lot_ids=tuple(json.loads(r["material_lot_ids_json"] or "[]")),
            investigation_id=r["investigation_id"], certainty=r["certainty_status"]) for r in rows),
            key=lambda c: c.applied_at)
        self._times = [c.applied_at for c in self._cases]
        self._by_label = defaultdict(list)
        for case in self._cases:
            self._by_label[case.intake_label].append(case)
        self._label_times = {k: [c.applied_at for c in v] for k, v in self._by_label.items()}
        self._by_rma = {c.rma_id: c for c in self._cases}

    def as_of(self, as_of, intake_label=None):
        if intake_label is None:
            return self._cases[:bisect.bisect_right(self._times, as_of)]
        cases = self._by_label.get(intake_label, [])
        return cases[:bisect.bisect_right(self._label_times.get(intake_label, []), as_of)]

    def get(self, rma_id, as_of):
        case = self._by_rma.get(rma_id)
        return case if case and case.applied_at <= as_of else None


class AsOfStore:
    """The only door agents use to read data. Everything returned was known at as_of."""

    def __init__(self, conn, as_of, library=None, subject=None):
        self.conn = conn
        self.as_of = as_of
        self.library = library or CaseLibrary(conn)
        self.subject = subject  # the return being analysed: never one of its own past cases

    # ---------- internal
    def _rows(self, sql, params, time_column):
        rows = [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        for row in rows:
            if row[time_column] is not None and row[time_column] > self.as_of:
                raise FutureDataError(f"{time_column}={row[time_column]} > as_of={self.as_of}")
        return rows

    # ---------- the claim being analysed
    def claim(self, rma_id):
        row = self.conn.execute("""
            select r.rma_id, r.customer_id, cu.industry, r.opened_at, r.raw_text, r.reported_part_no,
                   r.reported_lot_no, r.reported_date_code, r.detection_stage, i.rma_item_id, i.lot_id,
                   i.input_qty, i.claimed_defect_qty, l.spec_id, s.intake_label, s.created_at label_at,
                   t.severity, t.analysis_level, t.decided_at triage_at
            from rma_claim r join customer cu using(customer_id) join rma_item i using(rma_id)
            join symptom_assessment s using(rma_item_id) left join qa_triage t using(rma_id)
            left join production_lot l on l.lot_id=i.lot_id where r.rma_id=?""", (rma_id,)).fetchone()
        if row is None:
            raise KeyError(rma_id)
        claim = dict(row)
        self.subject = rma_id
        if claim["opened_at"] > self.as_of:
            raise FutureDataError(f"{rma_id} opened after as_of")
        if claim.pop("label_at") > self.as_of:
            claim["intake_label"] = None
        triage_at = claim.pop("triage_at")
        if triage_at is None or triage_at > self.as_of:  # new intakes have no QA triage until QA confirms one
            claim["severity"] = claim["analysis_level"] = None
        return claim

    # ---------- past cases (the case library)
    def past_cases(self, intake_label=None):
        """Approved cases known at as_of, without the return being analysed.

        A return that arrives after its incident's cause was approved gets that cause at intake, before QA
        triage. It must not appear as a past case of itself (that would be its own answer).
        """
        cases = self.library.as_of(self.as_of, intake_label)
        return [c for c in cases if c.rma_id != self.subject] if self.subject else cases

    def past_case(self, rma_id):
        return None if rma_id == self.subject else self.library.get(rma_id, self.as_of)

    def similar_cases(self, rma_id):
        """Baseline text-similarity results stored in the data (searched at QA triage)."""
        return self._rows("""select query_rma_id, reference_rma_id, similarity_score, searched_at
                             from similar_cases where query_rma_id=? and searched_at<=?
                             order by similarity_score desc, reference_rma_id""", (rma_id, self.as_of), "searched_at")

    # ---------- production history of a lot
    def lot_process_history(self, lot_id):
        return self._rows("""select r.process_run_id, r.process_id, p.sequence_no, r.equipment_id, r.load_no,
                                    r.panel_seq_from, r.panel_seq_to, r.recipe_version, r.started_at, r.ended_at, r.shift
                             from process_run r join process_master p using(process_id)
                             where r.lot_id=? and r.ended_at<=? order by p.sequence_no, r.started_at, r.process_run_id""",
                          (lot_id, self.as_of), "ended_at")

    def lot_observations(self, lot_id, process_id=None):
        sql = """select o.observation_id, r.process_id, r.equipment_id, r.load_no, o.item_code, o.value, o.unit,
                        o.target, o.lower_limit, o.upper_limit, o.spc_flag, o.measured_at
                 from process_observation o join process_run r using(process_run_id)
                 where r.lot_id=? and o.measured_at<=?"""
        params = [lot_id, self.as_of]
        if process_id:
            sql += " and r.process_id=?"
            params.append(process_id)
        return self._rows(sql + " order by o.measured_at, o.observation_id", params, "measured_at")

    def equipment_events(self, equipment_ids, since=None):
        ids = list(equipment_ids)
        if not ids:
            return []
        sql = f"""select event_id, equipment_id, event_type, occurred_at, recorded_at, metric_name, metric_value,
                         metric_unit, description, result
                  from equipment_event where equipment_id in ({','.join('?' * len(ids))}) and recorded_at<=?"""
        params = [*ids, self.as_of]
        if since:
            sql += " and occurred_at>=?"
            params.append(since)
        return self._rows(sql + " order by occurred_at, event_id", params, "recorded_at")

    def lot_tests(self, lot_id, phases=None):
        sql = """select test_run_id, phase, subject_type, equipment_id, plan_id, sample_origin, origin_verified,
                        overall_result, started_at, ended_at
                 from test_run where lot_id=? and ended_at<=?"""
        params = [lot_id, self.as_of]
        if phases:
            sql += f" and phase in ({','.join('?' * len(phases))})"
            params += list(phases)
        return self._rows(sql + " order by ended_at, test_run_id", params, "ended_at")

    def test_measurements(self, test_run_ids):
        ids = list(test_run_ids)
        if not ids:
            return []
        rows = self._rows(f"""select m.test_run_id, m.test_spec_id, m.trial_index, m.raw_value, m.unit, m.condition_json,
                                     m.result, t.ended_at
                              from test_measurement m join test_run t using(test_run_id)
                              where m.test_run_id in ({','.join('?' * len(ids))}) and t.ended_at<=?""",
                          (*ids, self.as_of), "ended_at")
        return rows

    def material_usage(self, lot_id):
        rows = self._rows("""select u.material_lot_id, u.material_type, u.issued_at, m.supplier_id, m.grade,
                                    m.coa_json, m.iqc_method, m.iqc_result, m.iqc_at
                             from material_usage u join material_lot m using(material_lot_id)
                             where u.lot_id=? and u.issued_at<=? order by u.issued_at, u.material_lot_id""",
                          (lot_id, self.as_of), "issued_at")
        for row in rows:
            if row["iqc_at"] and row["iqc_at"] > self.as_of:
                row["iqc_result"] = None
        return rows

    def material_lot_sources(self, material_lot_ids):
        """Supplier and material type of material lots received by as_of: {material_lot_id: (supplier_id, material_type)}."""
        ids = sorted(set(material_lot_ids))
        if not ids:
            return {}
        rows = self._rows(f"""select material_lot_id, supplier_id, material_type, received_at from material_lot
                              where material_lot_id in ({','.join('?' * len(ids))}) and received_at<=?""",
                          (*ids, self.as_of), "received_at")
        return {r["material_lot_id"]: (r["supplier_id"], r["material_type"]) for r in rows}

    # ---------- QA, inspection and past investigations
    def triage(self, rma_id):
        rows = self._rows("""select severity, characteristic_id, characteristic_symbol, customer_industry, repeat_signal,
                                    analysis_level, rationale, decided_at
                             from qa_triage where rma_id=? and decided_at<=?""", (rma_id, self.as_of), "decided_at")
        return rows[0] if rows else None

    def item_lab_tests(self, rma_item_id):
        """Return-lab tests on the returned item itself (for the QA evidence check)."""
        return self._rows("""select test_run_id, subject_type, sample_origin, origin_verified, overall_result, ended_at
                             from test_run where rma_item_id=? and phase='RMA_LAB' and ended_at<=?""",
                          (rma_item_id, self.as_of), "ended_at")

    def detection_counts(self, phase, since, test_spec_id=None):
        """Tests and fails per inspection equipment over [since, as_of], overall or for one test item."""
        if test_spec_id:
            sql = """select r.equipment_id, count(*) n, sum(case when m.result='FAIL' then 1 else 0 end) fails
                     from test_run r join test_measurement m using(test_run_id)
                     where r.phase=? and r.ended_at>=? and r.ended_at<=? and r.equipment_id is not null
                     and m.test_spec_id=? group by r.equipment_id"""
            params = (phase, since, self.as_of, test_spec_id)
        else:
            sql = """select equipment_id, count(*) n, sum(case when overall_result='FAIL' then 1 else 0 end) fails
                     from test_run where phase=? and ended_at>=? and ended_at<=? and equipment_id is not null
                     group by equipment_id"""
            params = (phase, since, self.as_of)
        return {r["equipment_id"]: (r["fails"], r["n"]) for r in self.conn.execute(sql, params)}

    def escapes(self, investigation_ids):
        ids = list(investigation_ids)
        if not ids:
            return []
        return self._rows(f"""select investigation_id, escape_code, detection_process_id, status, assessed_at
                              from escape_assessment where investigation_id in ({','.join('?' * len(ids))})
                              and assessed_at<=?""", (*ids, self.as_of), "assessed_at")

    def corrective_actions(self, investigation_ids):
        ids = list(investigation_ids)
        if not ids:
            return []
        rows = self._rows(f"""select investigation_id, action_role, action_category, description, owner_dept, cause_code,
                                     escape_code, selected_at, implemented_at
                              from corrective_action where investigation_id in ({','.join('?' * len(ids))})
                              and selected_at<=? order by selected_at, description""", (*ids, self.as_of), "selected_at")
        for row in rows:
            if row["implemented_at"] and row["implemented_at"] > self.as_of:
                row["implemented_at"] = None
        return rows

    # ---------- other lots that share something with a lot (candidate lots), recurrence of past actions
    def equipment_runs(self, equipment_id, start, end, process_id=None):
        """Process runs on one machine that started in [start, end] and ended by as_of."""
        sql = """select lot_id, process_id, equipment_id, load_no, started_at, ended_at from process_run
                 where equipment_id=? and started_at>=? and started_at<=? and ended_at<=?"""
        params = [equipment_id, start, end, self.as_of]
        if process_id:
            sql += " and process_id=?"
            params.append(process_id)
        return self._rows(sql + " order by started_at, lot_id", params, "ended_at")

    def material_lot_users(self, material_lot_ids):
        ids = sorted(set(material_lot_ids))
        if not ids:
            return []
        return self._rows(f"""select u.lot_id, u.material_lot_id, u.material_type, m.supplier_id, u.issued_at
                               from material_usage u join material_lot m using(material_lot_id)
                               where u.material_lot_id in ({','.join('?' * len(ids))}) and u.issued_at<=?
                               order by u.issued_at, u.lot_id""", (*ids, self.as_of), "issued_at")

    def lots(self, lot_ids):
        """Production lots started by as_of: spec and start; completion only once it happened."""
        ids = sorted(set(lot_ids))
        if not ids:
            return {}
        rows = self._rows(f"""select lot_id, spec_id, started_at, completed_at from production_lot
                              where lot_id in ({','.join('?' * len(ids))}) and started_at<=?""", (*ids, self.as_of), "started_at")
        for row in rows:
            if row["completed_at"] and row["completed_at"] > self.as_of:
                row["completed_at"] = None
        return {r["lot_id"]: r for r in rows}

    def lot_shipments(self, lot_ids):
        """First shipment time and boards shipped by as_of, per lot."""
        ids = sorted(set(lot_ids))
        if not ids:
            return {}
        rows = self._rows(f"""select sl.lot_id, s.shipped_at, sl.board_qty from shipment_lot sl join shipment s using(shipment_id)
                              where sl.lot_id in ({','.join('?' * len(ids))}) and s.shipped_at<=?""", (*ids, self.as_of), "shipped_at")
        out = {}
        for r in rows:
            first, qty = out.get(r["lot_id"], (None, 0))
            out[r["lot_id"]] = (min(first, r["shipped_at"]) if first else r["shipped_at"], qty + (r["board_qty"] or 0))
        return out

    def lot_claim_counts(self, lot_ids):
        """Returns opened by as_of per lot (the analysed return excluded)."""
        ids = sorted(set(lot_ids))
        if not ids:
            return {}
        rows = self._rows(f"""select i.lot_id, r.rma_id, r.opened_at from rma_item i join rma_claim r using(rma_id)
                              where i.lot_id in ({','.join('?' * len(ids))}) and r.opened_at<=?""", (*ids, self.as_of), "opened_at")
        out = defaultdict(int)
        for r in rows:
            if r["rma_id"] != self.subject:
                out[r["lot_id"]] += 1
        return dict(out)

    def reopened_investigations(self, investigation_ids):
        """Investigations reopened as a recurrence by as_of: {original investigation_id: [recurrence investigation_id]}."""
        ids = sorted(set(investigation_ids))
        if not ids:
            return {}
        rows = self._rows(f"""select investigation_id, reopened_from, opened_at from investigation
                              where reopened_from in ({','.join('?' * len(ids))}) and opened_at<=?""", (*ids, self.as_of), "opened_at")
        out = defaultdict(list)
        for r in rows:
            out[r["reopened_from"]].append(r["investigation_id"])
        return dict(out)

    def recent_same_symptom(self, customer_id, intake_label, since, until):
        """Other returns of one customer with one symptom opened in [since, until], label known by as_of.

        The symptom is the QA-confirmed one once reclassified by as_of (an intake of INSUFFICIENT_INFO counts as its
        confirmed label), otherwise the intake label."""
        rows = self._rows("""select r.rma_id, r.opened_at, s.created_at from rma_claim r join rma_item i using(rma_id)
                             join symptom_assessment s using(rma_item_id)
                             where r.customer_id=? and r.opened_at>=? and r.opened_at<=? and s.created_at<=?
                             and (case when s.reclassified_at is not null and s.reclassified_at<=? then s.primary_label
                                       else s.intake_label end)=?""",
                          (customer_id, since, until, self.as_of, self.as_of, intake_label), "created_at")
        return [r for r in rows if r["rma_id"] != self.subject]

    def occurrence_actions(self, cause_code):
        """Corrective actions on the occurrence of a cause, chosen by as_of (implemented_at hidden until it happens)."""
        rows = self._rows("""select corrective_action_id, investigation_id, action_category, description, cause_code, scope_json,
                                    selected_at, implemented_at
                             from corrective_action where action_role='OCCURRENCE' and cause_code=? and selected_at<=?
                             order by selected_at, corrective_action_id""", (cause_code, self.as_of), "selected_at")
        for row in rows:
            if row["implemented_at"] and row["implemented_at"] > self.as_of:
                row["implemented_at"] = None
            row["scope"] = json.loads(row.pop("scope_json") or "{}")
        return rows

    def intake(self, rma_item_id):
        """CS classification at intake: label and the phrases it was based on."""
        rows = self._rows("""select intake_label, evidence_spans_json, created_at from symptom_assessment
                             where rma_item_id=? and created_at<=?""", (rma_item_id, self.as_of), "created_at")
        if not rows:
            return None
        return {"intake_label": rows[0]["intake_label"], "evidence_spans": json.loads(rows[0]["evidence_spans_json"] or "[]")}
