"""The gate: a bot answers only what is addressed to it, in an allowed place,
from an allowed person or bot."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import (ALICE, GROUP, ME, OTHER_BOT, STRANGER, addressed,  # noqa: E402
                       mention_entity, message, settings)

import gate  # noqa: E402


def decide(msg, cfg=None):
    return gate.decide(msg, cfg if cfg is not None else settings(), ME)


class Addressing(unittest.TestCase):
    def test_mention_is_answered(self):
        d = decide(addressed())
        self.assertTrue(d["answer"], d)
        self.assertEqual(d["triggers"], ["username"])

    def test_mention_after_emoji_counts_utf16_offsets(self):
        text = "👋🏽 hi @alpha_bot"
        d = decide(message(text, entities=[mention_entity(text, "alpha_bot")]))
        self.assertTrue(d["answer"], d)

    def test_mention_of_another_bot_is_not_ours(self):
        d = decide(addressed(to=OTHER_BOT))
        self.assertFalse(d["answer"])
        self.assertEqual(d["reason"], "not_addressed")

    def test_plain_group_message_is_not_answered(self):
        d = decide(message("anyone around?"))
        self.assertFalse(d["answer"])
        self.assertEqual(d["reason"], "not_addressed")
        self.assertTrue(d["known_place"], "it still belongs to the tail")

    def test_reply_to_the_bot_is_answered(self):
        mine = {"message_id": 5, "from": ME}
        d = decide(message("and then?", reply_to=mine))
        self.assertTrue(d["answer"], d)
        self.assertEqual(d["triggers"], ["reply"])

    def test_reply_to_someone_else_is_not(self):
        theirs = {"message_id": 5, "from": OTHER_BOT}
        self.assertFalse(decide(message("and then?", reply_to=theirs))["answer"])

    def test_command_addressed_to_the_bot(self):
        text = "/status@alpha_bot now"
        d = decide(message(text, entities=[{"type": "bot_command", "offset": 0,
                                            "length": len("/status@alpha_bot")}]))
        self.assertTrue(d["answer"], d)
        self.assertEqual(d["triggers"], ["command"])

    def test_bare_command_in_a_group_is_ambiguous(self):
        d = decide(message("/status", entities=[{"type": "bot_command", "offset": 0,
                                                 "length": 7}]))
        self.assertFalse(d["answer"])

    def test_bare_command_in_private_chat_is_direct(self):
        d = decide(message("/start", sender=ALICE, chat_id=ALICE["id"], chat_type="private"))
        self.assertTrue(d["answer"])

    def test_trigger_switched_off_for_the_group(self):
        cfg = settings()
        cfg["allowed_groups"][str(GROUP)]["triggers"] = ["reply"]
        self.assertFalse(decide(addressed(), cfg)["answer"])

    def test_own_message_is_never_answered(self):
        self.assertEqual(decide(addressed(sender=ME))["reason"], "own_message")


class Places(unittest.TestCase):
    def test_chat_not_allowed(self):
        d = decide(addressed(chat_id=-100555))
        self.assertFalse(d["answer"])
        self.assertEqual(d["reason"], "chat_not_allowed")

    def test_chat_found_by_bare_number(self):
        cfg = settings(allowed_groups={str(GROUP)[4:]: {}})
        self.assertTrue(decide(addressed(), cfg)["answer"])

    def test_direct_messages_off(self):
        cfg = settings(direct_messages={"mode": "off"})
        d = decide(message("hi", chat_id=ALICE["id"], chat_type="private"), cfg)
        self.assertEqual(d["reason"], "direct_messages_off")


class Senders(unittest.TestCase):
    def test_stranger_is_refused(self):
        d = decide(addressed(sender=STRANGER))
        self.assertFalse(d["answer"])
        self.assertEqual(d["reason"], "sender_not_allowed")

    def test_stranger_direct_message_refused(self):
        d = decide(message("hi", sender=STRANGER, chat_id=STRANGER["id"], chat_type="private"))
        self.assertEqual(d["reason"], "sender_not_allowed")

    def test_allowed_bot_mentioning_us_is_answered_as_agent(self):
        d = decide(addressed(sender=OTHER_BOT))
        self.assertTrue(d["answer"], d)
        self.assertTrue(d["sender"]["agent"])

    def test_unknown_bot_is_refused(self):
        rogue = {"id": 7000000099, "is_bot": True, "username": "rogue_bot"}
        self.assertEqual(decide(addressed(sender=rogue))["reason"], "bot_not_allowed")

    def test_agent_bare_reply_is_context_not_a_turn(self):
        mine = {"message_id": 5, "from": ME}
        d = decide(message("thanks!", sender=OTHER_BOT, reply_to=mine))
        self.assertFalse(d["answer"])
        self.assertEqual(d["reason"], "not_addressed")

    def test_muted_member(self):
        cfg = settings()
        cfg["allowed_users"][str(ALICE["id"])]["may_address"] = False
        self.assertEqual(decide(addressed(), cfg)["reason"], "sender_muted")

    def test_role_from_entry(self):
        self.assertEqual(decide(addressed())["sender"]["role"], "supervisor")


class Review407(unittest.TestCase):
    """Findings of the 407cf6b review: key collisions and bots in direct messages."""

    def test_bare_number_does_not_admit_a_basic_group(self):
        cfg = settings(allowed_groups={"1234567890": {}})
        d = decide(addressed(chat_id=-1234567890, chat_type="group"), cfg)
        self.assertEqual(d["reason"], "chat_not_allowed")

    def test_supergroup_is_not_found_with_only_the_minus_dropped(self):
        cfg = settings(allowed_groups={"1001234567890": {}})
        d = decide(addressed(chat_id=-1001234567890), cfg)
        self.assertEqual(d["reason"], "chat_not_allowed")

    def test_unknown_bot_cannot_direct_message_in_anyone_mode(self):
        cfg = settings(direct_messages={"mode": "anyone"})
        d = decide(message("hi", sender=OTHER_BOT, chat_id=OTHER_BOT["id"],
                           chat_type="private"), cfg)
        self.assertFalse(d["answer"])
        self.assertEqual(d["reason"], "bot_not_allowed")
        stranger = decide(message("hi", sender=STRANGER, chat_id=STRANGER["id"],
                                  chat_type="private"), cfg)
        self.assertTrue(stranger["answer"], stranger)


if __name__ == "__main__":
    unittest.main()
