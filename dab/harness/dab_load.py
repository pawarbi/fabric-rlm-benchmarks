"""Build one DuckDB hub per DataAgentBench dataset.

Only sanctioned stores from that dataset's db_config.yaml are loaded, per
SUBMISSION_RUBRIC.md section 1. Nothing else is read.

Four store kinds, and two of them cost nothing:

  sqlite    ATTACH ... (TYPE sqlite, READ_ONLY)   zero copy
  duckdb    ATTACH ... (READ_ONLY)                zero copy
  postgres  pg_dump plain text, COPY ... FROM stdin -> materialised
  mongo     BSON dump -> materialised

Attached stores keep their own database namespace, so a query reads
``core_crm.customers`` exactly as it would against the real thing. Materialised
stores become schemas inside the hub, named after the store, so the namespacing
looks the same either way.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import tempfile

import duckdb
import yaml

DAB = pathlib.Path(r"C:/Users/sandeeppawar/Downloads/DataAgentBench")

# pg_dump COPY text format escapes. Real newlines and tabs never appear inside a
# value, so the block stays safe to read line by line.
PG_UNESCAPE = {r"\\": "\\", r"\t": "\t", r"\n": "\n", r"\r": "\r"}
PG_TO_DUCK = {
    "smallint": "SMALLINT", "integer": "INTEGER", "bigint": "BIGINT",
    "numeric": "DOUBLE", "decimal": "DOUBLE", "real": "FLOAT",
    "double precision": "DOUBLE", "boolean": "BOOLEAN", "date": "DATE",
    "timestamp": "TIMESTAMP", "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMPTZ", "json": "JSON", "jsonb": "JSON",
    "uuid": "VARCHAR", "text": "VARCHAR", "character varying": "VARCHAR",
    "character": "VARCHAR", "bytea": "BLOB",
}


def _duck_type(pg: str) -> str:
    pg = pg.strip().lower()
    pg = re.sub(r"\(.*?\)", "", pg).strip()          # varchar(255) -> varchar
    pg = re.sub(r"\[\]$", "", pg).strip()            # arrays -> text
    return PG_TO_DUCK.get(pg, "VARCHAR")


def _pg_tables(sql: str) -> dict[str, list[tuple[str, str]]]:
    """Column names and DuckDB types per table, from the dump's CREATE TABLEs."""
    out: dict[str, list[tuple[str, str]]] = {}
    for m in re.finditer(
        r"CREATE TABLE\s+(?:[\w\"]+\.)?\"?(\w+)\"?\s*\((.*?)\n\);", sql, re.S | re.I
    ):
        tname, body = m.group(1), m.group(2)
        cols = []
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(
                ("CONSTRAINT", "PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK")
            ):
                continue
            cm = re.match(r'"?([\w]+)"?\s+(.+?)(?:\s+(?:NOT NULL|DEFAULT|COLLATE).*)?$', line)
            if cm:
                cols.append((cm.group(1), _duck_type(cm.group(2))))
        if cols:
            out[tname] = cols
    return out


# Two COPY spellings exist across the dumps. Most datasets use the plain-text
# form "FROM stdin;" (backslash escapes, no quoting). imdb uses
# "FROM stdin WITH (FORMAT CSV, DELIMITER E'\t', NULL '\N');" whose body is
# CSV-quoted. The regex accepts both and captures the options so the body can be
# parsed per its actual format; requiring "FROM stdin;" exactly made every imdb
# COPY block silently non-match, which built an empty hub with a success exit.
COPY_START = re.compile(
    r"^COPY\s+(?:[\w\"]+\.)?\"?(\w+)\"?\s*\(([^)]*)\)\s+FROM stdin"
    r"(?:\s+WITH\s*\((?P<opts>[^)]*)\))?\s*;\s*$", re.I)
CREATE_START = re.compile(r"^CREATE TABLE\s+(?:[\w\"]+\.)?\"?(\w+)\"?\s*\(\s*$", re.I)


