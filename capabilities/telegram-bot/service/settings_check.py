"""The service settings document: how it composes and what makes it unusable.

One document per scope, `service/settings.json`, project over global by
top-level key, exactly as the contract's records adapter merges a project's
settings over the global ones. The shapes follow upstream `telegram`'s service
settings wherever the two mean the same thing - `connection`,
`direct_messages` (`mode`, `default_role`), `allowed_users`, `allowed_groups`
with `topics`, `members`, `triggers`, `agent_dialogue` and `profile`,
`control`, `authority`, and `defaults` with `tail_size`, `worker_timeout`,
`progress_after`, `debounce` and `profile` - so an entry copied from a telegram
room reads the same here for every key the two share. A key only upstream reads
(`sync_interval`, `group_aliases` and the like) is refused like any other
unknown key, and has to be dropped from a copied entry. Keys this bot
adds: `rate_limit`, `brief`, `restricted_profile`, the authority policy's
`restricted`, `defaults.poll_timeout` and `defaults.max_parallel`.

The check is upstream `settings_schema.py`'s, strict in the same ways: an
unknown key anywhere is refused with the keys that are supported (a typo such
as `allowed_grups` or `may_adress` would otherwise be a setting nobody reads),
every number has bounds (`DEFAULTS_NUMBERS`), booleans are booleans, a user or
member id is a positive integer and a group id a non-zero one, and triggers
and control commands are names from their lists.

`problems` answers what the daemon would refuse to adopt, one sentence each;
`warnings` what it adopts but a person should know they chose. `SEED` is what
`service init` writes, with upstream's seeded `authority` and `control`
(templates/settings.json); `DEFAULT_AUTHORITY` is what a document without an
`authority` key is held to, so a project seeded before it existed is not left
with every capability open to every sender. Outside the supervisor's, the
seeded grants carry verb lists: the contract gate passes every verb of a
capability granted without one, so a bare `telegram-bot` grant would let a
group member's turn change the bot's own settings, connections and token.

A turn whose authority says `restricted` (every sender but a person with the
role `supervisor`, unless a policy sets `restricted` itself) runs on a
restricted claude profile, whatever harness the profile in force uses:
`restricted_profile` where the room or `defaults` names one, else
`RESTRICTED_PROFILE`. Codex has no restricted profile: its read-only sandbox
still reads the whole disk.

A person entry is a person unless it says `"kind": "agent"`. An assistant that
speaks from a personal Telegram account (not a bot) is not marked by Telegram,
so it must be listed `"kind": "agent"`; listed as a supervisor person it gets
unrestricted turns and no dialogue cap. `warnings` names every supervisor entry
without a `kind`, since an assistant can be called anything.
"""

from __future__ import annotations

import re

DIRECT_MODES = ("allowed_users", "anyone", "off")
TRIGGER_NAMES = ("username", "reply", "command")
CONTROL_COMMANDS = ("status", "set", "reload", "stop", "help", "*")
MEMBER_KINDS = ("human", "agent")
DEFAULT_WORKER_TIMEOUT = 600
DEFAULT_POLL_TIMEOUT = 20
DEFAULT_PROGRESS_AFTER = 15
DEFAULT_DEBOUNCE = 3         # upstream's default when a room names none

TOP_LEVEL = {"connection", "direct_messages", "allowed_users", "allowed_groups", "control",
             "authority", "defaults"}
DIRECT_MESSAGE_KEYS = {"mode", "default_role"}
GRANT_KEYS = {"control", "authority", "allowed_capabilities", "capabilities"}
PERSON_KEYS = {"name", "username", "role", "kind", "may_address"} | GRANT_KEYS
# A user entry is the only declaration of the chat a person is alone in, so
# that chat's room settings belong there, as upstream's USER_KEYS.
USER_KEYS = PERSON_KEYS | {"profile", "brief", "worker_timeout", "tail_size"}
MEMBER_KEYS = PERSON_KEYS
ROOM_KEYS = {"name", "profile", "restricted_profile", "brief", "worker_timeout", "tail_size",
             "agent_dialogue", "rate_limit", "triggers", "debounce"}
