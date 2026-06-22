"""SAT parser coverage: wire, degenerate-curve, meshsurf-surface.

These previously fell through to an opaque generic AcisEntity ("geometry left behind").
They must now parse into their typed entities.
"""

from __future__ import annotations

from ada.cadit.sat.parser.acis_entities import (
    AcisDegenerateCurve,
    AcisMeshSurface,
    AcisWire,
)
from ada.cadit.sat.parser.parser import AcisSatParser


def _parser():
    return AcisSatParser("dummy.sat")


def test_wire_parses_typed():
    e = _parser()._parse_entity_line("-5 wire $-1 $-1 $-1 $6 $7 $8")
    assert isinstance(e, AcisWire) and e.entity_type == "wire"


def test_degenerate_curve_keeps_position():
    e = _parser()._parse_entity_line("-10 degenerate-curve 1.0 2.0 3.0 forward I I")
    assert isinstance(e, AcisDegenerateCurve)
    assert e.position == [1.0, 2.0, 3.0]


def test_meshsurf_parses_typed_and_keeps_raw():
    e = _parser()._parse_entity_line("-12 meshsurf-surface nodes 4 facets 2 data")
    assert isinstance(e, AcisMeshSurface)
    assert "facets" in e.raw_data
