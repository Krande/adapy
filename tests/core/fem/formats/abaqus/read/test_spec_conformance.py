"""Constructs the Abaqus Keywords Guide permits, which the card regexes rejected or mis-read.

Every case here is legal input. The regex reader spelled each card's parameters out in a fixed
order, which decided two things it had no business deciding: that parameters appear in that
order, and that no other parameter appears at all. The guide says otherwise — parameters are
order-free, and ``*Shell Section`` alone accepts ten optional ones.

The two failure modes were not equally visible. A card whose parameters were in the "wrong"
order simply did not match, and the section or constraint went missing. A card carrying an
extra optional parameter matched and absorbed it into the preceding value, so ``elset`` came
back as ``'solids, orientation=Ori-1'`` and the section was built against a set that does not
exist — no error, wrong model. The second kind is why these are tests rather than a changelog
entry.

Parameter lists are from the Abaqus 2026 Keywords Guide's own Required/Optional sections.
"""

from __future__ import annotations

import pytest

from ada.fem.formats.abaqus.read.lexer import iter_cards


def one(bulk: str, keyword: str):
    cards = list(iter_cards(bulk, keyword))
    assert len(cards) == 1, f"expected one *{keyword}, got {len(cards)}"
    return cards[0]


# ── parameter order is free ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bulk",
    [
        "*Solid Section, elset=solids, material=Steel\n,\n",
        "*Solid Section, material=Steel, elset=solids\n,\n",
        "*Solid Section,material=Steel,elset=solids\n,\n",
        "*SOLID SECTION, ELSET=solids, MATERIAL=Steel\n,\n",
        "*Solid Section, elset = solids, material = Steel\n,\n",
    ],
    ids=["as-written", "reversed", "no-spaces", "upper-case", "spaces-around-equals"],
)
def test_solid_section_parameters_are_order_and_case_free(bulk):
    card = one(bulk, "SOLID SECTION")
    assert card.params["ELSET"] == "solids"
    assert card.params["MATERIAL"] == "Steel"


def test_element_parameters_are_order_free():
    card = one("*Element, elset=plate, type=S4R\n1, 1, 2, 3, 4\n", "ELEMENT")
    assert card.params["TYPE"] == "S4R"
    assert card.params["ELSET"] == "plate"


def test_surface_parameters_are_order_free():
    card = one("*Surface, name=surf_a, type=ELEMENT\nplate, S1\n", "SURFACE")
    assert card.params["NAME"] == "surf_a"
    assert card.params["TYPE"] == "ELEMENT"


# ── optional parameters are tolerated, never absorbed ──────────────────────────────


@pytest.mark.parametrize(
    "bulk,expected",
    [
        # *Solid Section: CONTROLS, ORIENTATION, LAYUP, ORDER, STACK DIRECTION, SYMMETRIC
        ("*Solid Section, elset=solids, orientation=Ori-1, material=Steel\n,\n", ("solids", "Steel")),
        ("*Solid Section, elset=solids, material=Steel, controls=EC-1\n,\n", ("solids", "Steel")),
        ("*Solid Section, elset=solids, material=Steel, stack direction=3\n,\n", ("solids", "Steel")),
    ],
    ids=["orientation-between", "controls-after", "two-word-parameter"],
)
def test_solid_section_optional_parameters_are_not_absorbed(bulk, expected):
    card = one(bulk, "SOLID SECTION")
    assert (card.params["ELSET"], card.params["MATERIAL"]) == expected


@pytest.mark.parametrize(
    "bulk",
    [
        "*Shell Section, elset=p1, material=Steel, orientation=Ori-1\n0.04, 5\n",
        "*Shell Section, elset=p1, material=Steel, nodal thickness\n0.04, 5\n",
        "*Shell Section, elset=p1, material=Steel, poisson=0.3, controls=EC-1\n0.04, 5\n",
    ],
    ids=["orientation", "flag-parameter", "two-optionals"],
)
def test_shell_section_optional_parameters_are_not_absorbed(bulk):
    card = one(bulk, "SHELL SECTION")
    assert card.params["ELSET"] == "p1"
    assert card.params["MATERIAL"] == "Steel"


