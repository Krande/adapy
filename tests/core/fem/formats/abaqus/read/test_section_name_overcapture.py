"""A card's value groups must stay on the card's own line.

The Abaqus reader's regexes are compiled with ``re.DOTALL``, where ``.`` matches a newline. A
non-greedy ``.*?`` is then not the bound it looks like: it expands as little as possible *until
the rest of the pattern matches*, so a group whose terminator is absent from its own line keeps
growing until the terminator turns up somewhere else in the deck.

That is what ``re_solid``'s optional ``** Section: <name>`` prefix did. Starting the match at a
*shell* section's comment, the name group ran on to the next ``*Solid Section`` -- on a
463k-element deck, 1.18M lines later. The name reached ``make_name_fem_ready``, which strips
``=`` (hence a deck echoed back with every ``=`` gone) and logs a >25-characters notice with the
whole megabyte inlined: one log record, 1,178,653 lines of stderr.

So each test below states the distance between the decoy comment and the real card, and asserts
the captured name is the name -- independent of that distance.
"""

from __future__ import annotations

import logging

import pytest

import ada
from ada.core.utils import make_name_fem_ready
from ada.fem.formats.abaqus.read import cards
from ada.fem.formats.abaqus.read.helper_utils import AbaFF


def _filler(lines: int) -> str:
    """Keyword blocks between the decoy comment and the card we want matched."""
    return "".join(f"*Elset, elset=junk{i}\n 1, 2, 3\n" for i in range(lines))


@pytest.mark.parametrize("distance", [0, 10, 500])
def test_solid_section_name_stops_at_its_own_line(distance):
    """The reproduction of the flood, shrunk: a shell section's ``** Section:`` comment first, a
    solid section's much later, and only the latter belongs to the solid section."""
    bulk = (
        "** Section: Shell t40\n*Shell Section, elset=plate_t40, material=Steel\n0.04, 5\n"
        + _filler(distance)
        + "** Section: Cast node\n*Solid Section, elset=solids, material=Steel\n"
    )

    m = cards.re_solid.search(bulk)

    assert m is not None
    assert m.group("name") == "Cast node"
    assert "\n" not in m.group("name")
    assert m.group("elset") == "solids"
    assert m.group("material") == "Steel"


def test_solid_section_without_a_name_comment_still_parses():
    """The prefix is optional, and stays optional -- the reader falls back to a generated name."""
    bulk = "*Solid Section, elset=solids, material=Steel\n, \n"

    m = cards.re_solid.search(bulk)

    assert m is not None
    assert m.group("name") is None
    assert m.group("elset") == "solids"
    assert m.group("material") == "Steel"


def test_boundary_condition_name_stops_at_its_own_line():
    """``re_bcs`` carries the same optional ``** Name: ... Type: ...`` prefix, and a deck is full
    of those comments for things that are not boundary conditions (loads, interactions)."""
    bulk = (
        "** Name: Load-1   Type: Concentrated force\n*Cload\n1, 3, -1000.0\n"
        + _filler(200)
        + "** Name: BC-1 Type: Displacement/Rotation\n*Boundary\nfixed, 1, 6\n*Step\n"
    )

    m = cards.re_bcs.search(bulk)

    assert m is not None
    assert m.group("name") == "BC-1"
    assert m.group("type") == "Displacement/Rotation"
    assert "\n" not in m.group("name")


def test_abaff_nameprop_stops_at_its_own_line():
    """``AbaFF``'s ``nameprop`` builds ``\\*\\*\\s*<Prop>:\\s*(?P<name>...)\\n\\*<flag>``. The
    ``\\n`` looks like a bound but the ``\\*<flag>`` after it is the real terminator, so a name
    comment belonging to a different keyword used to capture everything up to the next match."""
    flag = AbaFF(
        "Contact Pair",
        [("interaction=", "type=|"), ("surf1", "surf2")],
        nameprop=("Interaction", "name"),
    )
    bulk = (
        "** Interaction: Decoy\n*Surface Interaction, name=IntProp-1\n1.,\n"
        + _filler(300)
        + "** Interaction: Real-1\n*Contact Pair, interaction=IntProp-1, type=SURFACE TO SURFACE\n"
        "surf_a, surf_b\n"
    )

    m = flag.regex.search(bulk)

    assert m is not None
    assert m.group("name") == "Real-1"
    assert "\n" not in m.group("name")


def test_no_fem_section_name_in_a_real_deck_spans_a_line(example_files):
    """End to end on a deck that has the construct: ``UUea.inp`` carries a ``** Section:``
    comment above its solid section, so this covers the whole read path, not just the regex."""
    a = ada.from_fem(example_files / "fem_files/abaqus/UUea.inp")

    sections = [fs for part in a.get_all_parts_in_assembly(True) for fs in part.fem.sections]
    assert sections, "no FemSections read from UUea.inp -- the guard would be vacuous"
    for fs in sections:
        assert "\n" not in fs.name, f"section name spans lines: {fs.name[:80]!r}"
        assert len(fs.name) < 100, f"section name is {len(fs.name)} characters: {fs.name[:80]!r}"
    assert [fs.name for fs in sections] == ["Section-1"]


def test_make_name_fem_ready_bounds_what_it_logs():
    """Defence in depth: even with the regexes fixed, a pathological name must not be able to
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
