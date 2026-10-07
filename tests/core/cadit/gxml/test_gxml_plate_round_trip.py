"""A GeniE plate is one adapy plate, whatever its ACIS body looks like.

A GeniE ``<flat_plate>``/``<curved_shell>`` names the ACIS faces it occupies, and a plate
that stiffeners or its neighbours imprinted names several. adapy reads such a plate as one
:class:`~ada.Plate` by merging the faces' outlines, and the SAT writer splits it again on
the way out, so the plate goes back to GeniE as one element over the same faces.

The fixtures are GeniE V9.3-00 workspaces saved twice, ACIS body text and binary (see
``test_from_gnx_binary_acis``); every case runs on both.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.core.vector_utils import merge_coplanar_loops_by_edge_cancellation

BODIES = ("text", "binary")


@pytest.fixture
def genie93(fem_files) -> pathlib.Path:
    return fem_files / "sesam" / "genie93_acis"


def _plates(a: ada.Assembly) -> list:
    return sorted(a.get_all_physical_objects(by_type=(ada.Plate, ada.PlateCurved)), key=lambda p: p.name)


def _outline_area(pl: ada.Plate) -> float:
    pts = np.asarray(pl.poly.points2d, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


# --- a plate split by a stiffener that ends on another (a T-junction) ----------------------
#
# GeniE: Plate(0,0 .. 4,3), Bm1 along x=2 over the full height, Bm2 along y=1.5 from x=2 to 4.
# The body holds 3 faces: the left half, whose edge along x=2 carries a vertex at (2, 1.5)
# where Bm2 meets Bm1, and the two right quarters. The left face's loop lists (2, 1.5) as a
# vertex on a straight line, the face reader drops it as collinear, and the right quarters'
# edges along x=2 then have nothing to cancel against. Measured before the fix: 3 plates
# (Pl1, Pl1_02, Pl1_03); GeniE holds 1.


def test_t_junction_loops_merge_only_when_split():
    left = [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 3, 0)]
    lower_right = [(2, 0, 0), (4, 0, 0), (4, 1.5, 0), (2, 1.5, 0)]
    upper_right = [(2, 1.5, 0), (4, 1.5, 0), (4, 3, 0), (2, 3, 0)]
    loops = [left, lower_right, upper_right]

    assert merge_coplanar_loops_by_edge_cancellation(loops) is None
    merged = merge_coplanar_loops_by_edge_cancellation(loops, split_t_junctions=True)
    assert merged is not None
    assert sorted(tuple(round(c, 12) for c in p) for p in merged) == [(0, 0, 0), (0, 3, 0), (4, 0, 0), (4, 3, 0)]


def test_t_junction_split_leaves_a_vertex_off_the_edge_alone():
    # (2, 1.5 + 1e-3) is a millimetre off the left face's edge: not on it, so nothing to split and
    # the three loops do not form one outline.
    left = [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 3, 0)]
    lower_right = [(2, 0, 0), (4, 0, 0), (4, 1.5, 0), (2.001, 1.5, 0)]
    upper_right = [(2.001, 1.5, 0), (4, 1.5, 0), (4, 3, 0), (2, 3, 0)]
    assert merge_coplanar_loops_by_edge_cancellation([left, lower_right, upper_right], split_t_junctions=True) is None


@pytest.mark.parametrize("body", BODIES)
def test_t_junction_plate_reads_as_one_plate(genie93, body):
    a = ada.from_gnx(genie93 / f"plate_t_junction_{body}.gnx")
    (pl,) = _plates(a)
    assert type(pl) is ada.Plate and pl.name == "Pl1"
    assert pl.metadata["props"]["gxml_face_refs"] == ["FACE00000001", "FACE00000002", "FACE00000003"]
    assert _outline_area(pl) == pytest.approx(12.0, rel=1e-12)
    assert len(list(a.get_all_physical_objects(by_type=ada.Beam))) == 2


@pytest.mark.parametrize("body", BODIES)
def test_t_junction_plate_writes_back_as_one_plate_over_three_faces(genie93, body, tmp_path):
    """The stiffeners split the merged plate again on the way out: one element, three faces."""
    from ada.cadit.sat.write.writer import part_to_sat_writer

    a = ada.from_gnx(genie93 / f"plate_t_junction_{body}.gnx")
    (pl,) = _plates(a)
    sw = part_to_sat_writer(a, imprint=True)
    assert len(sw.face_map[pl.guid]) == 3

    back = ada.from_gnx(a.to_gnx(tmp_path / "rt.gnx", binary_acis=body == "binary"))
    (rpl,) = _plates(back)
    assert rpl.name == "Pl1" and len(rpl.metadata["props"]["gxml_face_refs"]) == 3
    assert _outline_area(rpl) == pytest.approx(12.0, rel=1e-12)


# --- a cylindrical shell (GeniE writes it on an ACIS cone-surface) -----------------------
#
# cylinder_shell: a quarter cylinder, radius 1 about +z, height 2 (cone-surface, cosine 1).
# swept_arc_shell: SweepCurve of the arc (0,0,0)-(1,0.5,0)-(2,0,0) along z by 2 (cone-surface
# about (1, -0.75), radius 1.25, cosine -1). Both read as a flat Plate on their four corners
# before: 2.83 m2 and 4.00 m2 instead of pi and 4.64.

#: the swept arc's angle, as GeniE's edges state it
SWEPT_ANGLE = 1.8545904360032246


def _occ_area(pl) -> float:
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    from ada.occ.geom import geom_to_occ_geom

    props = GProp_GProps()
    # adaptive integration: the default under-integrates a face bounded by a rational spline
    brepgprop.SurfaceProperties(geom_to_occ_geom(pl.geom), props, 1e-9)
    return props.Mass()


def _oriented_normal(pl, point) -> np.ndarray:
    """The plate's normal at ``point`` on its cylinder: radial, through the face sense and flag."""
    surf = pl.geom.geometry.face_surface
    c = np.asarray(surf.position.location, dtype=float)
    ax = np.asarray(surf.position.axis, dtype=float)
    d = np.asarray(point, dtype=float) - c
    radial = d - ax * float(d @ ax)
    radial /= np.linalg.norm(radial)
    sign = (1 if pl.geom.geometry.same_sense else -1) * (1 if pl.gxml_sense_flag() else -1)
    return sign * radial


