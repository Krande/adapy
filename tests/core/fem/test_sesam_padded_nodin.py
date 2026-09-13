"""Sesam records that carry padding past what the card semantically needs.

A .SIN reaches the CAD targets (xml, gnx) through its own input deck: the deck is
extracted, then read back with ``ada.from_fem``. ``sin_to_sif`` writes four values
per line, so a one-noded MASS (eltyp 11) or SPRING1 (eltyp 18) comes back as
``58513 0 0 0`` — and the readers handed that entire field to ``str_to_int``, i.e.
``int(float(...))``::

    ValueError: could not convert string to float:
    '5.85130000E+04 0.00000000E+00 0.00000000E+00 0.00000000E+00'

The same padding shows up one card further along: MGSPRNG carries the 21 values of a
6-DOF lower triangle in a record padded out to 22, and consuming the extra one opened
a seventh matrix row (``operands could not be broadcast together with shapes (7,6)
(6,7)``).

``files/fem_files/sesam/mass_spring_padded_nodin.FEM`` is the smallest deck that
reproduces both: two nodes, one MASS and one SPRING1 written exactly as
``sin_to_sif._format_record_line`` writes them, with the MGMASS/MGSPRNG cards that
send the reader down the failing branches — MGSPRNG padded the way the real file is.
The ``fem`` target never hit any of this: it only extracts the deck, never reads it
back.
"""

from __future__ import annotations

import pytest

import ada
from ada.config import Config
from ada.fem.formats.sesam.read.read_elements import (
    gelmnt_node_ids,
    gelmnt_point_node_id,
)

_MASS_NODE = 58513
_SPRING_NODE = 58514


@pytest.fixture
def padded_deck(fem_files):
    return fem_files / "sesam/mass_spring_padded_nodin.FEM"


@pytest.fixture(params=[True, False], ids=["streaming", "object"])
def both_reader_paths(request, monkeypatch):
    """Sesam .FEM has two readers, picked by ``Config().meshing_array_backed``.

    Both stored the raw NODIN field and both consumers parsed it the same wrong way,
    so both have to be pinned — the streaming one is the default and the one the
    reported conversion took.
    """
    monkeypatch.setattr(Config(), "meshing_array_backed", request.param)
    return request.param


def test_padded_nodin_reads(padded_deck, both_reader_paths):
    a = ada.from_fem(padded_deck)

    fem = a.get_by_name("T1").fem
    assert len(fem.nodes) == 2
    # Node 0 does not exist; a padding zero surviving as a node reference would be a
    # silent corruption of the connectivity rather than the loud failure we started from.
    assert all(n.id != 0 for el in fem.elements for n in el.nodes)

    # The MASS sits on the node the record names, and on that node alone.
    mass_type = ada.fem.Elem.EL_TYPES.MASS_SHAPES.MASS
    mass_nodes = {n.id for el in fem.elements if el.type is mass_type for n in el.nodes}
    assert len(mass_nodes) == 1
    assert fem.nodes.from_id(mass_nodes.pop()).p == pytest.approx((0.0, 0.0, 0.0))

    # MGSPRNG resolves its node too — it reached the same parse by a different route.
    assert len(list(fem.springs)) == 1


def test_a_padded_mgsprng_still_assembles_a_square_matrix(padded_deck, both_reader_paths):
    a = ada.from_fem(padded_deck)

    stiff = a.get_by_name("T1").fem.springs["spr2"].stiff

    assert stiff.shape == (6, 6)
    # Built from the 21 triangle values 1..21 and mirrored, so the trailing pad would
    # show up as a wrong value, not only as a wrong shape.
    assert stiff[0].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert stiff[5][5] == 21.0
    assert (stiff == stiff.T).all()


def test_padding_zeros_are_dropped_not_read_as_nodes():
    # Zero is Sesam's padding, never a node number — a node 0 would be a silent
    # corruption of the connectivity rather than a parse error.
    assert gelmnt_node_ids("5.85130000E+04 0.00000000E+00 0.00000000E+00 0.00000000E+00") == [_MASS_NODE]
    assert gelmnt_node_ids("1.00000000E+00  2.00000000E+00") == [1, 2]
    assert gelmnt_point_node_id({"nids": "5.85130000E+04 0.00000000E+00", "elno": "1"}) == _MASS_NODE


def test_an_empty_nodin_names_the_element():
    # Reachable only from a deck that is itself wrong; an IndexError here would say
    # nothing about which record to go and look at.
    with pytest.raises(ValueError, match="element 7 references no node"):
        gelmnt_point_node_id({"nids": "0.00000000E+00 0.00000000E+00", "elno": "7"})
