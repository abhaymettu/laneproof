"""Render the real output of `laneproof demo` as an SVG terminal card: python3 docs/render_demo.py > docs/demo.svg"""
import html
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
out = subprocess.run([sys.executable, "-m", "laneproof", "demo"], capture_output=True, text=True,
                     env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")}, cwd=ROOT).stdout
lines = ["$ laneproof demo"]
for line in out.splitlines():
    if line.startswith("main now:"):
        break
    line = re.sub(r"demo repo: .*", "demo repo: a throwaway git repo", line)
    line = re.sub(r" \(pid \d+\)", "", line)
    lines.append(line[:104] + ("…" if len(line) > 104 else ""))
while lines and not lines[-1].strip():
    lines.pop()

COLORS = [(r"^\$", "prompt"), (r"^\s+landed", "ok"), (r"^\s+reject", "bad"), (r"^\s+refused", "bad"),
          (r"^\s+retry", "warn"), (r"^\s+queued", "warn")]
LH, W = 19, 940
H = 56 + LH * len(lines)
rows = []
for i, line in enumerate(lines):
    cls = next((c for pat, c in COLORS if re.search(pat, line)), "txt")
    rows.append(f'<text x="24" y="{62 + i * LH}" class="{cls}" xml:space="preserve">{html.escape(line)}</text>')
print(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="Output of laneproof demo: three lanes, two false done claims caught, all landed">
<style>
  .card {{ fill: #0d1117; }} .bar {{ fill: #161b22; }}
  text {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }}
  .txt {{ fill: #c9d1d9; }} .prompt {{ fill: #8b949e; }} .ok {{ fill: #3fb950; }} .bad {{ fill: #f85149; }} .warn {{ fill: #d29922; }}
</style>
<rect class="card" width="{W}" height="{H}" rx="12"/>
<rect class="bar" width="{W}" height="32" rx="12"/><rect class="bar" y="20" width="{W}" height="12"/>
<circle cx="22" cy="16" r="6" fill="#f85149"/><circle cx="42" cy="16" r="6" fill="#d29922"/><circle cx="62" cy="16" r="6" fill="#3fb950"/>
{chr(10).join(rows)}
</svg>''')
