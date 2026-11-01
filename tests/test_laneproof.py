import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from laneproof import admission  # noqa: E402
from laneproof.gate import RECEIPT, verify  # noqa: E402
from laneproof.git import git  # noqa: E402
from laneproof.lanes import state_dir  # noqa: E402
from laneproof.tasks import Criterion, Task  # noqa: E402

LIMITS = admission.Limits(cap=4, min_free_gb=0.0)


def make_repo():
    repo = tempfile.mkdtemp(prefix="lp-test-")
    git(repo, "init", "-q", "-b", "main")
    with open(os.path.join(repo, "a.txt"), "w") as f:
        f.write("one\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


def lane(repo, name, files, receipt):
    wt = tempfile.mkdtemp(prefix=f"lp-{name}-")
    os.rmdir(wt)
    git(repo, "worktree", "add", "-q", "-b", f"lane/{name}", wt, "main")
    for path, text in files.items():
        with open(os.path.join(wt, path), "w") as f:
            f.write(text)
    if receipt is not None:
        with open(os.path.join(wt, RECEIPT), "w") as f:
            json.dump(receipt, f)
    return wt


def done(*claims):
    return {"status": "done", "claims": list(claims)}


def fact(cmd, crit="c1", **kw):
    return {"criterion": crit, "label": "FACT", "cmd": cmd, **kw}


TASK = Task("t", "t", "do it", [Criterion("c1", "b.txt says yes")])


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.reg = admission.Registry(tempfile.mkdtemp())

    def test_concurrent_reserves_never_overbook(self):
        granted, refused = [], []

        def go(i):
            try:
                granted.append(admission.reserve(self.reg, f"h{i}", LIMITS, "/"))
            except admission.NoHeadroom:
                refused.append(i)
        threads = [threading.Thread(target=go, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((len(granted), len(refused)), (4, 16))

    def test_claim_is_exactly_once(self):
        r = admission.reserve(self.reg, "h", LIMITS, "/")
        admission.claim(self.reg, r["id"], r["token"])
        with self.assertRaises(admission.Refused):
            admission.claim(self.reg, r["id"], r["token"])

    def test_claim_needs_the_token(self):
        r = admission.reserve(self.reg, "h", LIMITS, "/")
        with self.assertRaises(admission.Refused):
            admission.claim(self.reg, r["id"], "wrong")

    def test_released_reservation_cannot_start(self):
        r = admission.reserve(self.reg, "h", LIMITS, "/")
        admission.release(self.reg, r["id"], "done")
        with self.assertRaises(admission.Refused):
            admission.claim(self.reg, r["id"], r["token"])

    def test_live_lane_without_reservation_still_counts(self):
        r = admission.reserve(self.reg, "h", admission.Limits(cap=1, min_free_gb=0), "/")
        admission.release(self.reg, r["id"], "done")
        with self.reg.locked() as reg:
            reg["lanes"]["ghost"] = {"pid": os.getpid(), "reservation": r["id"]}
            self.reg.save(reg)
        with self.assertRaises(admission.NoHeadroom):
            admission.reserve(self.reg, "h2", admission.Limits(cap=1, min_free_gb=0), "/")

    def test_unreadable_registry_refuses(self):
        with open(self.reg.path, "w") as f:
            f.write("{not json")
        with self.assertRaises(admission.Unverifiable):
            admission.reserve(self.reg, "h", LIMITS, "/")

    def test_quota_command_can_refuse(self):
        with self.assertRaises(admission.NoHeadroom):
            admission.reserve(self.reg, "h", admission.Limits(cap=4, min_free_gb=0, quota_cmd="echo out of quota; exit 1"), "/")


class GateTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.reg = admission.Registry(state_dir(self.repo))

    def test_lands_when_fact_replays_on_merge(self):
        wt = lane(self.repo, "ok", {"b.txt": "yes\n"}, done(fact("grep -q yes b.txt")))
        v = verify(self.repo, self.reg, "ok", wt, TASK)
        self.assertEqual(v.state, "landed", v.reasons)
        self.assertEqual(git(self.repo, "show", "main:b.txt"), "yes")
        self.assertNotIn(RECEIPT, git(self.repo, "ls-tree", "-r", "--name-only", "main"))

    def test_replay_is_idempotent(self):
        wt = lane(self.repo, "ok", {"b.txt": "yes\n"}, done(fact("grep -q yes b.txt")))
        first = verify(self.repo, self.reg, "ok", wt, TASK)
        main = git(self.repo, "rev-parse", "main")
        again = verify(self.repo, self.reg, "ok", wt, TASK)
        self.assertEqual((first.state, again.state), ("landed", "already"))
        self.assertEqual(git(self.repo, "rev-parse", "main"), main)

    def test_no_receipt_is_rejected(self):
        wt = lane(self.repo, "lazy", {"b.txt": "yes\n"}, None)
        self.assertEqual(verify(self.repo, self.reg, "lazy", wt, TASK).state, "rejected")

    def test_claim_without_fact_does_not_count(self):
        wt = lane(self.repo, "talk", {"b.txt": "yes\n"}, done({"criterion": "c1", "label": "CLAIM", "text": "it works"}))
        v = verify(self.repo, self.reg, "talk", wt, TASK)
        self.assertEqual(v.state, "rejected")
        self.assertIn("criterion c1", v.reasons[0])

    def test_false_fact_is_caught(self):
        wt = lane(self.repo, "liar", {"b.txt": "no\n"}, done(fact("grep -q yes b.txt")))
        v = verify(self.repo, self.reg, "liar", wt, TASK)
        self.assertEqual(v.state, "rejected")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "b.txt")))

    def test_passes_alone_fails_together(self):
        task = Task("t", "t", "x", [Criterion("c1", "c")], check="test \"$(cat a.txt)\" = one")
        other = lane(self.repo, "other", {"a.txt": "two\n"}, done(fact("true")))
        mine = lane(self.repo, "mine", {"c.txt": "x\n"}, done(fact("true")))
        subprocess.run(["sh", "-c", task.check], cwd=mine, check=True)  # passes on its own branch
        self.assertEqual(verify(self.repo, self.reg, "other", other, Task("o", "o", "x", [Criterion("c1", "c")])).state, "landed")
        v = verify(self.repo, self.reg, "mine", mine, task)
        self.assertEqual(v.state, "rejected")
        self.assertIn("coordinator check", v.reasons[0])

    def test_conflict_is_rejected_with_files(self):
        a = lane(self.repo, "a", {"a.txt": "alpha\n"}, done(fact("true")))
        b = lane(self.repo, "b", {"a.txt": "beta\n"}, done(fact("true")))
        self.assertEqual(verify(self.repo, self.reg, "a", a, TASK).state, "landed")
        v = verify(self.repo, self.reg, "b", b, TASK, union=False)
        self.assertEqual(v.state, "rejected")
        self.assertIn("a.txt", v.reasons[0])

    def test_union_merge_lands_only_when_evidence_holds(self):
        both = Task("t", "t", "x", [Criterion("c1", "c")], check="grep -q alpha a.txt && grep -q beta a.txt")
        a = lane(self.repo, "a", {"a.txt": "alpha\n"}, done(fact("true")))
        b = lane(self.repo, "b", {"a.txt": "beta\n"}, done(fact("grep -q beta a.txt")))
        self.assertEqual(verify(self.repo, self.reg, "a", a, TASK).state, "landed")
        v = verify(self.repo, self.reg, "b", b, both)
        self.assertEqual(v.state, "landed", v.reasons)
        self.assertEqual(git(self.repo, "show", "main:a.txt"), "alpha\nbeta")

    def test_union_merge_that_breaks_a_check_is_rejected(self):
        only_one = Task("t", "t", "x", [Criterion("c1", "c")], check="test $(wc -l < a.txt) -eq 1")
        a = lane(self.repo, "a", {"a.txt": "alpha\n"}, done(fact("true")))
        b = lane(self.repo, "b", {"a.txt": "beta\n"}, done(fact("true")))
        self.assertEqual(verify(self.repo, self.reg, "a", a, TASK).state, "landed")
        v = verify(self.repo, self.reg, "b", b, only_one)
        self.assertEqual(v.state, "rejected")
        self.assertIn("union merge was tried", v.reasons[0])
        self.assertEqual(git(self.repo, "show", "main:a.txt"), "alpha")

    def test_scope_and_protect(self):
        task = Task("t", "t", "x", [Criterion("c1", "c")], scope=["b.txt"], protect=["a.txt"])
        wt = lane(self.repo, "wide", {"b.txt": "yes\n", "a.txt": "changed\n"}, done(fact("true")))
        v = verify(self.repo, self.reg, "wide", wt, task)
        self.assertEqual(v.state, "rejected")
        self.assertIn("a.txt", v.reasons[0])

    def test_asking_is_not_a_rejection(self):
        wt = lane(self.repo, "q", {}, {"status": "asking", "question": "which file?"})
        v = verify(self.repo, self.reg, "q", wt, TASK)
        self.assertEqual((v.state, v.question), ("asking", "which file?"))


class DemoTest(unittest.TestCase):
    def test_demo_passes(self):
        env = {**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src")}
        p = subprocess.run([sys.executable, "-m", "laneproof", "demo"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("one reservation, one start", p.stdout)


if __name__ == "__main__":
    unittest.main()
