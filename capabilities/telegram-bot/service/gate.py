"""Whether a bot answers a message, and where the answer lands.

A bot answers only what is addressed to it: an @mention of its username (or a
text mention of its id), a reply to one of its own messages, or a command
written `/command@username` (a bare `/command` counts in a private chat only,
because in a group it is ambiguous between the bots in the room). Each of the
three triggers is switched on or off per group with `triggers`, the same key
and the same names the upstream `telegram` assistant reads (`username`,
`reply`) plus `command`.

The message must also stand in an allowed place - a private chat with an
allowed user, or a group listed in `allowed_groups` and, where the group lists
`topics`, one of those topics - and come from an allowed sender: a person in
`allowed_users` or the group's `members`, or a bot listed the same way. A bot
is an agent whatever its entry says; a person is one when the entry carries
`"kind": "agent"`, as upstream marks a peer agent.

From an agent, a reply alone is context and never a turn: another turn needs an
explicit mention or command. This is upstream's rule for agent peers and the
first layer of the loop guard (`guard.py` holds the rest).

A message whose last line is `#noreply` is never a turn, and a request whose
last line is `#external` is marked so that everything sent for it carries
`#noreply` (upstream's `NO_REPLY_MARKER` and `EXTERNAL_REQUEST_MARKER`, read
by `_marker_line`, carried unchanged). A forwarded message whose origin is not
an allowed sender is material, never a request: only the forwarder's own
message can ask. A message whose `bot_command` entity at offset 0 names this
bot (`/status@this_bot`), or names no bot in a private chat, is a control
command for the daemon, never a worker turn (`command` in the decision); a
message that merely starts with `/` (`/tmp/build.log is failing @this_bot`), or
opens with another bot's command, is not one.

An answer to an agent goes out top-level rather than as a reply (upstream's
`_reply_target`): a reply would reach the other bot and invite a mechanical
reply loop, while a top-level answer reaches it only when it is mentioned.

A sender's role and name follow upstream's `sender_role_for` and
`_sender_profile`: the global `allowed_users` entry first, then the group's
`members` entry, then `direct_messages.default_role` in a private chat or the
group's `member_role`/`role`; the name is the configured `name` before the
one the sender chose.

Forum topics follow upstream's reading of the wire, in Bot API terms: a
message carrying `is_topic_message` belongs to `message_thread_id`; a message
in a forum that carries no thread belongs to General (topic 1) when the group
declares a `topics` map, and to the bare chat otherwise. An answer to a topic
message is sent into the same topic.

Nothing here touches the network, a file or a clock: every function reads the
update and the settings it is handed, so the suite drives each decision
directly.
"""

from __future__ import annotations

TRIGGERS = ("username", "reply", "command")
GENERAL_TOPIC_ID = 1
CHANNEL_TOPIC_MARKER = "#topic:"
DEFAULT_ROLE_DIRECT = "direct_user"
DEFAULT_ROLE_GROUP = "group_member"

# --- Taken from upstream telegram service/daemon.py, unchanged ---------------
EXTERNAL_REQUEST_MARKER = "#external"
NO_REPLY_MARKER = "#noreply"


def _marker_line(text, marker):
    """Whether a message carries a protocol tag, which is its last line alone.

    Position is what separates the tag from a conversation about it. Agents
    discuss these tokens — this repository's own guide names them — and an
    inline match would let a message explaining the protocol silence the daemon
    it explains.
    """
    lines = [line.strip() for line in str(text or "").strip().splitlines()]
    return bool(lines) and lines[-1] == marker


def _with_marker(text, marker):
    """One outgoing message with its tag on the last line of its own."""
    body = str(text or "").rstrip()
    return f"{body}\n\n{marker}" if body else marker
# --- End of upstream text ----------------------------------------------------


def _mapping(value):
    return value if isinstance(value, dict) else {}


# --- Places ------------------------------------------------------------------

def chat_key_candidates(chat_id) -> list[str]:
    """The keys a settings map may name one chat under: the Bot API id as is,
    and for a supergroup also its bare number without the -100 prefix, so a
    group copied from upstream settings (which accept either) is found. No
    other spelling: a bare number never reaches a basic group (-N), and a
    supergroup is never found under its id with only the minus dropped."""
    key = str(chat_id)
    out = [key]
    if key.startswith("-100") and len(key) > 4:
        out.append(key[4:])
    return out


