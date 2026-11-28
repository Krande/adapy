"""The native IFC writer writes beams and plates as the members a model reader rebuilds.

The first half pins the member description adapy hands adacpp (``native_member_records``) and
needs no adacpp. The second writes a model natively and reads it back three ways -- ifcopenshell,
the native reader, the native member scan -- and requires each to give the same members as the
ifcopenshell writer + reader round trip. Skipped where the installed adacpp writer reads no member
descriptions.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.curves import CurvePoly2d
from ada.cadit.ifc.write.native_member_records import (
    member_record,
    section_profile_record,
)
from ada.cadit.ifc.write.property_sets import element_property_sets
from ada.cadit.ifc.write.write_material import material_property_values


def _poly_section() -> ada.Section:
    """A rounded hollow section stated by its corners, with the wall thickness its curves cannot."""

    def square(h, r):
        return CurvePoly2d(
            [(-h, -h, r), (h, -h, r), (h, h, r), (-h, h, r)], origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1)
        )

    return ada.Section(
        "RHS100", sec_type="poly", outer_poly=square(0.05, 0.01), inner_poly=square(0.044, 0.004), t_w=0.006
    )


def _model() -> ada.Assembly:
    angle = ada.Section("L60", sec_type="HP", h=0.06, w_top=0.06, w_btn=0.06, t_w=0.006, t_ftop=0.006, t_fbtn=0.006)
    bm1 = ada.Beam("bm1", (0, 0, 0), (2, 0, 0), angle, metadata={"Ref": "R-9/1", "imported": True})
    bm1.add_boolean(ada.BoolHalfSpace(origin=(1.9, 0, 0), normal=(1, 0, 0), flip=True, name="bm1_cut0"))
    bm1.add_boolean(ada.BoolHalfSpace(origin=(0.1, 0, 0), normal=(-0.8, 0.6, 0), flip=False, name="bm1_cut1"))
    bm2 = ada.Beam("bm2", (0, 0, 0), (0, 0, 1.5), "IPE300", up=(1, 0, 0), metadata={"Ref": "R-9/2"})
    bm3 = ada.Beam("bm3", (1, 1, 0), (1, 1, 3.25), _poly_section(), metadata={"Ref": "R-9/3", "Extra": {"k": 1.5}})
    pl = ada.Plate("pl1", [(0, 0), (0.4, 0), (0.4, 0.3), (0, 0.3)], 0.01, metadata={"Ref": "R-9/4"})
    # A hole: not a half-space, so it is an opening element voiding the plate, as both writers state it.
    pl.add_boolean(ada.PrimCyl("hole", (0.2, 0.15, -0.1), (0.2, 0.15, 0.1), 0.03))
    return ada.Assembly("a") / (ada.Part("p") / [bm1, bm2, bm3, pl])


# -- the description, no adacpp -------------------------------------------------------------------


def test_a_beam_is_described_with_its_class_axis_profile_and_cuts():
    a = _model()
    bm1 = a.get_by_name("bm1")
    rec = member_record(bm1)
    assert rec["ifc_class"] == "IfcBeam"
    assert rec["guid"] == bm1.guid
    assert rec["axis"] == ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0))
    assert rec["extrusion"]["depth"] == pytest.approx(2.0)
    assert [c["agreement"] for c in rec["cuts"]] == [True, False]
    assert rec["profile"]["properties"]["ADA_SectionParameters"]["sec_type"] == "HP"
    assert rec["psets"] == {"Properties": {"Ref": "R-9/1", "imported": True}}
    assert rec["material"]["properties"] == material_property_values(bm1.material)
    assert len(rec["placement"]) == 16


def test_a_poly_section_is_described_with_its_arcs():
    prof = section_profile_record(_poly_section())
    assert prof["outer"]["kind"] == "indexed"
    assert sum(1 for kind, _ in prof["outer"]["segments"] if kind == "arc") == 4
    assert len(prof["inners"]) == 1
    # POLY carries only the scalar its curves cannot state (beside its type and name string).
    bag = prof["properties"]["ADA_SectionParameters"]
    assert {k: v for k, v in bag.items() if k != "sec_str"} == {"sec_type": "poly", "t_w": 0.006}


def test_a_hole_is_described_as_an_opening():
    rec = member_record(_model().get_by_name("pl1"))
    assert rec["ifc_class"] == "IfcPlate"
    assert rec["cuts"] == []
    (opening,) = rec["openings"]
    assert opening["blob"] and len(opening["placement"]) == 16


def test_a_beam_that_is_not_a_straight_extrusion_is_left_to_its_solid():
    curve = ada.api.curves.CurveRevolve((0, 0, 0), (1, 1, 0), radius=1.0, rot_axis=(0, 0, 1), rot_origin=(1, 0, 0))
    bm = ada.BeamRevolve("rev", curve, "IPE300")
    assert member_record(bm) is None


def test_an_entity_choice_is_left_to_the_ifcopenshell_writer():
    from ada.base.ifc_types import IFC_ENTITY_KEY

    bm = ada.Beam("s", (0, 0, 0), (1, 0, 0), "IPE300", metadata={IFC_ENTITY_KEY: {"entity": "IfcMember"}})
    assert member_record(bm) is None


def test_metadata_splits_into_the_sets_the_ifcopenshell_writer_writes():
    assert element_property_sets({"a": 1, "Pset_X": {"b": 2}}) == {"Pset_X": {"b": 2}, "Properties": {"a": 1}}
    assert element_property_sets({"a": 1}) == {"Properties": {"a": 1}}
    assert element_property_sets({}) == {}


# -- written natively, read three ways -------------------------------------------------------------


def _natively_writable() -> bool:
    from ada.cadit.ifc.write.native_ifc_writer import native_ifc_member_records

    return native_ifc_member_records() >= 1


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    pytest.importorskip("ifcopenshell")
    if not _natively_writable():
        pytest.skip("the installed adacpp writer reads no member descriptions")
    d = tmp_path_factory.mktemp("native_members_write")
    native, ios = d / "native.ifc", d / "ios.ifc"
    model = _model()  # one model, so both files state the same guids
    model.to_ifc(str(native), writer="native")
    model.to_ifc(str(ios), streaming=False)
    return native, ios


def _by_name(objs) -> dict:
    return {o.name: o for o in objs}


def _assert_same(got: dict, want: dict):
    assert got.keys() == want.keys() == {"bm1", "bm2", "bm3", "pl1"}
    for name, w in want.items():
        g = got[name]
        assert type(g) is type(w), name
        assert g.guid == w.guid
        assert g.metadata == w.metadata, name
        assert g.material.name == w.material.name
        np.testing.assert_allclose(
            g.placement.get_absolute_placement().get_matrix4x4(),
            w.placement.get_absolute_placement().get_matrix4x4(),
            atol=1e-12,
        )
        if isinstance(w, ada.Beam):
            for attr in ("xvec", "yvec", "up"):
                np.testing.assert_allclose(getattr(g, attr), getattr(w, attr), atol=1e-12, err_msg=f"{name}.{attr}")
            np.testing.assert_allclose(g.n1.p, w.n1.p, atol=1e-12)
            np.testing.assert_allclose(g.n2.p, w.n2.p, atol=1e-12)
            assert g.section.type == w.section.type, name
            for key in ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt"):
                assert getattr(g.section, key) == getattr(w.section, key), f"{name}.section.{key}"
            for poly in ("poly_outer", "poly_inner"):
                gp, wp = getattr(g.section, poly, None), getattr(w.section, poly, None)
                assert (gp is None) == (wp is None)
                if wp is not None:
                    np.testing.assert_allclose(np.asarray(gp.points2d), np.asarray(wp.points2d), atol=1e-12)
            assert len(g.booleans) == len(w.booleans)
            for gb, wb in zip(g.booleans, w.booleans):
                np.testing.assert_allclose(gb.primitive.poly.origin, wb.primitive.poly.origin, atol=1e-12)
                np.testing.assert_allclose(gb.primitive.poly.normal, wb.primitive.poly.normal, atol=1e-12)
                assert gb.primitive.flip == wb.primitive.flip
        else:
            assert g.t == pytest.approx(w.t)
            np.testing.assert_allclose(np.asarray(g.poly.points3d), np.asarray(w.poly.points3d), atol=1e-12)


@pytest.fixture(scope="module")
def reference(files):
    _, ios = files
    return _by_name(ada.from_ifc(ios).get_all_physical_objects())


def test_native_write_reads_back_through_ifcopenshell(files, reference):
    native, _ = files
    _assert_same(_by_name(ada.from_ifc(native).get_all_physical_objects()), reference)


def test_native_write_reads_back_through_the_native_reader(files, reference):
    native, _ = files
    _assert_same(_by_name(ada.from_ifc(native, reader="native").get_all_physical_objects()), reference)


def test_native_write_reads_back_through_the_member_scan(files, reference):
    from ada.cadit.ifc.read.native_members import ifc_members_to_part

    native, _ = files
    _assert_same(_by_name(ifc_members_to_part(native).get_all_physical_objects()), reference)


def test_the_native_reader_reads_an_ifcopenshell_write_the_same(files, reference):
    _, ios = files
    _assert_same(_by_name(ada.from_ifc(ios, reader="native").get_all_physical_objects()), reference)


def test_the_native_reader_can_still_read_shapes_only(files):
    from ada.api.shapes import ShapeProxy
    from ada.cadit.ifc.read.native_reader import native_read_ifc_into

    native, _ = files
    a = ada.Assembly("shapes")
    native_read_ifc_into(a, native, members=False)
    assert {type(o) for o in a.get_all_physical_objects()} <= {ShapeProxy, ada.Shape}
