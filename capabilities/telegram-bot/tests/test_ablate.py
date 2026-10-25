"""The ablation runner refuses to ablate on a suite that already fails: every
ablation would then be reported as proven for the wrong reason."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablate  # noqa: E402


class CleanFirst(unittest.TestCase):
    def test_broken_suite_stops_before_any_ablation(self):
        calls = []

        def run(service_dir, module, cli=None):
            calls.append((service_dir, module))
            return False
        with mock.patch.object(ablate, "run", run), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ablate.main(), 1)
        self.assertEqual(calls, [(ablate.SERVICE, ".")])
        self.assertIn("nothing ablated", out.getvalue())
        self.assertNotIn("FAILS", out.getvalue())


if __name__ == "__main__":
    unittest.main()