TOPIC_KEYS = ROOM_KEYS
GROUP_KEYS = ROOM_KEYS | GRANT_KEYS | {"role", "member_role", "members", "topics"}
DEFAULTS_KEYS = {"profile", "restricted_profile", "tail_size", "worker_timeout",
                 "poll_timeout", "max_parallel", "progress_after", "agent_dialogue",
                 "rate_limit", "debounce"}
AGENT_DIALOGUE_KEYS = {"max_turns", "reset_on_human_message"}
RATE_LIMIT_KEYS = {"replies_per_minute", "replies_per_minute_total"}
AUTHORITY_KEYS = {"default", "roles"}
AUTHORITY_POLICY_KEYS = {"allowed_capabilities", "capabilities", "restricted"}
CAPABILITY_RULE_KEYS = {"allow", "deny", "enabled", "scope", "verbs", "connections"}
CONTROL_KEYS = {"roles"}
CONTROL_RULE_KEYS = {"commands"}
COMMAND_RULE_KEYS = {"allow", "deny", "enabled"}
CAPABILITY_SCOPES = ("current_chat",)
# The bundled profile every restricted turn runs on where no
# `restricted_profile` names another, whatever the profile in force.
RESTRICTED_PROFILE = "telegram-claude-restricted"
SUPERVISOR = "supervisor"

# (minimum, maximum, integer) of each number, by key, wherever it sits.
NUMBERS = {
    "tail_size": (1, 500, True),
    "worker_timeout": (1, 3600, False),
    "poll_timeout": (1, 50, True),       # Telegram's own ceiling on a long poll
    "max_parallel": (1, 32, True),
    "progress_after": (0, 3600, False),
    "debounce": (0, 300, False),
    "max_turns": (0, 100, True),
    "replies_per_minute": (0, 600, True),
    "replies_per_minute_total": (0, 6000, True),
}

CONTROL_DEFAULTS = {
    "roles": {
        "supervisor": {"commands": ["status", "set", "reload", "stop", "help"]},
        "channel_admin": {"commands": ["status", "set", "help"]},
        "direct_user": {"commands": ["status", "help"]},
        "group_member": {"commands": ["status", "help"]},
    }
}
# The read-only verbs of `routine`; `save` and `retire` change the project.
ROUTINE_READ_VERBS = ["list", "show", "validate", "render", "history", "diff"]
DEFAULT_AUTHORITY = {
    "roles": {
        "supervisor": {"allowed_capabilities": {"*": True}},
        "direct_user": {"allowed_capabilities": {}},
        "group_member": {"allowed_capabilities": {
            # `send` alone: `progress` is the worker shim's own verb and never
            # reaches the CLI's gate.
            "telegram-bot": {"scope": "current_chat", "verbs": ["send"]},
            "routine": ROUTINE_READ_VERBS,
        }},
    }
}

SEED = {
    "connection": None,
    "direct_messages": {"mode": "allowed_users", "default_role": "direct_user"},
    "allowed_users": {},
    "allowed_groups": {},
    "control": CONTROL_DEFAULTS,
    "authority": DEFAULT_AUTHORITY,
    "defaults": {
        "profile": "stub",
        "tail_size": 40,
        "worker_timeout": DEFAULT_WORKER_TIMEOUT,
        "poll_timeout": DEFAULT_POLL_TIMEOUT,
        "progress_after": DEFAULT_PROGRESS_AFTER,
        "debounce": 5,
        "agent_dialogue": {"max_turns": 3, "reset_on_human_message": True},
        "rate_limit": {"replies_per_minute": 6, "replies_per_minute_total": 20},
    },
}


def merged(global_doc, project_doc) -> dict:
    out = dict(global_doc) if isinstance(global_doc, dict) else {}
    if isinstance(project_doc, dict):
        out.update(project_doc)
    return out


