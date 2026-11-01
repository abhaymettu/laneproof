#!/bin/sh
# Reproduce the results: 4 conditions x REPS reps, interleaved so time-of-day load hits every condition alike.
# usage: bench/all.sh [MODEL] [REPS] [PARALLEL] [SUITE]     SUITE is independent (8 tasks) or coupled (10 tasks)
set -e
MODEL=${1:-sonnet}; REPS=${2:-3}; N=${3:-4}; SUITE=${4:-independent}
cd "$(dirname "$0")/.."
for rep in $(seq 1 "$REPS"); do
  for cond in seq shared worktree gated; do
    python3 bench/run.py --suite "$SUITE" --condition "$cond" --model "$MODEL" --rep "$rep" --parallel "$N" ${BENCH_WORK:+--work "$BENCH_WORK"}
  done
done
python3 bench/score.py
