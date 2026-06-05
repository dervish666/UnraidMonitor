"""Convert LLM-generated Markdown into Telegram-safe HTML.

The LLM providers (Anthropic, OpenAI, Ollama) return standard CommonMark:
``**bold**``, ``# headings``, ``*italic*``, ``` ``` ``` fenced code blocks. Telegram's
legacy Markdown parse mode does not understand double-asterisk bold or
headings, so that output renders with literal ``*`` characters all over it
(or fails to parse and gets stripped to plain text entirely).

Telegram's HTML parse mode is the most forgiving target for arbitrary model
output: the text is HTML-escaped first and we only ever emit a small set of
*balanced* tags (``<b>``, ``<i>``, ``<code>``, ``<pre>``, ``<a>``, ``<s>``), so an
unmatched Markdown delimiter degrades to a harmless literal character instead
of breaking the whole message. See the Telegram Bot API "HTML style" docs for
the supported tag set.
"""

import html
import re

__all__ = ["markdown_to_telegram_html", "strip_html_tags"]

# Fenced code blocks: ```lang\n ... ``` (contents preserved verbatim).
_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
# Inline code: `code`.
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# Markdown links: [text](url).
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
# Headings: up to three leading spaces, 1-6 '#', text, optional trailing '#'.
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
# Bold: **text** / __text__ (no surrounding word char for underscores).
_BOLD_STAR_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_UNDER_RE = re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
# Italic: *text* / _text_ (single delimiter, no adjacent space, snake_case-safe).
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)")
# Strikethrough: ~~text~~.
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
# List markers using '*' or '+' (a literal '*' bullet would otherwise show raw).
_BULLET_RE = re.compile(r"^([ \t]*)[*+][ \t]+(?=\S)", re.MULTILINE)
# Placeholder token used to protect code spans from further processing.
_PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")
_TAG_RE = re.compile(r"<[^>]+>")


def markdown_to_telegram_html(text: str) -> str:
    """Convert Markdown produced by an LLM into Telegram-safe HTML.

    Args:
        text: Raw model output in CommonMark-ish Markdown.

    Returns:
        HTML suitable for ``parse_mode="HTML"``. Returns an empty string for
        empty input.
    """
    if not text:
        return ""

    protected: list[str] = []

    def _stash(fragment: str) -> str:
        protected.append(fragment)
        return f"\x00{len(protected) - 1}\x00"

    # 1. Pull out code (fenced first, then inline) and escape its contents now,
    #    so later Markdown/HTML passes never touch it.
    def _repl_fence(m: re.Match[str]) -> str:
        return _stash(f"<pre>{html.escape(m.group(1).rstrip(chr(10)), quote=False)}</pre>")

    def _repl_inline_code(m: re.Match[str]) -> str:
        return _stash(f"<code>{html.escape(m.group(1), quote=False)}</code>")

    text = _FENCE_RE.sub(_repl_fence, text)
    text = _INLINE_CODE_RE.sub(_repl_inline_code, text)

    # 2. Escape everything else. Telegram HTML only requires &, < and > to be
    #    escaped in text; quotes are left intact so they read naturally. Markdown
    #    delimiters (*, _, #, [, ]) survive escaping, so the regex passes below
    #    still work; '\x00' placeholders are untouched.
    text = html.escape(text, quote=False)

    # 3. Links -> <a>. The captured url already had '&' escaped to '&amp;'; also
    #    make any quote attribute-safe inside the href.
    def _repl_link(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2).replace('"', "&quot;")
        return _stash(f'<a href="{url}">{label}</a>')

    text = _LINK_RE.sub(_repl_link, text)

    # 4. Headings -> bold line.
    text = _HEADING_RE.sub(r"<b>\1</b>", text)

    # 5. Bold, then italic, then strikethrough.
    text = _BOLD_STAR_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_UNDER_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_STAR_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDER_RE.sub(r"<i>\1</i>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)

    # 6. Normalise '*'/'+' bullets to a clean glyph ('-' bullets already read
    #    fine and are left as-is).
    text = _BULLET_RE.sub(r"\1• ", text)

    # 7. Collapse the doubled tags a heading-with-bold produces (e.g. '## **x**').
    text = text.replace("<b><b>", "<b>").replace("</b></b>", "</b>")

    # 8. Restore protected code/link fragments.
    text = _PLACEHOLDER_RE.sub(lambda m: protected[int(m.group(1))], text)
    return text


def strip_html_tags(text: str) -> str:
    """Strip HTML tags and unescape entities for a plain-text fallback."""
    return html.unescape(_TAG_RE.sub("", text))