def group_policy(settings, chat_id):
    """`(key, policy)` for an allowed group, or `(None, None)`."""
    groups = _mapping(_mapping(settings).get("allowed_groups"))
    for key in chat_key_candidates(chat_id):
        policy = groups.get(key)
        if isinstance(policy, dict):
            return key, policy
        if policy is True:
            return key, {}
    return None, None


def _topics(policy) -> dict | None:
    """The group's `topics` map, or None when it declares none. An empty map
    is a declared one: it lists no topic, so it admits none."""
    topics = _mapping(policy).get("topics")
    return topics if isinstance(topics, dict) else None


def topic_id(message, policy) -> int | None:
    """The forum topic a message belongs to, or None for a chat without one."""
    if message.get("is_topic_message") and message.get("message_thread_id"):
        try:
            return int(message["message_thread_id"])
        except (TypeError, ValueError):
            return None
    chat = _mapping(message.get("chat"))
    if chat.get("is_forum") and _topics(policy) is not None:
        return GENERAL_TOPIC_ID
    return None


def topic_policy(policy, topic) -> dict:
    if topic is None:
        return {}
    return _mapping(_mapping(_mapping(policy).get("topics")).get(str(topic)))


def place_allowed(policy, topic) -> bool:
    """A group with a `topics` map admits only those topics (none, for an empty
    map); one without a map admits the whole chat."""
    topics = _topics(policy)
    if topics is None:
        return True
    return topic is not None and str(topic) in topics


def channel_key(chat_id, topic=None) -> str:
    """One conversation's key: a chat, or one forum topic inside it - the same
    spelling upstream's job register uses."""
    base = str(chat_id)
    return base if topic is None else f"{base}{CHANNEL_TOPIC_MARKER}{int(topic)}"


ROOM_KEYS = ("profile", "brief", "worker_timeout", "tail_size", "name")


def merged_policy(settings, policy, topic, overlay=None) -> dict:
    """Group settings with the topic's entry laid over them, over `defaults`.
    `room_profile` is the profile the room itself names (topic, then group, then
    a direct user's entry), which outranks the environment's default profile;
    `profile` alone may still be the `defaults` one."""
    out = dict(_mapping(_mapping(settings).get("defaults")))
    room = {k: v for k, v in _mapping(policy).items() if k not in ("topics", "members")}
    room.update(topic_policy(policy, topic))
    room.update({k: v for k, v in _mapping(overlay).items() if k in ROOM_KEYS})
    out.update(room)
    out["room_profile"] = room.get("profile")
    return out


# --- Senders -----------------------------------------------------------------

def sender_entry(settings, policy, sender_id) -> dict | None:
    """The settings entry that admits a sender: the group's `members` entry over
    the global `allowed_users` one; None when neither names them."""
    sid = str(sender_id)
    member = _mapping(_mapping(policy).get("members")).get(sid)
    allowed = _mapping(_mapping(settings).get("allowed_users")).get(sid)
    if member is None and allowed is None:
        return None
    entry = dict(_mapping(allowed))
    entry.update(_mapping(member))
    return entry


def is_agent(sender: dict, entry: dict | None) -> bool:
    return bool(_mapping(sender).get("is_bot")) or \
        str(_mapping(entry).get("kind") or "").strip().lower() == "agent"


def sender_role(settings, policy, sender_id, is_direct: bool) -> str:
    """Upstream's `sender_role_for`: the global entry's role, then the
    member's, then the place's default."""
    sid = str(sender_id)
    allowed = _mapping(_mapping(_mapping(settings).get("allowed_users")).get(sid))
    member = _mapping(_mapping(_mapping(policy).get("members")).get(sid))
    if is_direct:
        default = (_mapping(_mapping(settings).get("direct_messages")).get("default_role")
                   or DEFAULT_ROLE_DIRECT)
    else:
        default = (_mapping(policy).get("member_role") or _mapping(policy).get("role")
                   or DEFAULT_ROLE_GROUP)
    return str(allowed.get("role") or member.get("role") or default)


def configured_name(settings, policy, sender_id) -> str | None:
    """The name the settings give a sender, global entry first, as upstream's
    job takes `allowed.name or member.name`."""
    sid = str(sender_id)
    allowed = _mapping(_mapping(_mapping(settings).get("allowed_users")).get(sid))
    member = _mapping(_mapping(_mapping(policy).get("members")).get(sid))
    name = allowed.get("name") or member.get("name")
    return str(name) if name else None


