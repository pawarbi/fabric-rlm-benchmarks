# Pre-registration: v2 stack on Luna (written before lunav2 launched)

Baseline, locked: luna5 = 0.6957 (270 records, RUNS=5, hints, verify v1, 0 leaks).

## Changes under test (one arm, all together)

1. CoVe gate on agreements: audit solver runs check queries; overrides only when
   a printed check fails and the corrected answer differs. Target: the 16
   agree-on-wrong records (measured in luna5's taxonomy).
2. Method-diverse second solve (decompose-first note). Target: decorrelating the
   same false agreements at the source.
3. Reconciler must-execute-a-query rule. Target: 37% lazy one-turn picks
   (3 records of direct decisive cost).
4. TIMEOUT 1200 -> 1800. Target: 39 near-timeout records.

Majority-of-3 was considered and REJECTED by data: luna's reconciler measured 77%
on decisive pairs and 39% both-wrong rescue; voting would demote its strongest
component. Do not reintroduce without new evidence.

## Endpoint and decision rule

Primary: stratified Pass@1 vs 0.6957, paired per query across the same 54.
This is a packaged-stack test, not component attribution; if the net is negative,
read verdict-level data (cove_override outcomes, diverse-S2 agreement rates)
before deciding what to strip, and rerun the reduced stack once.

Ship if >= +0.01 net with no dataset regressing by more than 0.10.
Report the delta either way; expected honest range +0.01 to +0.05, because the
16-record target class includes interpretation-consistent answers (q8-class)
that CoVe structurally cannot catch -- the smoke already showed one audit
confirming a wrong-but-consistent answer.

Known risks accepted: the CoVe auditor sees the agreed answer (anchoring toward
confirmation is the conservative direction by design); diverse-S2 may lower
agreement rate and raise cost via more reconciliations.

Cost estimate: ~$18-22 (luna5 ran $16; +15-20% for audit calls).
