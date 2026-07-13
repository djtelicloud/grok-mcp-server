#!/bin/zsh
# Phase C matrix: remaining families in two parallel waves (2 jobs each),
# then the combined-interference run, then the data-efficiency curve.
set -u
cd "$(dirname "$0")"
source .venv/bin/activate
mkdir -p logs

run() {  # run <family> [extra ft.py args...]
  local fam=$1; shift
  echo "[matrix] start $fam $(date +%H:%M:%S)"
  python ft.py "$fam" "$@" > "logs/ft_${fam}.log" 2>&1
  echo "[matrix] done  $fam $(date +%H:%M:%S) $(grep -o 'FINETUNED_EVAL.*' logs/ft_${fam}.log | head -c 200)"
}

# wave 1
run route_selection &
run recovery_selection &
wait
# wave 2
run tool_selection &
run memory_rerank &
wait
# wave 3
run extraction &
run abstention &
wait
# wave 4
run next_step &
# combined interference: 7 families in one file (abstention excluded —
# family VOIDED for test-train leakage; the committed combined.jsonl is
# this 7-family concat, sha256[:16] c0165afbebb2c0ee, 2618 rows)
cat data/route_selection.jsonl data/observation_typing.jsonl data/recovery_selection.jsonl \
    data/memory_rerank.jsonl data/tool_selection.jsonl data/extraction.jsonl \
    data/next_step.jsonl > data/combined.jsonl
run combined --data data/combined.jsonl --batch-size 32 &
wait
# data-efficiency curve on route_selection
python - <<'EOF'
import json, random
ex = [json.loads(l) for l in open("data/route_selection.jsonl")]
random.Random(9).shuffle(ex)
for n in (20, 60, 120):
    with open(f"data/route_n{n}.jsonl", "w") as f:
        for e in ex[:n]:
            f.write(json.dumps(e) + "\n")
    print("wrote", n)
EOF
run route_n20  --data data/route_n20.jsonl  --batch-size 8 &
run route_n60  --data data/route_n60.jsonl  --batch-size 8 &
wait
run route_n120 --data data/route_n120.jsonl --batch-size 8
echo "[matrix] ALL DONE $(date +%H:%M:%S)"