def load_postgres(con, schema: str, sql_path: pathlib.Path, log) -> None:
    """Materialise a pg_dump plain-text file into ``schema``, streaming.

    Read line by line rather than whole-file: imdb's movies.sql is 1.6 GB, and
    slurping it into a str and running a DOTALL regex over it costs several GB of
    RAM. pg_dump plain emits a table's CREATE before its COPY, so one forward pass
    can collect column types and then stream each COPY body straight to a TSV.
    """
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dab_pg_"))
    declared: dict[str, list[tuple[str, str]]] = {}
    made = 0
    try:
        with open(sql_path, encoding="utf-8", errors="replace") as fh:
            ddl_name, ddl_buf = None, []
            while True:
                line = fh.readline()
                if not line:
                    break
                raw = line.rstrip("\n").rstrip("\r")

                if ddl_name is not None:                      # inside a CREATE TABLE
                    if raw.startswith(");"):
                        declared[ddl_name] = _pg_tables(
                            f"CREATE TABLE {ddl_name} (\n" + "\n".join(ddl_buf) + "\n);"
                        ).get(ddl_name, [])
                        ddl_name, ddl_buf = None, []
                    else:
                        ddl_buf.append(raw)
                    continue

                cm = CREATE_START.match(raw)
                if cm:
                    ddl_name, ddl_buf = cm.group(1), []
                    continue

                m = COPY_START.match(raw)
                if not m:
                    continue

                tname = m.group(1)
                cols = [c.strip().strip('"') for c in m.group(2).split(",")]
                csv_fmt = bool(re.search(r"format\s+csv", m.group("opts") or "", re.I))
                tsv = tmp / f"{tname}.tsv"
                nrows = 0
                with open(tsv, "w", encoding="utf-8", newline="") as out:
                    for body in fh:                            # stream until the \. marker
                        b = body.rstrip("\n").rstrip("\r")
                        if b == r"\.":
                            break
                        out.write(b + "\n")
                        nrows += 1
                types = dict(declared.get(tname, []))
                spec = ", ".join(f"'{c}': '{types.get(c, 'VARCHAR')}'" for c in cols)
                # CSV-format bodies are quoted ("" escaping); plain bodies are not.
                quoting = "quote='\"', escape='\"'" if csv_fmt else "quote='', escape=''"
                con.execute(
                    f'CREATE OR REPLACE TABLE "{schema}"."{tname}" AS '
                    f"SELECT * FROM read_csv('{tsv.as_posix()}', delim='\t', header=false, "
                    f"{quoting}, nullstr='\\N', columns={{{spec}}}, "
                    f"ignore_errors=true, all_varchar=false)"
                )
                n = con.execute(f'SELECT count(*) FROM "{schema}"."{tname}"').fetchone()[0]
                log.append(f"      pg  {schema}.{tname}: {n:,} rows, {len(cols)} cols"
                           + (f"  (of {nrows:,} streamed)" if n != nrows else ""))
                made += 1
                tsv.unlink(missing_ok=True)                     # free disk as we go
        if made == 0:
            # Third dump dialect: INSERT INTO ... VALUES with no COPY at all
            # (crmarenapro support.sql, cve kev.sql, usaspending contracts.sql).
            made = _load_pg_inserts(con, schema, sql_path, log)
        if made == 0:
            # A dump this loader cannot parse must fail loudly. imdb's CSV-format
            # COPY silently produced an empty hub with a success exit before this.
            raise ValueError(
                f"load_postgres materialised 0 tables from {sql_path.name}; "
                "its COPY/CREATE/INSERT syntax is not covered by the parser")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _split_sql_statements(text: str):
    """Yield complete ;-terminated statements, respecting '...' string literals.

    Naive splitting on ';' breaks on values containing semicolons. pg dumps with
    standard_conforming_strings=on escape quotes only by doubling them, so a
    single in-string state flag is sufficient.
    """
    buf = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch == "'":
            if in_str and i + 1 < n and text[i + 1] == "'":
                buf.append("'")
                i += 1                      # doubled quote inside a string
            else:
                in_str = not in_str
        elif ch == ";" and not in_str:
            yield "".join(buf).strip()
            buf = []
        i += 1
    tail = "".join(buf).strip()
    if tail:
        yield tail


def _load_pg_inserts(con, schema: str, sql_path: pathlib.Path, log) -> int:
    """Execute CREATE TABLE + INSERT statements directly in DuckDB.

    DuckDB accepts pg's TEXT/INTEGER types and standard INSERT syntax, so the
    dump statements run as-is once table names are qualified into the store's
    schema. INSERTs are batched to keep call overhead sane (contracts.sql has
    20,187 of them).
    """
    text = sql_path.read_text(encoding="utf-8", errors="replace")
    qualify = re.compile(r'^(CREATE TABLE|INSERT INTO)\s+("?[A-Za-z_][\w]*"?)', re.I)
    made_tables = []
    batch: list[str] = []

    def flush():
        if batch:
            con.execute("\n".join(batch))
            batch.clear()

    for st in _split_sql_statements(text):
        m = qualify.match(st)
        if not m:
            continue                        # SET / ALTER / comments: skip
        verb, tname = m.group(1).upper(), m.group(2)
        qualified = qualify.sub(f'{m.group(1)} "{schema}".{tname}', st, count=1)
        if verb == "CREATE TABLE":
            flush()
            con.execute(qualified)
            made_tables.append(tname.strip('"'))
        else:
            batch.append(qualified if qualified.endswith(";") else qualified + ";")
            if len(batch) >= 500:
                flush()
    flush()
    for t in made_tables:
        n = con.execute(f'SELECT count(*) FROM "{schema}"."{t}"').fetchone()[0]
        log.append(f"      pg  {schema}.{t}: {n:,} rows (INSERT-format dump)")
    return len(made_tables)


