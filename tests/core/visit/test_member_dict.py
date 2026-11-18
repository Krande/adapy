"""A beam's metadata describes it fully enough to make it again: a POLY section by its outline, and its booleans.

Round trip: beam -> ``beam_metadata`` -> ``beam_from_metadata`` -> beam with the same section (outline, area) and
the same booleans (kind, parameters, placement, operation)."""

import json

import numpy as np
import pytest

import ada
from ada.api.curves import ArcSegment, CurvePoly2d, LineSegment
from ada.comms.msg_handling.member_dict import (
    UnknownPrimitive,
    beam_from_metadata,
    booleans_from_list,
    booleans_to_list,
    num,
    outline_from_list,
    outline_to_list,
    section_from_dict,
    section_outline,
)
from ada.comms.msg_handling.object_metadata import beam_metadata, section_to_dict
from ada.geom.booleans import BoolOpEnum
from ada.sections.categories import BaseTypes


def _curve(points):
    return CurvePoly2d(points, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))


def _i_poly(name="I_poly"):
    """An I-like outline with rounded web-to-flange corners (not a rectangle: no box keys)."""
    h, b, tw, tf, r = 0.3, 0.15, 0.0071, 0.0107, 0.015
    hh, hb, ht = h / 2, b / 2, tw / 2
    pts = [
        (-hb, -hh),
        (hb, -hh),
        (hb, -hh + tf),
        (ht, -hh + tf, r),
        (ht, hh - tf, r),
        (hb, hh - tf),
        (hb, hh),
        (-hb, hh),
        (-hb, hh - tf),
        (-ht, hh - tf, r),
        (-ht, -hh + tf, r),
        (-hb, -hh + tf),
    ]
    return ada.Section(name, sec_type=BaseTypes.POLY, outer_poly=_curve(pts))


def _rounded_box_poly(name="RHS_poly"):
    w, t, r = 0.2, 0.01, 0.02
    hw, iw = w / 2, w / 2 - t
    outer = [(-hw, -hw, r), (hw, -hw, r), (hw, hw, r), (-hw, hw, r)]
    inner = [(-iw, -iw, r - t), (iw, -iw, r - t), (iw, iw, r - t), (-iw, iw, r - t)]
    return ada.Section(name, sec_type=BaseTypes.POLY, outer_poly=_curve(outer), inner_poly=_curve(inner))


def _area(section) -> float:
    return float(section.properties.Ax)


def test_numbers_are_rounded_and_whole_ones_have_no_fraction():
    assert num(0.1 + 0.2) == 0.3
    assert num(2.0) == 2 and isinstance(num(2.0), int)
    assert num(-1e-9) == 0
    assert json.dumps([num(0.30000000000000004), num(0.01234567)]) == "[0.3, 0.0123]"


def test_only_a_poly_section_writes_its_outline():
    assert section_outline(ada.Section("box", from_str="BG200x200x10x10")) == {}
    d = section_to_dict(ada.Section("box", from_str="BG200x200x10x10"))
    assert "outer" not in d and "inner" not in d


def test_a_poly_outline_is_its_points_with_their_radii():
    d = section_outline(_i_poly())
    assert set(d) == {"outer"}  # no void: no inner
    assert len(d["outer"]) == 12
    radii = [p[2] for p in d["outer"] if len(p) == 3]
    assert radii == [0.015] * 4
    assert all(len(p) in (2, 3) for p in d["outer"])


@pytest.mark.parametrize("make", [_i_poly, _rounded_box_poly], ids=["i_like", "rounded_box"])
def test_a_poly_section_round_trips_with_the_same_outline_and_area(make):
    sec = make()
    d = section_to_dict(sec)
    again = section_from_dict(json.loads(json.dumps(d)))
    assert again.type == BaseTypes.POLY
    assert again.name == sec.name
    assert outline_to_list(again.poly_outer) == d["outer"]
    if "inner" in d:
        assert outline_to_list(again.poly_inner) == d["inner"]
    assert _area(again) == pytest.approx(_area(sec), rel=1e-4)


