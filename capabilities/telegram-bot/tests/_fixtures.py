"""Shared fixtures: where the service modules are, and Bot API shaped messages.

`TELEGRAM_BOT_SERVICE_DIR` points the suite at another copy of `service/`, which
is how a feature is shown missing: copy the bundle, take the feature out, run
the suite against the copy.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

CAPABILITY_DIR = Path(__file__).resolve().parents[1]
SERVICE_DIR = Path(os.environ.get("TELEGRAM_BOT_SERVICE_DIR") or CAPABILITY_DIR / "service")
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

ME = {"id": 7000000001, "is_bot": True, "username": "alpha_bot", "first_name": "Alpha"}
OTHER_BOT = {"id": 7000000002, "is_bot": True, "username": "beta_bot", "first_name": "Beta"}
ALICE = {"id": 1001, "is_bot": False, "first_name": "Alice", "username": "alice"}
STRANGER = {"id": 1999, "is_bot": False, "first_name": "Stranger"}
GROUP = -1001234567890
FORUM = -1009876543210

_next_id = [100]


def mention_entity(text: str, username: str) -> dict:
    handle = "@" + username
    start = text.index(handle)
    return {"type": "mention", "offset": len(text[:start].encode("utf-16-le")) // 2,
            "length": len(handle.encode("utf-16-le")) // 2}


def message(text: str, *, sender=ALICE, chat_id=GROUP, chat_type="supergroup",
            forum=False, topic=None, reply_to=None, entities=None, message_id=None) -> dict:
    if message_id is None:
        _next_id[0] += 1
        message_id = _next_id[0]
    out = {"message_id": message_id, "from": dict(sender),
           "chat": {"id": chat_id, "type": chat_type, "title": "Room", "is_forum": forum},
           "date": int(time.time()), "text": text, "entities": list(entities or [])}
    if topic is not None:
        out["is_topic_message"] = True
        out["message_thread_id"] = topic
    if reply_to is not None:
        out["reply_to_message"] = reply_to
    return out


def addressed(text_after: str = "hello", *, to=ME, **kw) -> dict:
    text = f"@{to['username']} {text_after}"
    return message(text, entities=[mention_entity(text, to["username"])], **kw)


def settings(**over) -> dict:
    base = {
        "direct_messages": {"mode": "allowed_users"},
        "allowed_users": {str(ALICE["id"]): {"role": "supervisor", "kind": "human"}},
        "allowed_groups": {
            str(GROUP): {"members": {str(OTHER_BOT["id"]): {"kind": "agent"}}},
            str(FORUM): {"topics": {"141": {"name": "Pilot", "brief": "pilot room",
                                            "profile": "telegram-claude"}},
                         "members": {str(OTHER_BOT["id"]): {"kind": "agent"}}},
        },
        # No debounce: the suite drives turns one call at a time.
        "defaults": {"profile": "stub", "debounce": 0, "agent_dialogue": {"max_turns": 3}},
    }
    base.update(over)
    return base
