"""The SteelStru demo blueprint: 2x1 cells -> reinforced floors, girders, columns.

Counts pinned from the verified first run: 2 cells sharing one wall give
4 external floor faces (2 per elevation), 14 deduped girder edges (7 x 2
elevations), 6 deduped column edges (4 corners + 2 on the shared wall) and
12 stringers per 5x5 floor face.
"""

from __future__ import annotations

import pytest

import ada
from ada.topo_model import build_topo_model


@pytest.fixture(scope="module")
def demo_assembly() -> ada.Assembly:
    return build_topo_model()


def test_steel_stru_counts(demo_assembly):
    plates = list(demo_assembly.get_all_physical_objects(by_type=ada.Plate))
    assert len(plates) == 4

    beams_by_sec: dict[str, int] = {}
    for bm in demo_assembly.get_all_physical_objects(by_type=ada.Beam):
        beams_by_sec[bm.section.name] = beams_by_sec.get(bm.section.name, 0) + 1

    assert beams_by_sec == {"HEB200": 6, "IPE200": 14, "HP140x8": 48}


def test_steel_stru_area_parts(demo_assembly):
    part_names = {p.name for p in demo_assembly.get_all_parts_in_assembly()}
    assert {"floors", "girders", "columns"} <= part_names
