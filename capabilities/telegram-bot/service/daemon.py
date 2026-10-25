#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "capabilities-contract>=0.18,<1",
#     "callva-harness-runner==0.10.1",
# ]
# ///
# callva-harness-runner launches every worker turn and is pinned equal to
# service/profiles.py, which resolves the profile a turn runs on (the pin is
# upstream telegram's; tests/test_upstream.py holds the two equal).
"""The telegram-bot service: one Telegram bot answering for the project it runs in.

Started by `telegram-bot service start|run`, which resolves the connection and
hands this process its inputs in the environment - the token under
TELEGRAM_BOT_SERVICE_TOKEN, the project root and id, the bot's expected id and
username, where its state and context files are, and the CLI that reads its
settings and its context document. The CLI leaves the connection's own token
key (`secret_env`) out of that environment, and a worker is started without the
token, without any TELEGRAM_BOT_* key and without the key `secret_env` names
(`worker_env`). That scrub guards against accidental leaks only: a worker runs
as the same user, so it could read this process's start-up environment or the
user's credentials.env if it set out to. The token's home is kept outside every
project so that at least the project's own files never hold it.

What a worker may use is the sender's authority (`authority.py`, upstream's
`_authority_policy_for`): written per turn to a private file and handed over as
CAPABILITIES_AUTH_CONTEXT, which every managed capability's contract gate reads
before it resolves a credential. The worker's PATH starts with `worker-bin/`,
whose `telegram-bot` keeps a `current_chat` grant to the current chat and
topic and carries the `progress` verb, relayed into the chat throttled (at
most `PROGRESS_LIMIT` lines a turn) while the "typing" action is kept up for
the whole turn.

That gate holds only against a turn that does not try to get round it: a turn
with a shell can drop the variable, read files or reach the network itself. So
a turn whose authority is `restricted` (every sender but a person who is a
supervisor, unless a policy says otherwise) runs on a restricted claude profile,
whatever harness the profile in force uses - the room's `restricted_profile`,
else `telegram-claude-restricted` (Read, Grep and Glob, no shell, no web, no
writes) - held to an allowlist of knobs (`restricted_fit`), and never falls
back to the profile in force when that one cannot be resolved. Codex carries no
restricted turn: its read-only sandbox reads the whole disk. Each restricted
turn is also handed deny rules (`restricted_denies`) for the user's config,
keys and state, this bot's state folder and every folder beside the project and
beside each folder above it, laid over the profile's settings: a deny rule
outranks any allow rule or extra directory the project's own Claude settings
add. A settings document naming a profile that does not resolve, or a
restricted profile that does not fit, parks the process at start
(`profile_refused`). `/set profile` is a supervisor's alone and changes the
profile of unrestricted turns only.

A turn waits out its conversation's `debounce` (upstream's, default
`DEFAULT_DEBOUNCE` seconds) after the last addressed message there, so a request
sent in two messages ("@bot look:", then the details) is read whole.

The loop long-polls `getUpdates`. Telegram holds every update until a later
call confirms it, which is the resume point; a request confirmed but not yet
answered is kept in the journal (`inflight.json`), so a process killed without
a stop (SIGKILL, OOM) takes it up again on the next start - answered if it is
still fresh, told the bot was offline if not. A message older than
`BACKLOG_SECONDS` when it arrives (a backlog after downtime or sleep), measured
against Telegram's clock rather than this machine's, joins the tail but is not
answered; its conversation is told once that the bot was offline. Each message
goes through the gate (`gate.py`); one from an allowed sender in a served place
lands in its conversation's tail (`prompt.py`) unless it is a service message
(a pin, a join) with no text, and an addressed one passes the loop guard
(`guard.py`) before a worker turn runs on a harness profile (`profiles.py`,
`workers.py`, carried from upstream telegram) in the project directory. The
answer is posted in the same chat and topic: as a reply to a person, top-level
to a bot. An addressed `/command` is a control command the daemon answers
itself (`/status`, `/profiles`, `/stop [N]`, `/reload`, `/set`, `/help`), gated
by role as upstream gates them; it is never a worker turn.

One turn runs per conversation at a time; each addressed message arriving while
one runs waits its own turn, in order, as upstream gives every addressed
message its own job. `defaults.max_parallel` bounds turns across conversations.
The rate limit counts the requests running and waiting as well as the answers
sent, and a conversation holds at most `QUEUE_LIMIT` of them (`QUEUE_LIMIT_TOTAL`
across the bot). The journal is written under the service lock to a temporary
file of its own, owner-only; a request is run even when the journal cannot be
written, and leaves the journal as soon as its answer is out.

SIGHUP re-reads the settings and adopts them only when they load, pass the
check, every profile they name resolves, and `connection` is unchanged (that
one needs a restart); the health record names the fingerprint in force and
counts attempts, which `service reload` waits on. SIGTERM stops polling,
cancels running turns and exits. A request whose turn never finished is
answered at a stop with `STOPPED_NOTICE`; a turn that crashes or fails answers
with a failure notice, its detail for a supervisor only.

The token refused (401, at start or while running), a token of another bot,
another poller of the same token (409, after `CONFLICT_LIMIT` attempts with a
growing pause), and this bot's lock held by a daemon of another project park
the process: it stays up without polling, its health record names the state
and the problem, and `service status|doctor` show it. Exiting instead would
make a supervisor restart it in a loop. SIGHUP (`service reload`) unparks it
to try again. The one exit by design is a vanished runtime: when uv's cache
prune deletes the interpreter or the harness launcher under the running
process, it exits so the supervisor rebuilds the environment (upstream's
`_vanished_runtime`).

Started by `service start`, the process writes its own log and rotates it by
size (`LOG_MAX_BYTES`, `LOG_BACKUPS`); under a supervisor (`service run`) the
log is stdout, and the supervisor's to keep.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import authority  # noqa: E402
import botapi  # noqa: E402
import gate  # noqa: E402
import markup  # noqa: E402
import guard as loopguard  # noqa: E402
import prompt as prompting  # noqa: E402
import settings_check  # noqa: E402

STUB = "stub"
PROFILE_ENV = "TELEGRAM_BOT_SERVICE_PROFILE"
TOKEN_ENV = "TELEGRAM_BOT_SERVICE_TOKEN"
WORKER_BIN = HERE / "worker-bin"
FAILURE_NOTICE = "The turn did not complete ({kind}). Nothing was sent beyond this note."
GENERIC_FAILURE_NOTICE = ("Something went wrong while processing this. "
                          "Please tell an administrator.")
STOPPED_BY_NOTICE = "This request was stopped by {name} before it was answered."
PARTIAL_NOTICE = ("The answer could not be sent in full ({sent} of {total} parts arrived). "
                  "Ask again if the rest is needed.")
STOPPED_NOTICE = ("The bot stopped before answering this. Ask again once it is back; "
                  "it will not pick this up by itself.")
RATE_NOTICE = ("Too many requests here in the last minute; this one was not answered. "
               "Ask again in a minute.")
OFFLINE_NOTICE = ("I was offline and did not see this in time. Ask again if it still "
                  "needs an answer.")
CONTROL_HELP = "commands: /status, /profiles, /reload, /stop [N], /set help"
BACKLOG_SECONDS = 300
CONFLICT_LIMIT = 5
PARKED_STATES = ("token_refused", "bot_mismatch", "conflict", "bot_locked",
                 "profile_refused")
PARKED_SLEEP = 1.0
WORKER_ENV_PREFIX = "TELEGRAM_BOT_"
SECRET_ENV_NAME = "TELEGRAM_BOT_SECRET_ENV"
TYPING_INTERVAL = 4.5        # Telegram shows "typing" for about five seconds
PROGRESS_POLL = 0.5
REFUSED_PLACES_LIMIT = 1000
QUEUE_LIMIT = 20             # requests running and waiting in one conversation
QUEUE_LIMIT_TOTAL = 200      # across the bot
PROGRESS_LIMIT = 3           # progress lines relayed in one turn, beside its answer
SENT_KEEP = 100              # sent messages kept in memory, for the suite
PRIVATE_DIR_MODE = 0o700
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3
STALE_AFTER_EXTRA = 60       # a poll may take poll_timeout; health is stale past this more
NOTICED_LIMIT = 1000         # conversations remembered for the once-a-minute notices
SETTLE_POLL = 0.5


def iso_utc(moment=None) -> str:
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Taken from upstream telegram service/daemon.py, unchanged ---------------
def now_display():
    """Human time for the state block: UTC always, plus Tallinn local best-effort."""
    utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        loc = utc.astimezone(ZoneInfo("Europe/Tallinn"))
        return f"{utc.isoformat(timespec='seconds')} (UTC) / {loc.strftime('%H:%M')} Tallinn"
    except Exception:
        return f"{utc.isoformat(timespec='seconds')} (UTC)"


def _vanished_runtime():
    """The first file worker turns start from that is no longer on disk, or None.

    The interpreter and the harness launcher live in uv's environment cache. A cache
    prune deletes them under the running daemon, which keeps going on what it already
    loaded while every turn it launches dies on the missing path; only a restart
    rebuilds the environment."""
    paths = [Path(sys.executable)]
    with contextlib.suppress(Exception):
        from callva.harness_runner import launch
        paths.append(launch.LAUNCHER)
    return next((str(path) for path in paths if not path.exists()), None)


class ProgressThrottle:
    """How long the chat waits between one progress line and the next.

    The first line of a turn is never held. Its whole point is that it arrives
    immediately — the way a person says "one moment" before going to look — and
    the worker is asked for it before its first read. `progress_after` governs
    the gap after that: the timestamp is stamped on each send, so a burst inside
    one window relays at most one line beyond the first, and a turn that keeps
    working stays quiet between lines.

    A window of 0 admits every line the moment it arrives, and that falls out
    of the arithmetic rather than out of a branch.
    """

    def __init__(self, after, now=None):
        try:
            self.after = max(0.0, float(after))
        except (TypeError, ValueError):
            self.after = 0.0
        self.since = time.monotonic() if now is None else float(now)
        self.relayed = False

    def elapsed(self, now=None):
        return (time.monotonic() if now is None else float(now)) - self.since

    def allows(self, now=None):
        return not self.relayed or self.elapsed(now) >= self.after

    def sent(self, now=None):
        self.since = time.monotonic() if now is None else float(now)
        self.relayed = True
# --- End of upstream text ----------------------------------------------------


class RuntimeVanished(Exception):
    """A file worker turns start from was deleted under the running process."""


def fingerprint(document) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()[:16]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _remember(store: dict, key, value) -> None:
    """`store[key] = value`, the oldest keys dropped past `NOTICED_LIMIT`, so a
    bot open to anyone does not grow a map per stranger without end."""
    store.pop(key, None)
    store[key] = value
    while len(store) > NOTICED_LIMIT:
        store.pop(next(iter(store)))


def write_private(path: Path, text: str) -> None:
    """`text` into `path` owner-only, through a temporary file of its own in
    the same directory, so two writers never share one and a reader never sees
    half a file."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# --- Workers -----------------------------------------------------------------

