"""A task is a TOML file the coordinator owns: what to do, which criteria prove it, what the lane may touch."""
import fnmatch
import tomllib
from dataclasses import dataclass, field


@dataclass
class Criterion:
    id: str
    text: str


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    criteria: list[Criterion]
    scope: list[str] = field(default_factory=list)    # globs a lane may change; empty means anything
    protect: list[str] = field(default_factory=list)  # globs a lane may never change
    check: str | None = None                          # coordinator's own command, run on the merged tree
    path: str = ""

    def out_of_scope(self, files):
        bad = [f for f in files if any(fnmatch.fnmatch(f, g) for g in self.protect)]
        if self.scope:
            bad += [f for f in files if f not in bad and not any(fnmatch.fnmatch(f, g) for g in self.scope)]
        return bad


def load(path):
    with open(path, "rb") as f:
        d = tomllib.load(f)
    crit = [Criterion(str(c["id"]), c["text"]) for c in d.get("criteria", [])]
    if not crit:
        raise ValueError(f"{path}: a task needs at least one [[criteria]] entry")
    return Task(id=d["id"], title=d.get("title", d["id"]), prompt=d["prompt"].strip(), criteria=crit,
                scope=d.get("scope", []), protect=d.get("protect", []), check=d.get("check"), path=path)
