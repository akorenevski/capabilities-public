"""What one bot connection resolves to, and what proves it is the bot it says.

A connection names one bot: its `username`, the `expected_bot_id` Telegram
gave it, and `secret_env`, the name of the environment key whose value is the
token. The token itself never sits in a record or on a command line: the key
resolves through the credential cascade the CLI hands in as `lookup`, a
callable `(key) -> (value, tier, source)`.

A token resolved from the project's own `.env.local` or `.env` is refused: a
worker turn runs inside the project with the project's permissions, so a file
there is one the agent can read. A bot token lives in the capability's
credentials.env in the user's config home, or on a server in the process
environment (`token_homes`).

`resolve` answers the resolved connection or raises `ConnectionProblem` with
the exit code and slug the CLI dies with; `check_me` compares what `getMe`
answered with what the connection expects. Neither touches the network, a file
or the process environment, so the suite drives both directly.
"""

from __future__ import annotations

import os
import re

TOKEN_SHAPE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
def token_homes() -> str:
    """Where a token may live, the config home as the CLI finds it."""
    config = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return (f"{config}/telegram-bot/credentials.env (`telegram-bot token set <id>`), "
            "or the process environment on a server")


class ConnectionProblem(Exception):
    def __init__(self, exit_code: int, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code
        self.message = message
        self.hint = hint


def env_key(cid: str) -> str:
    """The token key a connection is given by default: `TELEGRAM_BOT_<ID>_TOKEN`,
    the id upper-cased with every other character an underscore."""
    return "TELEGRAM_BOT_" + re.sub(r"[^A-Z0-9]", "_", str(cid).upper()) + "_TOKEN"


def _positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve(cid: str, entry: dict | None, lookup) -> dict:
    """`{id, username, expected_bot_id, secret_env, token, tier, source,
    allow_write}` for one connection, or ConnectionProblem."""
    entry = entry if isinstance(entry, dict) else {}
    username = str(entry.get("username") or "").strip().lstrip("@")
    if not username:
        raise ConnectionProblem(6, "username_required",
                                f"connection {cid!r} names no bot username",
                                "set `username` on the connection (the bot's @name without @)")
    expected = _positive_int(entry.get("expected_bot_id"))
    if expected is None:
        raise ConnectionProblem(6, "expected_bot_id_required",
                                f"connection {cid!r} has no positive expected_bot_id",
                                "the bot id is the number before the colon in its token; "
                                "`doctor` names it when it differs")
    key = str(entry.get("secret_env") or "").strip()
    if not key:
        raise ConnectionProblem(6, "secret_env_required",
                                f"connection {cid!r} names no secret_env for its token",
                                f"name the token's env key, e.g. {env_key(cid)}")
    token, tier, source = lookup(key)
    if not token:
        raise ConnectionProblem(2, "credentials_missing",
                                f"connection {cid!r}: {key} does not resolve",
                                f"set {key} in {token_homes()}")
    if tier == "project":
        raise ConnectionProblem(6, "token_in_project",
                                f"connection {cid!r}: {key} is set in {source}, inside the "
                                "project, where the project's agent can read it",
                                f"move it to {token_homes()}, then delete the line from {source}")
    if not TOKEN_SHAPE.match(token):
        raise ConnectionProblem(2, "token_malformed",
                                f"connection {cid!r}: {key} is not shaped like a bot token",
                                "a bot token is `<bot id>:<secret>` as BotFather gives it")
    if int(token.split(":", 1)[0]) != expected:
        raise ConnectionProblem(6, "token_for_other_bot",
                                f"connection {cid!r}: the token in {key} belongs to bot "
                                f"{token.split(':', 1)[0]}, not the expected {expected}",
                                "put this bot's token under the key, or correct expected_bot_id")
    return {"id": cid, "username": username, "expected_bot_id": expected,
            "secret_env": key, "token": token, "tier": tier,
            "source": str(source) if source else None,
            "allow_write": bool(entry.get("allow_write"))}


def check_me(resolved: dict, me) -> list[str]:
    """What `getMe` contradicts in the connection; empty when it is the bot."""
    if not isinstance(me, dict):
        return ["getMe answered no user"]
    problems = []
    if not me.get("is_bot"):
        problems.append("the token belongs to an account that is not a bot")
    if _positive_int(me.get("id")) != resolved["expected_bot_id"]:
        problems.append(f"getMe names bot {me.get('id')}, the connection expects "
                        f"{resolved['expected_bot_id']}")
    if str(me.get("username") or "").lower() != resolved["username"].lower():
        problems.append(f"getMe names @{me.get('username')}, the connection expects "
                        f"@{resolved['username']}")
    return problems


def capabilities_report(me: dict | None) -> dict:
    """The getMe flags enrolment depends on, named plainly."""
    me = me if isinstance(me, dict) else {}
    return {"can_join_groups": me.get("can_join_groups"),
            "reads_all_group_messages": me.get("can_read_all_group_messages"),
            "privacy_mode": (None if me.get("can_read_all_group_messages") is None
                             else not me.get("can_read_all_group_messages"))}
