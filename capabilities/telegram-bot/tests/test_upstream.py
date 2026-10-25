"""What UPSTREAM.md records about the upstream carry still holds: every verbatim
file is byte for byte what the record says, and the harness runner is pinned
the same in the daemon as in the carried profile resolver."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

CAPABILITY_DIR = Path(__file__).resolve().parents[1]


def record() -> dict:
    text = (CAPABILITY_DIR / "UPSTREAM.md").read_text()
    return json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))


class Upstream(unittest.TestCase):
    def test_verbatim_files_match_their_record(self):
        for rel, digest in record()["verbatim"].items():
            with self.subTest(rel):
                got = hashlib.sha256((CAPABILITY_DIR / rel).read_bytes()).hexdigest()
                self.assertEqual(got, digest, f"{rel} differs from the upstream it carries")

    def test_adapted_files_exist(self):
        for rel in record()["adapted"]:
            self.assertTrue((CAPABILITY_DIR / rel).is_file(), rel)

    def test_modules_loaded_by_path_are_carried(self):
        """A carried file that loads a sibling by path (`HERE / "jobs.py"`)
        finds it in this bundle: a missing one fails only when that path runs."""
        for rel in [*record()["verbatim"], *record()["adapted"]]:
            if not rel.endswith(".py"):
                continue
            for name in re.findall(r'HERE\s*/\s*"([\w.-]+\.py)"',
                                   (CAPABILITY_DIR / rel).read_text()):
                with self.subTest(rel=rel, loads=name):
                    self.assertTrue((CAPABILITY_DIR / rel).parent.joinpath(name).is_file())

    def test_runner_pinned_alike(self):
        pin = re.search(r'^RUNNER_PIN = "([^"]+)"$',
                        (CAPABILITY_DIR / "service" / "profiles.py").read_text(), re.M).group(1)
        self.assertIn(f'"{pin}"', (CAPABILITY_DIR / "service" / "daemon.py").read_text())

    def test_licence_notice_kept(self):
        text = (CAPABILITY_DIR / "LICENSE-UPSTREAM").read_text()
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 Cluster One", text)


if __name__ == "__main__":
    unittest.main()
