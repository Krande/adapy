"""The blocks these fixtures carry, read through the lexer.

These used to assert on the named groups of one regex per keyword. They now assert on the
parsed block, which is the thing the readers actually consume — and which does not care what
order a deck happens to write its parameters in.
"""

from ada.fem.formats.abaqus.read.lexer import comment_property, iter_keywords, tokenize
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
    blocks = list(iter_keywords(consec, "CONNECTOR SECTION"))
    assert len(blocks) == len(expected)
    for block, want in zip(blocks, expected):
        assert block.params["ELSET"] == want["elset"]
        assert block.params["BEHAVIOR"] == want["behavior"]
        # The two data lines are the connection type and the coordinate system.
        assert block.data_lines == (want["contype"], want["csys"])


def test_conn_beha(conbeh):
    behaviors = list(iter_keywords(conbeh, "CONNECTOR BEHAVIOR"))
    assert len(behaviors) == 1
    assert behaviors[0].params["NAME"] == "ConnProp-1_VISC_DAMPER_ELEM"

    elasticity = list(iter_keywords(conbeh, "CONNECTOR ELASTICITY"))
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
    for block in iter_keywords(shell2solids, "SHELL TO SOLID COUPLING"):
        assert block.params["CONSTRAINT NAME"]
        assert len(block.data_lines[0].split(",")) == 2


def test_couplings(couplings):
    blocks = list(iter_keywords(couplings, "COUPLING"))
    assert blocks
    for block in blocks:
        assert block.params["CONSTRAINT NAME"]
        assert block.params["REF NODE"]
        assert block.params["SURFACE"]


def test_surfaces(surfaces):
    blocks = list(iter_keywords(surfaces, "SURFACE"))
    assert blocks
    for block in blocks:
        assert block.params["NAME"]
        assert block.data_lines


def test_contact_pairs(interactions):
    blocks = list(iter_keywords(interactions, "CONTACT PAIR"))
    assert blocks
    for block in blocks:
        assert block.params["INTERACTION"]
        # The pair's name lives in the comment directly above it, never further away.
        assert comment_property(block, "Interaction").get("Interaction") is not None


def test_contact_general(interactions):
    keywords = [c.keyword for c in tokenize(interactions)]
    assert "CONTACT" in keywords or "CONTACT PAIR" in keywords