def worker_env(environ) -> dict:
    """The environment a worker starts from: upstream's scrub
    (`workers.scrub_worker_env`), then every TELEGRAM_BOT_* key - the service
    token and the connection's default token key among them - and the key the
    connection's `secret_env` names, whatever it is called."""
    import workers
    named = str(environ.get(SECRET_ENV_NAME) or "")
    env = workers.scrub_worker_env(environ)
    for key in [k for k in env if k.startswith(WORKER_ENV_PREFIX) or (named and k == named)]:
        env.pop(key, None)
    return env


def worker_extra(env: dict, files: dict) -> dict:
    """What the daemon lays over the scrubbed environment for one turn, as
    upstream's `worker_env_parts` does: the shim first on PATH, the real CLI
    it hands over to, the turn's authority file and its progress outbox."""
    extra = {"PATH": f"{WORKER_BIN}{os.pathsep}{env.get('PATH', '')}"}
    real = os.environ.get("TELEGRAM_BOT_CLI")
    if real:
        extra["TELEGRAM_BOT_REAL_CLI"] = real
    if files.get("authority"):
        extra["CAPABILITIES_AUTH_CONTEXT"] = files["authority"]
    if files.get("outbox"):
        extra["TELEGRAM_PROGRESS_OUTBOX"] = files["outbox"]
    return extra


def worker_stub(prompt_text, tail, knobs=None, cwd=None, cancel=None):
    """Upstream's in-process stub: names the tail and echoes the last line."""
    last = tail[-1]["text"] if tail else ""
    return {"ok": True, "answer": f"[harness-stub] tail {len(tail)} msgs, last: «{last[:80]}».",
            "kind": None, "reason": None}


def _profiles_module():
    import profiles
    return profiles


def stored_profiles(project_root, project_id) -> list:
    """Every active profile version this project reaches in the store, or none
    when the store cannot be read: a turn then resolves among the bundled ones,
    as upstream's `turn_profiles` does."""
    try:
        from capabilities_contract.db import connect, resolve_setting
        setting = resolve_setting(project_root)
        conn = connect(application_name="telegram-bot", setting=setting, transaction=True)
    except Exception as exc:
        log(f"profiles: store not read, resolving among the bundled profiles: "
            f"{type(exc).__name__}: {str(exc)[:200]}")
        return []
    try:
        return _profiles_module().stored(conn, project_id, setting.schema)
    except Exception as exc:
        log(f"profiles: store read failed: {type(exc).__name__}: {str(exc)[:200]}")
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def worker_harness(prompt_text, tail, knobs, cwd, cancel=None, files=None):
    """One headless turn through callva-harness-runner on a resolved profile."""
    profiles = _profiles_module()
    lib = profiles.runner()
    if isinstance(knobs.get("settings"), dict) and (files or {}).get("settings"):
        # As a file: on the command line, thousands of deny rules pass Linux's
        # 128 KiB limit on one argument.
        write_private(Path(files["settings"]), json.dumps(knobs["settings"]))
        knobs = {**knobs, "settings": files["settings"]}
    profile = lib.Profile.from_dict(knobs)
    env = worker_env(os.environ)
    env.update(worker_extra(env, files or {}))
    result = lib.run(prompt_text, profile, str(cwd), session=lib.Session.fresh(),
                     environ=env, cancel=cancel)
    for warning in getattr(result, "warnings", None) or ():
        log(f"worker: {warning}")
    if not result.ok:
        kind = str(getattr(result.failure, "kind", "error"))
        return {"ok": False, "answer": "", "kind": kind,
                "reason": str(getattr(result.failure, "message", "") or kind)[:500]}
    return {"ok": True, "answer": (result.answer or "").strip(), "kind": None, "reason": None}


RESTRICTED_TOOLS = ("Read", "Grep", "Glob")
# The knobs a restricted profile may set. Any other knob set to anything but
# nothing is refused, so a knob the runner adds later is refused until it is
# read here (an allowlist, not a list of what is known to widen a turn).
RESTRICTED_KNOBS = {"harness", "model", "effort", "thinking", "max_turns", "timeout_seconds",
                    "budget_usd", "tools", "disallowed_tools", "permission_mode",
                    "setting_sources", "settings", "mcp_config", "strict_mcp",
                    "system_prompt", "append_system_prompt", "name", "persist_session",
                    "strip_session_markers", "output_schema"}
RESTRICTED_SETTINGS = {"disableAllHooks", "permissions", "autoMemoryEnabled",
                       "includeGitInstructions"}
RESTRICTED_SOURCES = ("project", "local")
# Laid over every restricted turn's settings, whatever its profile says.
RESTRICTED_DENY = ["Read(**/.env)", "Read(**/.env.*)", "Read(**/.envrc)", "Read(**/*.env)",
                   "Read(**/.git/config)", "Read(~/.config/**)", "Read(~/.ssh/**)",
                   "Read(~/.local/state/**)", "Read(//proc/**)"]
# Also laid over them: no memory read or written, and no git status (the
# user's name, the recent commits) in the turn's context. The settings' `env`
# outranks the project's own, where the variables would otherwise turn them on.
RESTRICTED_OFF = {"autoMemoryEnabled": False, "includeGitInstructions": False}
RESTRICTED_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                  "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1"}
DENY_RULES_LIMIT = 3000


def _nothing(value) -> bool:
    return value is None or value is False or value in ("", [], (), {})


def restricted_fit(knobs: dict) -> list[str]:
    """Why a profile cannot carry a restricted turn, one sentence each: what
    any turn needs (upstream's fit, less its demand for full access), and an
    allowlist - claude, the tools that read, no permission bypass, no user
    settings, no MCP server, hooks off, and settings that hold deny rules
    alone. Codex is refused: its read-only sandbox reads the whole disk."""
    problems = []
    harness = knobs.get("harness")
    if harness != "claude":
        problems.append(f"harness {harness!r} is not claude: a restricted turn runs on claude "
                        "only (codex's read-only sandbox reads the whole disk)")
    for key in sorted(set(knobs) - RESTRICTED_KNOBS):
        if not _nothing(knobs[key]):
            problems.append(f"{key} is set; a restricted profile may not set it")
    if knobs.get("output_schema") is not None:
        problems.append("output_schema is set; the service reads free text")
    if knobs.get("timeout_seconds") is None:
        problems.append("timeout_seconds is not set")
    if knobs.get("permission_mode") not in ("default", "dontAsk"):
        problems.append("permission_mode must be default or dontAsk, so a read outside "
                        "the project is refused")
    tools = knobs.get("tools")
    if not isinstance(tools, (list, tuple)) or set(tools) - set(RESTRICTED_TOOLS):
        problems.append(f"tools must be listed and hold only {', '.join(RESTRICTED_TOOLS)}")
    sources = knobs.get("setting_sources")
    if not isinstance(sources, (list, tuple)) or set(sources) - set(RESTRICTED_SOURCES):
        problems.append("setting_sources must be listed and hold only project and local")
    if knobs.get("strict_mcp") is not True:
        problems.append("strict_mcp must be true")
    mcp = knobs.get("mcp_config")
    if mcp is not None and not (isinstance(mcp, dict) and set(mcp) <= {"mcpServers"}
                                and _nothing(mcp.get("mcpServers"))):
        problems.append("mcp_config may name no MCP server")
    settings = knobs.get("settings")
    if not isinstance(settings, dict) or settings.get("disableAllHooks") is not True:
        problems.append("settings must set disableAllHooks true")
    else:
        for key in sorted(set(settings) - RESTRICTED_SETTINGS):
            problems.append(f"settings.{key} is set; a restricted profile's settings hold "
                            "disableAllHooks, deny rules and the features it turns off alone")
        for key in sorted(set(settings) & set(RESTRICTED_OFF)):
            if settings[key] is not False:
                problems.append(f"settings.{key} must be false")
        permissions = settings.get("permissions", {})
        if not isinstance(permissions, dict) or set(permissions) - {"deny"} or not all(
                isinstance(rule, str) for rule in permissions.get("deny") or []):
            problems.append("settings.permissions may hold a deny list alone")
    return problems