class _Check:
    def __init__(self):
        self.out: list[str] = []

    def fail(self, where, message):
        self.out.append(f"{where}: {message}")

    def object(self, value, where) -> bool:
        if not isinstance(value, dict):
            self.fail(where, "must be a JSON object")
            return False
        return True

    def unknown(self, value, allowed, where):
        for key in value:
            if key not in allowed:
                self.fail(f"{where}.{key}", f"unsupported property {key!r}; supported "
                          f"properties: {', '.join(sorted(allowed))}")

    def string(self, value, where, *, nonempty=True):
        if not isinstance(value, str) or (nonempty and not value.strip()):
            self.fail(where, "must be a non-empty string" if nonempty else "must be a string")

    def boolean(self, value, where):
        if not isinstance(value, bool):
            self.fail(where, "must be a boolean (true or false, not a string)")

    def number(self, value, where, key):
        low, high, integer = NUMBERS[key]
        kind = int if integer else (int, float)
        if isinstance(value, bool) or not isinstance(value, kind):
            self.fail(where, "must be " + ("an integer" if integer else "a number"))
        elif not low <= value <= high:
            self.fail(where, f"must be between {low} and {high}")

    def enum(self, value, choices, where):
        if not isinstance(value, str) or value not in choices:
            self.fail(where, "must be one of: " + ", ".join(choices))

    def numbers(self, block, where, keys):
        for key in keys & set(block) & set(NUMBERS):
            self.number(block[key], f"{where}.{key}", key)

    # --- Blocks ---------------------------------------------------------------

    def room(self, block, where, allowed):
        self.unknown(block, allowed, where)
        self.numbers(block, where, allowed)
        for key in ("name", "profile", "restricted_profile", "role", "member_role", "username"):
            if key in block and key in allowed:
                self.string(block[key], f"{where}.{key}")
        if "brief" in block:
            self.string(block["brief"], f"{where}.brief", nonempty=False)
        if "triggers" in block:
            triggers = block["triggers"]
            if not isinstance(triggers, list) or any(t not in TRIGGER_NAMES for t in triggers):
                self.fail(f"{where}.triggers",
                          f"must be a list of {', '.join(TRIGGER_NAMES)}")
        dialogue = block.get("agent_dialogue")
        if "agent_dialogue" in block and self.object(dialogue, f"{where}.agent_dialogue"):
            self.unknown(dialogue, AGENT_DIALOGUE_KEYS, f"{where}.agent_dialogue")
            self.numbers(dialogue, f"{where}.agent_dialogue", AGENT_DIALOGUE_KEYS)
            if "reset_on_human_message" in dialogue:
                self.boolean(dialogue["reset_on_human_message"],
                             f"{where}.agent_dialogue.reset_on_human_message")
        rate = block.get("rate_limit")
        if "rate_limit" in block and self.object(rate, f"{where}.rate_limit"):
            self.unknown(rate, RATE_LIMIT_KEYS, f"{where}.rate_limit")
            self.numbers(rate, f"{where}.rate_limit", RATE_LIMIT_KEYS)
        self.grants(block, where)

    def grants(self, block, where):
        if "control" in block:
            self.control(block["control"], f"{where}.control")
        if "authority" in block:
            self.policy(block["authority"], f"{where}.authority")
        for key in ("allowed_capabilities", "capabilities"):
            if key in block:
                self.capabilities(block[key], f"{where}.{key}")

    def person(self, block, where, allowed):
        if not self.object(block, where):
            return
        self.room(block, where, allowed)
        if "kind" in block:
            self.enum(block["kind"], MEMBER_KINDS, f"{where}.kind")
        if "may_address" in block:
            self.boolean(block["may_address"], f"{where}.may_address")

    def people(self, block, where, allowed):
        if not self.object(block, where):
            return
        for key, entry in block.items():
            if not _int_key(key, positive=True):
                self.fail(f"{where}.{key}", "is not a Telegram user id (a positive integer)")
                continue
            self.person(entry, f"{where}.{key}", allowed)

    def capabilities(self, value, where):
        if value is True or value == "*":
            return
        if isinstance(value, list):
            for name in value:
                self.string(name, f"{where}[]")
            return
        if not self.object(value, where):
            return
        for name, rule in value.items():
            here = f"{where}.{name}"
            if isinstance(rule, bool) or rule == "*":
                continue
            if isinstance(rule, list):
                if not all(isinstance(v, str) for v in rule):
                    self.fail(here, "a verb list holds verb names")
                continue
            if not self.object(rule, here):
                continue
            self.unknown(rule, CAPABILITY_RULE_KEYS, here)
            for key in ("allow", "deny", "enabled"):
                if key in rule:
                    self.boolean(rule[key], f"{here}.{key}")
            if "scope" in rule:
                self.enum(rule["scope"], CAPABILITY_SCOPES, f"{here}.scope")
            for key in ("verbs", "connections"):
                if key in rule and (not isinstance(rule[key], list)
                                    or not all(isinstance(v, str) for v in rule[key])):
                    self.fail(f"{here}.{key}", "must be a list of names")

    def policy(self, value, where):
        if not self.object(value, where):
            return
        self.unknown(value, AUTHORITY_POLICY_KEYS, where)
        for key in {"allowed_capabilities", "capabilities"} & set(value):
            self.capabilities(value[key], f"{where}.{key}")
        if "restricted" in value:
            self.boolean(value["restricted"], f"{where}.restricted")

    def authority(self, value, where):
        if not self.object(value, where):
            return
        self.unknown(value, AUTHORITY_KEYS, where)
        if "default" in value:
            self.policy(value["default"], f"{where}.default")
        roles = value.get("roles")
        if "roles" in value and self.object(roles, f"{where}.roles"):
            for role, policy in roles.items():
                self.policy(policy, f"{where}.roles.{role}")

    def commands(self, value, where):
        if value is True or value == "*":
            return
        if isinstance(value, list):
            for name in value:
                if str(name).lower().lstrip("/") not in CONTROL_COMMANDS:
                    self.fail(f"{where}[]", f"{name!r} is not one of: "
                              f"{', '.join(CONTROL_COMMANDS)}")
            return
        if not self.object(value, where):
            return
        for name, rule in value.items():
            if str(name).lower().lstrip("/") not in CONTROL_COMMANDS:
                self.fail(f"{where}.{name}", f"is not one of: {', '.join(CONTROL_COMMANDS)}")
            if isinstance(rule, dict):
                self.unknown(rule, COMMAND_RULE_KEYS, f"{where}.{name}")
            elif not isinstance(rule, bool) and rule != "*":
                self.fail(f"{where}.{name}", "must be true, false or an object")

    def control_rule(self, value, where):
        if not self.object(value, where):
            return
        self.unknown(value, CONTROL_RULE_KEYS, where)
        if "commands" in value:
            self.commands(value["commands"], f"{where}.commands")

    def control(self, value, where):
        if not self.object(value, where):
            return
        allowed = CONTROL_KEYS | CONTROL_RULE_KEYS
        self.unknown(value, allowed, where)
        if "commands" in value:
            self.commands(value["commands"], f"{where}.commands")
        roles = value.get("roles")
        if "roles" in value and self.object(roles, f"{where}.roles"):
            for role, rule in roles.items():
                self.control_rule(rule, f"{where}.roles.{role}")


