"""The CLI's service lifecycle: `service start` launches the daemon through
`uv run --script` exactly as the real one is launched, and `service stop` stops
it, uv included; the daemon's environment carries the token under one key only.

The CLI is loaded as a module (it needs capabilities-contract, as its own
header says); its store and connection lookups are replaced, nothing else.
`TELEGRAM_BOT_CLI_FILE` points the suite at another copy of the CLI.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import CAPABILITY_DIR  # noqa: E402

CLI_FILE = Path(os.environ.get("TELEGRAM_BOT_CLI_FILE")
                or CAPABILITY_DIR / "bin" / "telegram-bot")
TOKEN = "7000000001:" + "A" * 35

# A stand-in for service/daemon.py launched the same way: a uv script, so uv
# is the process `start` spawns and the daemon is uv's child.
FAKE_DAEMON = """#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
import json, os, signal, sys, time
from pathlib import Path
d = Path(os.environ["TELEGRAM_BOT_STATE_DIR"])
(d / "env.json").write_text(json.dumps(dict(os.environ)))
(d / "uv.pid").write_text(f"{os.getppid()}\\n")
(d / "daemon.pid").write_text(f"{os.getpid()}\\n")
def term(*_):
    (d / "daemon.pid").unlink()
    sys.exit(0)
signal.signal(signal.SIGTERM, term)
(d / "health.json").write_text(json.dumps({
    "pid": os.getpid(), "launch_nonce": os.environ["TELEGRAM_BOT_LAUNCH_NONCE"],
    "state": os.environ.get("FAKE_STATE", "healthy"), "problem": "fake problem"}))
while True:
    time.sleep(0.1)
