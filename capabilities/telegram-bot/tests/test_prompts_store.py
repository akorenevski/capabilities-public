"""The context prompt lives in the project's store, so a node that runs the
service from its own copy of the project reads what the store master saved;
and a connection whose token lives only on that node is added from the master.

The store cases read TELEGRAM_BOT_TEST_DSN, a database URL, and skip without
one; each case binds a scratch schema of its own and drops it afterwards. The
CLI is loaded twice, once per machine - the master that writes, and a node
whose project folder holds no file of the bot's - with only its project root
and id given; its records, its store setting and the store are real.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import CAPABILITY_DIR, SERVICE_DIR  # noqa: E402

CLI_FILE = Path(os.environ.get("TELEGRAM_BOT_CLI_FILE")
                or CAPABILITY_DIR / "bin" / "telegram-bot")
STORE_DSN = os.environ.get("TELEGRAM_BOT_TEST_DSN")
# A store that is configured and does not answer: nothing listens on port 1.
DEAD_DSN = "postgresql://x@127.0.0.1:1/none?connect_timeout=2"
TEMPLATE = (SERVICE_DIR / "templates" / "context.md").read_text()

try:
    from capabilities_contract.db import KEYS as DB_KEYS
except ImportError:   # the suite's runner has it; a bare python skips the store cases
    DB_KEYS = ()


def isolated_environ(drop=()) -> dict:
    """The environment without the session's project, store or token keys."""
    return {k: v for k, v in os.environ.items()
            if k not in DB_KEYS and k not in drop and k != "CLAUDE_PROJECT_DIR"
            and not k.startswith("CAPABILITIES_")}


def load_cli(tag: str):
    name = f"telegram_bot_cli_{tag}_{uuid.uuid4().hex[:6]}"
    loader = importlib.machinery.SourceFileLoader(name, str(CLI_FILE))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Machine:
    """One machine's copy of the project: its own folder, the same project id."""

    def __init__(self, case, tag: str, project_id: str, scratch: Path | None = None):
        self.root = ((scratch or case.scratch) / tag).resolve()
        (self.root / "capabilities").mkdir(parents=True)
        (self.root / ".env.local").write_text(f"CAPABILITIES_PROJECT_ID={project_id}\n")
        self.cli = load_cli(tag)

    def call(self, fn, *args):
        """`fn(*args)`, or the error the CLI would print and its exit code."""
        err = io.StringIO()
        try:
            with redirect_stderr(err), self.here():
                # Never the session's own project: the CLI writes records there.
                assert self.cli._project_root() == self.root, self.cli._project_root()
                return getattr(self.cli, fn)(*args)
        except SystemExit as stop:
            line = err.getvalue().strip().splitlines()[-1]
            return {"exit": stop.code, **json.loads(line)}

    def here(self):
        """This machine's project as the one the CLI stands in."""
        return mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)})

    def service_file(self) -> Path:
        return self.root / "capabilities" / "telegram-bot" / "service" / "context.md"


