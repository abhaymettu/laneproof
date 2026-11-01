# RUNLOG

## 2026-11-01 05:00 start
- Read the source harness (admission controller + 57 tests, lane launcher, attention hook, manager doctrine, lane policy).
  Kept the ideas, dropped the personal plumbing: one-time reservations with an O_EXCL start marker, observed (not
  trusted) occupancy, fail-closed headroom, FACT/CLAIM/HYPOTHESIS receipts, done only against evidence, idempotent landing.
- Landscape (4 searches): orchestrators (claude-squad, Conductor, vibe-kanban, Sculptor) isolate agents but do not
  check their claims; single-agent completion gates (agent-done-or-not, Guildless, agent-gate) check one agent's claims but
  not the merged result of many; CooperBench and "Passes Alone, Fails Together" measure that gap but ship no tool.
- Name: lanegate taken on GitHub and PyPI, landed too close to landed-agents; chose laneproof.

## 05:07 to 05:15 core built
- src/laneproof: admission.py, gate.py, lanes.py (coordinator), agents.py (claude + any CLI template), demo.py, cli.py.
- The demo caught a real bug on first run: CommandAgent wrote every lane's prompt to one shared file, so a lane did
  the wrong task and its "done" was believed. Fixed (per-lane prompt file).
- tests/test_laneproof.py: 17 tests (overbooking under 20 concurrent reserves, exactly-once claim, orphan counting,
  fail-closed registry, quota refusal, gate: land, idempotent replay, no receipt, CLAIM-only, false FACT, passes alone
  fails together, conflict, scope/protect, asking). All pass.

## 05:10 benchmark built
- bench/fixture: textkit, a small CLI package with a command registry, README table and CHANGELOG that every task touches.
- bench/tasks: 8 tasks, 6 criteria each. bench/hidden/checks.py: ground truth, never shown to agents.
- bench/reference/apply.py: reference solutions. All 8 pass every hidden check; the bare fixture fails all 8 (checker is passable and not vacuous).
- Smoke (sonnet, 1-2 tasks): seq rot done in 17s, 4 turns. worktree: second branch hit a merge conflict
  (CHANGELOG.md, cli.py). gated: same conflict rejected, lane retried with feedback, landed.
- Worktree baseline given the same 2 conflict-fix retries as gated, so the only difference left is the evidence gate.

## 05:14 full run started
- `BENCH_WORK=<scratch> bench/all.sh sonnet 3 4`: 3 reps x 4 conditions x 8 tasks, interleaved by rep.

## 05:30 to 06:05 findings from the first runs (sonnet, independent suite)
- seq r1-r3: 8/8 each, 123 to 161 s. shared r1: 8/8 in 49 s, no edit collisions: on independent tasks Sonnet copes
  with a shared directory. worktree r1: 6/8 on main, 2 correct branches lost to repeated conflicts as main moved.
- gated r1 with the strict gate (every conflict back to the lane): 7/8, 810 s, 12 bounces, all of them textual
  conflicts on work that was right on its own branch (0/12 wrong). Kept as the `gated-strict` ablation.
- Change: on a conflict the gate now tries a union merge in its scratch tree and lands it only if every FACT and the
  coordinator check pass on it. Two new tests (lands when evidence holds, rejected with the conflict when not).
  gated r2: 8/8 in 142 s.
- gated r3: 8/8 but 810 s. Cause (FACT, from the lane log): the mdtable agent ran a command that blocked, Claude Code
  moved it to a background task, and the `claude -p` process stayed alive about 10 minutes after its final result
  event. The coordinator waited for process exit. Fix: a Claude lane ends at a new `result` event and its process
  group is stopped. Live check: a haiku run told to leave `sleep 600` in the background now returns in 7 s
  (exit 143, so the process really was still alive). The bench baselines use the same rule. r3 is moved out of the
  headline to bench/runs-excluded/ and re-run with the fix.
- Scorer fix: "gate bounces" and "wrong claims caught" are separate columns. A conflict bounce is not a false claim.

## 06:05 coupled suite running
- 10 tasks: the 8 features plus docsync (README cells must equal help strings, enforced by a test) and jsonout
  (a global --json flag that must work for every command). Reference solutions pass all 10 hidden checks.

## 06:35 more bugs found by the benchmark, fixed; chain 2 launched
- Lingering after the result event is common, not rare: in coupled shared r1 five lanes sat 7 to 11 minutes after
  their final result (killed by hand; that run's wall clock, 1376 s, is not usable and the cell is re-run).
- `os.killpg` raises PermissionError on macOS when the group leader has already exited; it crashed chain 1. Fixed.
- Hidden checks and FACT replays inherited the caller's stdin, so a command that reads stdin could block until a
  timeout and be scored as a failure. Both now get empty stdin. All runs are re-scored with the fix.
- Coupled shared r1 (correctness only): 10/10 done, main green, but 3 lanes reported "blocked" for work that was in
  fact done on main (other lanes finished it). Scorer gained "said not done, is".

## NEXT (if this session ends, start here)
- Chain 2 (background): `bench/all.sh sonnet 3 4 coupled`, then sonnet independent gated reps 1-3, then
  `bench/all.sh haiku 3 4 coupled`, then `bench/score.py`. Logs in the session scratchpad (c2-*.log).
- Then: `python3 bench/redact.py`, `python3 bench/score.py`, `python3 paper/make_table.py`; fill the
  `<!-- RESULTS -->`, `<!-- BENCHMARK -->`, `<!-- LIMITS -->` markers in README.md; write paper/laneproof.tex and
  build with tectonic; site/writeup.md, site/meta.json, site/launch-post.md, paper/SUBMIT.md.
- Then: blind-judge the README (hill-climb judge mode), gitleaks, create public repo abhaymettu/laneproof, push,
  fresh clone + install + demo, paste output here.

## 08:45 shipped
- Public: https://github.com/abhaymettu/laneproof (published as one clean commit; earlier local history held
  unredacted copies of the first runs). gitleaks: no leaks.
- Final table (bench/results.md), 84 lanes per condition pooled over sonnet independent, sonnet coupled, haiku coupled:
  seq 83/84 done, 1 wrong report, 230 s; shared 84/84, 16 wrong reports, 129 s; worktree 64/84, 20 wrong reports,
  129 s; laneproof 73/84, 0 wrong reports, 347 s. Gate bounces 86, of which 1 lane was wrong on its own branch.
- Fresh-clone proof (scratch dir, 08:44): `pip install git+https://github.com/abhaymettu/laneproof` -> `laneproof
  --version` 0.1.0; `laneproof demo` exit 0, rename landed first try, docs and double each caught once then landed,
  replayed start refused; `git clone` + `python3 bench/score.py` reproduces the table from the committed logs
  (e.g. sonnet coupled gated 24/30, 0 said-done-isn't; haiku coupled shared 30/30, 6 said-not-done-is).
- Not done: blind-judge rounds on the README (context ran short; the README went through one self-edit pass only).
- Next ideas: more retries or a priority boost for lanes bounced twice by conflicts (the 11 unlanded lanes);
  a Claude Code Stop hook that refuses to end a lane without a receipt.
