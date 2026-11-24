"""Layout engine tests: geometry in, indented text out.

These are the primary safety net for the whole project. If a refactor breaks
indentation fidelity, it must break here first.
"""

from __future__ import annotations

import pytest

from helpers import CHAR_W, LINE_H, row, token
from screen_ocr.layout import DEFAULT_MAX_GAP_SPACES, reconstruct
from screen_ocr.models import Token


def test_nested_python_indentation() -> None:
    tokens = [
        *row("def foo():", line=0, indent=0),
        *row("if x:", line=1, indent=4),
        *row("return 1", line=2, indent=8),
        *row("return 2", line=3, indent=4),
    ]

    assert reconstruct(tokens) == (
        "def foo():\n"
        "    if x:\n"
        "        return 1\n"
        "    return 2"
    )


def test_two_column_table_keeps_internal_gaps() -> None:
    tokens = [
        *row("name", line=0, indent=0),
        *row("age", line=0, indent=8),
        *row("alice", line=1, indent=0),
        *row("30", line=1, indent=8),
    ]

    assert reconstruct(tokens) == "name    age\nalice   30"


def test_blank_line_inserted_for_vertical_gap() -> None:
    tokens = [*row("first", line=0), *row("second", line=3)]

    assert reconstruct(tokens) == "first\n\nsecond"


def test_blank_lines_are_capped() -> None:
    tokens = [*row("first", line=0), *row("second", line=100)]

    text = reconstruct(tokens)

    assert text == "first" + "\n" * 6 + "second"
    assert text.count("\n") == 6


def test_single_line_has_no_phantom_indent() -> None:
    # The user selected one indented line, starting mid-file.
    tokens = row("return value", indent=8)

    assert reconstruct(tokens) == "return value"


def test_single_line_still_keeps_column_gap() -> None:
    tokens = [*row("key:", indent=0), *row("value", indent=10)]

    assert reconstruct(tokens) == "key:      value"


def test_leftmost_line_defines_column_zero() -> None:
    # Selection starts mid-indent: the least indented line becomes column 0
    # instead of everything gaining leading spaces.
    tokens = [*row("foo", line=0, indent=4), *row("bar", line=1, indent=8)]

    assert reconstruct(tokens) == "foo\n    bar"


def test_min_conf_filters_tokens() -> None:
    tokens = [*row("keep", line=0, conf=0.9), *row("drop", line=1, conf=0.1)]

    assert reconstruct(tokens, min_conf=0.5) == "keep"


def test_empty_input() -> None:
    assert reconstruct([]) == ""


def test_whitespace_only_tokens_are_ignored() -> None:
    tokens = [token("   ", col=0), token("\t", col=4)]

    assert reconstruct(tokens) == ""


def test_no_trailing_newline_and_no_trailing_whitespace() -> None:
    tokens = [*row("a", line=0), *row("b", line=1)]

    text = reconstruct(tokens)

    assert not text.endswith("\n")
    assert all(line == line.rstrip() for line in text.split("\n"))


def test_trailing_blank_lines_are_stripped() -> None:
    tokens = [*row("only", line=0)]

    assert reconstruct(tokens) == "only"


def test_stray_token_gap_is_capped() -> None:
    tokens = [token("a", col=0), token("b", col=1000)]

    assert reconstruct(tokens) == "a" + " " * DEFAULT_MAX_GAP_SPACES + "b"


def test_max_gap_spaces_is_configurable() -> None:
    tokens = [*row("a", indent=0), *row("b", indent=20)]

    assert reconstruct(tokens, max_gap_spaces=3) == "a   b"


def test_phrase_tokens_are_not_split() -> None:
    # Vision returns whole phrases; layout must treat them as one token.
    tokens = [token("def foo():", col=0), token("return 1", col=0, line=1)]

    assert reconstruct(tokens) == "def foo():\nreturn 1"


def test_uneven_baseline_stays_on_one_line() -> None:
    tokens = [
        token("if", col=0, top=0),
        token("x:", col=3, top=3),
        token("pass", col=0, top=LINE_H),
    ]

    assert reconstruct(tokens) == "if x:\npass"


def test_overlapping_boxes_never_produce_zero_spaces() -> None:
    # Overlapping bounding boxes (negative gap) must still separate words.
    tokens = [
        token("a", col=0, width=CHAR_W * 2),
        token("b", col=1),
    ]

    assert reconstruct(tokens) == "a b"


def test_proportional_widths_still_indent() -> None:
    # Proportional font: 'Wi' is narrower than 4 average characters. The
    # indent comes from the x-offset divided by the median advance.
    tokens = [
        token("Wi", col=0, width=22.0, char_w=8.5),
        token("word", col=4, line=1, width=34.0, char_w=8.5),
    ]

    text = reconstruct(tokens)
    lines = text.split("\n")

    assert lines[0] == "Wi"
    assert lines[1] == "    word"


def test_indent_is_capped_like_a_gap() -> None:
    tokens = [*row("a", line=0), *row("b", line=1, indent=500)]

    text = reconstruct(tokens)
    second = text.split("\n")[1]

    assert len(second) - len(second.lstrip(" ")) == DEFAULT_MAX_GAP_SPACES


@pytest.mark.parametrize(
    ("text", "expected_indent"),
    [("x = 1", 0), ("if x:", 4), ("return x", 8)],
)
def test_indent_scales_with_column(text: str, expected_indent: int) -> None:
    tokens = [*row("root", line=0, indent=0), *row(text, line=1, indent=expected_indent)]

    assert reconstruct(tokens) == "root\n" + " " * expected_indent + text


def test_tokens_are_sorted_by_x_not_input_order() -> None:
    tokens = [
        token("world", col=6),
        token("hello", col=0),
    ]

    assert reconstruct(tokens) == "hello world"


def test_vision_normalised_box_conversion_contract() -> None:
    # A regression guard for the box convention documented in agents.md:
    # layout works on top-left pixel coordinates, whatever produced them.
    tokens = [
        Token(text="col0", left=0.0, top=0.0, width=40.0, height=20.0),
        Token(text="col4", left=80.0, top=0.0, width=40.0, height=20.0),
    ]

    assert reconstruct(tokens) == "col0    col4"
