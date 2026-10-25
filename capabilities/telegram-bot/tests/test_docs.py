"""What ships says only what may ship: the guide never puts the token into a
typed command or into the project, and no shipped file names the owner's own
chats or topics. The names are held here only as hashes, so this file does not
name them either."""

from __future__ import annotations

import hashlib
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import CAPABILITY_DIR  # noqa: E402

SOURCE_BIN = CAPABILITY_DIR.parents[1] / "bin"
GUIDE = CAPABILITY_DIR / "guides" / "enrol-a-bot.md"
# sha256 of each owner's place, written `group/topic`, lower-case, no spaces
# (DOCTRINE.md: shipped text names none of them).
OWNER_PLACES = {"6838b08544751baf3a3122e329d608b5f35670e67306440b0b381f1a0e0d3d38"}
PLACE_SHAPE = re.compile(r"[A-Za-z][\w-]*\s*/\s*[A-Za-z][\w-]*")


def shipped() -> list[Path]:
    return [p for p in CAPABILITY_DIR.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts] + [SOURCE_BIN / "carry-upstream"]


def owner_places(text: str) -> list[str]:
    found = []
    for match in PLACE_SHAPE.finditer(text):
        spelled = re.sub(r"\s+", "", match.group(0)).lower()
        if hashlib.sha256(spelled.encode()).hexdigest() in OWNER_PLACES:
            found.append(match.group(0))
    return found


class Guide(unittest.TestCase):
    def test_token_is_read_not_typed(self):
        text = GUIDE.read_text()
        self.assertNotRegex(text, r"secret_env\"\s*:\s*\"<token>\"")
        self.assertRegex(text, r"read -rs \w+")
        typed = [line for line in text.splitlines()
                 if line.lstrip().startswith(("printf", "echo")) and not re.search(r"\$\w+", line)]
        self.assertEqual(typed, [])

    def test_token_goes_outside_the_project(self):
        text = GUIDE.read_text()
        self.assertIn("token set <id>", text)
        self.assertIn("--env secret_env=TELEGRAM_BOT_<ID>_TOKEN", text)
        self.assertNotRegex(text, r"--stdin\s*$|--stdin\n", "the token must not go to connection add")
        self.assertIn("~/.config/telegram-bot/credentials.env", text)
        self.assertIn("accidental leaks only", text)

    def test_scrub_limit_is_stated_in_the_change_log(self):
        self.assertIn("accidental leaks only", (CAPABILITY_DIR / "CHANGELOG.md").read_text())


class Sanitized(unittest.TestCase):
    def test_no_owner_place_in_shipped_files(self):
        for path in shipped():
            found = owner_places(path.read_text(errors="replace"))
            self.assertFalse(found, f"{path} names an owner's place")


if __name__ == "__main__":
    unittest.main()
