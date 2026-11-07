"""The prompt table's ledger owner, `tgbot`, is declared and migrated through the
contract's `migrate`, so the manager's store upgrade finds a release that moves
it (`owner_not_installed` stopped the schema 1 -> 2 upgrade, 07.11).

The capability's own name is not an owner name (a hyphen), so the manifest
names `tgbot` alone and `migrate [status]` answers for it. The database calls
are stood in for; each case checks the owner, steps and version they are given
and the answer the manager reads.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import SERVICE_DIR  # noqa: E402,F401
from test_cli import load_cli  # noqa: E402

import prompts  # noqa: E402


def run(cli, argv: list) -> tuple[int, dict]:
    out = io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["telegram-bot", *argv]), redirect_stdout(out):
        try:
            cli.main()
        except SystemExit as stop:
            code = stop.code or 0
    return code, json.loads(out.getvalue())


class FakeDb:
    """`capabilities_contract.db`'s connect, status and migrate, recording calls."""

    def __init__(self):
        self.calls = []
        self.closed = 0

    def connect(self, **kwargs):
        self.calls.append(("connect", kwargs))
        return SimpleNamespace(close=self._close)

    def _close(self):
        self.closed += 1

    def status(self, conn, owner, steps, *, major, minor):
        self.calls.append(("status", owner, tuple(steps), major, minor))
        return SimpleNamespace(owner=owner, schema="agentkit", state="current",
                               major=major, minor=minor, stored_major=1, stored_minor=0,
                               applied=[s[0] for s in steps], pending=[], changed=[],
                               warnings=[])

    def migrate(self, conn, owner, steps, *, major, minor):
        self.calls.append(("migrate", owner, tuple(steps), major, minor))
        return SimpleNamespace(owner=owner, schema="agentkit", applied=[],
                               skipped=[s[0] for s in steps], major=major, minor=minor,
                               warnings=[])


class StoreOwner(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()

    def test_the_manifest_declares_the_tgbot_tables(self):
        with mock.patch.object(self.cli, "_gate", lambda: None):
            code, manifest = run(self.cli, ["manifest"])
        self.assertEqual(code, 0)
        self.assertIs(manifest["tables"], True)
        self.assertEqual(manifest["owners"], ["tgbot"])
        self.assertIsInstance(manifest["store_major"], int)
        self.assertIn("service", manifest)

    def _migrate(self, argv):
        db = FakeDb()
        import capabilities_contract
        with mock.patch.object(self.cli, "_gate", lambda: None), \
                mock.patch.object(capabilities_contract, "db", db, create=True), \
                mock.patch.dict(sys.modules, {"capabilities_contract.db": db}):
            code, report = run(self.cli, argv)
        return db, code, report

    def test_migrate_status_answers_for_tgbot(self):
        db, code, report = self._migrate(["migrate", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(db.calls[1], ("status", "tgbot", tuple(prompts.STORE_STEPS),
                                       prompts.STORE_SCHEMA_MAJOR, prompts.STORE_SCHEMA_MINOR))
        self.assertEqual(report["owner"], "tgbot")
        self.assertEqual(report["state"], "current")
        self.assertEqual(report["code"], {"major": 1, "minor": 0})
        self.assertEqual(report["store"], {"major": 1, "minor": 0})
        self.assertEqual(report["applied"], ["0001-prompts"])
        self.assertNotIn("other_owners", report)
        self.assertEqual(db.closed, 1)

    def test_migrate_moves_the_tgbot_steps(self):
        db, code, report = self._migrate(["migrate"])
        self.assertEqual(code, 0)
        self.assertEqual(db.calls[1][:2], ("migrate", "tgbot"))
        self.assertEqual(db.calls[1][2], tuple(prompts.STORE_STEPS))
        self.assertEqual(report["owner"], "tgbot")
        self.assertEqual(report["version"], {"major": 1, "minor": 0})
        self.assertEqual(report["skipped"], ["0001-prompts"])

    def test_migrate_refuses_other_arguments(self):
        err = io.StringIO()
        with mock.patch.object(self.cli, "_gate", lambda: None), \
                mock.patch.object(sys, "argv", ["telegram-bot", "migrate", "now"]), \
                mock.patch("sys.stderr", err), self.assertRaises(SystemExit) as stop:
            self.cli.main()
        self.assertEqual(stop.exception.code, 6)


if __name__ == "__main__":
    unittest.main()