def test_beam_section_optional_parameter_is_not_absorbed():
    bulk = "*Beam Section, elset=b1, material=Steel, section=BOX, poisson=0.3\n0.1, 0.2\n0., 0., -1.\n"
    card = one(bulk, "BEAM SECTION")
    assert card.params["SECTION"] == "BOX"


def test_beam_section_accepts_the_sect_abbreviation():
    """The guide lists ``SECT=`` as an accepted spelling of ``SECTION=``."""
    card = one("*Beam Section, elset=b1, material=Steel, sect=PIPE\n0.1, 0.01\n0., 0., -1.\n", "BEAM SECTION")
    assert card.params.first("SECTION", "SECT") == "PIPE"


def test_elset_unsorted_is_not_absorbed_into_the_name():
    """``UNSORTED`` is one of *Elset's four optional parameters and was not in the pattern."""
    card = one("*Elset, elset=e1, unsorted\n1, 2, 3\n", "ELSET")
    assert card.params["ELSET"] == "e1"
    assert "UNSORTED" in card.params


def test_element_offset_is_not_absorbed_into_the_elset():
    card = one("*Element, type=S4R, elset=plate, offset=1000\n1, 1, 2, 3, 4\n", "ELEMENT")
    assert card.params["ELSET"] == "plate"
    assert card.params["OFFSET"] == "1000"


# ── parameters the guide marks optional really are optional ────────────────────────


def test_surface_type_is_optional():
    """NAME is *Surface's only required parameter; TYPE defaults to ELEMENT."""
    card = one("*Surface, name=surf_a\nplate, S1\n", "SURFACE")
    assert card.params["NAME"] == "surf_a"
    assert card.params.get("TYPE", "ELEMENT") == "ELEMENT"


def test_tie_adjust_is_optional():
    """*Tie requires NAME; ADJUST is optional, and a *Tie without it used not to match."""
    card = one("*Tie, name=t1\nsurf_a, surf_b\n", "TIE")
    assert card.params["NAME"] == "t1"
    assert "ADJUST" not in card.params


def test_tie_accepts_position_tolerance():
    card = one("*Tie, name=t1, adjust=yes, position tolerance=0.1\nsurf_a, surf_b\n", "TIE")
    assert card.params["POSITION TOLERANCE"] == "0.1"


def test_boundary_accepts_parameters():
    """``*Boundary`` takes OP, TYPE, AMPLITUDE and more. The pattern required the keyword to be
    followed immediately by a newline, so every parameterised *Boundary was silently dropped."""
    card = one("*Boundary, op=NEW\n1, 1, , 0.0\n", "BOUNDARY")
    assert card.params["OP"] == "NEW"
    assert card.data_lines == ("1, 1, , 0.0",)


# ── line structure ─────────────────────────────────────────────────────────────────


def test_keyword_line_continuation_is_joined():
    """A keyword line ending in a comma continues on the next line."""
    card = one("*Element, type=S4R,\n elset=plate\n1, 1, 2, 3, 4\n", "ELEMENT")
    assert card.params["TYPE"] == "S4R"
    assert card.params["ELSET"] == "plate"
    assert card.data_lines == ("1, 1, 2, 3, 4",)


def test_a_trailing_comma_on_a_data_line_does_not_swallow_the_next_line():
    """Decks write a trailing comma after a complete final value all the time. Joining data
    lines on it would make ``*Mass``'s single value eat the card that follows."""
    bulk = "*Mass, elset=MASS3001\n2.00000000E+03,\n*Elset, elset=other\n1, 2\n"
    mass = one(bulk, "MASS")
    assert mass.data_lines == ("2.00000000E+03,",)
    assert one(bulk, "ELSET").params["ELSET"] == "other"


def test_a_quoted_value_may_contain_a_comma():
    card = one('*Surface, name="my, surface", type=ELEMENT\nplate, S1\n', "SURFACE")
    assert card.params["NAME"] == "my, surface"


def test_comment_lines_are_not_data_lines():
    bulk = "*Elset, elset=e1\n1, 2\n** a note\n3, 4\n"
    assert one(bulk, "ELSET").data_lines == ("1, 2",)
