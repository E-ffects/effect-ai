"""Check a PostgreSQL load against the SQLite source: every table row by row, and every view's result.

    python db/postgres/verify.py data/pcb_rma_v4.sqlite "postgresql://user@host:5432/db"

Uses the psql command line (no Python driver needed). Exit code 1 if anything differs.
"""
import hashlib
import sqlite3
import subprocess
import sys

VIEWS = ["monthly_claims", "qa_case_overview", "spc_out_of_spec_by_equipment_month", "special_characteristic_results",
         "cause_confirmation_basis", "escape_point_summary", "qa_symptom_reproduction", "claim_loss", "loss_by_month",
         "loss_by_investigation"]


def unescape(s):
    if s == r"\N":
        return None
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append({"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def norm(v):
    """Compare values by meaning: numbers as rounded floats, everything else as text."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    try:
        return round(float(v), 6) if v.strip() and v.strip().lstrip("-").replace(".", "", 1).replace("e-", "", 1).replace("e+", "", 1).isdigit() else v
    except ValueError:
        return v


def digest(rows):
    h = hashlib.sha256()
    for row in sorted(repr(tuple(norm(v) for v in r)) for r in rows):
        h.update(row.encode())
    return len(rows), h.hexdigest()[:16]


def pg_rows(url, sql):
    res = subprocess.run(["psql", url, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", f"COPY ({sql}) TO STDOUT"],
                         capture_output=True, text=True, check=True)
    return [tuple(unescape(x) for x in line.split("\t")) for line in res.stdout.splitlines()]


def main(src, url):
    db = sqlite3.connect(src)
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    bad = 0
    print(f"{'object':40} {'sqlite':>10} {'postgres':>10}  result")
    for kind, names in (("table", tables), ("view", VIEWS)):
        for name in names:
            cols = [r[1] for r in db.execute(f'pragma table_info("{name}")')]
            select = f'select {", ".join(chr(34) + c + chr(34) for c in cols)} from "{name}"'
            a = digest(db.execute(select).fetchall())
            b = digest(pg_rows(url, select))
            ok = a == b
            bad += not ok
            print(f"{kind + ' ' + name:40} {a[0]:>10,} {b[0]:>10,}  {'same' if ok else 'DIFFERENT'}")
    fk = subprocess.run(["psql", url, "-X", "-At", "-c",
                         "select count(*) from pg_constraint where contype='f' and connamespace='public'::regnamespace"],
                        capture_output=True, text=True, check=True).stdout.strip()
    print(f"\nforeign keys in postgres: {fk} (all checked when the load committed)")
    print("ALL SAME" if not bad else f"{bad} object(s) differ")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(*sys.argv[1:3])
