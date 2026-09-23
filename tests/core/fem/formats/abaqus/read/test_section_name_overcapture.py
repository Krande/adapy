"""A card's name comes from its own comment line, and cannot come from anywhere else.

Abaqus/CAE writes a card's name in the comment directly above it -- ``** Section: Cast node``,
``** Name: BC-1  Type: Displacement/Rotation``, ``** Interaction: Real-1``. The regex reader
matched that prefix with a pattern under ``re.DOTALL``, where ``.`` matches a newline and a
non-greedy ``.*?`` expands until the *rest* of the pattern matches rather than stopping at the
end of the line. Starting at a shell section's comment, ``re_solid``'s name group ran on to the
next ``*Solid Section`` -- on a 463k-element deck, 1.18M lines later. That name reached
``make_name_fem_ready``, which strips ``=`` (hence a deck echoed back with every equals sign
gone) and logged the whole megabyte: one record, 1,178,653 lines of stderr.

The lexer removes the class of bug rather than the instance. A card's ``comments`` are the
unbroken run of comment lines immediately above its keyword line, captured while tokenizing, so
there is no pattern that could reach past them and nothing for a distance to affect. Each test
below still states the distance between a decoy comment and the real card, because that is what
used to decide the outcome.
"""

from __future__ import annotations

import logging

import pytest

import ada
from ada.core.utils import make_name_fem_ready
from ada.fem.formats.abaqus.read.lexer import comment_property, iter_cards


def _filler(lines: int) -> str:
    """Keyword blocks between the decoy comment and the card we want."""
    return "".join(f"*Elset, elset=junk{i}\n 1, 2, 3\n" for i in range(lines))


@pytest.mark.parametrize("distance", [0, 10, 500])
def test_solid_section_name_comes_from_its_own_comment(distance):
    """The reproduction of the flood, shrunk: a shell section's ``** Section:`` comment first,
    a solid section's much later, and only the latter belongs to the solid section."""
    bulk = (
        "** Section: Shell t40\n*Shell Section, elset=plate_t40, material=Steel\n0.04, 5\n"
        + _filler(distance)
        + "** Section: Cast node\n*Solid Section, elset=solids, material=Steel\n"
    )

    card = next(iter_cards(bulk, "SOLID SECTION"))

    assert comment_property(card, "Section") == {"Section": "Cast node"}
    assert card.params["ELSET"] == "solids"
    assert card.params["MATERIAL"] == "Steel"


def test_solid_section_without_a_name_comment_still_parses():
    """The comment is optional, and stays optional -- the reader falls back to a generated name."""
    bulk = "*Solid Section, elset=solids, material=Steel\n, \n"

    card = next(iter_cards(bulk, "SOLID SECTION"))

    assert comment_property(card, "Section") == {}
    assert card.params["ELSET"] == "solids"
    assert card.params["MATERIAL"] == "Steel"


def test_a_comment_separated_from_its_card_is_not_attached():
    """Only the *unbroken* run directly above the keyword line counts. A comment with a data
    line or a blank line after it annotates whatever it was written about, not the next card."""
    bulk = "** Section: Not mine\n*Elset, elset=junk\n1, 2, 3\n*Solid Section, elset=solids, material=Steel\n"

    card = next(iter_cards(bulk, "SOLID SECTION"))

    assert comment_property(card, "Section") == {}


def test_boundary_condition_name_comes_from_its_own_comment():
    """A deck is full of ``** Name: ... Type: ...`` comments for things that are not boundary
    conditions (loads, interactions), and the nearest one used to win however far away it was."""
    bulk = (
        "** Name: Load-1   Type: Concentrated force\n*Cload\n1, 3, -1000.0\n"
        + _filler(200)
        + "** Name: BC-1 Type: Displacement/Rotation\n*Boundary\nfixed, 1, 6\n*Step\n"
    )

    card = next(iter_cards(bulk, "BOUNDARY"))

    assert comment_property(card, "Name", "Type") == {"Name": "BC-1", "Type": "Displacement/Rotation"}


def test_contact_pair_name_comes_from_its_own_comment():
    """The same shape again, on the card that ``AbaFF``'s ``nameprop`` used to build a pattern
    for: its ``\\n`` looked like a bound, but the ``\\*<flag>`` after it was the real terminator."""
    bulk = (
        "** Interaction: Decoy\n*Surface Interaction, name=IntProp-1\n1.,\n"
        + _filler(300)
        + "** Interaction: Real-1\n*Contact Pair, interaction=IntProp-1, type=SURFACE TO SURFACE\n"
        "surf_a, surf_b\n"
    )

    card = next(iter_cards(bulk, "CONTACT PAIR"))

    assert comment_property(card, "Interaction") == {"Interaction": "Real-1"}
    assert card.params["INTERACTION"] == "IntProp-1"


def test_no_fem_section_name_in_a_real_deck_spans_a_line(example_files):
    """End to end on a deck that has the construct: ``UUea.inp`` carries a ``** Section:``
    comment above its solid section, so this covers the whole read path, not just the lexer."""
    a = ada.from_fem(example_files / "fem_files/abaqus/UUea.inp")

    sections = [fs for part in a.get_all_parts_in_assembly(True) for fs in part.fem.sections]
    assert sections, "no FemSections read from UUea.inp -- the guard would be vacuous"
    for fs in sections:
        assert "\n" not in fs.name, f"section name spans lines: {fs.name[:80]!r}"
        assert len(fs.name) < 100, f"section name is {len(fs.name)} characters: {fs.name[:80]!r}"
    assert [fs.name for fs in sections] == ["Section-1"]


def test_make_name_fem_ready_bounds_what_it_logs():
    """Defence in depth: even with the reader fixed, a pathological name must not be able to
    flood a terminal. The returned name is untouched; only the log record is bounded.

    A handler rather than ``caplog``: ``ada.config.configure_logger`` sets ``propagate = False``
    on the ``ada`` logger, so its records never reach the root handler ``caplog`` installs.
    """
    name = "A" * 5000
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collect(level=logging.INFO)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        out = make_name_fem_ready(name)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert out == name, "truncation belongs in the message, not in the name"
    notices = [r for r in records if ">25 characters" in r.getMessage()]
    assert len(notices) == 1
    message = notices[0].getMessage()
    assert len(message) < 200, f"log record is {len(message)} characters long"
    assert "5000 characters" in message, "the message should still say how long the name was"
