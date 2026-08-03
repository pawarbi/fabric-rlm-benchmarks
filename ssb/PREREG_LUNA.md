# Pre-registration: GPT-5.6 Luna at max reasoning on SpreadsheetBench

Fixed before running.

## Why a pilot rather than the full 400 with a kill-switch

A hard token abort fires after 5 tasks, on cost alone, long before accuracy is
measurable. That would cancel a run that might be worth keeping. So: a paired
pilot first, decide on both axes, then commit or stop.

## Pilot

60 tasks, stratified to match the full set's split (about 41 Cell-Level, 19
Sheet-Level), drawn by fixed seed. The **same** task ids the M3 arm already ran,
so the comparison is paired rather than against a different sample.

Model `openrouter/openai/gpt-5.6-luna-pro`, `reasoning_effort=max` allow-listed
through litellm so it cannot be silently dropped. No temperature (that route
rejects it). Everything else identical to the winning M3 configuration.

## Decision rule

Let **A** be the paired accuracy difference (Luna minus M3) on the pilot tasks,
and **T** the token multiple against M3's measured 41,134 tokens/task.

- **Continue to the full 400** if A >= +2.0 points, whatever T turns out to be.
  A real two-point gain on SpreadsheetBench is worth paying for.
- **Continue** if A is between 0 and +2.0 **and** T <= 3.
- **Stop** if A < 0, or if A < +2.0 and T > 3.
- If the pilot cannot be graded (crashes, provider errors), stop and report
  rather than guessing.

## Stated in advance

n=60 with a binary outcome is thin: the standard error on a single arm is about
5 points, and McNemar depends on discordant pairs. The pilot is a **screen**,
not a measurement — it answers "is this plausibly better and affordable enough
to spend an hour on", not "is Luna better than M3". Only the full 400 could
answer the latter, and even then at about +/-1.9 points.

So a pilot result of, say, +3 points will be reported as "passed the screen at
n=60", not as a measured improvement.

## Known risk

One Luna task at max reasoning on AgenticDataBench used 122k prompt and 70k
completion tokens including 59k reasoning tokens, and took 547s. If that carries
over, the full 400 is roughly 6 hours and well past 3x tokens, so the A >= +2.0
branch is the only one that would justify it.
