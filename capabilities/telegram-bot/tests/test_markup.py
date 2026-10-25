"""Answers go out formatted: the model's Markdown is sent as Telegram HTML
through the real Bot API client, everything else escaped; a long answer is cut
so no tag spans two messages; a piece Telegram refuses as badly formed is sent
again as plain text. The context template tells the model what shows."""

from __future__ import annotations

import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import ALICE, ME, SERVICE_DIR, addressed  # noqa: E402
from test_review3 import BOB, bot_command, with_bob  # noqa: E402
from test_service import Deferred, service, update  # noqa: E402

import botapi  # noqa: E402
import daemon  # noqa: E402
import markup  # noqa: E402
import prompt as prompting  # noqa: E402


class Telegram:
    """The Bot API's side of `sendMessage`: refuses HTML whose tags do not
    pair or that Telegram does not know, as Telegram does, before sending."""

    KNOWN = {"b", "i", "s", "code", "pre", "a"}

    def __init__(self):
        self.calls = []
        self.refuse_html = False

    def __call__(self, method, payload, timeout):
        self.calls.append((method, dict(payload)))
        if method != "sendMessage":
            return {"ok": True, "result": True}
        if payload.get("parse_mode") == "HTML":
            problem = "forced" if self.refuse_html else well_formed(payload["text"], self.KNOWN)
            if problem:
                return {"ok": False, "error_code": 400,
                        "description": f"Bad Request: can't parse entities: {problem}"}
        return {"ok": True, "result": {"message_id": len(self.calls), "from": dict(ME),
                                       "chat": {"id": payload["chat_id"]},
                                       "text": payload["text"]}}

    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]


def well_formed(text: str, known: set) -> str | None:
    stack, problems = [], []

    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in known:
                problems.append(f"unsupported start tag {tag}")
            stack.append(tag)

        def handle_endtag(self, tag):
            if not stack or stack.pop() != tag:
                problems.append(f"unmatched end tag {tag}")

    Parser(convert_charrefs=True).feed(text)
    if stack:
        problems.append(f"unclosed {stack[-1]}")
    if re.search(r"<(?![/a-z])", text):
        problems.append("stray <")
    return problems[0] if problems else None


def visible(text: str) -> str:
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def real_service(answer: str):
    tg = Telegram()
    api = botapi.BotApi("1:x", transport=tg, sleep=lambda s: None)
    api.get_me = lambda: dict(ME)

    def worker(prompt, tail, knobs, cwd, cancel):
        return {"ok": True, "answer": answer, "kind": None, "reason": None}
    s, _ = service(worker=worker, api=api)
    return s, tg


class Converts(unittest.TestCase):
    def test_spans(self):
        self.assertEqual(markup.to_html("**bold** and *it* and `x<y`"),
                         "<b>bold</b> and <i>it</i> and <code>x&lt;y</code>")
        self.assertEqual(markup.to_html("__b c__ _i_ ~~s~~"), "<b>b c</b> <i>i</i> <s>s</s>")
        self.assertEqual(markup.to_html("see [the docs](https://x.org/a?b=1&c=2)"),
                         "see the docs (https://x.org/a?b=1&amp;c=2)")
        self.assertEqual(markup.to_html("[https://x.org/a](https://x.org/a)"),
                         '<a href="https://x.org/a">https://x.org/a</a>')

    def test_everything_else_escaped(self):
        self.assertEqual(markup.to_html("a < b & <b>not a tag</b>"),
                         "a &lt; b &amp; &lt;b&gt;not a tag&lt;/b&gt;")
        self.assertEqual(markup.to_html("[x](javascript:alert(1))"),
                         "[x](javascript:alert(1))")

    def test_unpaired_markers_and_snake_case_stay(self):
        self.assertEqual(markup.to_html("**open and snake_case_name 2 * 3"),
                         "**open and snake_case_name 2 * 3")

    def test_code_block_and_heading(self):
        self.assertEqual(markup.to_html("## Status\n```python\nif a < b:\n    **x**\n```\nok"),
                         '<b>Status</b>\n<pre><code class="language-python">if a &lt; b:\n'
                         "    **x**</code></pre>\nok")

    def test_star_bullets_are_not_italic(self):
        self.assertEqual(markup.to_html("* one\n* two *three*"), "• one\n• two <i>three</i>")


