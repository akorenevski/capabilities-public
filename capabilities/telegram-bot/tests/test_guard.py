"""The loop guard: bot-to-bot turns capped per conversation and reset by a
person, redelivered updates dropped, replies rate-limited."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures  # noqa: E402,F401

import guard  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class AgentDialogue(unittest.TestCase):
    def test_cap_refuses_the_turn_after_max(self):
        g = guard.Guard()
        policy = {"agent_dialogue": {"max_turns": 2}}
        self.assertTrue(g.admit_agent_turn("k", policy)[0])
        self.assertTrue(g.admit_agent_turn("k", policy)[0])
        admitted, state = g.admit_agent_turn("k", policy)
        self.assertFalse(admitted)
        self.assertEqual(state, {"turns": 2, "max_turns": 2})

    def test_missing_cap_is_the_default_not_unbounded(self):
        g = guard.Guard()
        for _ in range(guard.DEFAULT_MAX_TURNS):
            self.assertTrue(g.admit_agent_turn("k", {})[0])
        self.assertFalse(g.admit_agent_turn("k", {})[0])

    def test_zero_lifts_the_cap(self):
        g = guard.Guard()
        for _ in range(50):
            self.assertTrue(g.admit_agent_turn("k", {"agent_dialogue": {"max_turns": 0}})[0])

    def test_a_human_message_resets(self):
        g = guard.Guard()
        policy = {"agent_dialogue": {"max_turns": 1}}
        g.admit_agent_turn("k", policy)
        self.assertFalse(g.admit_agent_turn("k", policy)[0])
        self.assertTrue(g.human_spoke("k", policy, 9))
        self.assertTrue(g.admit_agent_turn("k", policy)[0])

    def test_reset_can_be_switched_off(self):
        g = guard.Guard()
        policy = {"agent_dialogue": {"max_turns": 1, "reset_on_human_message": False}}
        g.admit_agent_turn("k", policy)
        self.assertFalse(g.human_spoke("k", policy))
        self.assertFalse(g.admit_agent_turn("k", policy)[0])

    def test_conversations_count_apart(self):
        g = guard.Guard()
        policy = {"agent_dialogue": {"max_turns": 1}}
        self.assertTrue(g.admit_agent_turn("a", policy)[0])
        self.assertTrue(g.admit_agent_turn("b", policy)[0])


class Dedup(unittest.TestCase):
    def test_update_seen_once(self):
        g = guard.Guard()
        self.assertTrue(g.first_update(10))
        self.assertFalse(g.first_update(10))

    def test_message_seen_once_per_chat(self):
        g = guard.Guard()
        self.assertTrue(g.first_message(-1, 5))
        self.assertFalse(g.first_message(-1, 5))
        self.assertTrue(g.first_message(-2, 5))

    def test_window_is_bounded(self):
        g = guard.Guard()
        for i in range(guard.SEEN_LIMIT + 10):
            g.first_update(i)
        self.assertLessEqual(len(g.seen_updates), guard.SEEN_LIMIT)


class Rate(unittest.TestCase):
    def test_per_conversation_limit_and_window(self):
        clock = Clock()
        g = guard.Guard(clock=clock)
        policy = {"rate_limit": {"replies_per_minute": 2}}
        for _ in range(2):
            self.assertTrue(g.rate_allows("k", policy)[0])
            g.record_reply("k")
        self.assertFalse(g.rate_allows("k", policy)[0])
        self.assertTrue(g.rate_allows("other", policy)[0])
        clock.now += 61
        self.assertTrue(g.rate_allows("k", policy)[0])

    def test_total_limit_across_conversations(self):
        g = guard.Guard(clock=Clock())
        policy = {"rate_limit": {"replies_per_minute": 10, "replies_per_minute_total": 2}}
        g.record_reply("a")
        g.record_reply("b")
        self.assertFalse(g.rate_allows("c", policy)[0])


if __name__ == "__main__":
    unittest.main()
