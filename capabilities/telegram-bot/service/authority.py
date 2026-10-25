"""What a sender may use in a turn, and which chat commands they may give.

Carried from upstream `telegram` service/daemon.py (`_authority_policy_for`,
`_normalize_allowed_capabilities`, `_authority_summary`,
`write_authority_context`, and the control gate `_control_policy_for`,
`_control_commands_allow`, `_control_command_key`). The functions take the
settings they read as an argument instead of upstream's module globals; their
logic is upstream's, with one change: a settings document without an
`authority` key is held to `settings_check.DEFAULT_AUTHORITY` (upstream's
seeded template) instead of granting everything, so no sender reaches the
project's whole capability set by omission.

Each turn's authority is written to a private file (0600, a name never reused)
and handed to the worker as CAPABILITIES_AUTH_CONTEXT. Every managed
capability's contract gate reads it and refuses a capability outside
`allowed_capabilities` before it resolves a credential. A rule with
`"scope": "current_chat"` on `telegram-bot` is enforced twice: by the worker's
`telegram-bot` shim (`worker-bin/telegram-bot`, upstream's
`worker-bin/telegram`), and by the CLI's own `send`.

The envelope also says whether the turn is `restricted`, which this bot adds:
every turn is but a person's with the role `supervisor`, unless a policy on the
way (role, user, group, member) sets `restricted` itself. A restricted turn
runs on a restricted profile (`daemon.py`), which is what holds when a turn
sets out to get round the gate: the gate is a check each managed capability
makes on itself, and a turn with a shell can unset the variable it reads.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

import settings_check


def _as_mapping(value):
    return value if isinstance(value, dict) else {}


def _deep_merge(base, overlay):
    out = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _safe_file_part(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "chat"


# --- Capabilities -------------------------------------------------------------

def _policy_allowed_capabilities(policy):
    if not isinstance(policy, dict):
        return None
    if "allowed_capabilities" in policy:
        return policy.get("allowed_capabilities")
    return policy.get("capabilities")


def _normalize_capability_rule(rule):
    if rule is True or rule == "*":
        return True
    if rule in (False, None):
        return False
    if isinstance(rule, list):
        return {"allow": True, "verbs": rule}
    if isinstance(rule, dict):
        out = dict(rule)
        out.setdefault("allow", True)
        return out
    return bool(rule)


def _normalize_allowed_capabilities(value):
    if value is True or value == "*":
        return {"*": True}
    if isinstance(value, list):
        return {str(name): True for name in value}
    if isinstance(value, dict):
        return {
            str(name): _normalize_capability_rule(rule)
            for name, rule in value.items()
        }
    return {}


def policy_for(settings, request, group_policy, is_direct, connection=None) -> dict:
    """Upstream's `_authority_policy_for`: the role's policy, then the user's
    entry, then the group's, then the member's, each laid over the last."""
    settings = _as_mapping(settings)
    authority = _as_mapping(settings.get("authority")) if "authority" in settings \
        else settings_check.DEFAULT_AUTHORITY
    allowed_users = _as_mapping(settings.get("allowed_users"))
    role = request.get("sender_role") or ("direct_user" if is_direct else "group_member")
    policy = _deep_merge(
        _as_mapping(authority.get("default")),
        _as_mapping(_as_mapping(authority.get("roles")).get(role)),
    )

    sender_id = str(request.get("sender_id") or "")
    if sender_id in allowed_users and isinstance(allowed_users[sender_id], dict):
        row = allowed_users[sender_id]
        policy = _deep_merge(policy, _as_mapping(row.get("authority")))
        caps = _policy_allowed_capabilities(row)
        if caps is not None:
            policy["allowed_capabilities"] = caps

    if isinstance(group_policy, dict):
        policy = _deep_merge(policy, _as_mapping(group_policy.get("authority")))
        caps = _policy_allowed_capabilities(group_policy)
        if caps is not None:
            policy["allowed_capabilities"] = caps
        member = _as_mapping(_as_mapping(group_policy.get("members")).get(sender_id))
        policy = _deep_merge(policy, _as_mapping(member.get("authority")))
        caps = _policy_allowed_capabilities(member)
        if caps is not None:
            policy["allowed_capabilities"] = caps

    caps = _normalize_allowed_capabilities(_policy_allowed_capabilities(policy))
    restricted = policy.get("restricted")
    if not isinstance(restricted, bool):
        restricted = role != settings_check.SUPERVISOR or bool(request.get("agent"))
    return {
        "version": 1,
        "source": "telegram-bot",
        "connection": connection,
        "chat_id": request.get("chat_id"),
        "topic_id": request.get("topic_id"),
        "message_id": request.get("message_id"),
        "chat_type": "private" if is_direct else "group",
        "chat_name": (group_policy or {}).get("name") if isinstance(group_policy, dict) else None,
        "sender_id": sender_id,
        "sender_name": request.get("sender_name"),
        "sender_role": role,
        "allowed_capabilities": caps,
        "restricted": restricted,
    }


def summary(ctx):
    """Upstream's `_authority_summary`, for the prompt and the log."""
    if not ctx:
        return "not declared"
    caps = ctx.get("allowed_capabilities") or {}
    if caps.get("*") is True:
        return "all capabilities"
    bits = []
    for name, rule in sorted(caps.items()):
        if rule is False:
            continue
        if rule is True:
            bits.append(name)
            continue
        if isinstance(rule, dict):
            detail = []
            if rule.get("scope"):
                detail.append(f"scope={rule['scope']}")
            if rule.get("verbs"):
                detail.append("verbs=" + ",".join(map(str, rule["verbs"])))
            if rule.get("connections"):
                detail.append("connections=" + ",".join(map(str, rule["connections"])))
            bits.append(f"{name} ({'; '.join(detail)})" if detail else name)
    return ", ".join(bits) if bits else "no capabilities"


