import math

import numpy as np
import pytest

import ada
from ada import Assembly, BeamSweep, CurvePoly2d, Part
from ada.geom import curves as gc
from ada.geom.solids import FixedReferenceSweptAreaSolid

# A tubular member bent along an arc: OD 406 mm, wall 20 mm, bend radius 5 m.
R = 5.0
LEG = 3.0
SEC_TUBE = "OD406x20"
RO, RI = 0.203, 0.183
TUBE_AREA = math.pi * (RO**2 - RI**2)


def test_sweep_beam(tmp_path):
    curve = CurvePoly2d.from_3d_points([(10, 0, 0), (11, 5.0, 0.0, 3), (10, 10, 0)])
    bm = BeamSweep("MyBeam", sec="IPE600", curve=curve)
    a = Assembly("ExportedPlates", units="m") / (Part("MyPart") / bm)
    _ = a.to_ifc(tmp_path / "my_swept_beam_elem_m.ifc", file_obj_only=False, validate=True)


def _quarter_arc() -> gc.ArcLine:
    """Quarter circle in the XZ plane: starts at the origin heading +X, ends at (R, 0, R)
    heading +Z."""
    s = math.sqrt(0.5)
    return gc.ArcLine((0, 0, 0), (R * s, 0, R - R * s), (R, 0, R))


def _j_points():
    """Two straight legs joined by a 90 degree bend: down a vertical leg, round the bend, out
    along a horizontal leg."""
    return [(0, 0, LEG + R), (0, 0, 0, R), (LEG + R, 0, 0)]


def _stream_mesh(obj):
    """Tessellate through the NGEOM stream kernel, the default engine of the GLB conversions."""
    import trimesh

    from ada.cad import active_backend

    be = active_backend()
    if not hasattr(be, "tessellate_stream"):
        pytest.skip("active CAD backend has no stream tessellator")
    bm = be.tessellate_stream([("0", obj.solid_geom())], pipeline="libtess2")
    pos = np.asarray(bm.positions, dtype=float).reshape(-1, 3)
    idx = np.asarray(bm.indices).reshape(-1, 3)
    mesh = trimesh.Trimesh(pos, idx, process=True)
    mesh.merge_vertices()
    return mesh


def test_quarter_circle_tube_frames_follow_the_arc():
    bm = BeamSweep("bend", _quarter_arc(), SEC_TUBE)
    geom = bm.solid_geom().geometry
    assert isinstance(geom, FixedReferenceSweptAreaSolid)
    # The section keeps its bore.
    assert len(geom.swept_area.inner_curves) == 1

    origins, dir_x, dir_y = geom.precomputed_frames
    tangents = np.cross(dir_x, dir_y)
    # End faces: normal to the path at both ends -- the start face in the x = 0 plane, the end
    # face in the z = R plane.
    np.testing.assert_allclose(origins[0], (0, 0, 0), atol=1e-12)
    np.testing.assert_allclose(origins[-1], (R, 0, R), atol=1e-12)
    np.testing.assert_allclose(tangents[0], (1, 0, 0), atol=1e-12)
    np.testing.assert_allclose(tangents[-1], (0, 0, 1), atol=1e-12)
    # Every station lies on the arc and is normal to it.
    centre = np.array([0.0, 0.0, R])
    np.testing.assert_allclose(np.linalg.norm(origins - centre, axis=1), R, atol=1e-12)
    np.testing.assert_allclose(np.einsum("ij,ij->i", tangents, origins - centre), 0.0, atol=1e-12)
    # No twist: the out-of-plane axis of the section stays the bend-plane normal throughout.
    plane_axis = dir_x if abs(dir_x[0][1]) > 0.5 else dir_y
    np.testing.assert_allclose(np.abs(plane_axis[:, 1]), 1.0, atol=1e-12)


