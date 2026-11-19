"""Database connection.

- DATABASE_URL set (postgresql://...): read the team's PostgreSQL, loaded with db/postgres (same tables and values).
- Otherwise the local SQLite: RMA_DB or the argument, default data/pcb_rma_v4.sqlite (v3 if v4 is absent).

Both return a connection with the sqlite3 interface the code uses: conn.execute(sql, params) with "?" placeholders,
rows readable by index and by column name (dict(row) works). Keep SQL portable: no SQLite-only functions.
"""
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_DB = DATA_DIR / "pcb_rma_v4.sqlite" if (DATA_DIR / "pcb_rma_v4.sqlite").exists() else DATA_DIR / "pcb_rma_v3.sqlite"


def connect(path=None, threads=False):
    """Open the database read-only. DATABASE_URL (PostgreSQL) is used unless a SQLite path is passed.

    threads=True lets parallel graph nodes share the connection (read-only, so this is safe).
    """
    url = os.environ.get("DATABASE_URL")
    if url and path is None:
        return PgConnection(url)
    path = Path(path or os.environ.get("RMA_DB") or DEFAULT_DB)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Extract pcb_rma.sqlite from PCB_RMA_dataset_v4_full.zip into data/pcb_rma_v4.sqlite, "
            "or set DATABASE_URL to the PostgreSQL database")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=not threads)
    conn.row_factory = sqlite3.Row
    return conn


class Row(tuple):
    """A result row readable like sqlite3.Row: row[0], row["col"], dict(row)."""

    def __new__(cls, names, values):
        row = super().__new__(cls, values)
        row._names = names
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._names.index(key))
            except ValueError:
                raise IndexError(f"No item with that key: {key}") from None
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._names)


class _Cursor:
    def __init__(self, rows, description):
        self._rows = rows
        self.description = description

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class PgConnection:
    """Read-only PostgreSQL connection with the part of the sqlite3 API this package uses.

    One connection in autocommit, read-only; a lock lets parallel graph nodes take turns (queries are short).
    """

    def __init__(self, url):
        import psycopg
        self._conn = psycopg.connect(url, autocommit=True)
        self._conn.read_only = True
        self._lock = threading.Lock()

    @staticmethod
    def _sql(sql):
        return sql.replace("%", "%%").replace("?", "%s")

    def execute(self, sql, params=()):
        with self._lock, self._conn.cursor() as cur:
            cur.execute(self._sql(sql), tuple(params) or None)
            if cur.description is None:
                return _Cursor([], None)
            names = [d.name for d in cur.description]
            return _Cursor([Row(names, r) for r in cur.fetchall()], cur.description)

    def close(self):
        self._conn.close()
