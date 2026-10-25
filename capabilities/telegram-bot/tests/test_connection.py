"""Connection resolution: a connection names one bot, its token resolves by
secret_env through the cascade, and getMe must be that bot."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures  # noqa: E402,F401

import connection  # noqa: E402

TOKEN = "7000000001:" + "A" * 35
ENTRY = {"username": "alpha_bot", "expected_bot_id": 7000000001,
         "secret_env": "TELEGRAM_BOT_ALPHA_TOKEN", "allow_write": True}


def lookup(values):
    def find(key):
        value = values.get(key)
        return (value, "user", Path("/u/.config/telegram-bot/credentials.env")) if value \
            else (None, None, None)
    return find


class Resolve(unittest.TestCase):
    def test_resolves_token_by_secret_env(self):
        out = connection.resolve("alpha", ENTRY, lookup({"TELEGRAM_BOT_ALPHA_TOKEN": TOKEN}))
        self.assertEqual(out["token"], TOKEN)
        self.assertEqual(out["expected_bot_id"], 7000000001)
        self.assertEqual(out["tier"], "user")
        self.assertTrue(out["allow_write"])

    def test_token_key_is_the_one_secret_env_names(self):
        entry = {**ENTRY, "secret_env": "ANY_NAME_WE_CHOSE"}
        out = connection.resolve("alpha", entry, lookup({"ANY_NAME_WE_CHOSE": TOKEN}))
        self.assertEqual(out["secret_env"], "ANY_NAME_WE_CHOSE")
        with self.assertRaises(connection.ConnectionProblem):
            connection.resolve("alpha", entry, lookup({"TELEGRAM_BOT_ALPHA_TOKEN": TOKEN}))

    def test_username_at_sign_is_dropped(self):
        out = connection.resolve("alpha", {**ENTRY, "username": "@alpha_bot"},
                                 lookup({"TELEGRAM_BOT_ALPHA_TOKEN": TOKEN}))
        self.assertEqual(out["username"], "alpha_bot")

    def assertProblem(self, entry, values, code, exit_code):
        with self.assertRaises(connection.ConnectionProblem) as caught:
            connection.resolve("alpha", entry, lookup(values))
        self.assertEqual((caught.exception.code, caught.exception.exit_code), (code, exit_code))

    def test_missing_username(self):
        self.assertProblem({**ENTRY, "username": ""}, {}, "username_required", 6)

    def test_missing_expected_id(self):
        self.assertProblem({**ENTRY, "expected_bot_id": None}, {},
                           "expected_bot_id_required", 6)

    def test_missing_secret_env(self):
        self.assertProblem({**ENTRY, "secret_env": None}, {}, "secret_env_required", 6)

    def test_token_not_resolving(self):
        self.assertProblem(ENTRY, {}, "credentials_missing", 2)

    def test_token_malformed(self):
        self.assertProblem(ENTRY, {"TELEGRAM_BOT_ALPHA_TOKEN": "nope"}, "token_malformed", 2)

    def test_token_of_another_bot(self):
        self.assertProblem(ENTRY, {"TELEGRAM_BOT_ALPHA_TOKEN": "7000000002:" + "B" * 35},
                           "token_for_other_bot", 6)

    def test_default_env_key(self):
        self.assertEqual(connection.env_key("team-room"), "TELEGRAM_BOT_TEAM_ROOM_TOKEN")


class GetMe(unittest.TestCase):
    def setUp(self):
        self.resolved = connection.resolve("alpha", ENTRY,
                                           lookup({"TELEGRAM_BOT_ALPHA_TOKEN": TOKEN}))

    def test_matching_bot(self):
        me = {"id": 7000000001, "is_bot": True, "username": "Alpha_Bot"}
        self.assertEqual(connection.check_me(self.resolved, me), [])

    def test_other_bot_id_is_named(self):
        me = {"id": 7000000002, "is_bot": True, "username": "alpha_bot"}
        problems = connection.check_me(self.resolved, me)
        self.assertEqual(len(problems), 1)
        self.assertIn("7000000002", problems[0])

    def test_wrong_username_and_not_a_bot(self):
        me = {"id": 7000000001, "is_bot": False, "username": "someone"}
        self.assertEqual(len(connection.check_me(self.resolved, me)), 2)

    def test_privacy_report(self):
        self.assertEqual(connection.capabilities_report(
            {"can_read_all_group_messages": False})["privacy_mode"], True)


class TokenHome(unittest.TestCase):
    """A token never resolves from inside the project, where its agent reads files."""

    def test_token_in_the_project_files_is_refused(self):
        def project(key):
            return (TOKEN, "project", Path("/p/.env.local")) if key == ENTRY["secret_env"] \
                else (None, None, None)
        with self.assertRaises(connection.ConnectionProblem) as caught:
            connection.resolve("alpha", ENTRY, project)
        self.assertEqual(caught.exception.code, "token_in_project")
        self.assertIn("credentials.env", caught.exception.hint)

    def test_process_environment_is_a_token_home(self):
        out = connection.resolve("alpha", ENTRY, lambda key: (TOKEN, "env", None))
        self.assertEqual(out["tier"], "env")


if __name__ == "__main__":
    unittest.main()
