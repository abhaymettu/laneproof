# laneproof: agents say done, the merged result decides

I run most of my work through parallel Claude Code agents. The hard part was never starting them. It was knowing
which "done" to believe. laneproof is the part of my own harness that earned its keep, rebuilt as a small tool:
admission that lets each reservation start exactly one agent, receipts where only re-runnable commands count as
evidence, and a gate that merges each lane onto the latest main and re-runs that evidence there before main moves.

I measured it against three other ways of running the same tasks: one agent at a time, four in one checkout, and four
in worktrees merged when the agent says done. 84 lanes per condition, Sonnet 5.5 and Haiku 5.5, hidden checks the
agents never saw.

- laneproof's status was right for all 84 lanes. Agents' own reports were wrong for 20 lanes in worktrees and 16 in
  a shared checkout.
- The shared checkout was the surprise: it finished 84/84 in the least time. Claude Code's stale-read check kept four
  agents in one directory from clobbering each other. Its reports were the problem, not its code.
- laneproof finished 73/84 and took about 2.7x the shared checkout's wall clock. Most of the gap is lanes that ran out
  of retries on merge conflicts.
- Because nothing lands unchecked, the gate can try a union merge on a conflict and keep it only if every check still
  passes. That cut the gated run from 810 s to about 200 s for the same 8 tasks.
- Building the benchmark found four bugs, including `claude -p` runs that stay alive for minutes after their final
  result event while a background job they started hangs.

Code, tasks, hidden checks, raw redacted logs and a short report: https://github.com/abhaymettu/laneproof
