#!/bin/bash
# Self-healing resume for the Kimi stack run.
# Recomputes what is still missing each pass, so a crash mid-pass costs only
# the tasks that were in flight. Stops when nothing is left or a pass adds
# nothing (genuinely unsolvable tasks) to avoid looping forever.
SCRATCH="$1"
OUT="$SCRATCH/full_stack_kimi"
TESTBED="$SCRATCH/AgenticDataBench/testbed"
export PYTHONIOENCODING=utf-8

remaining() {
  python - "$TESTBED" "$OUT" <<'PY'
import json, sys
from pathlib import Path
tb, out = Path(sys.argv[1]), Path(sys.argv[2])
tasks = {json.loads(l)["id"]: json.loads(l)
         for l in open(tb / "tasks" / "dev.jsonl", encoding="utf-8")}
al = lambda v: v if isinstance(v, list) else [v]
todo = [t for t in sorted(tasks)
        if not all((out / t / o).exists() for o in al(tasks[t]["output_file_name"]))]
print(",".join(todo))
PY
}

prev=999
for pass_no in 1 2 3 4 5; do
  TODO=$(remaining)
  if [ -z "$TODO" ]; then echo "ALL 246 COMPLETE"; break; fi
  n=$(echo "$TODO" | tr ',' '\n' | wc -l)
  echo "=== pass $pass_no: $n tasks remaining"
  if [ "$n" -ge "$prev" ]; then
    echo "no progress last pass; remaining tasks look unsolvable, stopping"
    break
  fi
  prev=$n

  pids=()
  for i in 0 1 2 3 4 5; do
    SHARD=$(python -c "
ids='''$TODO'''.split(',')
print(','.join(ids[$i::6]))")
    [ -z "$SHARD" ] && continue
    ( cd "$SCRATCH/runner" && python run_pilot.py \
        --testbed "$TESTBED" --tasks "$SHARD" --outdir "$OUT" \
        --lm openrouter/moonshotai/kimi-k2.5 --max-turns 25 --timeout 1800 \
        --temperature 0 --validate-outputs \
        > "$SCRATCH/kfix_p${pass_no}_$i.log" 2>&1 ) &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p"; done
  echo "=== pass $pass_no done"
done
echo "RESUME LOOP FINISHED"
