"""Agent adapters. An agent is a CLI that takes a prompt, works in a directory and exits.

claude:   Claude Code headless (`claude -p`, stream-json), resumable, reports turns and tokens.
command:  any CLI, as a template: `codex exec --full-auto {prompt}` or `aider --message-file {prompt_file}`.
"""
import json
import os
import shlex
import signal
import subprocess
import time


def stop(proc, grace=10):
    """End an agent's whole process group: SIGTERM, then SIGKILL after a grace period."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):  # gone, or only a zombie leader is left (macOS: EPERM)
            proc.poll()
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def _type(line):
    try:
        return json.loads(line).get("type")
    except (json.JSONDecodeError, AttributeError):
        return None


class Agent:
    resumable = False

    def launch(self, workdir, prompt, log_path, resume=None):
        raise NotImplementedError

    def turns_finished(self, log_path):
        """How many runs have reported their end in the log; None if the agent has no such signal."""
        return None

    def wait(self, proc, log_path, timeout=1800, poll=1.0):
        """Wait for the run to end. A Claude run is over at its `result` event even if a background job it
        started keeps the process alive, so the process group is stopped then."""
        before, t0 = self.turns_finished(log_path), time.time()
        while proc.poll() is None:
            if time.time() - t0 > timeout or (before is not None and self.turns_finished(log_path) > before):
                stop(proc)
                break
            time.sleep(poll)

    def usage(self, log_path):
        return {}


class ClaudeAgent(Agent):
    resumable = True

    def __init__(self, model=None, extra_args=(), binary="claude"):
        self.model, self.extra, self.binary = model, list(extra_args), binary

    def launch(self, workdir, prompt, log_path, resume=None):
        cmd = [self.binary, "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions", *self.extra]
        if self.model:
            cmd += ["--model", self.model]
        if resume:
            cmd += ["--resume", resume]
        log = open(log_path, "a")
        return subprocess.Popen(cmd, cwd=workdir, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)

    def turns_finished(self, log_path):
        try:
            with open(log_path) as f:
                return sum(1 for line in f if '"result"' in line and _type(line) == "result")
        except OSError:
            return 0

    def usage(self, log_path):
        """Sum every `result` event in the log: one per run, so retries add up."""
        tot = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
               "cache_write_tokens": 0, "runs": 0, "session_id": None, "final": ""}
        try:
            lines = open(log_path).read().splitlines()
        except OSError:
            return tot
        for line in lines:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "system" and ev.get("session_id"):
                tot["session_id"] = ev["session_id"]
            if ev.get("type") != "result":
                continue
            u = ev.get("usage") or {}
            tot["runs"] += 1
            tot["turns"] += ev.get("num_turns") or 0
            tot["input_tokens"] += u.get("input_tokens", 0)
            tot["output_tokens"] += u.get("output_tokens", 0)
            tot["cache_read_tokens"] += u.get("cache_read_input_tokens", 0)
            tot["cache_write_tokens"] += u.get("cache_creation_input_tokens", 0)
            tot["session_id"] = ev.get("session_id") or tot["session_id"]
            tot["final"] = ev.get("result") or ""
        return tot


class CommandAgent(Agent):
    def __init__(self, template):
        self.template = template

    def launch(self, workdir, prompt, log_path, resume=None):
        prompt_file = os.path.splitext(log_path)[0] + ".prompt.txt"
        with open(prompt_file, "w") as f:
            f.write(prompt)
        cmd = self.template.format(prompt=shlex.quote(prompt), prompt_file=shlex.quote(prompt_file),
                                   workdir=shlex.quote(workdir))
        log = open(log_path, "a")
        return subprocess.Popen(cmd, shell=True, cwd=workdir, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)


def from_spec(spec, model=None, extra_args=()):
    if spec == "claude":
        return ClaudeAgent(model=model, extra_args=extra_args)
    return CommandAgent(spec)