@unittest.skipUnless(STORE_DSN and DB_KEYS, "TELEGRAM_BOT_TEST_DSN is unset")
class ContextInTheStore(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="telegram-bot-prompts-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.schema = f"tgbtest_{uuid.uuid4().hex[:12]}"
        env = isolated_environ()
        env.update({"AGENTKIT_DB_URL": STORE_DSN, "AGENTKIT_DB_SCHEMA": self.schema,
                    "XDG_CONFIG_HOME": str(self.scratch / "config"),
                    "XDG_STATE_HOME": str(self.scratch / "state")})
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._drop_schema)
        self.project_id = f"prj_test_{uuid.uuid4().hex}"
        self.master = Machine(self, "master", self.project_id)
        self.node = Machine(self, "node", self.project_id)

    def _drop_schema(self):
        import psycopg
        with psycopg.connect(STORE_DSN, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def test_init_on_the_master_is_read_on_a_node(self):
        out = self.master.call("cmd_service_init", None, False)
        self.assertTrue(any(w.startswith("store:telegrambot_prompts/context@")
                            for w in out["written"]), out)
        self.assertFalse(self.master.service_file().exists(),
                         "the context went into the master's project folder")
        self.assertEqual(self.node.call("_context_body"), TEMPLATE)
        self.assertEqual(self.node.call("cmd_service_context_document"), {"body": TEMPLATE})
        again = self.master.call("cmd_service_init", None, False)
        self.assertTrue(again["skipped"][-1].startswith("store:telegrambot_prompts/context@"))

    def test_a_new_version_reaches_the_node_at_its_next_turn(self):
        self.master.call("cmd_service_init", None, False)
        source = self.scratch / "edited.md"
        source.write_text("# edited\n")
        put = self.master.call("cmd_prompts", "put", ["context", str(source)], None, None,
                               "tone", None)
        self.assertEqual(put["outcome"], "saved")
        self.assertEqual(self.node.call("_context_body"), "# edited\n")
        versions = self.node.call("cmd_prompts", "versions", ["context"], None, None, None,
                                  None)["versions"]
        self.assertEqual(len(versions), 2)
        first = self.node.call("cmd_prompts", "show", ["context"], versions[-1]["id"], None,
                               None, None)
        self.assertEqual((first["body"], first["active"]), (TEMPLATE, False))
        stale = self.master.call("cmd_prompts", "put", ["context", str(source)], None,
                                 versions[-1]["id"], None, None)
        self.assertEqual((stale["exit"], stale["error"]["code"]), (6, "conflict"))

    def test_a_kept_file_is_what_init_seeds_and_export_writes_it_back(self):
        kept = self.master.service_file()
        kept.parent.mkdir(parents=True)
        kept.write_text("# ours from before\n")
        self.master.call("cmd_service_init", None, False)
        self.assertEqual(self.node.call("_context_body"), "# ours from before\n")
        out = self.node.call("cmd_prompts", "export", [], None, None, None, None)
        self.assertEqual(out["prompts"][0]["outcome"], "written")
        self.assertEqual(self.node.service_file().read_text(), "# ours from before\n")

    def test_the_store_wins_over_a_file(self):
        self.master.call("cmd_service_init", None, False)
        stale = self.node.service_file()
        stale.parent.mkdir(parents=True)
        stale.write_text("# an old export\n")
        self.assertEqual(self.node.call("_context_body"), TEMPLATE)

    def test_a_node_without_a_store_version_has_no_context(self):
        self.assertEqual(self.node.call("_context_body"), "")

    def test_a_store_that_does_not_answer_is_a_failure_not_the_file(self):
        self.master.call("cmd_service_init", None, False)
        stale = self.node.service_file()
        stale.parent.mkdir(parents=True)
        stale.write_text("# an old export\n")
        with mock.patch.dict(os.environ, {"AGENTKIT_DB_URL": DEAD_DSN}):
            out = self.node.call("_context_body")
        self.assertEqual((out["exit"], out["error"]["code"]), (5, "store_unreachable"), out)

    def test_a_daemon_keeps_its_copy_while_the_store_does_not_answer(self):
        own = self.scratch / "own" / "context.md"
        with mock.patch.dict(os.environ, {"AGENTKIT_DB_URL": DEAD_DSN}):
            out = self.node.call("_context_or_own_copy", own)
            self.assertEqual(out["error"]["code"], "store_unreachable", out)
            own.parent.mkdir()
            own.write_text("# the daemon's copy\n")
            err = io.StringIO()
            with redirect_stderr(err), self.node.here():
                self.assertEqual(self.node.cli._context_or_own_copy(own), "# the daemon's copy\n")
        self.assertIn("keeps its copy", err.getvalue())
        self.assertIn("store_unreachable", err.getvalue())

    def test_the_store_is_migrated_once_per_process(self):
        import capabilities_contract.db as db
        self.master.call("cmd_service_init", None, False)
        with mock.patch.object(db, "migrate", wraps=db.migrate) as migrate:
            for _ in range(3):
                self.node.call("_context_body")
        self.assertLessEqual(migrate.call_count, 1)


class ContextWithoutAStore(unittest.TestCase):
    """A project that keeps no store at all reads its context file."""

    def test_the_file_is_read_where_no_store_is_configured(self):
        scratch = Path(tempfile.mkdtemp(prefix="telegram-bot-nostore-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        env = isolated_environ()
        env.update({"XDG_CONFIG_HOME": str(scratch / "config"),
                    "XDG_STATE_HOME": str(scratch / "state")})
        with mock.patch.dict(os.environ, env, clear=True):
            machine = Machine(self, "solo", "prj_test_nostore", scratch)
            kept = machine.service_file()
            kept.parent.mkdir(parents=True)
            kept.write_text("# kept as a file\n")
            self.assertEqual(machine.call("_context_body"), "# kept as a file\n")


class RemoteConnection(unittest.TestCase):
    """`connection add --remote` records an env key this machine does not set."""

    ARGS = ["connection", "add", "alpha", "--project", "--env",
            "secret_env=TELEGRAM_BOT_ALPHA_TOKEN", "--field", "username=alpha_bot",
            "--field", "expected_bot_id=7000000001"]

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="telegram-bot-remote-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        env = isolated_environ(drop=("TELEGRAM_BOT_ALPHA_TOKEN",))
        env.update({"XDG_CONFIG_HOME": str(self.scratch / "config"),
                    "XDG_STATE_HOME": str(self.scratch / "state")})
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.machine = Machine(self, "master", "prj_test_remote")
        self.machine.cli._gate = lambda: None

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["telegram-bot", *argv]), \
                redirect_stdout(out), redirect_stderr(err), self.machine.here():
            assert self.machine.cli._project_root() == self.machine.root
            try:
                self.machine.cli.main()
                code = 0
            except SystemExit as stop:
                code = stop.code or 0
        if out.getvalue().strip():
            return code, json.loads(out.getvalue())
        text = err.getvalue().strip().splitlines()
        return code, json.loads(text[-1]) if text else None

    def test_without_remote_the_unset_key_is_refused(self):
        code, out = self.run_cli(self.ARGS)
        self.assertEqual((code, out["error"]["code"]), (6, "env_key_unset"))

    def test_remote_records_the_key_unchecked(self):
        code, out = self.run_cli([*self.ARGS, "--remote"])
        self.assertEqual(code, 0, out)
        row = next(r for r in out["fields"] if r["key"] == "secret_env")
        self.assertEqual((row["env"], row["tier"], row["value"]),
                         ("TELEGRAM_BOT_ALPHA_TOKEN", "remote", None))
        self.assertIn("on the node", out["next"])
        with self.machine.here():
            reg, _ = self.machine.cli._connections_registry()
        self.assertEqual(reg["connections"]["alpha"]["secret_env"], "TELEGRAM_BOT_ALPHA_TOKEN")

    def test_remote_needs_an_env_key(self):
        code, out = self.run_cli(["connection", "add", "alpha", "--remote"])
        self.assertEqual((code, out["error"]["code"]), (6, "input"))


if __name__ == "__main__":
    unittest.main()
