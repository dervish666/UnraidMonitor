"""Tests for LLM-Markdown -> Telegram-HTML conversion."""

from src.utils.telegram_format import markdown_to_telegram_html, strip_html_tags


class TestBoldAndItalic:
    def test_double_asterisk_becomes_bold(self):
        assert markdown_to_telegram_html("a **real story** here") == "a <b>real story</b> here"

    def test_double_underscore_becomes_bold(self):
        assert markdown_to_telegram_html("__loud__") == "<b>loud</b>"

    def test_single_asterisk_becomes_italic(self):
        assert markdown_to_telegram_html('say *"hello"* now') == 'say <i>"hello"</i> now'

    def test_single_underscore_becomes_italic(self):
        assert markdown_to_telegram_html("a _quiet_ word") == "a <i>quiet</i> word"

    def test_bold_inside_italic_and_vice_versa(self):
        assert markdown_to_telegram_html("**_x_**") == "<b><i>x</i></b>"

    def test_multiple_bold_spans_on_one_line(self):
        assert markdown_to_telegram_html("**a** and **b**") == "<b>a</b> and <b>b</b>"


class TestSnakeCaseAndMath:
    def test_underscores_in_identifiers_are_not_italicised(self):
        assert markdown_to_telegram_html("my_var_name stays") == "my_var_name stays"

    def test_lone_asterisk_with_spaces_is_not_italic(self):
        # "2 * 3 = 6" should not collapse into an italic run.
        assert markdown_to_telegram_html("2 * 3 = 6") == "2 * 3 = 6"


class TestHeadings:
    def test_heading_becomes_bold_line(self):
        assert markdown_to_telegram_html("## Status report") == "<b>Status report</b>"

    def test_heading_with_inline_bold_does_not_double_nest(self):
        assert markdown_to_telegram_html("### **Container health**") == "<b>Container health</b>"


class TestCodeAndEscaping:
    def test_html_special_chars_are_escaped(self):
        assert markdown_to_telegram_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_inline_code_is_preserved_and_escaped(self):
        assert markdown_to_telegram_html("run `df -h` now") == "run <code>df -h</code> now"

    def test_inline_code_is_not_treated_as_markdown(self):
        # The * inside a code span must stay literal, not become formatting.
        assert markdown_to_telegram_html("`a*b*c`") == "<code>a*b*c</code>"

    def test_fenced_code_block_escapes_contents(self):
        out = markdown_to_telegram_html("```\n<tag> & **stuff**\n```")
        assert out == "<pre>&lt;tag&gt; &amp; **stuff**</pre>"

    def test_fenced_code_block_with_language(self):
        out = markdown_to_telegram_html("```python\nx = 1\n```")
        assert out == "<pre>x = 1</pre>"


class TestLinksAndLists:
    def test_markdown_link(self):
        out = markdown_to_telegram_html("see [docs](https://example.com/a)")
        assert out == 'see <a href="https://example.com/a">docs</a>'

    def test_asterisk_bullets_become_glyph(self):
        assert markdown_to_telegram_html("* one\n* two") == "• one\n• two"

    def test_dash_bullets_are_left_alone(self):
        assert markdown_to_telegram_html("- one\n- two") == "- one\n- two"


class TestRealWorldOutput:
    def test_screenshot_example(self):
        src = (
            "But if you want a **real story** about what's happening:\n\n"
            "- 🐳 **Container health** — who's up\n"
            '- *"How\'s the server doing?"*'
        )
        out = markdown_to_telegram_html(src)
        assert "<b>real story</b>" in out
        assert "🐳 <b>Container health</b>" in out
        assert '<i>"How\'s the server doing?"</i>' in out
        assert "**" not in out

    def test_diagnosis_sections(self):
        src = "**What happened:** It crashed.\n**Likely cause:** OOM.\n**How to fix it:** Add RAM."
        out = markdown_to_telegram_html(src)
        assert out.count("<b>") == 3
        assert "**" not in out


class TestEdgeCases:
    def test_empty_string(self):
        assert markdown_to_telegram_html("") == ""

    def test_plain_text_unchanged(self):
        assert markdown_to_telegram_html("just plain text") == "just plain text"

    def test_unmatched_delimiter_degrades_to_literal(self):
        # A lone ** must not break or vanish — it stays as harmless text.
        assert markdown_to_telegram_html("a ** b") == "a ** b"


class TestStripHtmlTags:
    def test_strips_tags_and_unescapes(self):
        assert strip_html_tags("a <b>bold</b> &amp; <i>x</i>") == "a bold & x"

    def test_roundtrip_from_converter(self):
        html_out = markdown_to_telegram_html("a **bold** & `code`")
        assert strip_html_tags(html_out) == "a bold & code"


class TestModelOutputCannotInjectHtml:
    """Regression guard: raw Telegram-HTML tags in model output must arrive escaped."""

    def test_tg_spoiler_in_model_output_is_escaped(self):
        from src.utils.telegram_format import markdown_to_telegram_html
        out = markdown_to_telegram_html("here is a <tg-spoiler>secret</tg-spoiler> trick")
        assert "<tg-spoiler>" not in out
        assert "&lt;tg-spoiler&gt;" in out

    def test_anchor_tag_in_model_output_is_escaped(self):
        from src.utils.telegram_format import markdown_to_telegram_html
        out = markdown_to_telegram_html('click <a href="https://evil.example">here</a>')
        assert '<a href="https://evil.example">' not in out
        assert "&lt;a href=" in out

    def test_echoed_log_content_with_angle_brackets_survives(self):
        from src.utils.telegram_format import markdown_to_telegram_html
        out = markdown_to_telegram_html("error in <module> at line 5 & column 3")
        assert "&lt;module&gt;" in out
        assert "&amp;" in out
