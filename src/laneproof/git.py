import subprocess


class GitError(Exception):
    pass


def git(cwd, *args, check=True):
    p = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def commit_all(cwd, message):
    """Commit whatever the agent left in its tree; returns the new HEAD (unchanged when nothing was left)."""
    git(cwd, "add", "-A")
    if git(cwd, "status", "--porcelain"):
        git(cwd, "-c", "user.name=laneproof", "-c", "user.email=laneproof@localhost",
            "commit", "-q", "--no-verify", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


def changed_files(cwd, base, head):
    out = git(cwd, "diff", "--name-only", f"{base}...{head}")
    return [f for f in out.splitlines() if f]