def _int_key(key, *, positive: bool) -> bool:
    try:
        number = int(str(key))
    except ValueError:
        return False
    return number > 0 if positive else number != 0


def problems(document) -> list[str]:
    if not isinstance(document, dict):
        return ["the settings document must be a JSON object"]
    check = _Check()
    check.unknown(document, TOP_LEVEL, "settings")
    if document.get("connection") is not None:
        check.string(document["connection"], "connection")
    direct = document.get("direct_messages")
    if direct is not None and check.object(direct, "direct_messages"):
        check.unknown(direct, DIRECT_MESSAGE_KEYS, "direct_messages")
        if "mode" in direct:
            check.enum(direct["mode"], DIRECT_MODES, "direct_messages.mode")
        if "default_role" in direct:
            check.string(direct["default_role"], "direct_messages.default_role")
    if document.get("allowed_users") is not None:
        check.people(document["allowed_users"], "allowed_users", USER_KEYS)
    groups = document.get("allowed_groups")
    if groups is not None and check.object(groups, "allowed_groups"):
        for chat, block in groups.items():
            where = f"allowed_groups.{chat}"
            if not _int_key(chat, positive=False):
                check.fail(where, "is not a Telegram chat id (a non-zero integer)")
                continue
            if block is True:
                continue
            if not check.object(block, where):
                continue
            check.room(block, where, GROUP_KEYS)
            if block.get("members") is not None:
                check.people(block["members"], f"{where}.members", MEMBER_KEYS)
            topics = block.get("topics")
            if topics is not None and check.object(topics, f"{where}.topics"):
                for topic, entry in topics.items():
                    here = f"{where}.topics.{topic}"
                    if not _int_key(topic, positive=True):
                        check.fail(here, "is not a topic id (a positive integer)")
                    if entry in (None, True):
                        continue
                    if check.object(entry, here):
                        check.room(entry, here, TOPIC_KEYS)
    if document.get("control") is not None:
        check.control(document["control"], "control")
    if document.get("authority") is not None:
        check.authority(document["authority"], "authority")
    defaults = document.get("defaults")
    if defaults is not None and check.object(defaults, "defaults"):
        check.room(defaults, "defaults", DEFAULTS_KEYS)
    return check.out


