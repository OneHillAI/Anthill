"""#89: double-escaped Unicode in task output is shown as characters, and nothing else is rewritten."""

from anthill.web.task_text import display_task_text as d


def test_double_escaped_sequences_become_characters():
    assert d(r"a \\u00b7 b") == "a \u00b7 b"
    assert d(r"caf\\u00e9") == "caf\u00e9"
    assert d(r"\\u00B7") == "\u00b7"  # hex digits in either case


def test_surrogate_pair_becomes_one_character():
    assert d(r"\\ud83d\\ude00") == "\U0001f600"


def test_lone_surrogate_is_left_as_written():
    assert d(r"x \\ud83d y") == r"x \\ud83d y"


def test_single_backslash_escapes_are_the_authors_text():
    assert d(r"print('\u00b7')") == r"print('\u00b7')"
    assert d(r"C:\users\me") == r"C:\users\me"


def test_three_backslashes_are_an_escaped_backslash_then_an_escape():
    assert d(r"\\\u00b7") == r"\\\u00b7"


def test_ordinary_text_and_empty_input_pass_through():
    assert d("plain **text**\nline two") == "plain **text**\nline two"
    assert d("") == ""
    assert d(None) == ""


def test_not_four_hex_digits_is_not_an_escape():
    assert d(r"\\u00zz and \\u12") == r"\\u00zz and \\u12"


def test_a_capital_u_escape_is_a_different_escape_and_stays():
    assert d(r"\\U0001f600") == r"\\U0001f600"
    assert d(r"\\U00e9") == r"\\U00e9"


def test_uppercase_hex_digits_still_decode():
    assert d(r"\\u00B7 \\uD83D\\uDE00") == "\u00b7 \U0001f600"


def test_controls_and_bidi_overrides_stay_visible_escapes():
    for esc in (
        r"\\u0000",
        r"\\u001b",
        r"\\u0085",
        r"\\u202e",
        r"\\u2066",
        r"\\u200e",
        r"\\u200b",
        r"\\u2028",
        r"\\ufeff",
    ):
        assert d(f"a{esc}b") == f"a{esc}b"


def test_zero_width_joiners_decode_so_emoji_sequences_survive():
    assert d(r"\\ud83d\\udc69\\u200d\\ud83d\\udcbb") == "\U0001f469\u200d\U0001f4bb"


def test_a_surrogate_pair_for_an_invisible_character_stays_a_visible_escape():
    # U+E0041 (a Unicode tag character) is invisible; its pair must not decode into hidden text.
    assert d(r"a \\udb40\\udc41 b") == r"a \\udb40\\udc41 b"
    assert d(r"\\udb40\\udc01") == r"\\udb40\\udc01"  # U+E0001, a language tag
