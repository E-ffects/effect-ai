"""Database connection. Local development uses the v4 SQLite (v3 if v4 is absent); keep SQL portable to PostgreSQL."""
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_DB = DATA_DIR / "pcb_rma_v4.sqlite" if (DATA_DIR / "pcb_rma_v4.sqlite").exists() else DATA_DIR / "pcb_rma_v3.sqlite"


def connect(path=None, threads=False):
    """Open the database read-only. Override the path with RMA_DB or the argument.

    threads=True lets parallel graph nodes share the connection (read-only, so this is safe).
    """
    path = Path(path or os.environ.get("RMA_DB") or DEFAULT_DB)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Extract pcb_rma.sqlite from PCB_RMA_dataset_v4_full.zip into data/pcb_rma_v4.sqlite")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=not threads)
    conn.row_factory = sqlite3.Row
    return conn
