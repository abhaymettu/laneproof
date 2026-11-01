"""Write paper/results-table.tex from bench/results.json (run bench/score.py first)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(HERE, "..", "bench", "results.json")))["summary"]
NAMES = {"seq": "sequential", "shared": "parallel, shared tree", "worktree": "parallel, worktrees",
         "gated-strict": "laneproof, strict merge", "gated": "laneproof"}
out = [r"\begin{tabular}{llrrrrrrr}", r"\toprule",
       r"model & condition & runs & truly done & said done, isn't & main green & bounces (wrong) & wall (s) & tokens (M) \\",
       r"\midrule"]
last = None
for r in rows:
    key = (r["model"], r["suite"])
    if last and key != last:
        out.append(r"\midrule")
    if key != last:
        out.append(rf"\multicolumn{{9}}{{l}}{{\emph{{{r['model']}, {r['suite']} suite}}}} \\")
    last = key
    gated = r["condition"].startswith("gated")
    bounces = f"{r['caught']} ({r['caught_wrong_alone']})" if gated else "--"
    out.append(rf"& {NAMES[r['condition']]} & {r['reps']} & {r['truly_done']}/{r['tasks']} & {r['false_done']} & "
               rf"{r['suite_green']}/{r['reps']} & {bounces} & {r['wall_s_mean']} & {r['tokens_m']} \\")
out += [r"\bottomrule", r"\end{tabular}"]
open(os.path.join(HERE, "results-table.tex"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