def test_quarter_circle_tube_mesh_volume_and_extent():
    bm = BeamSweep("bend", _quarter_arc(), SEC_TUBE)
    mesh = _stream_mesh(bm)
    assert mesh.is_watertight
    expected = TUBE_AREA * (math.pi * R / 2)
    assert mesh.volume == pytest.approx(expected, rel=0.015)

    (xmin, ymin, zmin), (xmax, ymax, zmax) = mesh.bounds
    # Nothing behind the start face (x = 0) or beyond the end face (z = R).
    assert xmin == pytest.approx(0.0, abs=1e-6)
    assert zmax == pytest.approx(R, abs=1e-6)
    assert xmax == pytest.approx(R + RO, abs=2e-3)
    assert zmin == pytest.approx(-RO, abs=2e-3)
    assert ymin == pytest.approx(-RO, abs=2e-3) and ymax == pytest.approx(RO, abs=2e-3)

    # The start face is the annulus around the path start, in the x = 0 plane.
    start = mesh.vertices[np.abs(mesh.vertices[:, 0]) < 1e-9]
    radii = np.linalg.norm(start[:, 1:], axis=1)
    assert radii.min() == pytest.approx(RI, abs=1e-6) and radii.max() == pytest.approx(RO, abs=1e-6)


def test_quarter_circle_tube_brep_is_hollow_and_open_ended():
    """The CAD-kernel build (B-rep for STEP/booleans/clash checks): the bore is cut through,
    not sealed off at the ends -- a sealed cavity has the same volume, so check the area."""
    from ada.cad import active_backend

    be = active_backend()
    bm = BeamSweep("bend", _quarter_arc(), SEC_TUBE)
    shape = be.build(bm.solid_geom())
    length = math.pi * R / 2
    assert be.volume(shape) == pytest.approx(TUBE_AREA * length, rel=0.02)
    open_area = 2 * math.pi * (RO + RI) * length + 2 * TUBE_AREA
    assert be.area(shape) == pytest.approx(open_area, rel=0.02)


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(_j_points(), id="corner-radius"),
        pytest.param(
            gc.IndexedPolyCurve(
                [
                    gc.Edge((0, 0, LEG + R), (0, 0, R)),
                    gc.ArcLine((0, 0, R), (R - R * math.sqrt(0.5), 0, R - R * math.sqrt(0.5)), (R, 0, 0)),
                    gc.Edge((R, 0, 0), (LEG + R, 0, 0)),
                ]
            ),
            id="explicit-arcs",
        ),
    ],
)
def test_straight_arc_straight_tube(path):
    bm = BeamSweep("jt", path, SEC_TUBE)
    np.testing.assert_allclose(bm.n1.p, (0, 0, LEG + R))
    np.testing.assert_allclose(bm.n2.p, (LEG + R, 0, 0))

    origins, dir_x, dir_y = bm.sweep_frames()
    tangents = np.cross(dir_x, dir_y)
    np.testing.assert_allclose(tangents[0], (0, 0, -1), atol=1e-12)
    np.testing.assert_allclose(tangents[-1], (1, 0, 0), atol=1e-12)

    mesh = _stream_mesh(bm)
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(TUBE_AREA * (2 * LEG + math.pi * R / 2), rel=0.015)
    np.testing.assert_allclose(mesh.bounds, [(-RO, -RO, -RO), (LEG + R, RO, LEG + R)], atol=2e-3)


def test_corner_radius_and_explicit_arcs_give_the_same_stations():
    a = BeamSweep("a", _j_points(), SEC_TUBE).sweep_frames()
    b_path = gc.IndexedPolyCurve(
        [
            gc.Edge((0, 0, LEG + R), (0, 0, R)),
            gc.ArcLine((0, 0, R), (R - R * math.sqrt(0.5), 0, R - R * math.sqrt(0.5)), (R, 0, 0)),
            gc.Edge((R, 0, 0), (LEG + R, 0, 0)),
        ]
    )
    b = BeamSweep("b", b_path, SEC_TUBE).sweep_frames()
    for x, y in zip(a, b):
        np.testing.assert_allclose(x, y, atol=1e-9)


def test_i_section_bent_in_plan_stays_upright():
    """An I-section rolled about its strong axis in a horizontal plane: the web stays vertical
    (the section's local z keeps to global Z) all the way round."""
    s = math.sqrt(0.5)
    arc = gc.ArcLine((0, 0, 0), (R * s, R - R * s, 0), (R, R, 0))
    bm = BeamSweep("ring", arc, "IPE300", up=(0, 0, 1))
    _, dir_x, dir_y = bm.sweep_frames()
    np.testing.assert_allclose(dir_y, np.tile((0, 0, 1), (len(dir_y), 1)), atol=1e-12)
    np.testing.assert_allclose(dir_x[:, 2], 0.0, atol=1e-12)

    mesh = _stream_mesh(bm)
    assert mesh.is_watertight
    assert mesh.volume > 0  # outward-facing
    h = bm.section.h
    assert mesh.bounds[0][2] == pytest.approx(-h / 2, abs=1e-6)
    assert mesh.bounds[1][2] == pytest.approx(h / 2, abs=1e-6)
    assert mesh.volume == pytest.approx(bm.section.properties.Ax * (math.pi * R / 2), rel=0.02)