class Delivered(unittest.TestCase):
    def test_answer_is_sent_as_html(self):
        s, tg = real_service("**Done**: `make test` passed")
        s.handle_update(update(addressed("status?")))
        sent = tg.sent()[-1]
        self.assertEqual(sent["parse_mode"], "HTML")
        self.assertEqual(sent["text"], "<b>Done</b>: <code>make test</code> passed")

    def test_refused_html_goes_again_as_plain_text(self):
        s, tg = real_service("**Done** <3")
        tg.refuse_html = True
        out = s.handle_update(update(addressed("status?")))
        first, second = tg.sent()[-2:]
        self.assertEqual(first["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", second)
        self.assertEqual(second["text"], "**Done** <3")
        self.assertEqual(second.get("reply_parameters"), first.get("reply_parameters"))
        self.assertEqual(len(s.sent), 1, out)

    def test_other_refusals_are_not_resent(self):
        exc = botapi.BotApiError("refused", "sendMessage: Bad Request: chat not found", 400)
        self.assertFalse(markup.refused_markup(exc))
        exc = botapi.BotApiError("refused", "sendMessage: Bad Request: can't parse entities: x",
                                 400)
        self.assertTrue(markup.refused_markup(exc))

    def test_long_answer_splits_without_breaking_a_tag(self):
        code = "\n".join(f"line {i} < {i + 1} **not bold**" for i in range(400))
        answer = "**Report**\n\n" + " ".join(["*word*"] * 600) + f"\n\n```\n{code}\n```\nend"
        s, tg = real_service(answer)
        s.handle_update(update(addressed("status?")))
        sent = tg.sent()
        self.assertGreater(len(sent), 2)
        for piece in sent:
            self.assertEqual(piece["parse_mode"], "HTML", piece["text"][:80])
            self.assertIsNone(well_formed(piece["text"], Telegram.KNOWN))
            self.assertLessEqual(len(visible(piece["text"]).encode("utf-16-le")) // 2,
                                 prompting.TELEGRAM_TEXT_LIMIT)
        whole = "\n".join(visible(p["text"]) for p in sent)
        self.assertIn("line 399 < 400 **not bold**", whole)
        self.assertIn("line 0 < 1 **not bold**", whole)
        self.assertTrue(sent[-1]["text"].endswith("</pre>\nend"))
        self.assertTrue(any(p["text"].startswith("<pre>") for p in sent[1:]))


class Review6(unittest.TestCase):
    """The 5aa01d7 review: no link hides its address, no scheme but the web's
    is read, notices carry names unread, emphasis stays out of words, code
    stands alone, a cut keeps indentation, and a fence no fence closes is
    text."""

    def test_a_link_shows_its_address(self):
        self.assertEqual(markup.to_html("[Admin](https://evil.example/login)"),
                         "Admin (https://evil.example/login)")

    def test_only_web_links_are_read(self):
        for text in ("[a](tg://user?id=1)", "[join](tg://join?invite=x)",
                     "[m](mailto:a@b.c)"):
            with self.subTest(text=text):
                self.assertEqual(markup.to_html(text), text)

    def test_no_emphasis_inside_a_word(self):
        for text in ("2*3*4", "foo**bar**baz", "call __init__ and self.__main__()",
                     "a~~b~~c"):
            with self.subTest(text=text):
                self.assertEqual(markup.to_html(text), text)
        self.assertEqual(markup.to_html("__real bold__ and *it*"),
                         "<b>real bold</b> and <i>it</i>")

    def test_code_is_never_inside_another_tag(self):
        self.assertEqual(markup.to_html("**run `make` now**"),
                         "<b>run </b><code>make</code><b> now</b>")
        self.assertEqual(markup.to_html("[`x`](https://a.b)"),
                         "<code>x</code> (https://a.b)")

    def test_a_cut_keeps_the_indentation(self):
        code = "\n".join(f"    line {i}" for i in range(40))
        pieces = prompting.split_text(code, 200)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertTrue(piece.startswith("    line"), repr(piece[:20]))

    def test_an_unclosed_fence_is_text(self):
        self.assertEqual(markup.to_html("ok\n```\nnever closed\n**b**"),
                         "ok\n```\nnever closed\n<b>b</b>")

    def test_an_unclosed_fence_does_not_swallow_later_pieces(self):
        answer = "start\n```\n" + "\n\n".join(f"**para {i}** " + "w " * 300
                                                for i in range(20))
        out = markup.pieces(answer, 1000)
        self.assertGreater(len(out), 3)
        for html_text, _plain in out:
            self.assertNotIn("<pre>", html_text)
        self.assertIn("<b>para 19</b>", out[-1][0])

    def test_a_notice_carries_a_name_unread(self):
        tg = Telegram()
        api = botapi.BotApi("1:x", transport=tg, sleep=lambda s: None)
        api.get_me = lambda: dict(ME)
        s, _ = service(with_bob(), api=api, executor=Deferred())
        boss = {**ALICE, "first_name": "**Boss** [x](https://e.x) `c`"}
        s.handle_update(update(addressed("one", sender=BOB)))
        s.handle_update(update(addressed("two", sender=BOB)))
        s.handle_update(update(bot_command("/stop@alpha_bot", sender=boss)))
        notices = [p["text"] for p in tg.sent() if "stopped by" in p["text"]]
        self.assertTrue(notices, tg.sent())
        head = daemon.STOPPED_BY_NOTICE.split("{name}")[0] + '"' + boss["first_name"]
        for text in notices:
            self.assertTrue(visible(text).startswith(head), text)
            self.assertNotRegex(text, r"<(b|a|code)\b")


class Template(unittest.TestCase):
    def test_model_is_told_formatting_shows(self):
        text = (SERVICE_DIR / "templates" / "context.md").read_text()
        self.assertIn("Telegram shows formatting", text)
        self.assertIn("no tables and no headings", text)


class Guide(unittest.TestCase):
    def test_readd_after_privacy_is_named(self):
        import os
        guides = Path(os.environ.get("TELEGRAM_BOT_GUIDES_DIR") or SERVICE_DIR.parent / "guides")
        text = (guides / "enrol-a-bot.md").read_text()
        self.assertIn("stays silent on plain @mentions", text)
        self.assertIn("Remove the bot from the group and add it again", text)


if __name__ == "__main__":
    unittest.main()
