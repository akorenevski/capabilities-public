"""The model's Markdown as Telegram HTML (`parse_mode` HTML).

An answer is written in Markdown; sent as it is, Telegram shows the asterisks
and backticks. `to_html` turns the parts Telegram can show into its tags -
`**bold**` and `__bold__` to `<b>`, `*italic*` and `_italic_` to `<i>`,
`~~strike~~` to `<s>`, `` `code` `` to `<code>`, fenced blocks to `<pre>`, a
heading line to a bold line - and escapes everything else, so no text the
model wrote can open a tag. A marker without its pair, or inside a word
(`2*3*4`, `__init__`), stays as written; so does a fence line no fence closes.
Code is never put inside another tag: a span around it is closed before it and
opened again after. Every tag is opened and closed in one call, so the output
is always well formed.

A link shows where it goes: `[text](url)` becomes `text (url)`, and is a live
`<a>` only where its text is its address, so no text can hide an address.
Only `http` and `https` links are read; `tg:` (which mentions people and opens
proxy and join links), `mailto:` and every other scheme stay as written.
`plain_pieces` is for the service's own notices, which carry names it did not
write: their text is escaped and nothing in it is read as Markdown.

`pieces` cuts an answer into messages first and converts each piece on its
own, so no tag spans two messages: a code block cut in two is closed at the end
of one piece and opened again at the start of the next. Each piece comes with
its plain text, which is what is sent when Telegram refuses the HTML
(`refused_markup`): a piece is never lost for its formatting. Telegram counts
a message's length after the tags are parsed, so a piece that fits as
Markdown fits as HTML.

New in this capability; upstream sends plain text.
"""

from __future__ import annotations

import html
import re

import prompt as prompting

FENCE = re.compile(r"^ {0,3}(`{3,})\s*([\w+#.-]*)\s*$")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
STAR_BULLET = re.compile(r"^(\s*)[*+]\s+")
INLINE = re.compile(
    r"(?P<code>(?P<ticks>`+)(?P<code_body>.+?)(?P=ticks))"
    r"|(?P<link>\[(?P<link_text>[^\]\n]+)\]\((?P<href>https?://[^\s()]+)\))"
    r"|(?<![\w*])\*\*(?P<bold>(?=\S).+?(?<=\S))\*\*(?![\w*])"
    r"|(?<!\w)__(?!\w+__(?!\w))(?P<bold2>(?=\S).+?(?<=\S))__(?!\w)"
    r"|(?<![\w~])~~(?P<strike>(?=\S).+?(?<=\S))~~(?![\w~])"
    r"|(?<![\w*])\*(?P<italic>(?=[^\s*]).+?(?<=[^\s*]))\*(?![\w*])"
    r"|(?<!\w)_(?P<italic2>(?=[^\s_]).+?(?<=[^\s_]))_(?!\w)")
TAGS = {"bold": "b", "bold2": "b", "strike": "s", "italic": "i", "italic2": "i"}


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _runs(text: str) -> list[tuple[bool, str]]:
    """One line of prose as `(is_code, html)` runs: a span holding code is cut
    around it, so code stands alone."""
    out, pos = [], 0
    while True:
        found = INLINE.search(text, pos)
        if found is None:
            out.append((False, _escape(text[pos:])))
            return [run for run in out if run[1]]
        out.append((False, _escape(text[pos:found.start()])))
        if found.group("code") is not None:
            out.append((True, f"<code>{_escape(found.group('code_body'))}</code>"))
        elif found.group("link") is not None:
            href, label = found.group("href"), found.group("link_text")
            if label == href:
                out.append((False, f'<a href="{html.escape(href, quote=True)}">'
                                   f"{_escape(href)}</a>"))
            else:
                out.extend(_runs(label))
                out.append((False, f" ({_escape(href)})"))
        else:
            name = next(k for k in TAGS if found.group(k) is not None)
            tag = TAGS[name]
            for code, part in _merged(_runs(found.group(name))):
                out.append((code, part if code else f"<{tag}>{part}</{tag}>"))
        pos = found.end()


def _merged(runs: list[tuple[bool, str]]) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    for code, part in runs:
        if out and not code and not out[-1][0]:
            out[-1] = (False, out[-1][1] + part)
        else:
            out.append((code, part))
    return out


def inline(text: str) -> str:
    """One line of prose: its spans as tags, the rest escaped."""
    return "".join(part for _code, part in _runs(text))


def _pre(lines: list[str], lang: str) -> str:
    body = _escape("\n".join(lines))
    if lang:
        return f'<pre><code class="language-{_escape(lang)}">{body}</code></pre>'
    return f"<pre>{body}</pre>"


def _closes(line: str, fence: str) -> bool:
    found = FENCE.match(line)
    return bool(found) and not found.group(2) and len(found.group(1)) >= len(fence)


def _closed_later(lines: list[str], fence: str) -> bool:
    return any(_closes(line, fence) for line in lines)


def to_html(text: str) -> str:
    """Markdown to Telegram HTML; a fence line no later fence closes is text."""
    out: list[str] = []
    code: list[str] | None = None
    fence, lang = "", ""
    lines = str(text or "").split("\n")
    for i, line in enumerate(lines):
        if code is not None:
            if _closes(line, fence):
                out.append(_pre(code, lang))
                code = None
            else:
                code.append(line)
            continue
        opening = FENCE.match(line)
        if opening:
            if _closed_later(lines[i + 1:], opening.group(1)):
                code, fence, lang = [], opening.group(1), opening.group(2)
            else:
                out.append(_escape(line))
            continue
        heading = HEADING.match(line)
        if heading:
            out.append(f"<b>{inline(heading.group(1))}</b>")
            continue
        out.append(inline(STAR_BULLET.sub(r"\1• ", line)))
    return "\n".join(out)


def _open_fence_after(text: str, opener: str | None, after: str = "") -> str | None:
    """The fence line still open at the end of `text`, given the one open at
    its start; a fence opens only where `text` or the `after` it closes it."""
    fence = FENCE.match(opener).group(1) if opener else ""
    lines = text.split("\n")
    rest = after.split("\n") if after else []
    for i, line in enumerate(lines):
        found = FENCE.match(line)
        if not found:
            continue
        if opener is None:
            if _closed_later(lines[i + 1:] + rest, found.group(1)):
                opener, fence = line.strip(), found.group(1)
        elif _closes(line, fence):
            opener = None
    return opener


def pieces(text: str, limit: int = prompting.TELEGRAM_TEXT_LIMIT) -> list[tuple[str, str]]:
    """`(html, plain)` per message: cut as plain text is, each piece's code
    block closed in it and opened again in the next."""
    out, opener = [], None
    plains = prompting.split_text(text, limit)
    for i, plain in enumerate(plains):
        source = f"{opener}\n{plain}" if opener else plain
        opener = _open_fence_after(plain, opener, "\n".join(plains[i + 1:]))
        if opener:
            source += "\n" + FENCE.match(opener).group(1)
        out.append((to_html(source), plain))
    return out


def plain_pieces(text: str, limit: int = prompting.TELEGRAM_TEXT_LIMIT) -> list[tuple[str, str]]:
    """`(html, plain)` per message for a notice: escaped, never read as
    Markdown."""
    return [(_escape(plain), plain) for plain in prompting.split_text(text, limit)]


def refused_markup(exc) -> bool:
    """Telegram refused the piece for its HTML, not for its place or size:
    nothing was sent, and the plain text may be."""
    said = str(exc).lower()
    return getattr(exc, "kind", None) == "refused" and getattr(exc, "status", None) == 400 \
        and ("can't parse entities" in said or "text is empty" in said)