def _whole_grants(caps) -> list[str]:
    """The capabilities a grant gives with every verb: a bare name, `true`, or
    a rule without `verbs`."""
    if caps is True or caps == "*":
        return ["*"]
    if isinstance(caps, list):
        return [str(name) for name in caps]
    out = []
    for name, rule in (caps.items() if isinstance(caps, dict) else ()):
        if rule is True or rule == "*" or (isinstance(rule, dict) and "verbs" not in rule
                                           and rule.get("allow") is not False
                                           and rule.get("deny") is not True
                                           and rule.get("enabled") is not False):
            out.append(str(name))
    return out


# A name or username that reads as an agent's rather than a person's.
_AGENT_NAME = re.compile(r"(bot$|agent|assistant)", re.IGNORECASE)


def _people(doc) -> list[tuple[str, dict]]:
    """Every person entry, with where it sits."""
    out = [(f"allowed_users.{uid}", entry)
           for uid, entry in (doc.get("allowed_users") or {}).items()
           if isinstance(entry, dict)] if isinstance(doc.get("allowed_users"), dict) else []
    groups = doc.get("allowed_groups")
    for chat, block in (groups.items() if isinstance(groups, dict) else ()):
        members = block.get("members") if isinstance(block, dict) else None
        out += [(f"allowed_groups.{chat}.members.{uid}", entry)
                for uid, entry in (members.items() if isinstance(members, dict) else ())
                if isinstance(entry, dict)]
    return out


def _grant_places(doc) -> list[tuple[str, object]]:
    """Every grant outside the supervisor's: the authority roles and default,
    and a group's, a topic's, a member's or a user's own grant."""
    authority = doc.get("authority")
    roles = authority.get("roles") if isinstance(authority, dict) else None
    places = [("authority.default", (authority or {}).get("default"))] \
        if isinstance(authority, dict) else []
    places += [(f"authority.roles.{role}", policy) for role, policy in
               (roles.items() if isinstance(roles, dict) else ()) if role != SUPERVISOR]
    blocks = [(where, entry) for where, entry in _people(doc)
              if entry.get("role") != SUPERVISOR]
    groups = doc.get("allowed_groups")
    for chat, block in (groups.items() if isinstance(groups, dict) else ()):
        if not isinstance(block, dict):
            continue
        blocks.append((f"allowed_groups.{chat}", block))
        topics = block.get("topics")
        blocks += [(f"allowed_groups.{chat}.topics.{topic}", entry)
                   for topic, entry in (topics.items() if isinstance(topics, dict) else ())
                   if isinstance(entry, dict)]
    for where, block in blocks:
        places.append((where, block))
        if isinstance(block.get("authority"), dict):
            places.append((f"{where}.authority", block["authority"]))
    return places


