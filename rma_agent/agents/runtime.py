"""Shared resources for one service process: DB connection, case library, process baseline, master tables.

Loaded once (about 0.3 s) and reused by every analysis. refresh() reloads the case library after Spring stores
newly approved causes (POST /cases/refresh).
"""
import threading

from ..asof import AsOfStore, CaseLibrary
from ..db import connect
from ..tools.process import ProcessBaseline


def load_masters(conn):
    q = lambda sql: {r[0]: dict(r) for r in conn.execute(sql)}
    return {
        "causes": q("select cause_code, * from cause_master"),
        "escapes": q("select escape_code, * from escape_master"),
        "processes": q("select process_id, * from process_master"),
        "symptoms": q("select minor_code, * from symptom_definition"),
        "requirements": q("select cause_code, * from qa_causal_requirement"),
        "equipment_process": {r[0]: r[1] for r in conn.execute("select equipment_id, process_id from equipment_master")},
    }


class Runtime:
    def __init__(self, db_path=None):
        self.conn = connect(db_path, threads=True)
        self.masters = load_masters(self.conn)
        self.library = CaseLibrary(self.conn)
        self.baseline = ProcessBaseline(self.conn)

    def store(self, as_of):
        return AsOfStore(self.conn, as_of, self.library)

    def refresh(self):
        self.library = CaseLibrary(self.conn)
        self.baseline = ProcessBaseline(self.conn)
        return len(self.library.as_of("9999"))


_runtime = None
_lock = threading.Lock()


def get_runtime():
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = Runtime()
        return _runtime