def test_the_box_keys_of_a_rounded_box_outline_are_kept_beside_it():
    d = section_to_dict(_rounded_box_poly())
    assert d["h"] == pytest.approx(0.2) and d["r"] == pytest.approx(0.02) and d["t_w"] == pytest.approx(0.01)
    assert len(d["outer"]) == 4 and len(d["inner"]) == 4


def test_a_box_sections_inner_corner_radius_round_trips_and_is_left_out_when_not_set():
    sec = ada.Section("RHS300", "BOX", h=0.3, w_top=0.3, w_btn=0.3, t_w=0.012, t_ftop=0.012, t_fbtn=0.012, r=0.018)
    assert "r_inner" not in section_to_dict(sec)  # None: the inner radius is r less the wall
    sec.r_inner = 0.012
    d = section_to_dict(sec)
    assert d["r"] == pytest.approx(0.018) and d["r_inner"] == pytest.approx(0.012)
    again = section_from_dict(json.loads(json.dumps(d)))
    assert again.type == BaseTypes.BOX
    assert again.r == pytest.approx(0.018) and again.r_inner == pytest.approx(0.012)
    assert (
        section_from_dict(
            section_to_dict(ada.Section("RHS300", "BOX", h=0.3, w_top=0.3, t_w=0.012, t_ftop=0.012, r=0.018))
        ).r_inner
        is None
    )


def test_an_outline_made_from_segments_writes_its_fillets_as_corners_and_radii():
    # A square 0.2 wide whose bottom-right corner is a 0.02 fillet, given as segments (as an IFC or SAT
    # reader makes it): the arc is written as the corner it rounds and its radius.
    r = 0.02
    c = (0.2 - r, r)
    mid = (c[0] + r * np.cos(-np.pi / 4), c[1] + r * np.sin(-np.pi / 4))
    segs = [
        LineSegment((0, 0, 0), (0.2 - r, 0, 0)),
        ArcSegment((0.2 - r, 0, 0), (0.2, r, 0), midpoint=(mid[0], mid[1], 0)),
        LineSegment((0.2, r, 0), (0.2, 0.2, 0)),
        LineSegment((0.2, 0.2, 0), (0, 0.2, 0)),
        LineSegment((0, 0.2, 0), (0, 0, 0)),
    ]
    curve = CurvePoly2d.from_segments(segs)
    pts = outline_to_list(curve)
    assert len(pts) == 4
    rounded = [p for p in pts if len(p) == 3]
    assert len(rounded) == 1 and rounded[0][2] == pytest.approx(r)
    again = outline_from_list(pts)
    assert len([s for s in again.segments if isinstance(s, ArcSegment)]) == 1


def test_a_section_without_an_outline_is_made_from_its_type_or_name():
    box = section_from_dict({"name": "B", "type": "BOX", "h": 0.3, "w_top": 0.3, "t_w": 0.012, "t_ftop": 0.012})
    assert box.type == BaseTypes.BOX and box.h == pytest.approx(0.3)
    named = section_from_dict({"name": "HP200x10"})
    assert named.type is not None
    with pytest.raises(ValueError):
        section_from_dict({})


def _cut_beam(section) -> ada.Beam:
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), section, up=(0, 0, 1))
    # A cope at the start (a box), a bolt hole through the web (a cylinder), a notch as an extruded triangle.
    bm.add_boolean(ada.PrimBox("cope", (-0.01, -0.1, 0.05), (0.2, 0.1, 0.2)))
    bm.add_boolean(ada.PrimCyl("hole", (1.0, -0.1, 0.0), (1.0, 0.1, 0.0), 0.011))
    bm.add_boolean(
        ada.PrimExtrude(
            "notch", [(0, 0), (0.1, 0), (0, 0.05)], 0.4, normal=(0, 1, 0), origin=(1.8, -0.2, -0.15), xdir=(1, 0, 0)
        )
    )
    return bm