CYLINDERS = {
    # model: (centre, radius, same_sense, area, a point on it, the normal GeniE meshes there)
    "cylinder_shell": ((0, 0, 0), 1.0, True, np.pi, (np.sqrt(0.5), np.sqrt(0.5), 1.0), (np.sqrt(0.5), np.sqrt(0.5), 0)),
    "swept_arc_shell": ((1, -0.75, 0), 1.25, False, 1.25 * SWEPT_ANGLE * 2, (1.0, 0.5, 1.0), (0, -1, 0)),
}


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("model", sorted(CYLINDERS))
def test_cylindrical_shell_reads_on_its_cylinder(genie93, model, body):
    """Area exact; the normal is the one GeniE meshes with.

    Measured on GeniE V9.3 meshes of the two (sense flag true, face forward): the element normals
    point away from the axis on cylinder_shell (cosine 1) and towards it on swept_arc_shell
    (cosine -1).
    """
    centre, radius, same_sense, area, point, normal = CYLINDERS[model]
    (pl,) = _plates(ada.from_gnx(genie93 / f"{model}_{body}.gnx"))
    assert type(pl) is ada.PlateCurved and pl.name == "Sh1"
    surf = pl.geom.geometry.face_surface
    assert type(surf).__name__ == "CylindricalSurface"
    assert surf.radius == pytest.approx(radius, rel=1e-15)
    assert np.allclose(surf.position.location, centre, atol=1e-15)
    assert np.allclose(surf.position.axis, (0, 0, 1))
    assert pl.geom.geometry.same_sense is same_sense
    assert _occ_area(pl) == pytest.approx(area, rel=1e-12)
    assert np.allclose(_oriented_normal(pl, point), normal, atol=1e-12)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("model", sorted(CYLINDERS))
