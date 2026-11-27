"""The native IFC reader names each shape after its product, as the ifcopenshell reader does.

A write/read round-trip must not rename what it carries: a shape read with ``reader="native"``
is named by the product's ``Name`` and keeps its ``GlobalId`` as its guid.
"""

from __future__ import annotations

import pytest

import ada
from ada.cadit.ifc.read.native_reader import native_adacpp_ifc_available

pytestmark = pytest.mark.skipif(not native_adacpp_ifc_available(), reason="adacpp without IfcNgeomStream")


def test_native_read_keeps_product_names_and_guids(tmp_path):
    beams = [ada.Beam(f"bm{i}", (i, 0, 0), (i, 0, 1), "IPE300") for i in range(3)]
    plate = ada.Plate("pl0", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    src = tmp_path / "named.ifc"
    (ada.Assembly("a") / (ada.Part("p") / [*beams, plate])).to_ifc(src)

    shapes = list(ada.from_ifc(src, reader="native").get_all_physical_objects())

    assert sorted(s.name for s in shapes) == ["bm0", "bm1", "bm2", "pl0"]
    by_name = {s.name: s for s in shapes}
    for obj in [*beams, plate]:
        assert by_name[obj.name].guid == obj.guid
