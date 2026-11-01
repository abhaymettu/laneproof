# AGENTS.md

Notes for coding agents working on this repository.

## Layout

- `src/laneproof/admission.py`: reservations, the flock-serialised registry, headroom checks. Exit codes 0/1/2/3.
- `src/laneproof/gate.py`: receipts and the evidence gate. The only code that moves the target branch.
- `src/laneproof/lanes.py`: the coordinator loop (admit, start, wait, gate, retry, release) and the lane prompt.
- `src/laneproof/agents.py`: `claude -p` adapter and the generic command-template adapter.
- `src/laneproof/demo.py`: the scripted quick start. It must keep running with no network and no model.
- `bench/`: the benchmark. `fixture/` is the repo agents work on, `tasks/` the specs, `hidden/checks.py` the ground
  truth, `reference/apply.py` the reference solutions, `run.py` and `score.py` the harness, `runs/` raw logs.

## Rules

- Standard library only. Python 3.11 or newer.
- Run `python3 -m unittest discover -s tests` before you finish; it includes the demo.
- If you change `bench/hidden/checks.py` or a task file, re-run the checker validation:
  copy `bench/fixture` somewhere, run `bench/reference/apply.py` on it, and every task must pass;
  on the bare fixture every task must fail.
- Never edit files under `bench/runs/` by hand. They are raw results.
- No em dashes in prose, code comments or commit messages.

## If you are a lane run by laneproof

Write `.laneproof-receipt.json` at your worktree root when you finish. Label every claim FACT, CLAIM or
HYPOTHESIS. Only FACTs with a `cmd` count, and every acceptance criterion needs one. The gate re-runs your
commands on your branch merged with the latest main, so prove the merged behaviour, not just your branch.