def test_cylindrical_shell_writes_back_the_records_genie_wrote(genie93, model, body, tmp_path):
    """The cone-surface goes out as GeniE wrote it, and comes back as the same shell."""
    import re
    import zipfile

    src = genie93 / f"{model}_text.gnx"
    a = ada.from_gnx(genie93 / f"{model}_{body}.gnx")
    gnx = a.to_gnx(tmp_path / "rt.gnx", binary_acis=body == "binary")

    def cone(text: str) -> list[float | str]:
        (rec,) = re.findall(r"cone-surface \$-1 -1 -1 \$-1 ([^#]*)#", text)
        return [float(x) if re.fullmatch(r"[-0-9.e]+", x) else x for x in rec.split()]

    original = cone(zipfile.ZipFile(src).read("acisGeometry.sat").decode())
    written = cone(ada.cadit.sat.write.writer.part_to_sat_writer(a).to_str())
    assert len(written) == len(original)
    for w, o in zip(written, original):
        assert w == o or (isinstance(w, float) and w == pytest.approx(o, rel=1e-15, abs=1e-15))
    assert re.findall(r"<(?:flat_plate|curved_shell)\b", zipfile.ZipFile(gnx).read("modelData.xml").decode()) == [
        "<curved_shell"
    ]

    (back,) = _plates(ada.from_gnx(gnx))
    (pl,) = _plates(a)
    s_back, s_read = back.geom.geometry.face_surface, pl.geom.geometry.face_surface
    assert s_back.radius == pytest.approx(s_read.radius, rel=1e-15)  # 1 ulp: |major| -> unit * radius -> |major|
    for field in ("location", "axis", "ref_direction"):
        assert np.allclose(getattr(s_back.position, field), getattr(s_read.position, field), rtol=0, atol=1e-15)
    assert back.geom.geometry.same_sense is pl.geom.geometry.same_sense
    assert back.gxml_sense_flag() is pl.gxml_sense_flag()
    assert _occ_area(back) == pytest.approx(_occ_area(pl), rel=1e-12)


@pytest.mark.parametrize(
    "fields, why",
    [
        ("0 0 0 0 0 1 1 0 0 1 I I 0.5 0.8660254037844386 1 forward I I I I", "a true cone"),
        ("0 0 0 0 0 1 1 0 0 0.5 I I 0 1 1 forward I I I I", "an elliptic cylinder"),
        ("0 0 0 0 0 1 1 0 0 1 I I 0 1 1 reversed I I I I", "u parameter 'reversed'"),
    ],
)
def test_a_cone_surface_that_is_no_circular_cylinder_is_refused_by_name(fields, why):
    from ada.cadit.sat.exceptions import ACISUnsupportedSurfaceType
    from ada.cadit.sat.read.advanced_face import get_cylindrical_surface
    from ada.cadit.sat.store import SatStore

    store = SatStore()
    store.add(f"-6 cone-surface $-1 -1 -1 $-1 {fields} #")
    with pytest.raises(ACISUnsupportedSurfaceType, match=why):
        get_cylindrical_surface(store.get("$6"))


# --- a plate with a hole ------------------------------------------------------------------
#
# GeniE: Plate(0,0 .. 4,3) and CreateHoleFromProfile(ProfileRR(0.8, 0.8, 0.4)) at (2, 1.5): a
# round hole of radius 0.4. GeniE's flat_plate names two faces: FACE00000002, the plate with a
# periphery of 4 lines and a hole loop of one closed rational B-spline, and FACE00000001, the
# disc inside -- which a <hole> concept names as its interior (GeniE's own area of the plate is
# 12). Before: two PlateCurved (the disc and the plate, the plate without its hole loop), and
# to_gnx raised IndexError.

HOLE_AREA = 12.0 - np.pi * 0.4**2


def _hole_plate(a):
    (pl,) = _plates(a)
    assert type(pl) is ada.PlateCurved and pl.name == "Pl1"
    return pl


@pytest.mark.parametrize("body", BODIES)
def test_plate_with_hole_reads_as_one_plate_with_an_inner_loop(genie93, body):
    pl = _hole_plate(ada.from_gnx(genie93 / f"plate_with_hole_{body}.gnx"))
    face = pl.geom.geometry
    assert type(face.face_surface).__name__ == "Plane"
    outer, hole = face.bounds
    assert [type(oe.edge_element.edge_geometry).__name__ for oe in outer.bound.edge_list] == ["Line"] * 4
    (circle,) = hole.bound.edge_list
    assert type(circle.edge_element.edge_geometry).__name__ == "RationalBSplineCurveWithKnots"
    assert np.allclose(circle.start, circle.end) and (circle.t_start, circle.t_end) == (np.pi, 0.0)
    assert pl.metadata["props"]["gxml_face_ref"] == "FACE00000002"
    assert pl.metadata["props"]["gxml_element"] == "flat_plate"
    # the hole wound against the outline: the face is valid and its area is the plate's less the disc
    assert _occ_area(pl) == pytest.approx(HOLE_AREA, rel=1e-12)


