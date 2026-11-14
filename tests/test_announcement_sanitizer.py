from __future__ import annotations

import pytest

from d2l_cli.announcement_sanitizer import (
    MAX_ANNOUNCEMENT_ELEMENTS,
    MAX_ANNOUNCEMENT_HTML_CHARS,
    MAX_ANNOUNCEMENT_NESTING,
    MAX_ANNOUNCEMENT_TEXT_CHARS,
    AnnouncementSanitizationError,
    sanitize_announcement_body,
)


def test_sanitizer_preserves_readable_paragraphs_lists_and_entities() -> None:
    html = """
    <p>Bring pens &amp; paper.</p>
    <ul><li>First item</li><li>Second&nbsp;item</li></ul>
    <p>Room &lt;fictional&gt;</p>
    """

    assert sanitize_announcement_body(html) == (
        "Bring pens & paper.\n\n- First item\n- Second item\n\nRoom <fictional>"
    )


def test_sanitizer_discards_active_hidden_and_form_content_without_loading_anything() -> None:
    html = """
    <p onclick="steal()">Visible <a href="https://external.invalid">link text</a></p>
    <script>PRIVATE_SCRIPT_VALUE</script>
    <style>.secret { display: block }</style>
    <iframe src="https://external.invalid">PRIVATE_FRAME_VALUE</iframe>
    <object data="https://external.invalid">PRIVATE_OBJECT_VALUE</object>
    <form><input type="hidden" value="PRIVATE_HIDDEN_VALUE">PRIVATE_FORM_VALUE</form>
    <p hidden>PRIVATE_HIDDEN_TEXT</p>
    <p aria-hidden="true">PRIVATE_ARIA_TEXT</p>
    <p style="display: none">PRIVATE_STYLE_TEXT</p>
    <img src="https://external.invalid/private.png" onerror="steal()">
    """

    result = sanitize_announcement_body(html)

    assert result == "Visible link text"
    assert "https://" not in result
    assert "PRIVATE_" not in result
    assert "onclick" not in result


def test_sanitizer_removes_ansi_osc_c1_and_format_controls() -> None:
    html = (
        "<p>\x1b[31mRed\x1b[0m "
        "\x1b]8;;https://external.invalid\x07Link\x1b]8;;\x07 "
        "\x9b32mGreen\x9b0m A\u202eB</p>"
    )

    result = sanitize_announcement_body(html)

    assert result == "Red Link Green AB"
    assert "\x1b" not in result
    assert "\x9b" not in result
    assert "\u202e" not in result


def test_sanitizer_handles_malformed_html_deterministically() -> None:
    html = "<div><p>First <b>part<p>Second &amp; final"

    assert sanitize_announcement_body(html) == "First part\nSecond & final"
    assert sanitize_announcement_body(html) == sanitize_announcement_body(html)


def test_sanitizer_returns_none_for_empty_or_only_unsafe_content() -> None:
    assert sanitize_announcement_body("") is None
    assert sanitize_announcement_body("<script>secret()</script><iframe>hidden</iframe>") is None


def test_sanitizer_rejects_excessive_input_and_nesting_with_fixed_codes() -> None:
    with pytest.raises(AnnouncementSanitizationError) as input_error:
        sanitize_announcement_body("x" * (MAX_ANNOUNCEMENT_HTML_CHARS + 1))
    assert input_error.value.code == "input_limit"

    nested = "<div>" * (MAX_ANNOUNCEMENT_NESTING + 1) + "text"
    with pytest.raises(AnnouncementSanitizationError) as nesting_error:
        sanitize_announcement_body(nested)
    assert nesting_error.value.code == "nesting_limit"

    with pytest.raises(AnnouncementSanitizationError) as text_error:
        sanitize_announcement_body("x" * (MAX_ANNOUNCEMENT_TEXT_CHARS + 1))
    assert text_error.value.code == "text_limit"

    with pytest.raises(AnnouncementSanitizationError) as element_error:
        sanitize_announcement_body("<br>" * (MAX_ANNOUNCEMENT_ELEMENTS + 1))
    assert element_error.value.code == "element_limit"