def warnings(document) -> list[str]:
    """Settings that are valid and dangerous, one sentence each."""
    out = []
    doc = document if isinstance(document, dict) else {}
    direct = doc.get("direct_messages")
    if isinstance(direct, dict) and direct.get("mode") == "anyone":
        out.append("direct_messages.mode is `anyone`: any Telegram user who messages the bot "
                   "gets an agent turn in this project, on its restricted profile and with "
                   "the direct_user authority")
    for where, entry in _people(doc):
        if entry.get("role") != SUPERVISOR or entry.get("kind"):
            continue
        # Every one, not only a name that looks like an agent's: an assistant
        # can be called anything ("Atlas").
        named = " ".join(str(entry.get(k) or "") for k in ("name", "username")).strip()
        looks = _AGENT_NAME.search(str(entry.get("username") or "")) or \
            re.search(r"agent|assistant", named, re.IGNORECASE)
        out.append(f"{where} is a supervisor" + (f" ({named})" if named else "")
                   + (", and its name looks like an agent's" if looks else "")
                   + ", with no \"kind\": an assistant on a personal Telegram account is "
                   "not marked by Telegram, so list it with \"kind\": \"agent\" or its turns "
                   "run unrestricted and uncapped; a person says \"kind\": \"human\"")
    for where, policy in _grant_places(doc):
        if not isinstance(policy, dict):
            continue
        caps = policy.get("allowed_capabilities", policy.get("capabilities"))
        whole = [name for name in _whole_grants(caps) if name in ("*", "telegram-bot")]
        if whole:
            out.append(f"{where} grants {whole[0]} without a verb list: a turn of that role "
                       "may change this bot's settings, connections, token and service; "
                       "give `telegram-bot` `\"verbs\": [\"send\"]`")
    return out


def restricted_profile_names(document) -> list[dict]:
    """Every `restricted_profile` the settings name, with where it is named."""
    found: dict[str, list[str]] = {}
    doc = document if isinstance(document, dict) else {}

    def note(block, where):
        name = block.get("restricted_profile") if isinstance(block, dict) else None
        if isinstance(name, str) and name:
            found.setdefault(name, []).append(where)
    note(doc.get("defaults"), "defaults.restricted_profile")
    for chat, block in (doc.get("allowed_groups") or {}).items():
        if isinstance(block, dict):
            note(block, f"allowed_groups.{chat}.restricted_profile")
            for topic, entry in (block.get("topics") or {}).items():
                note(entry, f"allowed_groups.{chat}.topics.{topic}.restricted_profile")
    return [{"profile": name, "declared_at": where} for name, where in found.items()]


def profile_names(document) -> list[dict]:
    """Every profile name the settings reach, with where it is named."""
    found: dict[str, list[str]] = {}

    def note(name, where):
        if isinstance(name, str) and name:
            found.setdefault(name, []).append(where)
    doc = document if isinstance(document, dict) else {}
    note((doc.get("defaults") or {}).get("profile") or "stub", "defaults.profile")
    for user, entry in (doc.get("allowed_users") or {}).items():
        if isinstance(entry, dict):
            note(entry.get("profile"), f"allowed_users.{user}.profile")
    for chat, block in (doc.get("allowed_groups") or {}).items():
        if isinstance(block, dict):
            note(block.get("profile"), f"allowed_groups.{chat}.profile")
            for topic, entry in (block.get("topics") or {}).items():
                if isinstance(entry, dict):
                    note(entry.get("profile"), f"allowed_groups.{chat}.topics.{topic}.profile")
    return [{"profile": name, "declared_at": where} for name, where in found.items()]
