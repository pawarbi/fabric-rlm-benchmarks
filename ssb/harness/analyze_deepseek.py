"""DeepSeek V4 Flash vs MiniMax M3 on SpreadsheetBench, both in the cards arm.

Paired on question_id: same tasks, same harness, same skill presentation, so the
only thing that differs is the model. Anything scored in one arm but not the
other is dropped rather than counted as a failure, because a missing row means
the task did not run, not that the model got it wrong.
"""
import glob, json, math, os, sys
from collections import defaultdict

SCRATCH = sys.argv[1]
PUBLISHED = 0.8225          # the number in the README, from the M3 run
M3_REPRO = 0.8380           # our own clean M3 reproduction


def load(pattern):
    out, skipped = {}, set()
    for f in glob.glob(pattern):
        if not os.path.isfile(f):
            continue
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("skipped"):
                skipped.add(r.get("question_id"))
                continue
            out[r["question_id"]] = r
    return out, skipped


def wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


m3, _ = load(rf"{SCRATCH}/ssb/run2/results_cards_shard*.jsonl")
ds, ds_sk = load(rf"{SCRATCH}/ssb/deepseek400/results_cards_shard*.jsonl")
print(f"M3 cards {len(m3)} scored | DeepSeek cards {len(ds)} scored, {len(ds_sk)} skipped\n")

for name, arm in (("M3", m3), ("DeepSeek", ds)):
    if not arm:
        continue
    k = sum(1 for r in arm.values() if r.get("passed"))
    lo, hi = wilson(k, len(arm))
    err = sum(1 for r in arm.values() if r.get("error"))
    print(f"{name:9s} {k}/{len(arm)} = {100*k/len(arm):.2f}%  "
          f"95% CI [{100*lo:.1f}, {100*hi:.1f}]  errors {err}")

common = sorted(set(m3) & set(ds))
if not common:
    sys.exit("\nno overlap yet")

a = b = c = d = 0
for q in common:
    mp, dp = bool(m3[q].get("passed")), bool(ds[q].get("passed"))
    a += mp and dp
    b += mp and not dp          # M3 only
    c += dp and not mp          # DeepSeek only
    d += not mp and not dp

mk = sum(1 for q in common if m3[q].get("passed"))
dk = sum(1 for q in common if ds[q].get("passed"))
delta = 100 * (dk - mk) / len(common)
p = mcnemar(b, c)

print(f"\npaired on {len(common)} questions")
print(f"  M3 {100*mk/len(common):.2f}%   DeepSeek {100*dk/len(common):.2f}%   "
      f"delta {delta:+.2f} points")
print(f"  both {a} | M3 only {b} | DeepSeek only {c} | neither {d}")
print(f"  McNemar exact p = {p:.4f} -> "
      f"{'DIFFERENT' if p < 0.05 else 'not significant'}")

# The discordant count caps what this run can ever show. Say so rather than
# letting a p-value near 1 read as "the models are the same".
if p >= 0.05:
    print(f"  {b+c} discordant pairs: with this few, only a large gap could reach p<0.05")

for label, ref in (("published README", PUBLISHED), ("our M3 repro", M3_REPRO)):
    print(f"  vs {label} {100*ref:.2f}%: {100*dk/len(common) - 100*ref:+.2f} points")

mt = sum(m3[q].get("total_tokens") or m3[q].get("prompt_tokens") or 0 for q in common)
dt = sum(ds[q].get("total_tokens") or ds[q].get("prompt_tokens") or 0 for q in common)
if mt:
    print(f"\n  tokens/task {mt/len(common):,.0f} -> {dt/len(common):,.0f} "
          f"= {dt/mt:.2f}x")
mc = sum(m3[q].get("cost_usd") or 0 for q in common)
dc = sum(ds[q].get("cost_usd") or 0 for q in common)
if mc:
    print(f"  cost ${mc:.2f} -> ${dc:.2f} = {dc/mc:.2f}x")

by_type = defaultdict(lambda: [0, 0, 0])
for q in common:
    t = m3[q].get("instruction_type") or ds[q].get("instruction_type") or "?"
    by_type[t][0] += 1
    by_type[t][1] += bool(m3[q].get("passed"))
    by_type[t][2] += bool(ds[q].get("passed"))
print("\n  by instruction type:")
for t, (n, mp, dp) in sorted(by_type.items()):
    print(f"    {t[:30]:30s} n={n:3d}  M3 {100*mp/n:5.1f}%  DS {100*dp/n:5.1f}%  "
          f"{100*(dp-mp)/n:+5.1f}")

print("\n  pilot said +5.0 at n=60; the full run is the measurement, the pilot was a screen")
