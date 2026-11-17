"""Database connection. Local development uses the v3 SQLite; keep SQL portable to PostgreSQL."""
import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "pcb_rma_v3.sqlite"


def connect(path=None):
    """Open the database read-only. Override the path with RMA_DB or the argument."""
    path = Path(path or os.environ.get("RMA_DB") or DEFAULT_DB)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Extract pcb_rma.sqlite from PCB_RMA_dataset_v3_full.zip into rma_agent/data/pcb_rma_v3.sqlite")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
