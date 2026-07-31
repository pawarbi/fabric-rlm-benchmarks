"""Cartographer-lite: deterministic, GT-firewalled data maps per dataset.

Modeled on LabRat's disclosed pre-pass (PR #72): before any solving, profile the
sanctioned stores mechanically and write a structure-only reference doc. No LLM
authors any of it; it reads only the hub, never the query folders. Contents:

  * grain: row count and whether each id-ish column is unique -- "title is NOT
    unique (17,201 distinct over 20,000 rows)" is the duplicate-entity warning
    handed over as a measured fact instead of a hint.
  * verified joins: candidate key pairs across stores are TESTED. A join is
    reported only with its measured match rate, including normalized forms
    (digits-only) that catch businessid_12 <-> businessref_12 style keys.
  * dimensions: low-cardinality text columns with their observed values.

Output capped so the doc stays a map, not a dump.
"""
from __future__ import annotations

import pathlib
import re
import sys

import duckdb

HERE = pathlib.Path(__file__).parent
HUBS = HERE / "dab_hubs"
OUT = HERE / "dab_carto"
MAX_DOC = 6000


def tables(con):
    return con.execute("""
        select database_name, schema_name, table_name from duckdb_tables()
        where database_name not in ('system','temp') order by 1,2,3""").fetchall()


def ref(db, sch, tab):
    return f'"{db}"."{sch}"."{tab}"'


def label(db, sch, tab):
    return f"{db}.{tab}" if sch == "main" else f"{sch}.{tab}"


def build_doc(ds: str) -> str:
    hub = HUBS / f"{ds}.duckdb"
    con = duckdb.connect(str(hub), read_only=True)
    con.execute((HUBS / f"{ds}.attach.sql").read_text(encoding="utf-8"))
    tabs = tables(con)
    lines = [f"DATA MAP for this task's stores (mechanically measured, trust the numbers):", ""]
    idcols = []          # (label, ref, col, n_rows)

    lines.append("GRAIN (row counts; id-ish columns marked unique or NOT unique):")
    for db, sch, tab in tabs:
        r = ref(db, sch, tab)
        try:
            n = con.execute(f"select count(*) from {r}").fetchone()[0]
        except Exception:
            continue
        cols = con.execute(f"describe select * from {r}").fetchall()
        marks = []
        for c, t, *_ in cols:
            if re.search(r"id$|_id|ref$|key$|title|name", c, re.I) and n:
                try:
                    d = con.execute(f'select count(distinct "{c}") from {r}').fetchone()[0]
                except Exception:
                    continue
                if re.search(r"id$|_id|ref$", c, re.I):
                    idcols.append((label(db, sch, tab), r, c, n))
                if d == n:
                    marks.append(f"{c} unique")
                elif d < n and n <= 500000:
                    marks.append(f"{c} NOT unique ({d:,} distinct)")
        lines.append(f"  {label(db, sch, tab)}: {n:,} rows" + ("; " + "; ".join(marks[:4]) if marks else ""))

    lines.append("")
    lines.append("VERIFIED JOINS (tested on the data, with measured match rates):")
    digits = "regexp_extract({c}, '([0-9]+)$', 1)"

    def stem(col):
        # business_id, business_ref, businessid -> "business"; track_id -> "track"
        s = re.sub(r"(_?(id|ref|key))+$", "", col.lower()).strip("_")
        return s or col.lower()

    seen = 0
    for i in range(len(idcols)):
        for j in range(i + 1, len(idcols)):
            (la, ra, ca, na), (lb, rb, cb, nb) = idcols[i], idcols[j]
            if la == lb or seen >= 10 or min(na, nb) == 0 or max(na, nb) > 2_000_000:
                continue
            # Same-stem keys only. Dense integer ids "match" ANY other dense
            # integer id (sale_id 1..58049 overlaps track_id 1..19375), and a
            # map that verifies coincidences would actively mislead the solver.
            if stem(ca) != stem(cb):
                continue
            base = min(na, nb)
            try:
                raw = con.execute(
                    f'select count(*) from (select distinct "{ca}" v from {ra}) a '
                    f'join (select distinct "{cb}" v from {rb}) b using(v)').fetchone()[0]
            except Exception:
                raw = 0
            norm = 0
            if raw < 0.5 * base:
                try:
                    ea, eb = digits.format(c=f'"{ca}"'), digits.format(c=f'"{cb}"')
                    norm = con.execute(
                        f"select count(*) from (select distinct {ea} v from {ra} where {ea}!='') a "
                        f"join (select distinct {eb} v from {rb} where {eb}!='') b using(v)").fetchone()[0]
                except Exception:
                    norm = 0
            if raw >= 0.5 * base:
                lines.append(f"  {la}.{ca} = {lb}.{cb}: {raw:,} matching values (direct)")
                seen += 1
            elif norm >= 0.5 * base:
                lines.append(f"  {la}.{ca} <-> {lb}.{cb}: {norm:,} match on trailing DIGITS "
                             f"(prefixes differ; join on regexp_extract(col,'([0-9]+)$',1))")
                seen += 1

    lines.append("")
    lines.append("DIMENSIONS (low-cardinality columns with observed values):")
    dim = 0
    for db, sch, tab in tabs:
        r = ref(db, sch, tab)
        try:
            cols = con.execute(f"describe select * from {r}").fetchall()
        except Exception:
            continue
        for c, t, *_ in cols:
            if dim >= 8 or "VARCHAR" not in str(t):
                continue
            try:
                d = con.execute(f'select count(distinct "{c}") from {r}').fetchone()[0]
                if 2 <= d <= 12:
                    vals = [str(x[0]) for x in con.execute(
                        f'select distinct "{c}" from {r} where "{c}" is not null limit 12').fetchall()]
                    lines.append(f"  {label(db, sch, tab)}.{c}: {', '.join(v[:28] for v in vals)}")
                    dim += 1
            except Exception:
                continue
    con.close()
    doc = "\n".join(lines)
    return doc[:MAX_DOC]


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for ds in (sys.argv[1:] or ["music_brainz_20k", "googlelocal", "yelp"]):
        doc = build_doc(ds)
        (OUT / f"{ds}.txt").write_text(doc, encoding="utf-8")
        print(f"=== {ds} ({len(doc)} chars)")
        print(doc[:1200])
        print()