def write_context(authority, stem, directory) -> str:
    """Write one request authority envelope owner-only and without a reusable
    name. The path is handed to a child process, then unlinked by its owner."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix=f"{_safe_file_part(stem)}-", suffix=".json", dir=directory)
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(
                json.dumps(authority, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n")
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    return str(path)


# --- Control commands ---------------------------------------------------------

def command_key(command):
    key = str(command or "").strip().split("@", 1)[0].lower().lstrip("/")
    # Listing the profiles is reading the channel's settings, so it passes the
    # gate as /status does; choosing one is a /set.
    if key == "profiles":
        return "status"
    return key if key in {"status", "set", "reload", "stop"} else "help"


def _commands_allow(commands, command):
    if commands is True or commands == "*":
        return True
    key = command_key(command)
    if isinstance(commands, list):
        allowed = {str(item).strip().lower().lstrip("/") for item in commands}
        return "*" in allowed or key in allowed
    if isinstance(commands, dict):
        rule = commands.get(key, commands.get("*"))
        if rule is True or rule == "*":
            return True
        if isinstance(rule, dict):
            if rule.get("deny") is True:
                return False
            if rule.get("enabled") is False or rule.get("allow") is False:
                return False
            return True
    return False


def control_policy(settings, role, sender_id, group_policy=None):
    settings = _as_mapping(settings)
    control = _as_mapping(settings.get("control"))
    policy = _deep_merge(
        _as_mapping(_as_mapping(settings_check.CONTROL_DEFAULTS.get("roles")).get(role)),
        _as_mapping(_as_mapping(control.get("roles")).get(role)),
    )
    allowed_users = _as_mapping(settings.get("allowed_users"))
    sid = str(sender_id or "")
    if sid in allowed_users and isinstance(allowed_users[sid], dict):
        policy = _deep_merge(policy, _as_mapping(allowed_users[sid].get("control")))
    if isinstance(group_policy, dict):
        policy = _deep_merge(policy, _as_mapping(group_policy.get("control")))
        member = _as_mapping(_as_mapping(group_policy.get("members")).get(sid))
        policy = _deep_merge(policy, _as_mapping(member.get("control")))
    return policy


def command_allowed(settings, command, role, sender_id, group_policy=None) -> bool:
    policy = control_policy(settings, role, sender_id, group_policy)
    return _commands_allow(policy.get("commands"), command)