def test_a_beam_without_booleans_writes_none():
    bm = ada.Beam("plain", (0, 0, 0), (1, 0, 0), "BG200x200x10x10")
    assert "booleans" not in beam_metadata(bm.name, bm.section, bm.material, beam=bm)


def test_a_beam_round_trips_through_its_metadata_with_its_section_and_booleans():
    bm = _cut_beam(_i_poly())
    meta = json.loads(json.dumps(beam_metadata(bm.name, bm.section, bm.material, beam=bm)))
    assert [b["kind"] for b in meta["booleans"]] == ["box", "cyl", "extrude"]
    assert all("op" not in b for b in meta["booleans"])  # cuts: the default is not written
    assert meta["booleans"][1] == {"kind": "cyl", "p1": [1, -0.1, 0], "p2": [1, 0.1, 0], "r": 0.011}

    again = beam_from_metadata(meta)
    assert again.section.type == BaseTypes.POLY
    assert _area(again.section) == pytest.approx(_area(bm.section), rel=1e-4)
    assert np.allclose(again.n1.p, bm.n1.p) and np.allclose(again.n2.p, bm.n2.p)
    assert len(again.booleans) == 3
    box, cyl, ext = (b.primitive for b in again.booleans)
    assert (
        isinstance(box, ada.PrimBox)
        and np.allclose(box.p1, (-0.01, -0.1, 0.05))
        and np.allclose(box.p2, (0.2, 0.1, 0.2))
    )
    assert isinstance(cyl, ada.PrimCyl) and cyl.r == pytest.approx(0.011) and np.allclose(cyl.p1, (1.0, -0.1, 0.0))
    assert isinstance(ext, ada.PrimExtrude) and ext.extrude_depth == pytest.approx(0.4)
    assert np.allclose(ext.poly.origin, (1.8, -0.2, -0.15)) and np.allclose(ext.poly.normal, (0, 1, 0))
    assert all(b.bool_op == BoolOpEnum.DIFFERENCE for b in again.booleans)
    # And written again, the same.
    assert booleans_to_list(again) == meta["booleans"]


def test_a_boolean_that_is_not_a_cut_says_its_operation():
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "BG200x200x10x10")
    bm.add_boolean(ada.PrimSphere("s", (0.5, 0, 0), 0.05), "union")
    items = booleans_to_list(bm)
    assert items == [{"kind": "sphere", "center": [0.5, 0, 0], "r": 0.05, "op": "union"}]
    (b,) = booleans_from_list(items)
    assert b.bool_op == BoolOpEnum.UNION and b.primitive.radius == pytest.approx(0.05)


def test_a_box_with_a_placement_of_its_own_keeps_it():
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "BG200x200x10x10")
    bm.add_boolean(ada.PrimBox("b", (0, 0, 0), (0.1, 0.1, 0.1), origin=(0.5, 0, 0)))
    (item,) = booleans_to_list(bm)
    assert item["place"]["origin"] == [0.5, 0, 0]
    (b,) = booleans_from_list([item])
    assert np.allclose(b.primitive.placement.origin, (0.5, 0, 0))


def test_a_primitive_of_an_unknown_kind_is_written_with_a_note_and_not_made_silently():
    item = {"kind": "PrimSweep", "note": "not written: a primitive of this kind has no description here"}
    with pytest.raises(UnknownPrimitive):
        booleans_from_list([item])
    assert booleans_from_list([item], strict=False) == []


def test_a_large_metadata_entry_stays_small():
    # The I-section beam with three cuts: its metadata entry, compact JSON, within 1.5 kB.
    bm = _cut_beam(_i_poly())
    text = json.dumps(beam_metadata(bm.name, bm.section, bm.material, beam=bm), separators=(",", ":"))
    assert len(text) < 1500, len(text)