def sender_name(sender: dict, configured: str | None = None) -> str:
    if configured:
        return configured
    sender = _mapping(sender)
    name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p)
    if sender.get("username"):
        name = f"{name} (@{sender['username']})" if name else f"@{sender['username']}"
    return name or str(sender.get("id") or "unknown")


# --- Addressing --------------------------------------------------------------

def triggers(policy) -> frozenset:
    """The triggers a group leaves on: every one where it names none."""
    named = _mapping(policy).get("triggers")
    if not isinstance(named, list):
        return frozenset(TRIGGERS)
    return frozenset(str(one) for one in named if one in TRIGGERS)


def _text(message) -> tuple[str, list]:
    if message.get("text") is not None:
        return str(message.get("text") or ""), list(message.get("entities") or [])
    return str(message.get("caption") or ""), list(message.get("caption_entities") or [])


def _entity_text(text: str, entity: dict) -> str:
    # Offsets count UTF-16 code units, as the Bot API defines them.
    raw = text.encode("utf-16-le")
    start = int(entity.get("offset") or 0) * 2
    end = start + int(entity.get("length") or 0) * 2
    return raw[start:end].decode("utf-16-le", errors="replace")


def own_command(message, me: dict, *, private: bool) -> str | None:
    """`status` for a message opening with `/status@this_bot` (or a bare
    `/status` in a private chat): the command its `bot_command` entity at offset
    0 gives this bot, or None."""
    username = str(me.get("username") or "").lower()
    text, entities = _text(message)
    for entity in entities:
        if entity.get("type") == "bot_command" and int(entity.get("offset") or 0) == 0:
            name, _, target = _entity_text(text, entity).lstrip("/").partition("@")
            if (target and username and target.lower() == username) or (not target and private):
                return name.lower() or None
            return None
    return None


def addressing(message, me: dict, *, private: bool) -> set[str]:
    """Every way the message addresses this bot: `username`, `reply`, `command`."""
    found: set[str] = set()
    username = str(me.get("username") or "").lower()
    my_id = me.get("id")
    text, entities = _text(message)
    for entity in entities:
        kind = entity.get("type")
        if kind == "mention" and username:
            if _entity_text(text, entity).lstrip("@").lower() == username:
                found.add("username")
        elif kind == "text_mention":
            if _mapping(entity.get("user")).get("id") == my_id:
                found.add("username")
    if own_command(message, me, private=private):
        found.add("command")
    reply = _mapping(message.get("reply_to_message"))
    if reply and not reply.get("forum_topic_created") \
            and _mapping(reply.get("from")).get("id") == my_id:
        found.add("reply")
    return found


def forward_origin(message) -> dict | None:
    """`{"id", "name"}` for a forwarded message's origin, or None when the
    message is not forwarded. `id` is None for an origin that hides itself or
    is a chat or channel."""
    origin = _mapping(message.get("forward_origin"))
    if origin:
        kind = origin.get("type")
        if kind == "user":
            user = _mapping(origin.get("sender_user"))
            return {"id": user.get("id"), "name": sender_name(user)}
        if kind == "hidden_user":
            return {"id": None, "name": str(origin.get("sender_user_name") or "hidden user")}
        chat = _mapping(origin.get("sender_chat") or origin.get("chat"))
        return {"id": None, "name": str(chat.get("title") or chat.get("username")
                                        or chat.get("id") or "a chat")}
    legacy = _mapping(message.get("forward_from"))
    if legacy:
        return {"id": legacy.get("id"), "name": sender_name(legacy)}
    if message.get("forward_sender_name") or message.get("forward_from_chat"):
        return {"id": None, "name": str(message.get("forward_sender_name") or "a chat")}
    return None


