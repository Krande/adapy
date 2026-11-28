"""The native IFC writer keeps the Part tree: each Part is a spatial zone holding its objects."""

from __future__ import annotations

import pytest

import ada
from ada.cadit.ifc.read.native_reader import native_adacpp_ifc_available
from ada.cadit.ifc.write.native_ifc_writer import native_ifc_writer_available

pytestmark = pytest.mark.skipif(
    not (native_ifc_writer_available() and native_adacpp_ifc_available()), reason="adacpp without the native IFC IO"
)


def _model():
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    box = ada.PrimBox("box", (2, 2, 2), (3, 3, 3))
    return ada.Assembly("a") / (ada.Part("outer") / [bm, ada.Part("inner") / [pl, box]])


@pytest.mark.parametrize("reader", ["ifcopenshell", "native"])
def test_native_write_keeps_each_object_in_its_part(tmp_path, reader):
    dst = tmp_path / "tree.ifc"
    _model().to_ifc(dst, writer="native")
    back = ada.from_ifc(dst, reader=reader)
    parents = {o.name: o.parent.name for o in back.get_all_physical_objects()}
    assert parents == {"bm": "outer", "pl": "inner", "box": "inner"}
    inner = next(p for p in back.get_all_parts_in_assembly() if p.name == "inner")
    assert inner.parent.name == "outer"


def test_native_read_puts_each_object_in_its_part(tmp_path):
    dst = tmp_path / "tree.ifc"
    _model().to_ifc(dst)  # the ifcopenshell writer
    native = {o.name: o.parent.name for o in ada.from_ifc(dst, reader="native").get_all_physical_objects()}
    full = {o.name: o.parent.name for o in ada.from_ifc(dst, reader="ifcopenshell").get_all_physical_objects()}
    assert native == full == {"bm": "outer", "pl": "inner", "box": "inner"}


@pytest.mark.xfail(
    reason="the native spatial path names its levels but not their IFC class, so the native reader cannot "
    "fold the IfcSite into the assembly and skip an implicit IfcBuilding as the ifcopenshell reader does",
    strict=True,
)
def test_native_read_builds_the_same_part_tree(tmp_path):
    dst = tmp_path / "tree.ifc"
    _model().to_ifc(dst)
    native = [p.name for p in ada.from_ifc(dst, reader="native").get_all_parts_in_assembly()]
    full = [p.name for p in ada.from_ifc(dst, reader="ifcopenshell").get_all_parts_in_assembly()]
    assert native == full