# Claude Code reads a rule's path as a gitignore-style pattern. Measured on
# 2.1.310: `*`, `[` and `]` hold escaped with a backslash; `?` and `\` do not,
# so they are written as the `?` wildcard (one character); `{}` and `()` are
# literal.
_ESCAPED = set("*[]")
_WILD = set("?\\")


def _rule_path(path: Path) -> str:
    return "".join("\\" + c if c in _ESCAPED else "?" if c in _WILD or not c.isprintable()
                   else c for c in str(path))


def _covers(path: Path, project: Path) -> bool:
    """Whether the rule for `path` matches the project or a folder holding it,
    as a `?` wildcard can when a sibling's name differs only there."""
    def segment(name: str) -> str:
        return "".join("." if c in _WILD or not c.isprintable() else re.escape(c)
                       for c in name)
    parts, inner = path.parts, project.parts
    return len(parts) <= len(inner) and all(
        re.fullmatch(segment(a), b, re.DOTALL) for a, b in zip(parts, inner))


def restricted_denies(project_root, state_dir=None) -> list[str]:
    """The deny rules a restricted turn is handed beside `RESTRICTED_DENY`:
    this bot's state folder, the user's config and state homes where the
    environment moves them, and every entry beside the project and beside each
    folder above it - the root's first, as the widest, then the nearest - so
    nothing outside the project is readable, whatever allow rule or extra
    directory the project's own Claude settings add (Claude Code's deny
    outranks its allow). An entry
    whose real path holds the project is left alone, and so is one whose rule
    would match the project too (`_covers`). Past `DENY_RULES_LIMIT` rules the
    folders furthest up but below the root are left to the permission mode."""
    project = Path(project_root).resolve()
    out: list[str] = []
    left_out = 0

    def deny(path: Path) -> None:
        nonlocal left_out
        if _covers(path, project):
            log(f"restricted: {str(path)!r} not denied: its rule would match the project")
            return
        rule = f"Read(/{_rule_path(path)}/**)" if path.is_dir() else f"Read(/{_rule_path(path)})"
        if rule in out:
            return
        if len(out) >= DENY_RULES_LIMIT:
            left_out += 1
        else:
            out.append(rule)
    if state_dir:
        deny(Path(state_dir).resolve())
    for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        if os.environ.get(key):
            deny(Path(os.environ[key]).resolve())
    levels = []
    here = project
    while here.parent != here:
        levels.append(here)
        here = here.parent
    # The root's entries first, as the widest; then from the project up, the
    # nearest (other projects) before the far ones.
    for here in levels[-1:] + levels[:-1]:
        parent = here.parent
        try:
            entries = sorted(parent.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if entry.name == here.name:
                continue
            try:
                real = entry.resolve()
            except OSError:
                continue
            if project.is_relative_to(real):
                continue
            deny(entry)
            if real != entry:
                deny(real)
    if left_out:
        log(f"restricted: {len(out)} deny rules, the limit; {left_out} more, for folders "
            "further up, are left to the permission mode")
    return out


def narrowed(knobs: dict, denies: list[str]) -> dict:
    """`knobs` with `RESTRICTED_DENY` and `denies` added to its settings'
    deny rules, and `RESTRICTED_OFF` and `RESTRICTED_ENV` laid over them."""
    settings = json.loads(json.dumps(knobs.get("settings") or {}))
    permissions = settings.setdefault("permissions", {})
    permissions["deny"] = list(dict.fromkeys([*(permissions.get("deny") or []),
                                              *RESTRICTED_DENY, *denies]))
    settings.update(RESTRICTED_OFF)
    settings["env"] = {**(settings.get("env") or {}), **RESTRICTED_ENV}
    return {**knobs, "settings": settings}


def restricted_problems(document, resolve_restricted) -> list[str]:
    """Every restricted profile the settings reach that does not resolve or
    does not fit: each `restricted_profile`, and `RESTRICTED_PROFILE` unless
    every turn runs on the stub (a restricted turn is the stub only there)."""
    names = [row["profile"] for row in settings_check.profile_names(document)]
    if os.environ.get(PROFILE_ENV):
        names.append(os.environ[PROFILE_ENV])
    restricted = [row["profile"] for row in settings_check.restricted_profile_names(document)]
    if set(names) - {STUB} or restricted:
        restricted.append(settings_check.RESTRICTED_PROFILE)
    out = []
    for name in dict.fromkeys(restricted):
        _knobs, refused = resolve_restricted(name)
        if refused:
            out.append(f"restricted profile {name!r}: {refused}")
    return out


def check_restricted(argv: list[str]) -> int:
    """`daemon.py check-restricted --project-root R [--project-id I]`, the
    settings document on stdin: the CLI's doctor, start and reload ask it
    whether the restricted profiles fit, since only this file's header carries
    the harness runner. Answers `{"problems": [...]}`."""
    root = argv[argv.index("--project-root") + 1]
    ident = argv[argv.index("--project-id") + 1] if "--project-id" in argv else None
    document = json.loads(sys.stdin.read() or "{}")
    with contextlib.redirect_stdout(sys.stderr):   # the log, kept off the answer
        problems = restricted_problems(
            document, lambda name: resolve_profile(name, root, ident, restricted=True))
    sys.stdout.write(json.dumps({"problems": problems}, ensure_ascii=False) + "\n")
    return 0


def resolve_profile(name, project_root, project_id, restricted=False):
    """`(knobs, error)` for a profile name: None knobs for the stub. A
    restricted profile is resolved without upstream's fit, which asks for full
    access, and held to `restricted_fit` instead."""
    if name == STUB:
        return None, None
    profiles = _profiles_module()
    try:
        active = stored_profiles(project_root, project_id)
        if not restricted:
            found = profiles.turn(name, active, project_id)
        else:
            found = profiles.resolve(name, active, project_id, fit=False)
            problems = restricted_fit(found["knobs"])
            if problems:
                return None, (f"profile {name!r} cannot carry a restricted turn: "
                              + "; ".join(problems))
    except profiles.Refusal as refused:
        return None, refused.message
    return dict(found["knobs"]), None


# --- Logging -----------------------------------------------------------------

_LOG_LOCK = threading.Lock()
LOG_FILE: Path | None = None   # set when this process owns its log (`service start`)


def _rotate_log() -> None:
    """`service.log` -> `.1` -> ... -> `.LOG_BACKUPS`, then a fresh file on
    stdout and stderr. Runs under the log lock."""
    if LOG_FILE is None:
        return
    try:
        if LOG_FILE.stat().st_size < LOG_MAX_BYTES:
            return
    except OSError:
        return
    for index in range(LOG_BACKUPS - 1, 0, -1):
        older = LOG_FILE.with_name(f"{LOG_FILE.name}.{index}")
        if older.exists():
            os.replace(older, LOG_FILE.with_name(f"{LOG_FILE.name}.{index + 1}"))
    os.replace(LOG_FILE, LOG_FILE.with_name(f"{LOG_FILE.name}.1"))
    fd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    sys.stdout.flush()
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def log(message: str) -> None:
    with _LOG_LOCK:
        sys.stdout.write(f"{iso_utc()} {message}\n")
        sys.stdout.flush()
        _rotate_log()


# --- The service -------------------------------------------------------------

class Service:
    def __init__(self, *, api, load_settings, project_root, project_id=None,
                 expected_id=None, connection=None, context_file=None,
                 health_file=None, launch_nonce=None, run_worker=None,
                 resolve=None, clock=time.monotonic, executor=None, sleep=time.sleep,
                 wall=time.time, notice_executor=None, load_context=None,
                 turn_dir=None, journal_file=None, acquire_lock=None,
                 vanished=_vanished_runtime, resolve_restricted=None,
                 overrides_file=None, state_dir=None):
        self.api = api
        self.load_settings = load_settings
        self.project_root = Path(project_root)
        self.project_id = project_id
        self.expected_id = expected_id
        self.connection = connection
        self.context_file = Path(context_file) if context_file else None
        self.load_context = load_context
        self.health_file = Path(health_file) if health_file else None
        self.journal_file = Path(journal_file) if journal_file else None
        self.turn_dir = Path(turn_dir) if turn_dir else None
        self.launch_nonce = launch_nonce
        self.run_worker = run_worker
        self.resolve = resolve or (lambda name: resolve_profile(name, self.project_root,
                                                                self.project_id))
        self.resolve_restricted = resolve_restricted or (
            lambda name: resolve_profile(name, self.project_root, self.project_id,
                                         restricted=True))
        self.overrides_file = Path(overrides_file) if overrides_file else None
        self.state_dir = Path(state_dir) if state_dir else None
        self.clock = clock
        self.sleep = sleep
        self.wall = wall
        self.acquire_lock = acquire_lock
        self.holds_lock = acquire_lock is None
        self.vanished = vanished
        self.guard = loopguard.Guard(clock=clock)
        self.tails = prompting.Tails()
        self.lock = threading.RLock()
        self.running: dict[str, dict] = {}
        self.pending: dict[str, deque] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.stopped_ids: set = set()
        self.stopped_by: dict = {}
        self.settle_until: dict[str, float] = {}
        self.overrides: dict[str, dict] = {}
        self.turn_files: dict[str, dict] = {}
        self.offset = None
        self.me: dict = {}
        self.stopping = False
        self.reload_requested = False
        self.reload_attempts = 0
        self.reload_error = None
        self.settings = {}
        self.settings_fingerprint = None
        self.last_poll_at = None
        self.state = "starting"
        self.executor = executor
        self.sent: deque = deque(maxlen=SENT_KEEP)
        self.refused_places: OrderedDict = OrderedDict()
        self.conflicts = 0
        self.unfinished: dict[int, dict] = {}
        self.rate_noticed: dict[str, float] = {}
        self.offline_noticed: dict[str, float] = {}
        self.parked: str | None = None
        self.problem: str | None = None
        self.warnings: list[str] = []
        self.retired: list = []
        self.notices = notice_executor
        self.previous_exit: dict | None = None
        self.uncertain_sends = 0

    # --- Settings ----------------------------------------------------------

    def profile_problems(self, document) -> list[str]:
        """Every profile the settings (or the environment) name that does not
        resolve, as upstream checks them before it adopts a reload."""
        names = [row["profile"] for row in settings_check.profile_names(document)]
        if os.environ.get(PROFILE_ENV):
            names.append(os.environ[PROFILE_ENV])
        names = [name for name in dict.fromkeys(names) if name != STUB]
        out = []
        for name in names:
            _knobs, refused = self.resolve(name)
            if refused:
                out.append(f"profile {name!r}: {refused}")
        return out + restricted_problems(document, self.resolve_restricted)

    def adopt_settings(self, document, *, check_profiles=False) -> None:
        problems = settings_check.problems(document)
        if not problems and self.settings_fingerprint is not None and \
                _mapping(document).get("connection") != self.settings.get("connection"):
            problems = ["connection changed: a running service keeps its connection; "
                        "restart it (`service stop`, then `service start`) to change it"]
        if not problems and check_profiles:
            problems = self.profile_problems(document)
        if problems:
            raise ValueError("; ".join(problems))
        self.settings = document
        self.settings_fingerprint = fingerprint(document)
        self.warnings = settings_check.warnings(document)
        for warning in self.warnings:
            log(f"WARNING settings: {warning}")
        workers = int(_mapping(document.get("defaults")).get("max_parallel") or 1)
        if self.executor is None or getattr(self.executor, "_tb_size", None) != workers:
            old = self.executor
            self.executor = ThreadPoolExecutor(max_workers=max(1, workers),
                                               thread_name_prefix="turn")
            self.executor._tb_size = workers
            if old is not None:
                # Its running and queued turns finish there; a stop waits for them.
                self.retired.append(old)
                old.shutdown(wait=False)

    def check_profiles(self) -> bool:
        """Every profile the settings in force name, resolved before the first
        turn: one that does not, or a restricted one that does not fit, parks
        the process (`profile_refused`) until a reload brings settings whose
        profiles do."""
        problems = self.profile_problems(self.settings)
        if problems:
            self.park("profile_refused", "; ".join(problems)[:1000])
            return False
        return True

    def reload(self) -> None:
        self.reload_requested = False
        self.reload_attempts += 1
        was = self.parked
        if self.parked:
            log(f"parked ({self.parked}): reload asks for another try")
            self.parked = self.problem = None
            self.conflicts = 0
            self.me = {}
        try:
            self.adopt_settings(self.load_settings(), check_profiles=True)
            self.reload_error = None
            log(f"settings: reloaded, fingerprint {self.settings_fingerprint}")
        except Exception as exc:
            self.reload_error = str(exc)[:500]
            log(f"settings: reload refused, previous settings stay: {self.reload_error}")
            if was == "profile_refused":
                self.park("profile_refused", self.reload_error)
        self.write_health()

    # --- Health -------------------------------------------------------------

    def health(self) -> dict:
        poll = int(_mapping(self.settings.get("defaults")).get("poll_timeout")
                   or settings_check.DEFAULT_POLL_TIMEOUT)
        return {"state": self.state, "pid": os.getpid(), "launch_nonce": self.launch_nonce,
                "connection": self.connection, "project_root": str(self.project_root),
                "bot_id": self.me.get("id"), "username": self.me.get("username"),
                "last_poll_at": self.last_poll_at,
                "stale_after_seconds": poll + STALE_AFTER_EXTRA,
                "settings_fingerprint": self.settings_fingerprint,
                "settings_reload_attempts": self.reload_attempts,
                "settings_reload_error": self.reload_error,
                "running_turns": sorted(self.running), "problem": self.problem,
                "queued_requests": sum(len(q) for q in self.pending.values()),
                "settings_warnings": list(self.warnings),
                "uncertain_sends": self.uncertain_sends,
                "previous_exit": self.previous_exit,
                "clock_offset_seconds": self.clock_offset(), "updated_at": iso_utc()}

    def clock_offset(self):
        """This machine's clock minus Telegram's, as the last answer's Date said."""
        offset = getattr(self.api, "clock_offset", None)
        return round(offset, 1) if isinstance(offset, (int, float)) else None

    def report_unclean_exit(self) -> None:
        """Upstream's `report_unclean_exit`: a health file the last process left
        at anything but `stopped` means it was killed, not stopped. Called before
        this process's first health write, which overwrites what it reads."""
        if self.health_file is None:
            return
        try:
            previous = json.loads(self.health_file.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(previous, dict):
            return
        state = previous.get("state")
        self.previous_exit = {"state": state, "pid": previous.get("pid"),
                              "unclean": state not in (None, "stopped")}
        if self.previous_exit["unclean"]:
            log(f"daemon: the previous process (pid {previous.get('pid')}) left its health "
                f"at {state!r} with no stop - it was killed, not stopped; last update "
                f"{previous.get('updated_at')}")

    def park(self, state: str, problem: str) -> None:
        """Stay up without polling, saying why (see the module docstring)."""
        self.parked = self.state = state
        self.problem = problem
        log(f"parked ({state}): {problem}; not polling until `service reload` or a stop")
        self.write_health()

    def write_health(self) -> None:
        if self.health_file is None:
            return
        write_private(self.health_file,
                      json.dumps(self.health(), indent=2, ensure_ascii=False) + "\n")

    # --- Journal ------------------------------------------------------------

    def _journal_save(self) -> None:
        """The messages of every request not yet answered, for the next start:
        written under the lock, so the file on disk is always the newest state,
        through a temporary file of its own, owner-only."""
        if self.journal_file is None:
            return
        with self.lock:
            rows = [d["message"] for d in self.unfinished.values() if d.get("message")]
            write_private(self.journal_file, json.dumps(rows, ensure_ascii=False) + "\n")

    def _journal_save_safe(self) -> None:
        """The journal saved, or the failure logged: a request runs and a
        conversation moves on whether or not its journal could be written."""
        try:
            self._journal_save()
        except Exception as exc:
            log(f"journal: not saved ({type(exc).__name__}: {exc}); requests run regardless")

    def recover(self) -> int:
        """Take up the requests a killed process left unanswered (upstream
        requeues them, `_recover_incomplete_jobs`). Each goes through the gate
        again; one gone stale gets the offline notice instead of an answer."""
        if self.journal_file is None:
            return 0
        try:
            rows = json.loads(self.journal_file.read_text())
        except (OSError, ValueError):
            return 0
        self.journal_file.unlink(missing_ok=True)
        count = 0
        for message in rows if isinstance(rows, list) else []:
            if isinstance(message, dict):
                log(f"recover: request #{message.get('message_id')} left unanswered by the "
                    "previous process")
                self.handle_message(message)
                count += 1
        return count

    # --- Start --------------------------------------------------------------

    def start(self) -> None:
        """Adopt the settings and connect; a refused profile or connection parks."""
        self.adopt_settings(self.load_settings())
        self.load_overrides()
        if self.check_profiles():
            self.connect()

    def load_overrides(self) -> None:
        """The `/set` overrides a previous process kept, so they outlive a restart."""
        if self.overrides_file is None:
            return
        try:
            held = json.loads(self.overrides_file.read_text())
        except (OSError, ValueError):
            return
        if isinstance(held, dict):
            self.overrides = {str(k): dict(v) for k, v in held.items() if isinstance(v, dict)}

    def save_overrides(self) -> None:
        if self.overrides_file is None:
            return
        try:
            with self.lock:
                write_private(self.overrides_file, json.dumps(
                    {k: v for k, v in self.overrides.items() if v}, ensure_ascii=False) + "\n")
        except OSError as exc:
            log(f"/set: overrides not saved ({exc}); they hold until a restart")

    def take_lock(self) -> bool:
        """This bot's lock, held across projects: one bot polls in one place."""
        if self.holds_lock:
            return True
        if self.acquire_lock():
            self.holds_lock = True
            return True
        self.park("bot_locked", f"another daemon on this machine holds bot "
                  f"{self.expected_id}'s lock - it runs for another project; one bot "
                  "answers for one project")
        return False

    def connect(self) -> bool:
        """`getMe`, the identity check and the webhook cleared. A refused token
        or another bot's parks; anything else leaves it to the next try."""
        try:
            me = self.api.get_me()
            if isinstance(me, dict) and me.get("is_bot") and (
                    self.expected_id is None or int(me.get("id") or 0) == int(self.expected_id)):
                self.api.delete_webhook()
        except botapi.BotApiError as exc:
            if exc.kind == "auth" or exc.status in (401, 404):
                self.park("token_refused", f"Telegram refused the token ({exc.status}): it was "
                          "revoked or replaced; put the current one in the token's home and "
                          "restart the service")
                return False
            self.state = "degraded"
            log(f"start: {exc.kind} {exc.status}: {exc}")
            self.write_health()
            return False
        if not isinstance(me, dict) or not me.get("is_bot"):
            self.park("bot_mismatch", "the token does not belong to a bot")
            return False
        if self.expected_id is not None and int(me.get("id") or 0) != int(self.expected_id):
            self.park("bot_mismatch", f"the token belongs to bot {me.get('id')}, not the "
                      f"expected {self.expected_id}")
            return False
        self.me = me
        log(f"start: @{me.get('username')} ({me.get('id')}) for {self.project_root}, "
            f"settings {self.settings_fingerprint}")
        self.state = "healthy"
        self.write_health()
        return True

    # --- Updates ------------------------------------------------------------

    def poll_once(self) -> int:
        timeout = int(_mapping(self.settings.get("defaults")).get("poll_timeout")
                      or settings_check.DEFAULT_POLL_TIMEOUT)
        try:
            updates = self.api.get_updates(self.offset, timeout)
        except botapi.BotApiError as exc:
            log(f"poll: {exc.kind} {exc.status}: {exc}")
            if exc.kind == "auth" or exc.status in (401, 404):
                self.park("token_refused", f"Telegram refused the token ({exc.status}): it was "
                          "revoked or replaced; put the current one in the token's home and "
                          "restart the service")
                return 0
            if exc.status == 409:
                self.conflicts += 1
                self.state = "conflict"
                self.write_health()
                if self.conflicts >= CONFLICT_LIMIT:
                    self.park("conflict", f"another process polls this bot's token "
                              f"({self.conflicts} conflicts in a row): one bot runs in one "
                              "place")
                    return 0
                self.sleep(min(60.0, 5.0 * 2 ** (self.conflicts - 1)))
                return 0
            self.conflicts = 0
            self.state = "degraded"
            self.write_health()
            self.sleep(min(30.0, (exc.retry_after or 5.0)))
            return 0
        self.conflicts = 0
        self.state = "healthy"
        self.last_poll_at = iso_utc()
        for update in updates:
            self.offset = int(update.get("update_id", 0)) + 1
            try:
                self.handle_update(update)
            except Exception as exc:
                log(f"update {update.get('update_id')}: {type(exc).__name__}: {exc}")
        self.write_health()
        return len(updates)

    def handle_update(self, update: dict):
        """The update's dedupe, then `handle_message`."""
        if not self.guard.first_update(update.get("update_id")):
            return {"outcome": "duplicate_update"}
        message = update.get("message")
        if not isinstance(message, dict):
            return {"outcome": "not_a_message"}
        return self.handle_message(message)

    def handle_message(self, message: dict):
        """The gate, the tail, the guard; a control command or a turn for what
        passes. Returns the gate's decision with `outcome` naming what became
        of the message."""
        chat_id = _mapping(message.get("chat")).get("id")
        if not self.guard.first_message(chat_id, message.get("message_id")):
            return {"outcome": "duplicate_message"}
        decision = gate.decide(message, self.settings, self.me)
        key, policy = decision["key"], decision["policy"]
        sender = decision["sender"]
        with self.lock:
            # Only an allowed sender shapes a turn: a stranger's words never
            # reach the prompt, and a stranger never re-opens a capped dialogue.
            # A service message (a pin, a join) carries no text and is skipped.
            if decision["known_place"] and sender.get("allowed") and \
                    prompting.message_text(message) is not None:
                origin = decision.get("forwarded_from")
                self.tails.add(key, prompting.tail_row(
                    message, sender["name"], mine=False, agent=sender["agent"],
                    my_id=self.me.get("id"), forwarded_from=origin["name"] if origin else None),
                    self.tail_size(key, policy))
                if not sender["agent"]:
                    self.guard.human_spoke(key, policy, message.get("message_id"))
        now = self.wall()
        age = now - (self.clock_offset() or 0) - float(message.get("date") or 0)
        if decision["answer"] and age > BACKLOG_SECONDS:
            log(f"{key} #{message.get('message_id')}: not answered (stale, sent "
                f"{int(age)}s ago); from {sender['name']} id {sender.get('id')}")
            with self.lock:
                tell = now - self.offline_noticed.get(key, float("-inf")) > BACKLOG_SECONDS
                if tell:
                    _remember(self.offline_noticed, key, now)
            if tell:
                self._notify_later(decision, OFFLINE_NOTICE)
            decision["outcome"] = "stale"
            return decision
        if not decision["answer"]:
            reason = decision["reason"]
            first_refusal = False
            if reason in ("chat_not_allowed", "topic_not_allowed"):
                with self.lock:
                    first_refusal = (key, reason) not in self.refused_places
                    if first_refusal:
                        # Once per place: how a person enrolling the bot learns
                        # the chat and topic ids to allow. Bounded, so a bot
                        # added to many groups does not grow without end.
                        self.refused_places[(key, reason)] = True
                        while len(self.refused_places) > REFUSED_PLACES_LIMIT:
                            self.refused_places.popitem(last=False)
            if first_refusal or decision.get("triggers") or reason not in (
                    "not_addressed", "chat_not_allowed", "topic_not_allowed", "own_message"):
                log(f"{key} #{message.get('message_id')}: not answered ({reason}); from "
                    f"{sender['name']} id {sender.get('id')}"
                    + (" [bot]" if sender["agent"] else ""))
            decision["outcome"] = decision["reason"]
            return decision
        decision["message"] = message
        if decision.get("command"):
            decision["outcome"] = "command"
            reply = self.control(decision)
            if reply:
                self._notify_later(decision, reply)
            return decision
        dialogue = None
        notice = False
        with self.lock:
            # Requests running and waiting count as the answers they will be,
            # so a burst during a turn cannot queue past the limit.
            here = len(self.pending.get(key) or ()) + (1 if key in self.running else 0)
            total = len(self.running) + sum(len(q) for q in self.pending.values())
            allowed, why = self.guard.rate_allows(key, policy, in_flight=here,
                                                  in_flight_total=total)
            if allowed and (here >= QUEUE_LIMIT or total >= QUEUE_LIMIT_TOTAL):
                allowed, why = False, (f"{here} requests running or waiting here, {total} "
                                       "across the bot")
            if not allowed and not sender["agent"]:
                # A person is told, once a minute per conversation; a bot is
                # not, since telling it would be one more message to answer.
                now = self.clock()
                notice = now - self.rate_noticed.get(key, float("-inf")) >= \
                    loopguard.WINDOW_SECONDS
                if notice:
                    _remember(self.rate_noticed, key, now)
            if allowed and sender["agent"]:
                admitted, dialogue = self.guard.admit_agent_turn(
                    key, policy, sender.get("id"), message.get("message_id"))
                if not admitted:
                    log(f"{key} #{message.get('message_id')}: loop guard, agent turns "
                        f"{dialogue['turns']}/{dialogue['max_turns']} until a human speaks")
                    decision["outcome"] = "loop_guard"
                    return decision
        if not allowed:
            log(f"{key} #{message.get('message_id')}: rate limit, {why}")
            if notice:
                self._notify_later(decision, RATE_NOTICE)
            decision["outcome"] = "rate_limited"
            return decision
        decision["dialogue"] = dialogue
        decision["outcome"] = self.dispatch(decision)
        return decision

    def _notify_later(self, decision: dict, text: str) -> None:
        """A notice sent by the notice thread, so a slow send never holds polling."""
        if self.notices is None:
            self.notices = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notice")
        self.notices.submit(self._notify, decision, text, False)

    def _notify(self, decision: dict, text: str, count: bool = True) -> None:
        """A short notice to the request; a failure to send it is logged."""
        try:
            self.deliver(decision, gate.send_target(decision), text, count=count,
                         markdown=False)
        except Exception as exc:
            log(f"{decision['key']}: notice not sent: {type(exc).__name__}: {exc}")

    # --- Control commands -----------------------------------------------------

    def control(self, decision: dict) -> str:
        """An addressed `/command`, answered by the daemon and gated by role as
        upstream gates it (`_control_command_allowed`)."""
        key = decision["key"]
        sender = decision["sender"]
        text = (prompting.message_text(decision["message"]) or "").strip()
        command = decision["command"]
        args = text.split()[1:]
        log(f"{key}: /command «{text[:40]}» from {sender.get('id')} ({sender.get('role')})")
        if sender.get("agent") and authority.command_key(command) not in ("status", "help"):
            # A bot may ask what runs here, never stop, reload or set anything,
            # whatever role its entry gives it.
            name = "/" + authority.command_key(command)
            log(f"{key}: denied {name} for bot {sender.get('id')}")
            return f"nope: {name} is not for a bot"
        if not authority.command_allowed(self.settings, command, sender.get("role"),
                                         sender.get("id"), decision.get("group")):
            name = "/" + authority.command_key(command)
            log(f"{key}: denied {name} for {sender.get('id')} ({sender.get('role')})")
            return f"nope: {name} is not allowed for role {sender.get('role')}"
        if command == "stop":
            return self.stop_requests(key, args[0] if args else None, by=sender)
        if command == "status":
            return self.status_reply(key, full=self._supervisor(sender))
        if command == "profiles":
            name, where = self.profile_in_force(key, decision["policy"])
            limited = decision["policy"].get("restricted_profile") or \
                settings_check.RESTRICTED_PROFILE
            return (f"this chat runs on {name} (from {where}) for a supervisor, and on "
                    f"{limited if name != STUB else STUB} for everyone else; a supervisor's "
                    "/set profile <name> picks another for supervisors")
        if command == "reload":
            self.reload()
            return (f"nope: {self.reload_error}" if self.reload_error
                    else f"ok, settings reloaded ({self.settings_fingerprint})")
        if command == "set":
            if args and args[0].lower() == "profile" and not self._supervisor(sender):
                log(f"{key}: denied /set profile for {sender.get('id')} ({sender.get('role')})")
                return "nope: /set profile is for a supervisor"
            reply = self.set_reply(key, decision["policy"], args)
            self.save_overrides()
            return reply
        return CONTROL_HELP

    @staticmethod
    def _supervisor(sender: dict) -> bool:
        """A person whose role is supervisor; a bot never is one here."""
        return sender.get("role") == settings_check.SUPERVISOR and not sender.get("agent")

    def stop_requests(self, key: str, which: str | None, by: dict | None = None) -> str:
        """`/stop`: the running turn here and everything queued behind it;
        `/stop N`: request N alone, running or queued. A request someone else
        stopped tells its asker so."""
        dropped_requests: list = []
        with self.lock:
            running = self.running.get(key)
            queue = self.pending.get(key) or deque()
            if which is not None:
                try:
                    wanted = int(which.lstrip("#"))
                except ValueError:
                    return "usage: /stop [message number]"
                if running is not None and running.get("message_id") == wanted:
                    self._mark_stopped(running, by)
                    self.cancels.get(key, threading.Event()).set()
                    return f"ok, stopping #{wanted}"
                found = [queued for queued in queue if queued.get("message_id") == wanted]
                if not found:
                    return f"nothing running or waiting here as #{wanted}"
                queue.remove(found[0])
                self.unfinished.pop(id(found[0]), None)
                dropped_requests = found
                reply = f"ok, #{wanted} will not run"
            else:
                dropped_requests = list(queue)
                for queued in queue:
                    self.unfinished.pop(id(queued), None)
                queue.clear()
                if running is None:
                    reply = "nothing running here" + (
                        f"; dropped {len(dropped_requests)} waiting" if dropped_requests else "")
                else:
                    self._mark_stopped(running, by)
                    self.cancels.get(key, threading.Event()).set()
                    reply = f"ok, stopping #{running.get('message_id')}" + (
                        f" and {len(dropped_requests)} waiting" if dropped_requests else "")
        self._journal_save_safe()
        for dropped in dropped_requests:
            notice = self._stopped_notice(dropped, by)
            if notice:
                self._notify_later(dropped, notice)
        return reply

    def _mark_stopped(self, decision: dict, by: dict | None) -> None:
        self.stopped_ids.add(decision.get("message_id"))
        if by is not None:
            self.stopped_by[decision.get("message_id")] = by

    @staticmethod
    def _stopped_notice(decision: dict, by: dict | None) -> str | None:
        """What the asker of a stopped request is told: who stopped it, unless
        they stopped it themselves."""
        if not by or str(by.get("id")) == str(_mapping(decision.get("sender")).get("id")):
            return None
        return STOPPED_BY_NOTICE.format(name=prompting.quoted_name(by.get("name")))

    def status_reply(self, key: str, full: bool = True) -> str:
        """The daemon's state here; request numbers and `/set` overrides for a
        supervisor only, counts for anyone else."""
        with self.lock:
            running = self.running.get(key)
            queued = [d.get("message_id") for d in self.pending.get(key) or ()]
        lines = [f"bot @{self.me.get('username')}: {self.state}, settings "
                 f"{self.settings_fingerprint}"]
        if not full:
            lines.append(f"here: {'a turn running' if running else 'nothing running'}, "
                         f"{len(queued)} waiting")
            return "\n".join(lines)
        lines.append(f"running here: #{running.get('message_id')}" if running
                     else "running here: nothing")
        if queued:
            lines.append("waiting here: " + ", ".join(f"#{i}" for i in queued))
        if self.overrides.get(key):
            lines.append("set here: " + ", ".join(f"{k}={v}" for k, v in
                                                  sorted(self.overrides[key].items())))
        return "\n".join(lines)

    def set_reply(self, key: str, policy: dict, args: list) -> str:
        """`/set profile|tail|worker-timeout <value|default>` for this
        conversation, held while the process runs (upstream keeps it in its
        register; this bot keeps no register)."""
        usage = ("usage: /set <setting> <value>\n  profile <name|default>\n  tail <1..500>\n"
                 "  worker-timeout <1..3600|default>\n  debounce <0..300|default>")
        if len(args) < 2:
            return usage
        setting, value = args[0].lower(), args[1]
        here = self.overrides.setdefault(key, {})
        if value.lower() == "default":
            mapped = {"tail": "tail_size", "worker-timeout": "worker_timeout",
                      "worker_timeout": "worker_timeout", "timeout": "worker_timeout",
                      "profile": "profile", "debounce": "debounce"}.get(setting)
            if mapped is None:
                return usage
            here.pop(mapped, None)
            return f"ok, {setting} = default"
        try:
            if setting == "tail":
                number = int(value)
                if not 1 <= number <= 500:
                    raise ValueError("tail must be 1..500")
                here["tail_size"] = number
                return f"ok, tail = {number}"
            if setting in ("worker-timeout", "worker_timeout", "timeout"):
                number = float(value)
                if not 1 <= number <= 3600:
                    raise ValueError("worker-timeout must be 1..3600 seconds or default")
                here["worker_timeout"] = number
                return f"ok, worker-timeout = {number:g}s"
            if setting == "debounce":
                number = float(value)
                if not 0 <= number <= 300:
                    raise ValueError("debounce must be 0..300 seconds or default")
                here["debounce"] = number
                return f"ok, debounce = {number:g}s"
            if setting == "profile":
                if value != STUB:
                    _knobs, refused = self.resolve(value)
                    if refused:
                        raise ValueError(refused)
                here["profile"] = value
                return f"ok, profile = {value}"
        except ValueError as exc:
            return f"nope: {exc}"
        return usage

    # --- Turns --------------------------------------------------------------

    def tail_size(self, key: str, policy: dict) -> int:
        return int(self.overrides.get(key, {}).get("tail_size") or policy.get("tail_size")
                   or prompting.DEFAULT_TAIL_SIZE)

    def dispatch(self, decision: dict) -> str:
        """A turn now, or a place in this conversation's queue: every addressed
        message is its own request, answered in order."""
        key = decision["key"]
        delay = self.debounce(key, decision["policy"])
        with self.lock:
            self.unfinished[id(decision)] = decision
            # Upstream's `arm`: each addressed message restarts the wait.
            self.settle_until[key] = time.monotonic() + delay
            if key in self.running:
                self.pending.setdefault(key, deque()).append(decision)
                outcome = "queued"
            else:
                self.running[key] = decision
                self.cancels[key] = threading.Event()
                outcome = "dispatched"
        self._journal_save_safe()
        if outcome == "queued":
            log(f"{key} #{decision['message_id']}: waits for the turn running here")
            return outcome
        self.executor.submit(self._turn_then_next, decision)
        return outcome

    def _turn_then_next(self, decision: dict) -> None:
        """This conversation's turns, one after another, until none waits."""
        key = decision["key"]
        while decision is not None:
            settled = self._settle(key, decision)
            if settled:
                try:
                    self.turn(decision)
                except Exception as exc:
                    log(f"{key}: turn crashed: {type(exc).__name__}: {exc}")
                    self._notify(decision, self.failure_notice(decision, "crash"))
            elif not self.stopping:
                log(f"{key} #{decision.get('message_id')}: stopped by /stop before its turn")
                self._tell_stopped(decision)
            with self.lock:
                if settled or not self.stopping:
                    # Left unfinished, a request a service stop caught in its
                    # wait is told so by the stop.
                    self.unfinished.pop(id(decision), None)
                self.stopped_ids.discard(decision.get("message_id"))
                self.stopped_by.pop(decision.get("message_id"), None)
                queue = self.pending.get(key)
                decision = None if self.stopping or not queue else queue.popleft()
                if decision is None:
                    self.running.pop(key, None)
                    self.cancels.pop(key, None)
                    self.pending.pop(key, None)
                    self.settle_until.pop(key, None)
                else:
                    self.running[key] = decision
                    self.cancels[key] = threading.Event()
            self._journal_save_safe()

    def debounce(self, key: str, policy: dict) -> float:
        """Seconds a turn here waits after the last addressed message: `/set`,
        the room's, `defaults`', else upstream's default."""
        for value in (self.overrides.get(key, {}).get("debounce"), policy.get("debounce")):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return max(0.0, float(value))
        return float(settings_check.DEFAULT_DEBOUNCE)

    def _settle(self, key: str, decision: dict) -> bool:
        """Wait until the conversation's debounce has passed since its last
        addressed message, "typing" kept up meanwhile, as through the turn.
        False when a service stop or a /stop of this request ended the wait."""
        next_typing = 0.0
        while True:
            with self.lock:
                left = self.settle_until.get(key, 0.0) - time.monotonic()
                cancel = self.cancels.get(key)
            if self.stopping or decision.get("message_id") in self.stopped_ids:
                return False
            if left <= 0:
                return True
            if time.monotonic() >= next_typing:
                where = gate.send_target(decision)
                with contextlib.suppress(Exception):
                    self.api.send_chat_action(where["chat_id"], "typing",
                                              message_thread_id=where.get("message_thread_id"))
                next_typing = time.monotonic() + TYPING_INTERVAL
            if cancel is not None:
                cancel.wait(min(left, SETTLE_POLL))
            else:
                time.sleep(min(left, SETTLE_POLL))

    def _tell_stopped(self, decision: dict) -> None:
        by = self.stopped_by.get(decision.get("message_id"))
        notice = self._stopped_notice(decision, by)
        if notice:
            self._notify(decision, notice, count=False)

    def profile_in_force(self, key: str, policy: dict) -> tuple[str, str]:
        """The profile a turn here runs on, and where it comes from: `/set`,
        the room's own, the environment's default (a deployed node chooses
        without editing settings, as upstream's TELEGRAM_SERVICE_PROFILE does),
        `defaults.profile`, then the stub."""
        if self.overrides.get(key, {}).get("profile"):
            return self.overrides[key]["profile"], "/set"
        if policy.get("room_profile"):
            return str(policy["room_profile"]), "the room's settings"
        if os.environ.get(PROFILE_ENV):
            return os.environ[PROFILE_ENV], PROFILE_ENV
        if policy.get("profile"):
            return str(policy["profile"]), "defaults.profile"
        return STUB, "the default"

    def profile_name(self, policy: dict, key: str | None = None) -> str:
        return self.profile_in_force(key or "", policy)[0]

    def restricted_choice(self, policy: dict, name: str):
        """`(name, knobs, refused)` a restricted turn runs on: the room's
        `restricted_profile`, else `RESTRICTED_PROFILE`, whatever harness the
        profile in force uses; the stub stays the stub. Never the profile in
        force itself: a restricted profile that cannot be resolved, or does not
        fit, refuses the turn."""
        if name == STUB:
            return STUB, None, None
        chosen = policy.get("restricted_profile") or settings_check.RESTRICTED_PROFILE
        knobs, refused = self.resolve_restricted(chosen)
        return chosen, knobs, refused

    def context(self) -> str:
        """The context document, read for every turn as upstream reads it; the
        copy written at start or reload stands in when the store cannot be read."""
        if self.load_context is not None:
            try:
                return str(self.load_context() or "")
            except Exception as exc:
                log(f"context: not read, using the copy from start or reload: "
                    f"{type(exc).__name__}: {str(exc)[:200]}")
        if self.context_file is None:
            return ""
        try:
            return self.context_file.read_text()
        except OSError:
            return ""

    def failure_notice(self, decision: dict, kind: str) -> str:
        """The failure's kind for a supervisor; a plain note for anyone else
        (upstream shows the detail to a supervisor only)."""
        if _mapping(decision.get("sender")).get("role") == "supervisor":
            return FAILURE_NOTICE.format(kind=kind)
        return GENERIC_FAILURE_NOTICE

    def _turn_files(self, decision: dict, ctx: dict) -> dict:
        """The turn's authority file, progress outbox and settings file, owner-only."""
        if self.turn_dir is None:
            self.turn_dir = Path(tempfile.mkdtemp(prefix="telegram-bot-turns-"))
        self.turn_dir.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        os.chmod(self.turn_dir, PRIVATE_DIR_MODE)
        stem = f"{decision['key']}-{decision['message_id']}"
        path = authority.write_context(ctx, stem, self.turn_dir)
        outbox = Path(path).with_suffix(".progress.jsonl")
        return {"authority": path, "outbox": str(outbox),
                "settings": str(Path(path).with_suffix(".settings.json"))}

    def _companion(self, decision: dict, target: dict, files: dict, stop: threading.Event,
                   after) -> None:
        """For the length of a turn: "typing" kept up, and the worker's progress
        lines relayed, the first at once and the rest throttled."""
        throttle = ProgressThrottle(after)
        relayed = 0
        read = 0
        next_typing = 0.0
        while True:
            now = time.monotonic()
            if now >= next_typing:
                with contextlib.suppress(Exception):
                    self.api.send_chat_action(target["chat_id"], "typing",
                                              message_thread_id=target.get("message_thread_id"))
                next_typing = now + TYPING_INTERVAL
            try:
                with open(files["outbox"], encoding="utf-8") as fh:
                    fh.seek(read)
                    chunk = fh.read()
                    read = fh.tell()
            except OSError:
                chunk = ""
            for line in chunk.splitlines():
                try:
                    text = str(json.loads(line).get("text") or "").strip()
                except (ValueError, AttributeError):
                    continue
                if text and relayed >= PROGRESS_LIMIT:
                    log(f"{decision['key']}: progress held back, {PROGRESS_LIMIT} lines "
                        f"already this turn: {text[:80]}")
                elif text and throttle.allows():
                    self.deliver(decision, target, text, count=False)
                    throttle.sent()
                    relayed += 1
                elif text:
                    log(f"{decision['key']}: progress held back by the throttle: {text[:80]}")
            if stop.wait(min(PROGRESS_POLL, TYPING_INTERVAL)):
                return

    def turn(self, decision: dict) -> dict:
        key = decision["key"]
        policy = dict(decision["policy"])
        policy.update({k: v for k, v in self.overrides.get(key, {}).items()
                       if k != "profile"})
        message = decision["message"]
        chat = _mapping(message.get("chat"))
        target = gate.send_target(decision)
        is_direct = chat.get("type") == "private"
        sender = decision["sender"]
        with self.lock:
            tail = self.tails.get(key)
            waiting = [d.get("message_id") for d in self.pending.get(key) or ()]
        name = self.profile_name(policy, key)
        knobs, refused = self.resolve(name)
        ctx = authority.policy_for(
            self.settings, {"sender_id": sender.get("id"), "sender_role": sender.get("role"),
                            "sender_name": sender.get("name"), "chat_id": chat.get("id"),
                            "topic_id": decision.get("topic_id"),
                            "message_id": decision.get("message_id"),
                            "agent": sender.get("agent")},
            decision.get("group"), is_direct, self.connection)
        restricted = bool(ctx.get("restricted"))
        if restricted:
            name, knobs, refused = self.restricted_choice(policy, name)
        replied = prompting.replied_to(message, self.me.get("id"))
        if replied and not replied.get("mine") and gate.sender_entry(
                self.settings, decision.get("group"), replied.get("sender_id")) is None:
            # Only allowed senders shape a turn: the text a stranger wrote is
            # left out even when an allowed sender replies to it.
            replied["text"] = "[not shown: not from an allowed sender]"
        state = {"bot_username": self.me.get("username"), "now": now_display(),
                 "chat_id": chat.get("id"), "chat_type": chat.get("type"),
                 "chat_title": chat.get("title"), "topic_id": decision.get("topic_id"),
                 "topic_name": policy.get("name") if decision.get("topic_id") else None,
                 "agent_dialogue": decision.get("dialogue"), "brief": policy.get("brief"),
                 "progress_command": None if restricted else "telegram-bot progress",
                 "restricted": restricted,
                 "authority": authority.summary(ctx), "other_requests": waiting,
                 "request": {"message_id": decision["message_id"],
                             "sender_name": sender["name"],
                             "sender_role": sender["role"],
                             "agent": sender["agent"],
                             "triggers": decision["triggers"],
                             "in_reply_to": replied,
                             "text": prompting.message_text(message) or ""}}
        text = prompting.build_prompt(self.context(), state, tail)
        if refused:
            log(f"{key}: profile {name!r} refused: {refused}")
            return self.deliver(decision, target,
                                self.failure_notice(decision, "profile_refused"),
                                markdown=False)
        timeout = float(policy.get("worker_timeout") or settings_check.DEFAULT_WORKER_TIMEOUT)
        files = self._turn_files(decision, ctx)
        with self.lock:
            self.turn_files[key] = files
        stop = threading.Event()
        companion = threading.Thread(
            target=self._companion, name="turn-companion", daemon=True,
            args=(decision, target, files, stop,
                  policy.get("progress_after", settings_check.DEFAULT_PROGRESS_AFTER)))
        companion.start()
        started = time.monotonic()
        try:
            if knobs is None:
                runner = self.run_worker or worker_stub
                result = runner(text, tail, None, self.project_root, self.cancels.get(key))
            else:
                knobs = {**knobs, "timeout_seconds": timeout}
                if restricted:
                    knobs = narrowed(knobs, restricted_denies(self.project_root,
                                                              self.state_dir))
                runner = self.run_worker or partial(worker_harness, files=files)
                result = runner(text, tail, knobs, self.project_root, self.cancels.get(key))
        finally:
            stop.set()
            companion.join(timeout=10)
            with self.lock:
                self.turn_files.pop(key, None)
            for path in files.values():
                with contextlib.suppress(OSError):
                    Path(path).unlink()
        seconds = round(time.monotonic() - started, 1)
        if decision.get("message_id") in self.stopped_ids:
            log(f"{key} #{decision['message_id']}: stopped by /stop after {seconds}s")
            self._tell_stopped(decision)
            return {"sent": [], "silent": True}
        if self.stopping and not result.get("ok"):
            log(f"{key} #{decision['message_id']}: cancelled by the service stop after "
                f"{seconds}s")
            return self.deliver(decision, target, STOPPED_NOTICE, count=False,
                                markdown=False)
        if not result.get("ok"):
            log(f"{key} #{decision['message_id']}: {name} ended {result.get('kind')} after "
                f"{seconds}s: {result.get('reason')}")
            return self.deliver(decision, target,
                                self.failure_notice(decision, result.get("kind") or "error"),
                                markdown=False)
        answer, outcome = prompting.cut_at_reply_marker(result.get("answer") or "")
        if outcome == "mislabelled":
            # Unlike upstream, a marker with nothing under it is silence: what
            # stands above a marker is working-out and never reaches the chat.
            answer = ""
        log(f"{key} #{decision['message_id']}: {name} answered in {seconds}s ({outcome}"
            + (", restricted" if restricted else "") + ")")
        if not answer.strip():
            return {"sent": [], "silent": True}
        return self.deliver(decision, target, answer)

    def deliver(self, decision: dict, target: dict, text: str, count: bool = True,
                markdown: bool = True) -> dict:
        """Send `text` in as many pieces as it takes, each tagged `#noreply` when
        the request was `#external`. The rate limit counts the answer once,
        however many pieces it took (`count` false: not at all). A piece whose
        send may or may not have arrived is never sent again: a rare loss is
        better than a duplicate, and the log says it is not sure it arrived.
        A notice (`markdown` false) is escaped, never read as Markdown: it
        carries names the service did not write."""
        key = decision["key"]
        mark = gate.NO_REPLY_MARKER if decision.get("external") else None
        reserve = len(f"\n\n{mark}") if mark else 0
        sent = []
        uncertain = 0
        cut = markup.pieces if markdown else markup.plain_pieces
        pieces = cut(text, prompting.TELEGRAM_TEXT_LIMIT - reserve)
        for i, (piece, plain) in enumerate(pieces):
            if mark:
                piece, plain = gate._with_marker(piece, mark), gate._with_marker(plain, mark)
            payload = dict(target)
            if i:
                payload.pop("reply_parameters", None)
            try:
                result = self._send_formatted(payload.pop("chat_id"), piece, plain, payload,
                                              f"{key} #{decision.get('message_id')}")
            except botapi.BotApiError as exc:
                if exc.kind == "uncertain":
                    uncertain += 1
                    self.uncertain_sends += 1
                    log(f"{key} #{decision.get('message_id')}: not sure it arrived "
                        f"(piece {i + 1}): {exc}; not sent again")
                    continue
                log(f"{key}: send failed: {exc.kind} {exc.status}: {exc}")
                if count:
                    self._tell_partial(target, len(sent), len(pieces))
                break
            sent.append(result)
            answered = False
            with self.lock:
                if count and len(sent) == 1:
                    self.guard.record_reply(key)
                    # Out of the journal as soon as the answer is: a kill after
                    # this point does not answer the request a second time.
                    answered = self.unfinished.pop(id(decision), None) is not None
                if isinstance(result, dict):
                    self.tails.add(key, prompting.tail_row(
                        result, f"@{self.me.get('username')}", mine=True, agent=True,
                        my_id=self.me.get("id")),
                        self.tail_size(key, decision["policy"]))
            if answered:
                self._journal_save_safe()
        self.sent.extend(sent)
        return {"sent": sent, "silent": False, "uncertain": uncertain}

    def _send_formatted(self, chat_id, html_text: str, plain: str, payload: dict,
                        where: str):
        """One piece as Telegram HTML; the same piece as plain text when
        Telegram refuses the HTML, which it does before sending anything."""
        try:
            return self.api.send_message(chat_id, html_text, parse_mode="HTML", **payload)
        except botapi.BotApiError as exc:
            if not markup.refused_markup(exc):
                raise
            log(f"{where}: formatting refused ({exc}); sent as plain text")
        return self.api.send_message(chat_id, plain, **payload)

    def _tell_partial(self, target: dict, sent: int, total: int) -> None:
        """One plain notice that an answer stopped part way, never retried."""
        payload = {k: v for k, v in target.items() if k != "reply_parameters"}
        try:
            self.api.send_message(payload.pop("chat_id"),
                                  PARTIAL_NOTICE.format(sent=sent, total=total), **payload)
        except Exception as exc:
            log(f"partial-answer notice not sent: {type(exc).__name__}: {exc}")

    # --- Run ----------------------------------------------------------------

    def run(self) -> None:
        self.adopt_settings(self.load_settings())
        self.check_profiles()
        recovered = False
        while not self.stopping:
            if self.reload_requested:
                self.reload()
            if self.parked:
                self.sleep(PARKED_SLEEP)
                continue
            if not self.take_lock():
                continue
            vanished = self.vanished() if self.vanished else None
            if vanished is not None:
                self.state, self.problem = "unhealthy", f"runtime file gone: {vanished}"
                log(f"exit: {self.problem}; a restart rebuilds the environment")
                self.write_health()
                raise RuntimeVanished(vanished)
            if not self.me and not self.connect():
                if not self.parked:
                    self.sleep(5.0)
                continue
            if not recovered:
                recovered = True
                self.recover()
            self.poll_once()

    def stop(self) -> None:
        self.stopping = True
        with self.lock:
            for cancel in self.cancels.values():
                cancel.set()
        for executor in [*self.retired, self.executor]:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            left = list(self.unfinished.values())
            self.unfinished.clear()
        for decision in left:
            log(f"{decision['key']} #{decision['message_id']}: stopped before its turn")
            self._notify(decision, STOPPED_NOTICE, count=False)
        self._journal_save_safe()
        if self.notices is not None:
            self.notices.shutdown(wait=True)
        self.state = "stopped"
        self.write_health()


# --- Process -----------------------------------------------------------------

class _Stop(Exception):
    pass


def term_handler(service: "Service"):
    """SIGTERM/SIGINT: the first stops the loop; a second one while the stop
    runs is logged and ignored, so the stop still answers what it caught and
    leaves its health at `stopped`."""
    def on_term(_sig, _frame):
        if service.stopping:
            log("stop: another signal while stopping; the stop runs to its end")
            return None
        service.stopping = True
        raise _Stop()
    return on_term


def _from_cli(cli: str, root: str, verb: str):
    def load():
        proc = subprocess.run([cli, "service", verb], cwd=root,
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise ValueError((proc.stderr or proc.stdout).strip()[:500]
                             or f"{verb} exited {proc.returncode}")
        return json.loads(proc.stdout)
    return load


def clear_turn_files(turn_dir: Path) -> int:
    """The authority files and outboxes a killed process left, removed at the
    next start: no turn of this bot is running yet."""
    count = 0
    for path in turn_dir.iterdir() if turn_dir.is_dir() else ():
        if path.is_file():
            with contextlib.suppress(OSError):
                path.unlink()
                count += 1
    return count


def bot_lock(expected_id):
    """A callable taking this bot's machine-wide lock, held for the life of
    the process: keyed by the bot, not the project, so two projects given one
    bot do not both poll it (upstream keys its lock by account)."""
    home = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    path = home / "telegram-bot" / "locks" / f"{expected_id or 'unknown'}.lock"
    held = {}

    def acquire() -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Opened without truncating: a refused second daemon must not erase the
        # holder's pid. Only the holder empties and writes it.
        handle = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()} {os.environ.get('TELEGRAM_BOT_PROJECT_ROOT', '')}\n")
        handle.flush()
        held["handle"] = handle
        return True
    return acquire


def main() -> int:
    global LOG_FILE
    token = os.environ.pop(TOKEN_ENV, "")
    root = os.environ["TELEGRAM_BOT_PROJECT_ROOT"]
    state_dir = Path(os.environ["TELEGRAM_BOT_STATE_DIR"])
    state_dir.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    os.chmod(state_dir, PRIVATE_DIR_MODE)
    clear_turn_files(state_dir / "turns")
    if os.environ.get("TELEGRAM_BOT_LOG_FILE"):
        LOG_FILE = Path(os.environ["TELEGRAM_BOT_LOG_FILE"])
    cli = os.environ["TELEGRAM_BOT_CLI"]
    expected = os.environ.get("TELEGRAM_BOT_EXPECTED_ID") or None
    context_load = _from_cli(cli, root, "context-document")
    service = Service(
        api=botapi.BotApi(token),
        load_settings=_from_cli(cli, root, "settings-document"),
        load_context=lambda: context_load().get("body") or "",
        project_root=root, project_id=os.environ.get("TELEGRAM_BOT_PROJECT_ID") or None,
        expected_id=expected,
        connection=os.environ.get("TELEGRAM_BOT_CONNECTION"),
        context_file=os.environ.get("TELEGRAM_BOT_CONTEXT"),
        health_file=state_dir / "health.json",
        journal_file=state_dir / "inflight.json",
        turn_dir=state_dir / "turns",
        overrides_file=state_dir / "overrides.json", state_dir=state_dir,
        acquire_lock=bot_lock(expected),
        launch_nonce=os.environ.pop("TELEGRAM_BOT_LAUNCH_NONCE", None))
    service.report_unclean_exit()
    (state_dir / "daemon.pid").write_text(f"{os.getpid()}\n")

    def on_hup(_sig, _frame):
        service.reload_requested = True

    on_term = term_handler(service)
    signal.signal(signal.SIGHUP, on_hup)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    code, ended = 0, None
    try:
        service.run()
    except _Stop:
        log("stop: signal received")
    except RuntimeVanished:
        code, ended = 5, "unhealthy"
    except Exception as exc:
        log(f"exit: {type(exc).__name__}: {exc}")
        code, ended = 5, "failed"
    finally:
        with contextlib.suppress(_Stop):
            service.stop()
        if code:
            service.state = ended
            service.write_health()
        with contextlib.suppress(OSError):
            (state_dir / "daemon.pid").unlink()
    return code


if __name__ == "__main__":
    sys.exit(check_restricted(sys.argv[2:]) if sys.argv[1:2] == ["check-restricted"]
             else main())
