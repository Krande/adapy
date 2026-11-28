"""The native member read rebuilds a member the way the ifcopenshell reader does from the same file.

Two halves. The first builds scan RECORDS directly -- the contract the adacpp scan answers with --
so it needs no adacpp and pins the mapping: section from the parameter bag or the curves (fillets
kept), cuts as half-spaces, property sets as metadata, the placement. The second writes a small
model with the ifcopenshell writer and reads it both ways, and is skipped where the installed
adacpp scan predates the structural fields.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.cadit.ifc.read.native_members import member_object, members_to_part
from ada.sections.categories import BaseTypes

_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _rounded_square(half: float, r: float) -> dict:
    """A closed outline of four straight edges and four tangent quarter-arc fillets, as an
    IfcIndexedPolyCurve states it (points in order, arcs as (start, mid, end))."""
    c = half - r
    m = r * (1 - np.sqrt(0.5))
    pts = [
        (-half, -c), (-half + m, -half + m), (-c, -half),
        (c, -half), (half - m, -half + m), (half, -c),
        (half, c), (half - m, half - m), (c, half),
        (-c, half), (-half + m, half - m), (-half, c),
    ]  # fmt: skip
    segs = [
        ("arc", (1, 2, 3)), ("line", (3, 4)), ("arc", (4, 5, 6)), ("line", (6, 7)),
        ("arc", (7, 8, 9)), ("line", (9, 10)), ("arc", (10, 11, 12)), ("line", (12, 1)),
    ]  # fmt: skip
    return {"kind": "indexed", "dim": 2, "points": pts, "segments": segs}


def _beam_record(**overrides) -> dict:
    rec = {
        "id": 11,
        "ifc_class": "IFCBEAM",
        "name": "b1",
        "guid": "0000000000000000000B01",
        "profile_name": "RHS100",
        "profile_type": "IFCARBITRARYPROFILEDEFWITHVOIDS",
        "depth": 2.0,
        "unit_scale": 1.0,
        "placement": _IDENTITY,
        "placement_d": _IDENTITY,
        "origin": (1.0, 2.0, 3.0),
        "normal": (1.0, 0.0, 0.0),
        "xdir": (0.0, 0.0, 1.0),
        "extrude_dir": (0.0, 0.0, 1.0),
        "outline": [],
        "outer_curve": _rounded_square(0.05, 0.01),
        "inner_curves": [_rounded_square(0.044, 0.004)],
        "profile_params": {},
        "profile_properties": {"ADA_SectionParameters": {"sec_type": "poly", "t_w": 0.006}},
        "psets": {"Properties": {"Ref": "R-1/2", "imported": True}},
        "material": "S355",
        "material_props": {"MassDensity": 7850.0},
        "cuts": [],
    }
    rec.update(overrides)
    return rec


def test_a_poly_section_keeps_its_fillets_and_its_bag_scalar():
    beam = member_object(_beam_record())
    sec = beam.section
    assert sec.type == BaseTypes.POLY
    assert sec.t_w == pytest.approx(0.006)  # from the bag: the curves cannot state it
    # Four corners, each rounded by an arc, rather than a sampled polygon.
    assert len(sec.poly_outer.points2d) == 4
    kinds = [type(s).__name__ for s in sec.poly_outer.segments3d]
    assert kinds.count("ArcSegment") == 4 and kinds.count("LineSegment") == 4
    assert sec.poly_inner is not None


def test_a_parametric_bag_is_the_section():
    rec = _beam_record(
        profile_properties={
            "ADA_SectionParameters": {
                "sec_type": "HP",
                "h": 0.06,
                "w_top": 0.06,
                "w_btn": 0.06,
                "t_w": 0.006,
                "t_ftop": 0.006,
                "t_fbtn": 0.006,
            }
        }
    )
    sec = member_object(rec).section
    assert sec.type == BaseTypes.ANGULAR
    assert (sec.h, sec.w_top, sec.t_w) == pytest.approx((0.06, 0.06, 0.006))


def test_a_bag_in_millimetres_is_scaled_to_metres():
    rec = _beam_record(
        unit_scale=1e-3,
        profile_properties={
            "ADA_SectionParameters": {
                "sec_type": "HP",
                "h": 60.0,
                "w_top": 60.0,
                "w_btn": 60.0,
                "t_w": 6.0,
                "t_ftop": 6.0,
                "t_fbtn": 6.0,
            }
        },
    )
    assert member_object(rec).section.h == pytest.approx(0.06)


def test_the_beam_is_built_in_its_local_frame_like_the_full_reader():
    beam = member_object(_beam_record())
    # The extrusion's position is the node line's start; it runs along the extrusion axis.
    assert tuple(beam.n1.p) == pytest.approx((1.0, 2.0, 3.0))
    assert tuple(beam.n2.p) == pytest.approx((3.0, 2.0, 3.0))
    # up is the profile's local y: axis (x) cross ref (z) -> -y, as calc_yvec(ref, axis) gives.
    assert tuple(beam.up) == pytest.approx((0.0, -1.0, 0.0))


def test_the_placement_comes_from_the_double_matrix_in_metres():
    moved = list(_IDENTITY)
    moved[12:15] = [1000.25, 2000.5, -3000.125]  # file units (mm), column-major translation
    beam = member_object(_beam_record(unit_scale=1e-3, placement_d=moved))
    m4 = np.asarray(beam.placement.get_absolute_placement().get_matrix4x4())
    assert m4[:3, 3] == pytest.approx((1.00025, 2.0005, -3.000125))


def test_cuts_become_half_spaces_in_order():
    cuts = [
        {"origin": (1.5, 2.0, 3.0), "normal": (1.0, 0.0, 0.0), "xdir": (0.0, 0.0, 1.0), "agreement": True},
        {"origin": (2.5, 2.0, 3.0), "normal": (-0.6, 0.8, 0.0), "xdir": (0.0, 0.0, 1.0), "agreement": False},
    ]
    beam = member_object(_beam_record(cuts=cuts))
    assert len(beam.booleans) == 2
    first, second = (b.primitive for b in beam.booleans)
    assert isinstance(first, ada.BoolHalfSpace)
    assert tuple(first.poly.origin) == pytest.approx((1.5, 2.0, 3.0))
    assert first.flip is True and second.flip is False
    assert tuple(second.poly.normal) == pytest.approx((-0.6, 0.8, 0.0))


def test_property_sets_are_the_metadata():
    beam = member_object(_beam_record(psets={"Properties": {"Ref": "R-1/2"}, "Pset_Custom: x": {"a": 1}}))
    assert beam.metadata == {"Properties": {"Ref": "R-1/2"}, "Pset_Custom": {"a": 1}}


def test_a_plate_takes_its_corners_from_the_curve():
    curve = {
        "kind": "indexed",
        "dim": 2,
        "points": [(0.0, 0.0), (0.4, 0.0), (0.4, 0.3), (0.0, 0.3)],
        "segments": [("line", (1, 2)), ("line", (2, 3)), ("line", (3, 4)), ("line", (4, 1))],
    }
    rec = _beam_record(
        ifc_class="IFCPLATE",
        name="p1",
        profile_name="",
        profile_type="IFCARBITRARYCLOSEDPROFILEDEF",
        depth=0.01,
        origin=(0.0, 0.0, 0.0),
        normal=(0.0, 0.0, 1.0),
        xdir=(1.0, 0.0, 0.0),
        outer_curve=curve,
        inner_curves=[],
        profile_properties={},
    )
    plate = member_object(rec)
    assert isinstance(plate, ada.Plate)
    assert plate.t == pytest.approx(0.01)
    # The same corners (the plate normalises the winding, as it does on any read).
    got = sorted(tuple(round(float(c), 9) for c in p) for p in plate.poly.points2d)
    assert got == sorted([(0.0, 0.0), (0.4, 0.0), (0.4, 0.3), (0.0, 0.3)])
    assert plate.metadata["Properties"]["Ref"] == "R-1/2"


def test_a_polyline_plate_drops_the_closing_point():
    curve = {"kind": "polyline", "dim": 2, "points": [(0, 0), (1, 0), (0, 1), (0, 0)], "segments": None}
    rec = _beam_record(
        ifc_class="IFCPLATE", depth=0.02, normal=(0, 0, 1), xdir=(1, 0, 0), outer_curve=curve, profile_properties={}
    )
    assert len(member_object(rec).poly.points2d) == 3


def test_the_part_counts_stated_materials_per_member():
    part = members_to_part([_beam_record(), _beam_record(name="b2", guid="0000000000000000000B02", material="")])
    assert len(list(part.get_all_physical_objects())) == 2
    assert part.metadata["native_members_unstated_materials"] == 1


# -- against the full reader, on a file the ifcopenshell writer wrote -----------------------------


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    pytest.importorskip("ifcopenshell")
    from ada.cadit.ifc.read.native_members import native_members_available

    if not native_members_available():
        pytest.skip("adacpp has no IfcMemberScan")

    angle = ada.Section("L60", sec_type="HP", h=0.06, w_top=0.06, w_btn=0.06, t_w=0.006, t_ftop=0.006, t_fbtn=0.006)
    bm1 = ada.Beam("bm1", (0, 0, 0), (2, 0, 0), angle, metadata={"Ref": "R-9/1", "imported": True})
    bm1.add_boolean(ada.BoolHalfSpace(origin=(1.9, 0, 0), normal=(1, 0, 0), flip=True, name="bm1_cut0"))
    bm2 = ada.Beam("bm2", (0, 0, 0), (0, 0, 1.5), "IPE300", up=(1, 0, 0), metadata={"Ref": "R-9/2"})
    bm2.add_boolean(ada.BoolHalfSpace(origin=(0, 0, 1.4), normal=(0, 0.6, 0.8), flip=False, name="bm2_cut0"))
    pl = ada.Plate("pl1", [(0, 0), (0.4, 0), (0.4, 0.3), (0, 0.3)], 0.01, metadata={"Ref": "R-9/3"})
    path = tmp_path_factory.mktemp("native_structure") / "model.ifc"
    (ada.Assembly("a") / (ada.Part("p") / [bm1, bm2, pl])).to_ifc(str(path), streaming=False)
    return path


def test_the_native_members_are_the_full_readers_members(written):
    from ada.cadit.ifc.read.native_members import scan_ifc_members

    records = list(scan_ifc_members(written))
    if not records or "cuts" not in records[0]:
        pytest.skip("the installed adacpp scan reports no structural fields")

    native = {o.name: o for o in members_to_part(records).get_all_physical_objects()}
    full = {o.name: o for o in ada.from_ifc(written).get_all_physical_objects()}
    assert native.keys() == full.keys() == {"bm1", "bm2", "pl1"}

    for name, f in full.items():
        n = native[name]
        assert type(n) is type(f)
        assert n.guid == f.guid
        assert n.metadata == f.metadata
        assert n.material.name == f.material.name
        np.testing.assert_allclose(
            n.placement.get_absolute_placement().get_matrix4x4(),
            f.placement.get_absolute_placement().get_matrix4x4(),
            atol=1e-12,
        )
        if isinstance(f, ada.Beam):
            for attr in ("xvec", "yvec", "up"):
                np.testing.assert_allclose(getattr(n, attr), getattr(f, attr), atol=1e-12)
            np.testing.assert_allclose(n.n1.p, f.n1.p, atol=1e-12)
            np.testing.assert_allclose(n.n2.p, f.n2.p, atol=1e-12)
            assert n.section.type == f.section.type
            for key in ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt"):
                assert getattr(n.section, key) == getattr(f.section, key), key
            assert len(n.booleans) == len(f.booleans) == 1
            nb, fb = n.booleans[0].primitive, f.booleans[0].primitive
            np.testing.assert_allclose(nb.poly.origin, fb.poly.origin, atol=1e-12)
            np.testing.assert_allclose(nb.poly.normal, fb.poly.normal, atol=1e-12)
            assert nb.flip == fb.flip
        else:
            assert n.t == pytest.approx(f.t)
            np.testing.assert_allclose(np.asarray(n.poly.points3d), np.asarray(f.poly.points3d), atol=1e-12)
