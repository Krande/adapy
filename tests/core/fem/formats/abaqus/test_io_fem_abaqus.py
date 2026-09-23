"""The cards these fixtures carry, read through the lexer.

These used to assert on the named groups of one regex per card. They now assert on the
parsed card, which is the thing the readers actually consume — and which does not care what
order a deck happens to write its parameters in.
"""

from ada.fem.formats.abaqus.read.lexer import comment_property, iter_cards, tokenize
from ada.fem.formats.abaqus.read.read_sections import (
    conn_from_groupdict,
    get_connector_sections_from_bulk,
)


def test_consec(consec):
    expected = [
        {
            "elset": "Wire-1-Set-1",
            "behavior": "ConnProp-1_VISC_DAMPER_ELEM",
            "contype": "Bushing,",
            "csys": '"Datum csys-1",',
        },
        {
            "elset": "Wire-2-Set-1",
            "behavior": "ConnProp-1_VISC_DAMPER_ELEM",
            "contype": "Bushing,",
            "csys": '"Datum csys-2",',
        },
    ]
    cards = list(iter_cards(consec, "CONNECTOR SECTION"))
    assert len(cards) == len(expected)
    for card, want in zip(cards, expected):
        assert card.params["ELSET"] == want["elset"]
        assert card.params["BEHAVIOR"] == want["behavior"]
        # The two data lines are the connection type and the coordinate system.
        assert card.data_lines == (want["contype"], want["csys"])


def test_conn_beha(conbeh):
    behaviors = list(iter_cards(conbeh, "CONNECTOR BEHAVIOR"))
    assert len(behaviors) == 1
    assert behaviors[0].params["NAME"] == "ConnProp-1_VISC_DAMPER_ELEM"

    elasticity = list(iter_cards(conbeh, "CONNECTOR ELASTICITY"))
    assert len(elasticity) == 1
    assert elasticity[0].params["COMPONENT"] == "1"
    # ``nonlinear`` is a flag: it carries no value, and presence is its meaning.
    assert "NONLINEAR" in elasticity[0].params
    assert elasticity[0].params["NONLINEAR"] is None

    conn = conn_from_groupdict(
        dict(name=behaviors[0].params["NAME"], component="1", bulk=elasticity[0].data_text), None
    )
    assert conn.name == "ConnProp-1_VISC_DAMPER_ELEM"
    assert len(conn.elastic_comp) == 1


def test_connector_sections_are_owned_by_the_behavior_above_them(conbeh):
    sections = get_connector_sections_from_bulk(conbeh, None)
    assert list(sections) == ["ConnProp-1_VISC_DAMPER_ELEM"]


def test_shell2solid(shell2solids):
    for card in iter_cards(shell2solids, "SHELL TO SOLID COUPLING"):
        assert card.params["CONSTRAINT NAME"]
        assert len(card.data_lines[0].split(",")) == 2


def test_couplings(couplings):
    cards = list(iter_cards(couplings, "COUPLING"))
    assert cards
    for card in cards:
        assert card.params["CONSTRAINT NAME"]
        assert card.params["REF NODE"]
        assert card.params["SURFACE"]


def test_surfaces(surfaces):
    cards = list(iter_cards(surfaces, "SURFACE"))
    assert cards
    for card in cards:
        assert card.params["NAME"]
        assert card.data_lines


def test_contact_pairs(interactions):
    cards = list(iter_cards(interactions, "CONTACT PAIR"))
    assert cards
    for card in cards:
        assert card.params["INTERACTION"]
        # The pair's name lives in the comment directly above it, never further away.
        assert comment_property(card, "Interaction").get("Interaction") is not None


def test_contact_general(interactions):
    keywords = [c.keyword for c in tokenize(interactions)]
    assert "CONTACT" in keywords or "CONTACT PAIR" in keywords
