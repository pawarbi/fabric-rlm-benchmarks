"""data_context prototype: the SAME mechanical map as dab_carto, restructured
into topics and delivered as a sandbox-queryable JSON file instead of prompt
paste. The A/B holds content constant; only delivery differs.

Topics: one per table (grain + id-uniqueness + dimension values scoped to that
table), plus "joins" (verified cross-store joins) and "overview" (topic index).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import duckdb

HERE = pathlib.Path(__file__).parent
HUBS = HERE / "dab_hubs"
OUT = HERE / "dab_ctx"


def build_ctx(ds: str) -> dict:
    hub = HUBS / f"{ds}.duckdb"
    con = duckdb.connect(str(hub), read_only=True)
    con.execute((HUBS / f"{ds}.attach.sql").read_text(encoding="utf-8"))
    tabs = con.execute("""
        select database_name, schema_name, table_name from duckdb_tables()
        where database_name not in ('system','temp') order by 1,2,3""").fetchall()

    def ref(db, sch, tab): return f'"{db}"."{sch}"."{tab}"'
    def label(db, sch, tab): return f"{db}.{tab}" if sch == "main" else f"{sch}.{tab}"
    def stem(col):
        s = re.sub(r"(_?(id|ref|key))+$", "", col.lower()).strip("_")
        return s or col.lower()

    ctx: dict[str, str] = {}
    idcols = []
    for db, sch, tab in tabs:
        r = ref(db, sch, tab)
        try:
            n = con.execute(f"select count(*) from {r}").fetchone()[0]
        except Exception:
            continue
        cols = con.execute(f"describe select * from {r}").fetchall()
        lines = [f"{label(db, sch, tab)}: {n:,} rows"]
        for c, t, *_ in cols:
            note = ""
            if re.search(r"id$|_id|ref$|key$|title|name", c, re.I) and n and n <= 500000:
                try:
                    d = con.execute(f'select count(distinct "{c}") from {r}').fetchone()[0]
                    note = " UNIQUE" if d == n else f" NOT unique ({d:,} distinct)"
                    if re.search(r"id$|_id|ref$", c, re.I):
                        idcols.append((label(db, sch, tab), r, c, n))
                except Exception:
                    pass
            if "VARCHAR" in str(t) and not note:
                try:
                    d = con.execute(f'select count(distinct "{c}") from {r}').fetchone()[0]
                    if 2 <= d <= 12:
                        vals = [str(x[0]) for x in con.execute(
                            f'select distinct "{c}" from {r} where "{c}" is not null limit 12').fetchall()]
                        note = f" values: {', '.join(v[:24] for v in vals)}"
                except Exception:
                    pass
            lines.append(f"  {c} {t}{note}")
        ctx[label(db, sch, tab)] = "\n".join(lines)

    digits = "regexp_extract({c}, '([0-9]+)$', 1)"
    jlines = []
    for i in range(len(idcols)):
        for j in range(i + 1, len(idcols)):
            (la, ra, ca, na), (lb, rb, cb, nb) = idcols[i], idcols[j]
            if la == lb or len(jlines) >= 10 or min(na, nb) == 0 or stem(ca) != stem(cb):
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
                jlines.append(f"{la}.{ca} = {lb}.{cb}: {raw:,} matching values (verified direct join)")
            elif norm >= 0.5 * base:
                jlines.append(f"{la}.{ca} <-> {lb}.{cb}: {norm:,} match on trailing DIGITS; "
                              f"join on regexp_extract(col,'([0-9]+)$',1) (verified)")
    ctx["joins"] = "\n".join(jlines) if jlines else "no verified cross-store joins found"
    con.close()
    ctx["overview"] = ("topics: " + ", ".join(sorted(k for k in ctx if k != "overview")) +
                       "\nEach table topic lists grain, columns, uniqueness and observed values. "
                       "'joins' lists cross-store joins verified against the data.")
    return ctx


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for ds in (sys.argv[1:] or ["music_brainz_20k", "googlelocal", "yelp"]):
        c = build_ctx(ds)
        (OUT / f"{ds}.json").write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{ds}: {len(c)} topics, {sum(len(v) for v in c.values())} chars -> dab_ctx/{ds}.json")
        print("  topics:", ", ".join(sorted(c)))
