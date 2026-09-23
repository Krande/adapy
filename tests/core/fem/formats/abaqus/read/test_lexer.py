"""The lexer itself: the grammar, the streaming path, and the cost of using it."""

from __future__ import annotations

import io
import time

import pytest

from ada.fem.formats.abaqus.read.keywords import KEYWORDS, lookup
from ada.fem.formats.abaqus.read.lexer import (
    iter_enclosed,
    iter_keywords,
    normalize,
    stream_file,
    stream_keywords,
    tokenize,
)

DECK = """\
*Heading
 a deck
** Section: Shell t40
*Shell Section, elset=plate_t40, material=Steel, offset=SPOS
0.04, 5
*Part, name=P1
*Node, nset=all
1, 0., 0., 0.
2, 1., 0., 0.
*Element, type=S4R,
 elset=plate
1, 1, 2, 3, 4
*End Part
** Name: BC-1 Type: Displacement/Rotation
*Boundary
fixed, 1, 6
"""


def test_every_card_is_found_in_order():
    assert [c.keyword for c in tokenize(DECK)] == [
        "HEADING",
        "SHELL SECTION",
        "PART",
        "NODE",
        "ELEMENT",
        "END PART",
        "BOUNDARY",
    ]


def test_line_numbers_point_at_the_keyword_line():
    by_kw = {c.keyword: c.lineno for c in tokenize(DECK)}
    assert by_kw["HEADING"] == 1
    assert by_kw["SHELL SECTION"] == 4
    assert by_kw["PART"] == 6
    # The *Element keyword line starts on line 10 and is continued onto line 11; the number
    # reported is where the block starts, not where its continuation ends.
    assert by_kw["ELEMENT"] == 10
    assert by_kw["BOUNDARY"] == 15


def test_flag_parameters_are_present_with_a_none_value():
    block = next(iter_keywords("*Elset, elset=e1, generate\n1, 10, 1\n", "ELSET"))
    assert "GENERATE" in block.params
    assert block.params["GENERATE"] is None
    assert block.params["ELSET"] == "e1"


def test_parameter_lookup_ignores_case_and_inner_spacing():
    block = next(iter_keywords("*Coupling, constraint name=c1, ref node=rp, surface=s1\n", "COUPLING"))
    assert block.params["CONSTRAINT NAME"] == "c1"
    assert block.params["constraint name"] == "c1"
    assert block.params["Constraint  Name"] == "c1"


def test_data_text_and_data_lines_are_two_views_of_one_block():
    block = next(iter_keywords("*Elset, elset=e1\n 1, 2\n** note\n", "ELSET"))
    # data_text is the block as written -- free, because it is a slice of the source.
    assert block.data_text == " 1, 2\n"
    # data_lines is the same block, stripped and without comments or blanks.
    assert block.data_lines == ("1, 2",)


def test_blocks_are_nesting_aware():
    bulk = "*Part, name=outer\n*Part, name=inner\n*End Part\n*Node\n1, 0., 0., 0.\n*End Part\n"
    blocks = list(iter_enclosed(bulk, "PART", "END PART"))
    assert len(blocks) == 1
    block, body = blocks[0]
    assert block.params["NAME"] == "outer"
    assert "inner" in body and "*Node" in body


def test_normalize_collapses_case_and_spacing():
    assert normalize("  ref   node ") == "REF NODE"


# ── streaming ──────────────────────────────────────────────────────────────────────


def test_streaming_produces_the_same_cards_as_tokenizing():
    from_memory = tokenize(DECK)
    streamed = list(stream_keywords(io.StringIO(DECK)))

    assert len(streamed) == len(from_memory)
    for a, b in zip(from_memory, streamed):
        assert (a.keyword, dict(a.params), a.data_lines, a.comments, a.lineno) == (
            b.keyword,
            dict(b.params),
            b.data_lines,
            b.comments,
            b.lineno,
        )


@pytest.mark.parametrize("name", ["box.inp", "box_rigid.inp", "element_elset.inp", "nle1xf3c.inp", "UUea.inp"])
def test_streaming_matches_tokenizing_on_the_example_decks(example_files, name):
    path = example_files / "fem_files/abaqus" / name
    from_memory = tokenize(path.read_text())
    streamed = list(stream_file(path))

    assert [c.keyword for c in streamed] == [c.keyword for c in from_memory]
    for a, b in zip(from_memory, streamed):
        assert dict(a.params) == dict(b.params)
        assert a.data_lines == b.data_lines
        assert a.comments == b.comments


def test_stream_file_reads_without_holding_the_deck(example_files):
    """The streaming entry point yields as it goes rather than returning a list."""
    blocks = stream_file(example_files / "fem_files/abaqus/box.inp")
    assert next(iter(blocks)).keyword == "HEADING"


# ── keyword table ──────────────────────────────────────────────────────────────────


def test_registered_keywords_accept_their_own_parameters():
    for name, spec in KEYWORDS.items():
        for param in spec.required | spec.optional | set(spec.aliases):
            assert spec.accepts(param), f"*{name} does not accept its own {param}"
        for group in spec.one_of:
            for param in group:
                assert spec.accepts(param), f"*{name} does not accept its own {param}"


def test_keyword_names_are_normalized():
    for name in KEYWORDS:
        assert normalize(name) == name, f"{name!r} is not in normalized form"


def test_an_unregistered_keyword_still_parses():
    """The table gates validation, never parsing -- a deck may use any keyword Abaqus has."""
    assert lookup("BUCKLING ENVELOPE") is None
    block = next(iter_keywords("*Buckling Envelope, name=be1\n1., 2.\n", "BUCKLING ENVELOPE"))
    assert block.params["NAME"] == "be1"
    assert block.data_lines == ("1., 2.",)


# ── cost ───────────────────────────────────────────────────────────────────────────


def test_tokenizing_is_linear_in_deck_size():
    """A guard against a quadratic regression (the line-number counting and the data spans are
    both easy to make per-block rather than per-buffer). Ten times the deck, not a hundred times
    the time -- the bound is loose because it is a CI machine."""

    def deck(n):
        return "*Element, type=S4R, elset=plate\n" + "".join(f"{i}, {i}, {i+1}, {i+2}, {i+3}\n" for i in range(n))

    def best(bulk, repeat=3):
        return min(_timed(tokenize, bulk) for _ in range(repeat))

    small, large = deck(2_000), deck(20_000)
    t_small, t_large = best(small), best(large)
    assert t_large < max(t_small * 30, 0.5), f"{t_small:.4f}s -> {t_large:.4f}s for 10x the input"


def _timed(fn, arg):
    t0 = time.perf_counter()
    fn(arg)
    return time.perf_counter() - t0