def decide(update_message: dict, settings: dict, me: dict) -> dict:
    """The whole gate for one incoming message.

    Answers `{"answer": bool, "reason": str, ...}`; with `answer` true it also
    carries the conversation (`chat_id`, `topic_id`, `key`), the sender (`id`,
    `name`, `role`, `agent`), the triggers that fired and the policy in force
    for the place. A message the bot does not answer is still described as far
    as the gate got: `known_place` and `sender.allowed` tell the daemon whether
    it joins the tail and whether a human spoke there."""
    message = _mapping(update_message)
    chat = _mapping(message.get("chat"))
    sender = _mapping(message.get("from"))
    chat_id = chat.get("id")
    private = chat.get("type") == "private"
    text, _ = _text(message)
    out = {"answer": False, "chat_id": chat_id, "topic_id": None,
           "key": channel_key(chat_id), "message_id": message.get("message_id"),
           "sender": {"id": sender.get("id"), "name": sender_name(sender),
                      "agent": bool(sender.get("is_bot")), "role": None,
                      "allowed": False},
           "triggers": [], "policy": {}, "known_place": False,
           "external": _marker_line(text, EXTERNAL_REQUEST_MARKER),
           "forwarded_from": forward_origin(message), "command": None, "group": None}
    if sender.get("id") is not None and sender.get("id") == me.get("id"):
        out["reason"] = "own_message"
        return out
    if private:
        entry = sender_entry(settings, None, sender.get("id"))
        mode = str(_mapping(_mapping(settings).get("direct_messages")).get("mode")
                   or "allowed_users")
        out["known_place"] = True
        out["policy"] = merged_policy(settings, {}, None, entry)
        agent = is_agent(sender, entry)
        # `anyone` opens direct messages to people, not to bots: an unknown bot
        # would start turns no human in the chat could ever reset.
        allowed = entry is not None or (mode == "anyone" and not agent)
        out["sender"].update(agent=agent, allowed=allowed,
                             role=sender_role(settings, None, sender.get("id"), True),
                             name=sender_name(sender, configured_name(settings, None,
                                                                      sender.get("id"))))
        if mode == "off":
            out["reason"] = "direct_messages_off"
            return out
        if not allowed:
            out["reason"] = "bot_not_allowed" if agent else "sender_not_allowed"
            return out
        if entry is not None and entry.get("may_address") is False:
            out["reason"] = "sender_muted"
            return out
        return _admit(out, message, settings, None, text, ["direct"], "direct",
                      own_command(message, me, private=True))
    key, policy = group_policy(settings, chat_id)
    if policy is None:
        out["reason"] = "chat_not_allowed"
        return out
    topic = topic_id(message, policy)
    out.update(topic_id=topic, key=channel_key(chat_id, topic))
    if not place_allowed(policy, topic):
        out["reason"] = "topic_not_allowed"
        return out
    out["known_place"] = True
    out["group"] = policy
    out["policy"] = merged_policy(settings, policy, topic)
    entry = sender_entry(settings, policy, sender.get("id"))
    agent = is_agent(sender, entry)
    out["sender"].update(agent=agent, allowed=entry is not None,
                         role=sender_role(settings, policy, sender.get("id"), False),
                         name=sender_name(sender, configured_name(settings, policy,
                                                                  sender.get("id"))))
    fired = addressing(message, me, private=False) & triggers(out["policy"])
    if agent and fired == {"reply"}:
        # An agent's reply is context; another turn needs an explicit address.
        fired = set()
    if not fired:
        out["reason"] = "not_addressed"
        return out
    out["triggers"] = sorted(fired)
    if entry is None:
        out["reason"] = "bot_not_allowed" if agent else "sender_not_allowed"
        return out
    if entry.get("may_address") is False:
        out["reason"] = "sender_muted"
        return out
    return _admit(out, message, settings, policy, text, out["triggers"], "addressed",
                  own_command(message, me, private=False))


def _admit(out, message, settings, policy, text, fired, reason, command=None) -> dict:
    """The last checks every addressed message passes: `#noreply`, a forward
    from outside the allowed senders, and a control command."""
    if _marker_line(text, NO_REPLY_MARKER):
        out["reason"] = "noreply"
        return out
    origin = out.get("forwarded_from")
    if origin is not None and (origin.get("id") is None or sender_entry(
            settings, policy, origin["id"]) is None):
        out["reason"] = "forwarded_material"
        return out
    out.update(answer=True, reason=reason, triggers=list(fired), command=command)
    return out


def send_target(decision: dict, policy: dict | None = None) -> dict:
    """Where the answer goes: the same chat, the same topic, as a reply to the
    message that asked - unless an agent asked, which gets a top-level answer
    (upstream's `_reply_target`). General takes no thread id, as the Bot API
    wants."""
    target = {"chat_id": decision["chat_id"]}
    topic = decision.get("topic_id")
    if topic is not None and int(topic) != GENERAL_TOPIC_ID:
        target["message_thread_id"] = int(topic)
    if _mapping(decision.get("sender")).get("agent"):
        return target
    if decision.get("message_id") is not None:
        target["reply_parameters"] = {"message_id": decision["message_id"],
                                      "allow_sending_without_reply": True}
    return target