def load_mongo(con, schema: str, dump_folder: pathlib.Path, log) -> None:
    """Materialise every .bson collection under ``dump_folder`` into ``schema``.

    Top-level scalars become columns. Nested documents and arrays become JSON
    text, which keeps them queryable through DuckDB's JSON functions instead of
    being silently flattened away.
    """
    import bson

    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    files = sorted(dump_folder.rglob("*.bson"))
    for bf in files:
        if bf.name.startswith("system."):
            continue
        # Stream doc-by-doc to JSONL. krama's files.bson is 529 MB and holding every
        # decoded dict in a list first costs multiple GB.
        jl = pathlib.Path(tempfile.mkdtemp(prefix="dab_mongo_")) / f"{bf.stem}.jsonl"
        count = 0
        with open(bf, "rb") as fh, open(jl, "w", encoding="utf-8") as out:
            for doc in bson.decode_file_iter(fh):
                rec = {}
                for k, v in doc.items():
                    if k == "_id":
                        v = str(v)
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, default=str, ensure_ascii=False)
                    elif not isinstance(v, (str, int, float, bool, type(None))):
                        v = str(v)
                    rec[k] = v
                out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                count += 1
        if not count:
            shutil.rmtree(jl.parent, ignore_errors=True)
            continue
        con.execute(
            f'CREATE OR REPLACE TABLE "{schema}"."{bf.stem}" AS '
            f"SELECT * FROM read_json_auto('{jl.as_posix()}', format='newline_delimited')"
        )
        n = con.execute(f'SELECT count(*) FROM "{schema}"."{bf.stem}"').fetchone()[0]
        log.append(f"      mongo {schema}.{bf.stem}: {n:,} docs")
        shutil.rmtree(jl.parent, ignore_errors=True)


def build_hub(dataset: str, out_dir: pathlib.Path, force: bool = False):
    """Create <out_dir>/<dataset>.duckdb wired to that dataset's stores."""
    root = DAB / f"query_{dataset}"
    cfg = yaml.safe_load((root / "db_config.yaml").read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    hub = out_dir / f"{dataset}.duckdb"
    if hub.exists() and not force:
        return hub, [f"  (reusing {hub.name})"]
    if hub.exists():
        hub.unlink()

    log = [f"  building {hub.name}"]
    con = duckdb.connect(str(hub))
    con.execute("INSTALL sqlite; LOAD sqlite; INSTALL json; LOAD json;")
    attach_lines = []

    for store, spec in (cfg.get("db_clients") or {}).items():
        kind = (spec.get("db_type") or "").lower()
        if kind == "sqlite":
            p = root / spec["db_path"]
            con.execute(f"ATTACH '{p.as_posix()}' AS \"{store}\" (TYPE sqlite, READ_ONLY)")
            attach_lines.append(
                f"ATTACH '{p.as_posix()}' AS \"{store}\" (TYPE sqlite, READ_ONLY);")
            n = con.execute(
                "select count(*) from duckdb_tables() where database_name=?", [store]
            ).fetchone()[0]
            log.append(f"      sqlite {store}: {n} tables (attached, zero copy)")
        elif kind == "duckdb":
            p = root / spec["db_path"]
            con.execute(f"ATTACH '{p.as_posix()}' AS \"{store}\" (READ_ONLY)")
            attach_lines.append(f"ATTACH '{p.as_posix()}' AS \"{store}\" (READ_ONLY);")
            n = con.execute(
                "select count(*) from duckdb_tables() where database_name=?", [store]
            ).fetchone()[0]
            log.append(f"      duckdb {store}: {n} tables (attached, zero copy)")
        elif kind == "postgres":
            load_postgres(con, store, root / spec["sql_file"], log)
        elif kind == "mongo":
            load_mongo(con, store, root / spec["dump_folder"], log)
        else:
            log.append(f"      !! unknown db_type {kind!r} for store {store}")

    # Attachments do not persist inside a DuckDB file, so record them for the
    # consumer to replay. Materialised schemas are already in the file.
    (out_dir / f"{dataset}.attach.sql").write_text(
        "INSTALL sqlite; LOAD sqlite; INSTALL json; LOAD json;\n" + "\n".join(attach_lines),
        encoding="utf-8")
    con.close()
    return hub, log


if __name__ == "__main__":
    out = pathlib.Path(__file__).parent / "dab_hubs"
    force = "--force" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or [
        p.name.replace("query_", "") for p in sorted(DAB.glob("query_*"))]
    for ds in names:
        hub, log = build_hub(ds, out, force=force)
        print(f"\n=== {ds} ===")
        for line in log:
            print(line)
        print(f"  -> {hub} ({hub.stat().st_size/1e6:.1f} MB)")