"""


def load_cli():
    loader = importlib.machinery.SourceFileLoader("telegram_bot_cli", str(CLI_FILE))
    spec = importlib.util.spec_from_loader("telegram_bot_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it has stopped all the same.
    import subprocess
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                           text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.scratch = Path(tempfile.mkdtemp(prefix="telegram-bot-cli-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.daemon = self.scratch / "fake-daemon.py"
        self.daemon.write_text(FAKE_DAEMON)
        self.daemon.chmod(0o755)
        (self.scratch / "project").mkdir()
        real_service_file = self.cli._service_file
        resolved = {"id": "alpha", "token": TOKEN, "secret_env": "ANY_NAME_WE_CHOSE",
                    "expected_bot_id": 7000000001, "allow_write": True,
                    "username": "alpha_bot", "tier": "process", "source": None}
        patches = {
            "_valid_settings": lambda: {"defaults": {}},
            "_resolved_connection": lambda wanted: dict(resolved),
            "_write_gate": lambda *a: None,
            "_project_root": lambda: self.scratch / "project",
            "_state_dir": lambda: self.scratch / "state",
            "_store_context": lambda: "# context\n",
            "_project_id": lambda **kw: None,
            "_project_capabilities_dir": lambda root: Path(root) / "capabilities",
            "_connections_registry": lambda: ({"connections": {"alpha": {}}}, None),
            "_select_connection": lambda reg, wanted: ("alpha", {}),
            "_service_file": lambda *parts: (self.daemon if parts == ("daemon.py",)
                                             else real_service_file(*parts)),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(self.cli, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"ANY_NAME_WE_CHOSE": TOKEN,
                                           "TELEGRAM_BOT_SERVICE_TOKEN": "stale"})
        env.start()
        self.addCleanup(env.stop)
        self.paths = self.cli._service_paths("alpha")

    def pids(self):
        read = lambda name: int((self.paths["dir"] / name).read_text())  # noqa: E731
        return read("daemon.pid"), read("uv.pid")

    def cleanup_group(self, pid):
        def kill():
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.addCleanup(kill)

    def test_stop_stops_what_start_started(self):
        started = self.cli.cmd_service_start(None)
        self.assertTrue(started.get("started"))
        daemon_pid, uv_pid = self.pids()
        self.cleanup_group(uv_pid)
        self.assertNotEqual(daemon_pid, uv_pid, "the daemon should run as uv's child")
        stopped = self.cli.cmd_service_stop(None, force=False, timeout=15)
        self.assertTrue(stopped["stopped"])
        deadline = time.time() + 10
        while time.time() < deadline and (alive(daemon_pid) or alive(uv_pid)):
            time.sleep(0.1)
        self.assertFalse(alive(daemon_pid), "the daemon survived service stop")
        self.assertFalse(alive(uv_pid), "uv survived service stop")
        self.assertFalse(self.cli.cmd_service_status(None)["running"])

    def test_force_stop_kills_the_group(self):
        self.cli.cmd_service_start(None)
        daemon_pid, uv_pid = self.pids()
        self.cleanup_group(uv_pid)
        os.kill(daemon_pid, signal.SIGSTOP)   # a daemon that will not stop by itself
        self.addCleanup(lambda: os.kill(daemon_pid, signal.SIGCONT)
                        if alive(daemon_pid) else None)
        stopped = self.cli.cmd_service_stop(None, force=True, timeout=1)
        self.assertTrue(stopped.get("killed"))
        deadline = time.time() + 10
        while time.time() < deadline and (alive(daemon_pid) or alive(uv_pid)):
            time.sleep(0.1)
        self.assertFalse(alive(daemon_pid))
        self.assertFalse(alive(uv_pid))

    def test_start_reports_a_parked_daemon_at_once_and_stops_it(self):
        with mock.patch.dict(os.environ, {"FAKE_STATE": "token_refused"}):
            began = time.time()
            with self.assertRaises(SystemExit) as caught:
                self.cli.cmd_service_start(None)
        self.assertEqual(caught.exception.code, 2)
        self.assertLess(time.time() - began, 60)
        uv_pid = int((self.paths["dir"] / "uv.pid").read_text())
        self.cleanup_group(uv_pid)
        daemon_pid = json.loads(self.paths["health"].read_text())["pid"]
        deadline = time.time() + 10
        while time.time() < deadline and alive(daemon_pid):
            time.sleep(0.1)
        self.assertFalse(alive(daemon_pid))

    def test_daemon_gets_the_token_under_one_key_only(self):
        _argv, env, _paths, _resolved = self.cli._launch(None)
        self.assertEqual(env["TELEGRAM_BOT_SERVICE_TOKEN"], TOKEN)
        self.assertFalse("ANY_NAME_WE_CHOSE" in env, "the connection key reached the daemon")
        self.assertEqual(env["TELEGRAM_BOT_SECRET_ENV"], "ANY_NAME_WE_CHOSE")
        self.assertEqual([k for k, v in env.items() if v == TOKEN],
                         ["TELEGRAM_BOT_SERVICE_TOKEN"])



class TokenHome(unittest.TestCase):
    """Bot tokens live outside every project."""

    def setUp(self):
        self.cli = load_cli()
        self.scratch = Path(tempfile.mkdtemp(prefix="telegram-bot-token-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.credentials = self.scratch / "config" / "telegram-bot" / "credentials.env"
        self.project = self.scratch / "project"
        self.project.mkdir()
        for name, value in {"CREDENTIALS_ENV": self.credentials,
                            "_project_root": lambda: self.project}.items():
            patcher = mock.patch.object(self.cli, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_token_set_writes_the_user_credentials_file_only(self):
        out = self.cli.cmd_token_set("alpha", TOKEN + "\n")
        self.assertEqual(out["written"], "TELEGRAM_BOT_ALPHA_TOKEN")
        self.assertNotIn(TOKEN, json.dumps(out))
        self.assertIn(f"TELEGRAM_BOT_ALPHA_TOKEN='{TOKEN}'", self.credentials.read_text())
        self.assertEqual(self.credentials.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_project_connection_with_the_token_on_stdin_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.cli._refuse_project_secret(["connection", "add", "alpha", "--project",
                                             "--stdin"])
        self.assertEqual(caught.exception.code, 6)
        self.cli._refuse_project_secret(["connection", "add", "alpha", "--project",
                                         "--env", "secret_env=TELEGRAM_BOT_ALPHA_TOKEN"])


class Clock(unittest.TestCase):
    def test_doctor_warns_on_a_skewed_clock(self):
        cli = load_cli()
        self.assertEqual(cli._clock_report(2.0)["warnings"], [])
        warned = cli._clock_report(-400.0)
        self.assertEqual(warned["clock_offset_seconds"], -400.0)
        self.assertIn("behind", warned["warnings"][0])

    def test_offset_read_from_the_date_header(self):
        import botapi
        now = 1_700_000_000.0
        header = "Tue, 14 Nov 2023 22:11:40 GMT"   # 1700000000 - 100 s
        self.assertAlmostEqual(botapi.clock_offset(header, now), 100.0)
        self.assertIsNone(botapi.clock_offset(None, now))


if __name__ == "__main__":
    unittest.main()