def test_i_section_bent_in_elevation_keeps_its_web_in_the_bend_plane():
    """The same section bent about its weak axis: with ``up`` normal to the bend plane the
    flanges stay parallel to it (section local z along global Y on every station)."""
    bm = BeamSweep("bent", _j_points(), "IPE300", up=(0, 1, 0))
    _, dir_x, dir_y = bm.sweep_frames()
    np.testing.assert_allclose(dir_y, np.tile((0, 1, 0), (len(dir_y), 1)), atol=1e-12)

    mesh = _stream_mesh(bm)
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(bm.section.properties.Ax * (2 * LEG + math.pi * R / 2), rel=0.02)
    h, w = bm.section.h, bm.section.w_top
    np.testing.assert_allclose(mesh.bounds[:, 1], (-h / 2, h / 2), atol=1e-6)
    np.testing.assert_allclose(mesh.bounds[0][[0, 2]], (-w / 2, -w / 2), atol=1e-6)


def test_box_section_sweep_is_hollow():
    bm = BeamSweep("box", _j_points(), "BOX300x200x10x10", up=(0, 1, 0))
    mesh = _stream_mesh(bm)
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(bm.section.properties.Ax * (2 * LEG + math.pi * R / 2), rel=0.02)


def test_swept_tube_to_gltf(tmp_path):
    import trimesh

    bm = BeamSweep("jt", _j_points(), SEC_TUBE)
    a = Assembly("a") / (Part("p") / bm)
    glb = tmp_path / "bent_tube.glb"
    a.to_gltf(glb)
    scene = trimesh.load(glb)
    meshes = list(scene.geometry.values())
    assert len(meshes) >= 1
    tris = sum(len(m.faces) for m in meshes)
    assert tris > 100
    # The GLB is Y-up; compare extents rather than axes.
    extents = sorted(np.ptp(np.vstack([m.vertices for m in meshes]), axis=0))
    np.testing.assert_allclose(extents, sorted([2 * RO, LEG + R + RO, LEG + R + RO]), atol=5e-3)


def test_swept_tube_ifc_roundtrip_keeps_the_arc(tmp_path):
    bm = BeamSweep("jt", _j_points(), SEC_TUBE)
    fp = (Assembly() / (Part("p") / bm)).to_ifc(tmp_path / "bent.ifc", file_obj_only=True, validate=True)

    got = ada.from_ifc(fp).get_by_name("jt")
    assert isinstance(got, BeamSweep)
    assert any(isinstance(s, gc.ArcLine) for s in got.directrix.segments)
    for x, y in zip(got.sweep_frames(), bm.sweep_frames()):
        np.testing.assert_allclose(x, y, atol=1e-6)


def test_prim_sweep_centred_profile_stays_on_the_path():
    """A PrimSweep profile is placed in 3D at the path start. The stream kernel used to read it
    as a flat section and collapse the sweep to a sheet; it must sweep the profile centred on the
    path as authored."""
    sq = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
    ps = ada.PrimSweep("ps", [(0, 0, 0), (LEG + R, 0, 0, R), (LEG + R, 0, LEG + R)], sq)
    mesh = _stream_mesh(ps)
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(0.04 * (2 * LEG + math.pi * R / 2), rel=0.01)
    np.testing.assert_allclose(mesh.bounds, [(0, -0.1, -0.1), (LEG + R + 0.1, 0.1, LEG + R)], atol=1e-6)


def test_swept_beam_follows_a_planar_curve():
    """The planar-curve input of the original API: sweeps the curve's points first to last as an
    open path, not round the closed polygon."""
    curve = CurvePoly2d.from_3d_points([(0, 0, 0), (5, 0, 0), (5, 5, 0)])
    bm = BeamSweep("planar", curve, SEC_TUBE)
    np.testing.assert_allclose(bm.n1.p, curve.points3d[0])
    np.testing.assert_allclose(bm.n2.p, curve.points3d[-1])
    assert len(bm.directrix.segments) == 2
    mesh = _stream_mesh(bm)
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(TUBE_AREA * (5 + math.sqrt(50)), rel=0.03)