@pytest.mark.parametrize("body", BODIES)
def test_plate_with_hole_writes_back_as_one_flat_plate_with_its_hole(genie93, body, tmp_path):
    import re
    import zipfile

    from ada.cadit.sat.write import sat_entities as se
    from ada.cadit.sat.write.writer import part_to_sat_writer

    a = ada.from_gnx(genie93 / f"plate_with_hole_{body}.gnx")
    sw = part_to_sat_writer(a)
    (face,) = sw.get_entities_by_type(se.Face)
    hole_loop = face.loop.next_loop
    assert hole_loop is not None and hole_loop.next_loop is None
    assert hole_loop.coedge.next_coedge is hole_loop.coedge
    assert "exactcur full nurbs 2 closed 5" in sw.to_str()

    gnx = a.to_gnx(tmp_path / "rt.gnx", binary_acis=body == "binary")
    xml = zipfile.ZipFile(gnx).read("modelData.xml").decode()
    (plate_xml,) = re.findall(r"<(?:flat_plate|curved_shell)\b.*?</(?:flat_plate|curved_shell)>", xml, re.S)
    assert plate_xml.startswith('<flat_plate name="Pl1"')
    assert '<vector x="0.0" y="0.0" z="1.0" dir="z" />' in plate_xml
    assert len(re.findall(r"<face ", plate_xml)) == 1

    back = _hole_plate(ada.from_gnx(gnx))
    assert len(back.geom.geometry.bounds) == 2
    assert _occ_area(back) == pytest.approx(HOLE_AREA, rel=1e-12)


def test_a_loop_of_one_coedge_is_read_once(genie93, tmp_path):
    """The hole's loop is a single coedge that is its own successor; it used to come back twice."""
    import zipfile

    from ada.cadit.sat.read.curves import iter_loop_coedges
    from ada.cadit.sat.store import SatReaderFactory

    sat = tmp_path / "body.sat"
    sat.write_bytes(zipfile.ZipFile(genie93 / "plate_with_hole_text.gnx").read("acisGeometry.sat"))
    factory = SatReaderFactory(sat)
    (disc,) = [f for f in factory.iter_faces() if f.get_name() == "FACE00000001"]
    assert len(list(iter_loop_coedges(factory.sat_store.get(disc.chunks[7])))) == 1


def test_a_straight_edged_hole_reads_as_a_hole(genie93, tmp_path):
    """A plane face whose loops are all straight is still not a polygon when it has a hole.

    The flat path takes the periphery alone, so a square hole would have vanished. Written here
    by adapy's own SAT writer: GeniE's round hole re-cut as a 0.8 m square hole.
    """
    import copy

    from ada.cadit.sat.store import SatReaderFactory
    from ada.cadit.sat.write.writer import part_to_sat_writer
    from ada.geom import curves as geo_cu
    from ada.geom import surfaces as geo_su

    a = ada.from_gnx(genie93 / "plate_with_hole_text.gnx")
    pl = _hole_plate(a)
    face = pl.geom.geometry
    corners = [(1.6, 1.1, 0.0), (1.6, 1.9, 0.0), (2.4, 1.9, 0.0), (2.4, 1.1, 0.0)]  # against the outline
    edges = []
    for i, p1 in enumerate(corners):
        p2 = corners[(i + 1) % 4]
        d = np.subtract(p2, p1)
        line = geo_cu.Line(ada.Point(*p1), ada.Direction(*d))
        ec = geo_cu.EdgeCurve(start=ada.Point(*p1), end=ada.Point(*p2), edge_geometry=line, same_sense=True)
        edges.append(geo_cu.OrientedEdge(ada.Point(*p1), ada.Point(*p2), ec, True, t_start=0.0, t_end=0.8))
    square = copy.copy(face)
    square.bounds = [face.bounds[0], geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)]
    pl.geom.geometry = square

    sat = tmp_path / "square_hole.sat"
    sat.write_text(part_to_sat_writer(a).to_str())
    factory = SatReaderFactory(sat)
    (face_record,) = list(factory.iter_faces())
    assert factory.face_has_hole(face_record) and not factory.face_has_curved_edge(face_record)
    ((name, geom),) = list(factory.iter_curved_face())
    assert len(geom.geometry.bounds) == 2


@pytest.mark.parametrize("body", BODIES)
def test_a_vertex_embedded_in_a_face_is_no_hole(genie93, body):
    """GeniE embeds a point mass's position in the face it lies on as a loop of one curve-less edge.

    That loop bounds nothing: the plate is the polygon it always was (4 of the 889 faces of a
    250-plate workspace carry one). Twin: Plate(0,0 .. 4,3) with PointMass at (1, 1, 0).
    """
    a = ada.from_gnx(genie93 / f"plate_point_mass_{body}.gnx")
    (pl,) = _plates(a)
    assert type(pl) is ada.Plate and pl.name == "Pl1"
    assert _outline_area(pl) == pytest.approx(12.0, rel=1e-12)
