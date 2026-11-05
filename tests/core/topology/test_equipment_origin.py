"""Where a TopoEquipment sits relative to its space.

A unit with local coordinates (``GLOBAL_COORDS=False``) is placed from its space's origin, plus the
space height when it sits on the ROOF. A unit with global coordinates is placed at its X/Y/Z as given:
no space origin and no roof height. ``get_origin`` used to add the roof height to global units too, so
every roof unit saved by Ada Studio's cellbuilder (which writes global coordinates with the roof
height already in Z) compiled one cell height above its roof. ``ada.topo_model.compile.
equipment_space_offset`` already followed the rule; these pin ``get_origin`` to it.
"""

from __future__ import annotations

import pytest

from ada.topo_model.compile import equipment_space_offset
from ada.topology.entities import TopoEquipment, TopoSpace

SPACE = TopoSpace(NAME="cell_a", X=10.0, Y=2.0, Z=-1.3, DX=5.0, DY=4.0, DZ=7.8, INCLUDE=True)


class _Config:
    def get_space(self, name, structure_name=None):
        return SPACE if name == SPACE.NAME else None


def _unit(space_loc: str, global_coords: bool, x: float, y: float, z: float) -> TopoEquipment:
    eq = TopoEquipment(
        NAME="unit",
        SPACE_NAME="cell_a",
        SPACE_LOC=space_loc,
        X=x,
        Y=y,
        Z=z,
        LX=1.0,
        LY=1.0,
        LZ=2.0,
        GLOBAL_COORDS=global_coords,
        COGx=0.0,
        COGy=0.0,
        COGz=1.0,
        massDry=100.0,
        massCont=0.0,
    )
    eq.parent_config = _Config()
    return eq


@pytest.mark.parametrize("space_loc", ["FLOOR", "ROOF"])
def test_global_unit_is_placed_at_its_coordinates(space_loc):
    eq = _unit(space_loc, True, 11.0, 3.0, 6.5)
    assert tuple(eq.get_p1()) == pytest.approx((11.0, 3.0, 6.5))


def test_local_roof_unit_sits_on_the_roof():
    eq = _unit("ROOF", False, 1.0, 1.0, 0.0)
    assert tuple(eq.get_p1()) == pytest.approx((11.0, 3.0, -1.3 + 7.8))


def test_local_floor_unit_sits_on_the_floor():
    eq = _unit("FLOOR", False, 1.0, 1.0, 0.0)
    assert tuple(eq.get_p1()) == pytest.approx((11.0, 3.0, -1.3))


@pytest.mark.parametrize("space_loc", ["FLOOR", "ROOF"])
@pytest.mark.parametrize("global_coords", [False, True])
def test_get_origin_agrees_with_equipment_space_offset(space_loc, global_coords):
    """The two places that seat a unit on its space must agree."""
    eq = _unit(space_loc, global_coords, 1.0, 1.0, 0.0)
    assert tuple(eq.get_origin()) == pytest.approx(equipment_space_offset(eq, SPACE))
