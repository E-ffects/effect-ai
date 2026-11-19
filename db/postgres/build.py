"""Convert the PCB RMA SQLite database into a PostgreSQL load package.

    python db/postgres/build.py data/pcb_rma_v4.sqlite data/postgres_v4

Writes into the output folder:
  schema.sql   tables (primary keys, foreign keys DEFERRABLE INITIALLY DEFERRED) and indexes
  views.sql    the 10 views (copied from db/postgres/views.sql, written for PostgreSQL)
  data/*.tsv   one file per table in PostgreSQL COPY text format (NULL = \\N)
  load.sql     psql script: schema, data in one transaction, views, ANALYZE
  manifest.json  row count per table (verify.py compares against it)

Types are kept as in SQLite so values come back unchanged: TEXT -> text (timestamps stay ISO-8601 text with time zone,
JSON stays text), INTEGER -> bigint, REAL -> double precision. Converting to timestamptz/jsonb/boolean is a separate
decision for the backend schema (see README.md).

Load:  psql "$RMA_DATABASE_URL" -v ON_ERROR_STOP=1 -f data/postgres_v4/load.sql   (run from inside the output folder)
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

TYPES = {"TEXT": "text", "INTEGER": "bigint", "REAL": "double precision"}
HERE = Path(__file__).resolve().parent


def q(name):
    return '"' + name.replace('"', '""') + '"'


def tsv(value):
    if value is None:
        return r"\N"
    if isinstance(value, float):
        return repr(value)
    s = str(value)
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def main(src, out):
    db = sqlite3.connect(src)
    out = Path(out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    ddl, fks, counts = [], [], {}
    for t in tables:
        cols = list(db.execute(f"pragma table_info({q(t)})"))
        lines = [f"  {q(c[1])} {TYPES[c[2].upper()]}{' NOT NULL' if c[3] else ''}" for c in cols]
        pk = [c[1] for c in sorted(cols, key=lambda c: c[5]) if c[5]]
        if pk:
            lines.append(f"  PRIMARY KEY ({', '.join(map(q, pk))})")
        ddl.append(f"CREATE TABLE {q(t)} (\n" + ",\n".join(lines) + "\n);")
        groups = {}
        for fk in db.execute(f"pragma foreign_key_list({q(t)})"):
            groups.setdefault(fk[0], []).append(fk)
        for i, rows in sorted(groups.items()):
            src_cols = ", ".join(q(r[3]) for r in rows)
            ref = rows[0][2]
            ref_cols = ", ".join(q(r[4]) if r[4] else q(pk_col) for r, pk_col in
                                 zip(rows, [c[1] for c in sorted(db.execute(f"pragma table_info({q(ref)})"), key=lambda c: c[5]) if c[5]]))
            fks.append(f"ALTER TABLE {q(t)} ADD CONSTRAINT {q(f'fk_{t}_{i}')} FOREIGN KEY ({src_cols}) "
                       f"REFERENCES {q(ref)} ({ref_cols}) DEFERRABLE INITIALLY DEFERRED;")
        names = [c[1] for c in cols]
        n = 0
        with open(out / "data" / f"{t}.tsv", "w", encoding="utf-8", newline="\n") as f:
            for row in db.execute(f"select {', '.join(map(q, names))} from {q(t)}"):
                f.write("\t".join(tsv(v) for v in row) + "\n")
                n += 1
        counts[t] = n
    indexes = [r[0] + ";" for r in db.execute(
        "select sql from sqlite_master where type='index' and sql is not null order by name")]

    (out / "schema.sql").write_text(
        f"-- PCB RMA schema for PostgreSQL, generated from {Path(src).name} by db/postgres/build.py\n\n"
        + "\n\n".join(ddl) + "\n\n" + "\n".join(fks) + "\n\n" + "\n".join(indexes) + "\n", encoding="utf-8")
    shutil.copy(HERE / "views.sql", out / "views.sql")
    load = ["-- psql -v ON_ERROR_STOP=1 -f load.sql   (run inside this folder; the schema must be empty)",
            "\\set ON_ERROR_STOP 1", "\\i schema.sql", "BEGIN;"]
    load += [f"\\copy {q(t)} FROM 'data/{t}.tsv'" for t in tables]
    load += ["COMMIT;", "\\i views.sql", "ANALYZE;"]
    (out / "load.sql").write_text("\n".join(load) + "\n", encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({"source": Path(src).name, "tables": counts,
                                                   "views": 10, "indexes": len(indexes), "foreign_keys": len(fks)},
                                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(tables)} tables, {sum(counts.values()):,} rows, {len(fks)} foreign keys, {len(indexes)} indexes -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
