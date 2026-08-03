"""Analyse the SpreadsheetBench arms: regression check plus a paired A/B."""
import glob, json, math, sys
from collections import defaultdict

SCRATCH = sys.argv[1]
PUBLISHED = 0.8225


def load(arm):
    """Scored rows only. Five tasks ship no workbook in this extract and are
    recorded as skipped; counting them as failures would understate both arms
    and compare against a different denominator than the published 82.25%."""
    out, skipped = {}, set()
    for f in glob.glob(rf"{SCRATCH}/ssb/run2/results_{arm}_shard*.jsonl"):
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("skipped"):
                skipped.add(r["question_id"])
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
    """Exact two-sided binomial test on discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


body, sk_b = load("body")
cards, sk_c = load("cards")
print(f"skipped, no workbook in this extract: body {len(sk_b)}, cards {len(sk_c)}")
print(f"body  {len(body)}/400   cards {len(cards)}/400\n")

for name, arm in (("body", body), ("cards", cards)):
    if not arm:
        continue
    k = sum(1 for r in arm.values() if r.get("passed"))
    lo, hi = wilson(k, len(arm))
    err = sum(1 for r in arm.values() if r.get("error"))
    print(f"{name:6s} pass {k}/{len(arm)} = {100*k/len(arm):.2f}%  "
          f"95% CI [{100*lo:.1f}, {100*hi:.1f}]   harness errors {err}")

# --- regression check -------------------------------------------------------
if len(body) >= 400:
    k = sum(1 for r in body.values() if r.get("passed"))
    rate = k / len(body)
    delta = 100 * (rate - PUBLISHED)
    verdict = "REGRESSION" if delta < -5 else "no regression"
    print(f"\nregression check vs published 82.25%: {delta:+.2f} points -> {verdict}")

# --- paired A/B -------------------------------------------------------------
common = sorted(set(body) & set(cards))
if common:
    a = b = c = d = 0
    for q in common:
        bp, cp = bool(body[q].get("passed")), bool(cards[q].get("passed"))
        a += bp and cp
        b += bp and not cp        # body only
        c += cp and not bp        # cards only
        d += not bp and not cp
    p = mcnemar(b, c)
    print(f"\npaired on {len(common)} questions")
    print(f"  both pass {a} | body only {b} | cards only {c} | both fail {d}")
    print(f"  McNemar exact p = {p:.4f}  -> "
          f"{'DIFFERENT' if p < 0.05 else 'no significant difference'}")

    bt = sum(r.get("prompt_tokens") or 0 for q, r in body.items() if q in common)
    ct = sum(r.get("prompt_tokens") or 0 for q, r in cards.items() if q in common)
    if bt:
        print(f"  prompt tokens {bt/1e6:.2f}M -> {ct/1e6:.2f}M ({100*(ct-bt)/bt:+.1f}%)")
    bc = sum(r.get("cost_usd") or 0 for q, r in body.items() if q in common)
    cc = sum(r.get("cost_usd") or 0 for q, r in cards.items() if q in common)
    if bc:
        print(f"  cost ${bc:.2f} -> ${cc:.2f} ({100*(cc-bc)/bc:+.1f}%)")

    # pre-registered subgroup: Sheet-Level is where degradation would show
    by_type = defaultdict(lambda: [0, 0, 0])
    for q in common:
        t = body[q].get("instruction_type") or "?"
        by_type[t][0] += 1
        by_type[t][1] += bool(body[q].get("passed"))
        by_type[t][2] += bool(cards[q].get("passed"))
    print("\n  by instruction type (n, body, cards):")
    for t, (n, bp, cp) in sorted(by_type.items()):
        print(f"    {t[:32]:32s} n={n:3d}  body {100*bp/n:5.1f}%  cards {100*cp/n:5.1f}%")

    print("\n  VERDICT:", "ADOPT" if (p >= 0.05 and bt and (bt - ct) / bt >= 0.30)
          else ("REJECT" if p < 0.05 and c < b else "NOT MEASURED"))
