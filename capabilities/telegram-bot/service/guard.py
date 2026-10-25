"""The loop guard: what keeps bots from answering each other forever.

Telegram's Bot-to-Bot Communication Mode lets a bot hear another bot that
mentions it or replies to it, and warns that two bots configured to answer each
other will loop. Three things here bound that, beside the gate's rule that an
agent's bare reply is never a turn:

- **Agent dialogue cap.** Each conversation (a chat, or one forum topic)
  counts the turns an agent started; once `agent_dialogue.max_turns` is
  reached, agents are not answered there until a human speaks, which resets
  the count (`reset_on_human_message`, on unless set false). The policy shape
  and the counting are upstream `telegram`'s (`_agent_dialogue_policy`,
  `_reset_agent_dialogue_for_human`, `_admit_agent_turn`), held per bot here.
  Upstream treats a missing cap as none; a bot's guard ships on, so a missing
  `max_turns` means `DEFAULT_MAX_TURNS` and only an explicit 0 lifts it.
- **Seen messages.** Every update id and every `(chat, message)` pair is
  remembered for a bounded window, so a redelivered update never starts a
  second turn.
- **Rate limit.** At most `rate_limit.replies_per_minute` answers per
  conversation in any sliding minute, and `rate_limit.replies_per_minute_total`
  across the bot; an answer counts once however many pieces it is sent in, and
  the daemon counts the requests running and waiting beside the answers sent,
  so a burst during a turn cannot queue past the limit. A request over either
  is dropped and logged, never queued (the daemon tells a person so, once a
  minute).

State lives in memory for the life of the daemon, for at most
`CONVERSATIONS_LIMIT` conversations (the least recently used dropped first,
so a bot open to anyone does not grow a map per stranger): Telegram itself holds the
unconfirmed updates across a restart, and a restart is a human-visible event
that may fairly reset a dialogue count. The clock is injected so the suite
drives time.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque

DEFAULT_MAX_TURNS = 3
DEFAULT_REPLIES_PER_MINUTE = 6
DEFAULT_REPLIES_PER_MINUTE_TOTAL = 20
SEEN_LIMIT = 5000
CONVERSATIONS_LIMIT = 1000
WINDOW_SECONDS = 60.0


def _mapping(value):
    return value if isinstance(value, dict) else {}


def dialogue_policy(policy) -> dict:
    """`agent_dialogue` as upstream reads it, with the bot's default cap."""
    block = _mapping(_mapping(policy).get("agent_dialogue"))
    raw = block.get("max_turns", DEFAULT_MAX_TURNS)
    try:
        max_turns = max(0, int(raw if raw is not None else DEFAULT_MAX_TURNS))
    except (TypeError, ValueError):
        max_turns = DEFAULT_MAX_TURNS
    return {"max_turns": max_turns,
            "reset_on_human_message": block.get("reset_on_human_message") is not False}


def rate_policy(policy) -> dict:
    block = _mapping(_mapping(policy).get("rate_limit"))

    def number(key, default):
        try:
            return max(0, int(block.get(key, default)))
        except (TypeError, ValueError):
            return default
    return {"per_conversation": number("replies_per_minute", DEFAULT_REPLIES_PER_MINUTE),
            "total": number("replies_per_minute_total", DEFAULT_REPLIES_PER_MINUTE_TOTAL)}


class Guard:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.dialogue: OrderedDict[str, dict] = OrderedDict()
        self.seen_updates: OrderedDict = OrderedDict()
        self.seen_messages: OrderedDict = OrderedDict()
        self.sent: OrderedDict[str, deque] = OrderedDict()
        self.sent_total: deque = deque()

    # --- Seen ----------------------------------------------------------------

    @staticmethod
    def _remember(store: OrderedDict, key) -> bool:
        """True the first time `key` is seen; the oldest entries fall away."""
        if key in store:
            store.move_to_end(key)
            return False
        store[key] = True
        while len(store) > SEEN_LIMIT:
            store.popitem(last=False)
        return True

    def first_update(self, update_id) -> bool:
        return self._remember(self.seen_updates, update_id)

    def first_message(self, chat_id, message_id) -> bool:
        return self._remember(self.seen_messages, (str(chat_id), message_id))

    # --- Agent dialogue -------------------------------------------------------

    @staticmethod
    def _slot(store: OrderedDict, key: str, fresh):
        """`store[key]`, made with `fresh()` if new, and the least recently
        used conversations dropped past `CONVERSATIONS_LIMIT`."""
        if key not in store:
            store[key] = fresh()
        store.move_to_end(key)
        while len(store) > CONVERSATIONS_LIMIT:
            store.popitem(last=False)
        return store[key]

    def state(self, key: str) -> dict:
        return self._slot(self.dialogue, key, lambda: {"turns": 0})

    def human_spoke(self, key: str, policy, message_id=None) -> bool:
        """A human message in the conversation resets the agent count, addressed
        or not. True when there was something to reset."""
        rule = dialogue_policy(policy)
        if not rule["max_turns"] or not rule["reset_on_human_message"]:
            return False
        state = self.state(key)
        if not state.get("turns"):
            return False
        state.update(turns=0, reset_message_id=message_id)
        return True

    def admit_agent_turn(self, key: str, policy, sender_id=None,
                         message_id=None) -> tuple[bool, dict | None]:
        """Consume one agent turn, or refuse once the cap is full."""
        rule = dialogue_policy(policy)
        if not rule["max_turns"]:
            return True, None
        state = self.state(key)
        turns = max(0, int(state.get("turns") or 0))
        if turns >= rule["max_turns"]:
            return False, {"turns": turns, "max_turns": rule["max_turns"]}
        turns += 1
        state.update(turns=turns, last_agent_sender_id=sender_id,
                     last_agent_message_id=message_id)
        return True, {"turns": turns, "max_turns": rule["max_turns"],
                      "reset_on_human_message": rule["reset_on_human_message"]}

    # --- Rate -----------------------------------------------------------------

    def _trim(self, window: deque, now: float) -> None:
        while window and now - window[0] >= WINDOW_SECONDS:
            window.popleft()

    def rate_allows(self, key: str, policy, in_flight: int = 0,
                    in_flight_total: int = 0) -> tuple[bool, str | None]:
        """Whether one more request fits: the answers sent in the last minute
        and the requests still running or waiting (`in_flight` here,
        `in_flight_total` across the bot) against each limit."""
        rule = rate_policy(policy)
        now = self.clock()
        window = self._slot(self.sent, key, deque)
        self._trim(window, now)
        self._trim(self.sent_total, now)
        if rule["per_conversation"] and len(window) + in_flight >= rule["per_conversation"]:
            return False, (f"{len(window)} replies in this conversation in the last minute "
                           f"and {in_flight} running or waiting")
        if rule["total"] and len(self.sent_total) + in_flight_total >= rule["total"]:
            return False, (f"{len(self.sent_total)} replies across the bot in the last "
                           f"minute and {in_flight_total} running or waiting")
        return True, None

    def record_reply(self, key: str) -> None:
        now = self.clock()
        self._slot(self.sent, key, deque).append(now)
        self.sent_total.append(now)
