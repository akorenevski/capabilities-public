"""Forum topics: which topic a message is in, which topics a group admits, the
policy a topic lays over its group, and that the answer lands in the same topic."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import FORUM, GROUP, ME, addressed, settings  # noqa: E402

import gate  # noqa: E402


def decide(msg, cfg=None):
    return gate.decide(msg, cfg if cfg is not None else settings(), ME)


class TopicRouting(unittest.TestCase):
    def test_allowed_topic_is_answered_with_its_key(self):
        d = decide(addressed(chat_id=FORUM, forum=True, topic=141))
        self.assertTrue(d["answer"], d)
        self.assertEqual(d["topic_id"], 141)
        self.assertEqual(d["key"], f"{FORUM}#topic:141")

    def test_other_topic_of_the_same_forum_is_refused(self):
        d = decide(addressed(chat_id=FORUM, forum=True, topic=200))
        self.assertFalse(d["answer"])
        self.assertEqual(d["reason"], "topic_not_allowed")

    def test_general_is_topic_one_and_refused_unless_listed(self):
        d = decide(addressed(chat_id=FORUM, forum=True))
        self.assertEqual(d["topic_id"], 1)
        self.assertEqual(d["reason"], "topic_not_allowed")

    def test_general_listed_is_answered_without_a_thread(self):
        cfg = settings()
        cfg["allowed_groups"][str(FORUM)]["topics"]["1"] = {}
        d = decide(addressed(chat_id=FORUM, forum=True), cfg)
        self.assertTrue(d["answer"], d)
        self.assertNotIn("message_thread_id", gate.send_target(d))

    def test_answer_lands_in_the_same_topic_as_a_reply(self):
        msg = addressed(chat_id=FORUM, forum=True, topic=141)
        target = gate.send_target(decide(msg))
        self.assertEqual(target["chat_id"], FORUM)
        self.assertEqual(target["message_thread_id"], 141)
        self.assertEqual(target["reply_parameters"]["message_id"], msg["message_id"])

    def test_two_topics_are_two_conversations(self):
        cfg = settings()
        cfg["allowed_groups"][str(FORUM)]["topics"]["142"] = {"name": "Other"}
        a = decide(addressed(chat_id=FORUM, forum=True, topic=141), cfg)
        b = decide(addressed(chat_id=FORUM, forum=True, topic=142), cfg)
        self.assertNotEqual(a["key"], b["key"])

    def test_topic_policy_overlays_group_and_defaults(self):
        d = decide(addressed(chat_id=FORUM, forum=True, topic=141))
        self.assertEqual(d["policy"]["profile"], "telegram-claude")
        self.assertEqual(d["policy"]["room_profile"], "telegram-claude")
        self.assertEqual(d["policy"]["brief"], "pilot room")
        self.assertEqual(d["policy"]["agent_dialogue"], {"max_turns": 3})

    def test_group_without_topics_map_admits_the_whole_chat(self):
        d = decide(addressed(chat_id=GROUP))
        self.assertTrue(d["answer"])
        self.assertIsNone(d["topic_id"])
        self.assertNotIn("message_thread_id", gate.send_target(d))


class EmptyTopics(unittest.TestCase):
    def test_empty_topics_map_admits_no_topic(self):
        cfg = settings(allowed_groups={str(FORUM): {"topics": {}}})
        for msg in (addressed(chat_id=FORUM, forum=True, topic=141),
                    addressed(chat_id=FORUM, forum=True)):
            d = gate.decide(msg, cfg, ME)
            self.assertEqual(d["reason"], "topic_not_allowed", d)


if __name__ == "__main__":
    unittest.main()
