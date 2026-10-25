"""`bin/carry-upstream` against a scratch upstream: a commit as REF, verbatim
files carried byte for byte with upstream's own hash, the suite run under uv,
and a failing suite reported in the exit code.

`CARRY_UPSTREAM_FILE` points the suite at another copy of the script.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import CAPABILITY_DIR  # noqa: E402

SCRIPT = Path(os.environ.get("CARRY_UPSTREAM_FILE")
              or CAPABILITY_DIR.parents[1] / "bin" / "carry-upstream")
OLD = b"one\r\n"
NEW = b"two\r\nand a byte no text decoding keeps: \xff\r\n"


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


class Carry(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="carry-upstream-test-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        up = self.scratch / "upstream"
        (up / "capabilities" / "telegram" / "service").mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=up)
        target = up / "capabilities" / "telegram" / "service" / "v.txt"
        target.write_bytes(OLD)
        git("add", ".", cwd=up)
        git("commit", "-q", "-m", "one", cwd=up)
        self.old = git("rev-parse", "HEAD", cwd=up)
        target.write_bytes(NEW)
        git("commit", "-q", "-am", "two", cwd=up)
        self.new = git("rev-parse", "HEAD", cwd=up)
        git("commit", "-q", "--allow-empty", "-m", "later", cwd=up)   # main moves past REF
        root = self.scratch / "source"
        (root / "bin").mkdir(parents=True)
        shutil.copy2(SCRIPT, root / "bin" / "carry-upstream")
        self.cap = root / "capabilities" / "fake"
        (self.cap / "service").mkdir(parents=True)
        (self.cap / "tests").mkdir()
        (self.cap / "service" / "v.txt").write_bytes(OLD)
        record = {"upstream": up.as_uri(), "path": "capabilities/telegram", "commit": self.old,
                  "verbatim": {"service/v.txt": hashlib.sha256(OLD).hexdigest()},
                  "adapted": {}}
        (self.cap / "UPSTREAM.md").write_text(
            "# Upstream\n\n```json\n" + json.dumps(record, indent=2) + "\n```\n")
        self.root = root

    def carry(self, passing: bool, ref: str | None = None):
        (self.cap / "tests" / "test_one.py").write_text(
            f"def test_one():\n    assert {passing}\n")
        # A bare interpreter, as `#!/usr/bin/env python3` finds one: no pytest in it.
        bare = [shutil.which("uv"), "run", "--quiet", "--isolated", "--no-project", "python"]
        done = subprocess.run([*bare, str(self.root / "bin" / "carry-upstream"), "fake",
                               "--ref", ref or self.new, "--apply"],
                              capture_output=True, text=True, timeout=600)
        return done.returncode, json.loads(done.stdout) if done.stdout.strip() else done.stderr

    def test_commit_ref_carried_byte_for_byte_and_suite_run(self):
        code, out = self.carry(passing=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["to"], self.new)
        self.assertEqual((self.cap / "service" / "v.txt").read_bytes(), NEW)
        record = json.loads((self.cap / "UPSTREAM.md").read_text().split("```json\n")[1]
                            .split("\n```")[0])
        self.assertEqual(record["verbatim"]["service/v.txt"], hashlib.sha256(NEW).hexdigest())
        self.assertEqual(out["tests"]["ok"], True, out["tests"])

    def test_branch_ref_carries_bytes_and_runs_the_suite_without_pytest_at_hand(self):
        code, out = self.carry(passing=True, ref="main")
        self.assertEqual((self.cap / "service" / "v.txt").read_bytes(), NEW)
        self.assertEqual(out["tests"]["ok"], True, out["tests"])
        self.assertEqual(code, 0, out)

    def test_failing_suite_fails_the_carry(self):
        code, out = self.carry(passing=False)
        self.assertEqual(code, 1, out)
        self.assertFalse(out["tests"]["ok"])
        self.assertIn("tests fail", out["needs_person"])


class Review(unittest.TestCase):
    """S6 of the e66fd83 gate: adapted changes stay in view until reviewed, a
    module a verbatim file loads by path is noticed, and an origin feeding
    several adapted files is shown once."""

    VERBATIM = 'HERE = 1\nspec = HERE / "jobs.py"\n'

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="carry-upstream-review-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        up = self.scratch / "upstream"
        service = up / "capabilities" / "telegram" / "service"
        service.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=up)
        (service / "profiles.py").write_text(self.VERBATIM)
        (service / "jobs.py").write_text("x = 1\n")
        (service / "daemon.py").write_text("one\n")
        git("add", ".", cwd=up)
        git("commit", "-q", "-m", "one", cwd=up)
        self.old = git("rev-parse", "HEAD", cwd=up)
        (service / "daemon.py").write_text("one\ntwo\n")
        git("commit", "-q", "-am", "two", cwd=up)
        self.new = git("rev-parse", "HEAD", cwd=up)
        root = self.scratch / "source"
        (root / "bin").mkdir(parents=True)
        shutil.copy2(SCRIPT, root / "bin" / "carry-upstream")
        self.cap = root / "capabilities" / "fake"
        (self.cap / "service").mkdir(parents=True)
        (self.cap / "tests").mkdir()
        (self.cap / "tests" / "test_one.py").write_text("def test_one():\n    assert True\n")
        (self.cap / "service" / "profiles.py").write_text(self.VERBATIM)
        (self.cap / "service" / "gate.py").write_text("ours\n")
        (self.cap / "service" / "guard.py").write_text("ours\n")
        record = {"upstream": up.as_uri(), "path": "capabilities/telegram", "commit": self.old,
                  "verbatim": {"service/profiles.py":
                               hashlib.sha256(self.VERBATIM.encode()).hexdigest()},
                  "adapted": {"service/gate.py": ["service/daemon.py"],
                              "service/guard.py": ["service/daemon.py"]}}
        (self.cap / "UPSTREAM.md").write_text(
            "# Upstream\n\n```json\n" + json.dumps(record, indent=2) + "\n```\n")
        self.root = root

    def carry(self, *flags):
        bare = [shutil.which("uv"), "run", "--quiet", "--isolated", "--no-project", "python"]
        done = subprocess.run([*bare, str(self.root / "bin" / "carry-upstream"), "fake",
                               "--ref", self.new, *flags],
                              capture_output=True, text=True, timeout=600)
        return done.returncode, json.loads(done.stdout), done.stderr

    def record(self):
        return json.loads((self.cap / "UPSTREAM.md").read_text().split("```json\n")[1]
                          .split("\n```")[0])

    def test_a_module_loaded_by_path_and_not_carried_is_named(self):
        code, out, _err = self.carry()
        self.assertEqual(out["missing_modules"], ["service/jobs.py"])
        self.assertEqual(code, 1)
        self.assertIn("service/jobs.py", out["needs_person"])
        self.assertIn("not applied", out["needs_person"])   # a dry run carried nothing
        self.assertNotIn("carried", out["needs_person"])

    def test_adapted_changes_keep_the_commit_until_reviewed(self):
        (self.cap / "service" / "jobs.py").write_text("x = 1\n")
        code, out, _err = self.carry("--apply")
        self.assertEqual(code, 0, out)
        self.assertFalse(out["commit_moved"])
        held = self.record()
        self.assertEqual(held["commit"], self.old)
        self.assertEqual(held["pending"], {"commit": self.new,
                                           "adapted": ["service/gate.py", "service/guard.py"]})
        self.assertIn("the recorded commit kept", out["needs_person"])
        self.assertNotIn("carried", out["needs_person"])
        _code, again, _err = self.carry()
        self.assertEqual(sorted(again["adapted_to_review"]),
                         ["service/gate.py", "service/guard.py"])
        code, out, _err = self.carry("--apply", "--reviewed")
        self.assertTrue(out["commit_moved"])
        self.assertEqual(self.record()["commit"], self.new)
        self.assertNotIn("pending", self.record())

    def test_an_origin_diff_is_printed_once(self):
        _code, _out, err = self.carry()
        self.assertEqual(err.count("diff --git a/capabilities/telegram/service/daemon.py"), 1)
        self.assertIn("adapted into: service/gate.py, service/guard.py", err)


if __name__ == "__main__":
    unittest.main()
