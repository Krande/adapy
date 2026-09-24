"""render_keyword is the inverse of tokenize: what the writer renders, the reader reads back as meant.

The point of one grammar for both directions is that a writer cannot produce a line the reader
would misread. Each case renders a block and tokenizes it back, asserting the reader sees the
keyword, the parameters by name, the data and the name-bearing comment the writer intended.
"""

from __future__ import annotations

import pytest

from ada.fem.formats.abaqus.grammar import (
    MAX_LINE_LENGTH,
    format_value,
    render_keyword,
    tokenize,
)
from ada.fem.formats.abaqus.read.lexer import comment_property


def _one(text: str):
    (block,) = tokenize(text)
    return block


def test_spelling_order_and_layout_are_the_callers():
    text = render_keyword("Shell Section", [("elset", "plates"), ("material", "Steel")], ["0.01, 5"])
    assert text == "*Shell Section, elset=plates, material=Steel\n0.01, 5\n"


def test_round_trip_parameters_flags_and_data():
    text = render_keyword("Elset", [("elset", "all"), ("generate", None)], [(1, 100, 1)])
    block = _one(text)
    assert block.keyword == "ELSET"
    assert block.params["ELSET"] == "all"
    assert "GENERATE" in block.params and block.params["GENERATE"] is None
    assert list(block.data_lines) == ["1, 100, 1"]


@pytest.mark.parametrize("name", ["Beam 1", "a,b", " padded ", "plain"])
def test_a_name_the_reader_would_split_or_trim_is_quoted_and_comes_back_whole(name):
    block = _one(render_keyword("Nset", [("nset", name)], ["1, 2"]))
    assert block.params["NSET"] == name
    assert format_value(name).startswith('"') == (name != "plain")


def test_the_comment_that_names_a_block_is_read_back_by_the_reader():
    block = _one(
        render_keyword("Solid Section", [("elset", "s"), ("material", "m")], [","], comments=["Section: Cast"])
    )
    assert comment_property(block, "Section") == {"Section": "Cast"}


def test_a_keyword_line_past_the_limit_is_continued_the_way_the_reader_continues_it():
    long_name = "e" * 240  # with the other parameters, well past 256 characters
    text = render_keyword("Elset", [("elset", long_name), ("instance", "part-1"), ("internal", None)], ["1, 2"])
    assert all(len(line) <= MAX_LINE_LENGTH for line in text.splitlines())
    assert len(text.splitlines()) == 3  # keyword line continued once, then the data line
    block = _one(text)
    assert block.params["ELSET"] == long_name
    assert block.params["INSTANCE"] == "part-1"
    assert "INTERNAL" in block.params
    assert list(block.data_lines) == ["1, 2"]


def test_booleans_are_written_as_abaqus_spells_them():
    assert format_value(True) == "YES" and format_value(False) == "NO"
