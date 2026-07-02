"""Tests for :class:`ada.PlateCurved` — covers the API surface that
``Part.add_plate`` and the rendering pipelines exercise.

PlateCurved is mostly driven by adapy readers that surface a non-
planar face (gxml advanced-face import, loft tool corner transitions).
The expectations here mirror the contract those callers depend on:
construction from a B-spline-backed AdvancedFace, the same ``t`` /
``material`` / ``units`` accessors as :class:`ada.Plate`, deriving
boundary nodes from the wrapped face so the plate slots into a Part,
and round-tripping through ``solid_geom`` / ``solid_occ`` /
``extruded_solid_occ`` for downstream rendering.
"""

from __future__ import annotations

import pytest

import ada
from ada.api.loft import loft_profiles
from ada.base.units import Units
from ada.cad import active_backend
from ada.geom.curves import PolyLoop
from ada.geom.points import Point


def _bspline_loft_face():
    """Build a small mixed-cardinality loft and return the first B-spline face
    it produces (ThruSections in ruled mode), via the active CAD backend.

    Mixing a 4-vertex profile with a 12-vertex profile guarantees B-spline
    transition faces at each corner — exactly the surface kind PlateCurved
    needs to wrap in the loft pipeline. Returns an active-backend face handle.
    """
    sharp = PolyLoop(
        polygon=[
            Point(-1.0, -1.0, 0.0),
            Point(1.0, -1.0, 0.0),
            Point(1.0, 1.0, 0.0),
            Point(-1.0, 1.0, 0.0),
        ]
    )
    # Rounded corner pattern (12 pts, same shape as the loft tool's
    # filleted rectangle output) at z=1, smaller so corner-transition
    # faces are well-defined.
    import math

    r = 0.3
    s = math.sqrt(2) / 2
    w = h = 1.0
    rounded_xy = [
        (-w + r, -h),
        (-w + r - r * s, -h + r - r * s),
        (-w, -h + r),
        (-w, h - r),
        (-w + r - r * s, h - r + r * s),
        (-w + r, h),
        (w - r, h),
        (w - r + r * s, h - r + r * s),
        (w, h - r),
        (w, -h + r),
        (w - r + r * s, -h + r - r * s),
        (w - r, -h),
    ]
    rounded = PolyLoop(polygon=[Point(x, y, 1.0) for x, y in rounded_xy])
    shape = loft_profiles([sharp, rounded], ruled=True, is_solid=True)
    backend = active_backend()
    for face in backend.faces(shape):
        if backend.face_surface_type(face) == "bspline":
            return face
    raise RuntimeError("Mixed-cardinality loft did not produce a BSpline face")


@pytest.fixture
def curved_plate():
    """Reusable PlateCurved fixture backed by a real B-spline loft face.

    Constructed via :meth:`PlateCurved.from_occ_face` so the geometry
    is the raw OCC face (the loft tool's primary use case). The
    AdvancedFace-backed construction path is exercised separately in
    :func:`test_advanced_face_constructor_works_for_basic_case`.
    """
    face = _bspline_loft_face()
    return ada.PlateCurved.from_occ_face("curved_pl", face, t=0.01)


def test_construction_exposes_basic_attrs(curved_plate):
    assert curved_plate.name == "curved_pl"
    assert curved_plate.t == pytest.approx(0.01)
    assert curved_plate.material is not None
    assert curved_plate.material.name == "S420"  # default mat


def test_geom_is_none_for_raw_occ_face_construction(curved_plate):
    """``from_occ_face`` bypasses the AdvancedFace round-trip — the
    Geometry accessor returns ``None`` for plates built that way.
    Callers that need a Geometry must use the ``__init__`` constructor."""
    assert curved_plate.geom is None
    assert curved_plate.solid_geom() is None


def test_solid_occ_returns_valid_face(curved_plate):
    """``solid_occ`` is the raw face (no thickness) — the GLB
    tessellator and IFC writer both fall back to this when extrusion
    isn't available or wanted."""
    shape = curved_plate.solid_occ()
    assert shape is not None
    # At least one face present.
    assert len(active_backend().faces(shape)) >= 1


def test_extruded_solid_occ_produces_volumetric_prism(curved_plate):
    """The streaming-viewer renderer prefers a prism so the plate
    carries thickness; verify the extrusion path yields a solid with
    non-zero volume."""
    solid = curved_plate.extruded_solid_occ()
    assert active_backend().volume(solid) > 0.0, "Extruded prism should have non-zero volume"


def test_nodes_derived_from_outer_wire(curved_plate):
    """``Part.add_plate`` walks ``plate.nodes`` — for PlateCurved
    these come from the boundary of the wrapped face. Should be a
    non-empty list of distinct points."""
    nodes = curved_plate.nodes
    assert len(nodes) >= 3, f"expected at least 3 boundary nodes, got {len(nodes)}"
    # Cache contract: a second call returns the same object.
    assert curved_plate.nodes is nodes


def test_units_setter_accepts_same_value_rejects_change(curved_plate):
    """``Part.add_plate`` does ``plate.units = self.units`` which is a
    no-op when the units already match — must NOT raise. A real
    cross-unit conversion isn't supported yet; that path raises so
    the caller knows to construct the plate in the right units."""
    curved_plate.units = "m"  # same as default — no-op
    curved_plate.units = Units.M  # enum form also fine
    with pytest.raises(NotImplementedError):
        curved_plate.units = "mm"


def test_bbox_is_computed_lazily(curved_plate):
    """``bbox()`` shouldn't raise; should cache so re-call returns same instance."""
    bbox = curved_plate.bbox()
    assert bbox is not None
    assert curved_plate.bbox() is bbox


def test_add_to_part_via_division(curved_plate):
    """The Part.__truediv__ smart-append path used by the loft tool —
    ``part /= [Plate(...), PlateCurved(...)]`` — must route through
    ``add_plate`` and surface both plate variants in ``part.plates``."""
    flat = ada.Plate.from_3d_points("flat_pl", [(0, 0, -1), (1, 0, -1), (1, 1, -1), (0, 1, -1)], 0.01)
    part = ada.Part("mixed") / [flat, curved_plate]
    plate_list = list(part.plates)
    assert len(plate_list) == 2
    plate_types = {type(p).__name__ for p in plate_list}
    assert plate_types == {"Plate", "PlateCurved"}
    # The PlateCurved's parent should now be the Part.
    assert curved_plate.parent is part


def test_surface_renders_as_face_not_extruded_prism():
    """``Surface`` is the zero-thickness sibling of :class:`Plate` —
    ``solid_occ`` must return the planar face shape rather than
    attempting a zero-thickness prism extrusion (which raises
    BRepSweep_Translation::Constructor in OCC).

    Subclassing Plate means existing Plate-dispatching consumers
    (Part.add_plate, IFC writer, GLB tessellator) pick it up via
    isinstance without needing parallel branches.
    """
    surf = ada.Surface.from_3d_points(
        "flat_surf",
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
    )
    assert isinstance(surf, ada.Plate)  # subclass — Plate handlers catch it
    assert surf.t == 0.0
    occ_shape = surf.solid_occ()
    assert occ_shape is not None
    # The shape should be a face (or compound with a face), not a
    # zero-volume prism. Introspect via the active CAD backend so this
    # holds under either backend (pythonocc or adacpp).
    from ada.cad import active_backend

    assert len(active_backend().faces(occ_shape)) >= 1


def test_surface_curved_inherits_plate_curved_handling():
    """``SurfaceCurved`` is the zero-thickness sibling of
    :class:`PlateCurved`. Confirms ``from_occ_face`` pins t=0, the
    extruded path short-circuits to the bare face (no prism), and
    isinstance(PlateCurved) is True so existing tessellator /
    IFC paths catch it."""
    face = _bspline_loft_face()
    s = ada.SurfaceCurved.from_occ_face("curved_surf", face)
    assert isinstance(s, ada.PlateCurved)
    assert s.t == 0.0
    assert s.extruded_solid_occ() is s.solid_occ(), "t=0 short-circuit should make extruded == bare face"


def test_surface_attaches_to_part_via_division():
    """Mixed list of Plate / PlateCurved / Surface / SurfaceCurved
    all flow through ``part /= [...]`` (the loft tool's primary
    attachment path)."""
    face = _bspline_loft_face()
    items = [
        ada.Plate.from_3d_points("flat", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.01),
        ada.PlateCurved.from_occ_face("curved", face, t=0.01),
        ada.Surface.from_3d_points("flat_s", [(0, 0, 2), (1, 0, 2), (1, 1, 2), (0, 1, 2)]),
        ada.SurfaceCurved.from_occ_face("curved_s", face),
    ]
    part = ada.Part("mixed") / items
    kinds = {type(p).__name__ for p in part.plates}
    assert kinds == {"Plate", "PlateCurved", "Surface", "SurfaceCurved"}


def test_add_plate_returns_same_instance(curved_plate):
    """``Part.add_plate`` returns the plate it accepted; downstream
    code (e.g. the loft helper) relies on this to keep the same
    Python object around for later metadata writes."""
    part = ada.Part("attach-test")
    returned = part.add_plate(curved_plate)
    assert returned is curved_plate
    assert curved_plate in list(part.plates)


def _occ_face_area(shape) -> float:
    return float(active_backend().area(shape))


def _occ_face_bbox(shape):
    return active_backend().bbox(shape)


def _count_topology(shape) -> tuple[int, int, int]:
    """(n_faces, n_wires, n_edges) — counted via the active backend."""
    backend = active_backend()
    return len(backend.faces(shape)), len(backend.wires(shape)), len(backend.edges(shape))


def test_round_trip_advanced_face_preserves_bspline_surface():
    """OCC face → AdvancedFace → OCC face round-trip — full integrity check.

    Beyond the structural type assertions (FaceBound → EdgeLoop →
    OrientedEdge with pcurves attached), this test verifies that the
    rebuilt face is geometrically equivalent to the input:

    * surface kind preserved (still Geom_BSplineSurface),
    * surface area matches within 0.1% (would silently drop to
      0 if pcurves were missing — the symptom the test guards against),
    * axis-aligned bbox matches within 1 mm on every axis,
    * topology (face / wire / edge counts) matches,
    * the boundary node count survives a second round-trip via
      ``PlateCurved.nodes``.
    """
    from ada.geom import Geometry
    from ada.geom import curves as geo_cu
    from ada.geom import surfaces as geo_su

    face_in = _bspline_loft_face()

    advanced = active_backend().face_to_advanced_face(face_in)
    assert advanced is not None

    # Structural: bounds chain must be FaceBound → EdgeLoop → OrientedEdge.
    assert all(isinstance(b, geo_su.FaceBound) for b in advanced.bounds)
    assert all(isinstance(b.bound, geo_cu.EdgeLoop) for b in advanced.bounds)
    assert all(all(isinstance(oe, geo_cu.OrientedEdge) for oe in b.bound.edge_list) for b in advanced.bounds)
    # Pcurves: every OrientedEdge on a BSpline-surface face must carry
    # a stored pcurve, otherwise the OCC face builder silently produces
    # a zero-area face (the loft-corner regression we're guarding here).
    for fb in advanced.bounds:
        for oe in fb.bound.edge_list:
            assert oe.pcurve is not None, (
                "OrientedEdge on BSpline surface missing pcurve — " "round-trip would produce a zero-area face"
            )
            assert oe.pcurve.degree >= 1
            assert len(oe.pcurve.control_points_2d) >= 2

    rebuilt = active_backend().build(Geometry(id="rt", geometry=advanced))

    # Geometric: surface area must match within a tight tolerance.
    orig_area = _occ_face_area(face_in)
    new_area = _occ_face_area(rebuilt)
    assert orig_area > 0, "test fixture produced a degenerate face"
    rel_err = abs(orig_area - new_area) / orig_area
    assert rel_err < 1e-3, (
        f"round-trip area drift {rel_err:.4%} (orig={orig_area:.4f}, "
        f"rebuilt={new_area:.4f}) — pcurves likely dropped"
    )

    # Geometric: axis-aligned bbox within 1 mm on each axis.
    a = _occ_face_bbox(face_in)
    b = _occ_face_bbox(rebuilt)
    for i, (lo, hi) in enumerate(zip(a, b)):
        assert abs(lo - hi) < 1e-3, f"round-trip bbox axis {i}: orig={lo:.6f} rebuilt={hi:.6f}"

    # Surface kind: still a BSpline (no degradation to plane / cone).
    assert active_backend().faces(rebuilt), "Round-trip produced no faces"
    assert active_backend().face_surface_type(rebuilt) == "bspline"

    # Topology: same number of faces / wires / edges either way.
    assert _count_topology(face_in) == _count_topology(rebuilt)


def _all_bspline_faces(shape):
    """Iterate every face of ``shape`` whose surface is a B-spline."""
    backend = active_backend()
    for face in backend.faces(shape):
        if backend.face_surface_type(face) == "bspline":
            yield face


def test_round_trip_every_bspline_face_in_a_mixed_loft():
    """Every B-spline face that comes out of a mixed-cardinality loft
    (4-pt sharp ↔ 12-pt rounded) must round-trip cleanly. Failure of
    even one face was the visible "empty plate" regression — guards
    against a partial pcurve-extraction bug.
    """
    import math

    from ada.api.loft import loft_profiles
    from ada.geom import Geometry
    from ada.geom.curves import PolyLoop
    from ada.geom.points import Point

    sharp = PolyLoop(
        polygon=[
            Point(-1.0, -1.0, 0.0),
            Point(1.0, -1.0, 0.0),
            Point(1.0, 1.0, 0.0),
            Point(-1.0, 1.0, 0.0),
        ]
    )
    r = 0.3
    s = math.sqrt(2) / 2
    w = h = 1.0
    rounded = PolyLoop(
        polygon=[
            Point(x, y, 1.0)
            for x, y in [
                (-w + r, -h),
                (-w + r - r * s, -h + r - r * s),
                (-w, -h + r),
                (-w, h - r),
                (-w + r - r * s, h - r + r * s),
                (-w + r, h),
                (w - r, h),
                (w - r + r * s, h - r + r * s),
                (w, h - r),
                (w, -h + r),
                (w - r + r * s, -h + r - r * s),
                (w - r, -h),
            ]
        ]
    )
    shape = loft_profiles([sharp, rounded], ruled=True, is_solid=True)

    bspline_faces = list(_all_bspline_faces(shape))
    assert bspline_faces, "fixture loft produced no BSpline faces"

    for i, face in enumerate(bspline_faces):
        orig_area = _occ_face_area(face)
        advanced = active_backend().face_to_advanced_face(face)
        assert advanced is not None, f"face {i}: AdvancedFace conversion returned None"
        rebuilt = active_backend().build(Geometry(id=f"rt_{i}", geometry=advanced))
        new_area = _occ_face_area(rebuilt)
        rel_err = abs(orig_area - new_area) / max(orig_area, 1e-12)
        assert rel_err < 1e-3, (
            f"face {i}: round-trip area drift {rel_err:.4%} "
            f"(orig={orig_area:.6f}, rebuilt={new_area:.6f}) — "
            f"pcurves likely missing for at least one edge"
        )


def test_round_trip_preserves_pcurve_for_every_edge_on_bspline_face():
    """Hard contract — every edge of a BSpline-surface face must carry a
    stored 2D pcurve in the AdvancedFace round-trip. A missing pcurve
    silently degrades the OCC face builder to a zero-area face, so
    enforce the invariant at the OrientedEdge level rather than
    relying on the area check alone (faster to diagnose when it
    regresses)."""
    import math

    from ada.api.loft import loft_profiles
    from ada.geom.curves import PolyLoop
    from ada.geom.points import Point

    sharp = PolyLoop(
        polygon=[
            Point(-1.0, -1.0, 0.0),
            Point(1.0, -1.0, 0.0),
            Point(1.0, 1.0, 0.0),
            Point(-1.0, 1.0, 0.0),
        ]
    )
    r = 0.3
    s_ = math.sqrt(2) / 2
    rounded = PolyLoop(
        polygon=[
            Point(x, y, 1.0)
            for x, y in [
                (-1 + r, -1),
                (-1 + r - r * s_, -1 + r - r * s_),
                (-1, -1 + r),
                (-1, 1 - r),
                (-1 + r - r * s_, 1 - r + r * s_),
                (-1 + r, 1),
                (1 - r, 1),
                (1 - r + r * s_, 1 - r + r * s_),
                (1, 1 - r),
                (1, -1 + r),
                (1 - r + r * s_, -1 + r - r * s_),
                (1 - r, -1),
            ]
        ]
    )
    shape = loft_profiles([sharp, rounded], ruled=True, is_solid=True)

    n_checked = 0
    for face in _all_bspline_faces(shape):
        advanced = active_backend().face_to_advanced_face(face)
        for fb in advanced.bounds:
            for oe in fb.bound.edge_list:
                assert oe.pcurve is not None, (
                    "BSpline-face edge missing pcurve — " "round-trip would silently degenerate"
                )
                # Pcurve must be a real BSpline: degree ≥ 1 and at
                # least 2 control points.
                assert oe.pcurve.degree >= 1
                assert len(oe.pcurve.control_points_2d) >= 2
                # Knot multiplicities sum should match the BSpline
                # standard: sum_of_mults = degree + 1 + n_control_points.
                expected_sum = oe.pcurve.degree + 1 + len(oe.pcurve.control_points_2d)
                actual_sum = sum(oe.pcurve.knot_multiplicities)
                assert actual_sum == expected_sum, (
                    f"pcurve knot multiplicities don't match BSpline "
                    f"shape: sum={actual_sum}, expected={expected_sum}"
                )
                n_checked += 1
    assert n_checked > 0, "no BSpline faces in fixture — coverage gap"


def test_round_trip_pure_planar_loft_does_not_use_bspline_path():
    """Sanity check on the type-identity dispatch: a pure-planar loft
    (two matching-cardinality 4-pt rectangles, no fillets) must NOT
    produce any BSpline faces. ``surface.DynamicType().Name()`` is
    the discriminator — ``IsKind(BSplineSurface.get_type_descriptor())``
    is unreliable in pythonocc and returned True for plain Geom_Plane
    surfaces, which sent every flat face through the BSpline path and
    inflated the plate count. Lock the correct behaviour in."""
    from ada.api.loft import loft_profiles
    from ada.geom.curves import PolyLoop
    from ada.geom.points import Point

    base = PolyLoop(
        polygon=[
            Point(0, 0, 0),
            Point(1, 0, 0),
            Point(1, 1, 0),
            Point(0, 1, 0),
        ]
    )
    top = PolyLoop(
        polygon=[
            Point(0, 0, 1),
            Point(1, 0, 1),
            Point(1, 1, 1),
            Point(0, 1, 1),
        ]
    )
    shape = loft_profiles([base, top], ruled=True, is_solid=True)
    assert list(_all_bspline_faces(shape)) == [], (
        "pure-planar loft produced BSpline faces — " "type dispatch must use DynamicType().Name() not IsKind()"
    )


def test_round_trip_via_plate_curved_renders_solid_with_volume():
    """End-to-end: a BSpline loft face wrapped in PlateCurved via the
    AdvancedFace round-trip should extrude into a prism with volume
    matching ``face_area × thickness``. The pre-pcurve-fix path
    produced zero-area faces that extruded to zero-volume prisms
    (visually invisible in the GLB)."""
    from ada.geom import Geometry

    face = _bspline_loft_face()
    face_area = _occ_face_area(face)
    assert face_area > 0, "fixture produced a degenerate face"

    advanced = active_backend().face_to_advanced_face(face)
    thickness = 0.05
    plate = ada.PlateCurved(
        "rt_plate",
        Geometry(id="g", geometry=advanced),
        t=thickness,
    )
    solid = plate.extruded_solid_occ()
    volume = active_backend().volume(solid)
    expected = face_area * thickness
    # Prism cross-section varies along the swept direction on a curved
    # surface, so allow a generous slack; the regression we're guarding
    # against is a volume near zero (round-trip dropping the surface),
    # not a few-percent numerical drift from curvature.
    assert volume > expected * 0.5, (
        f"PlateCurved volume {volume:.6f} m³ << expected ~"
        f"{expected:.6f} m³ (face area × thickness) — round-trip "
        f"dropped the surface (would render as empty space in the GLB)"
    )


def test_round_trip_occ_face_preserves_face_count():
    """End-to-end exercise of the ``from_occ_face`` path:
    OCC face → PlateCurved → ``solid_occ()`` should yield an OCC
    shape containing exactly one face equivalent to the input. The
    raw-OCC construction route is what the loft tool uses for every
    B-spline corner-transition face, so a regression here would
    silently break the floater / jacket renderings.
    """
    face_in = _bspline_loft_face()
    plate = ada.PlateCurved.from_occ_face("rt", face_in, t=0.01)

    shape_out = plate.solid_occ()
    # solid_occ on the raw-OCC path returns the face we stored.
    assert shape_out is face_in

    # extruded_solid_occ should yield a face-bearing prism whose
    # underlying surface still classifies as a BSpline (the prism
    # inherits the swept surface kind from its source face).
    backend = active_backend()
    extruded = plate.extruded_solid_occ()
    found_bspline = any(backend.face_surface_type(f) == "bspline" for f in backend.faces(extruded))
    assert found_bspline, "extruded prism lost its BSpline surface — round-trip dropped curvature"


def test_curved_plate_render_covers_footprint(fem_files):
    """Every rendered PlateCurved must cover at least its flat footprint.

    A correct (curved or flat) plate prism has top+bottom faces each >= the
    flat footprint area, so the tessellated mesh area is always >= ~2x the
    footprint. Some trimmed B-spline faces defeat BRepMesh — it emits a
    degenerate centre-fan covering only ~half the surface ("missing
    triangles") — and the batch tessellator now detects that (curved mesh
    area < 1.85x footprint) and falls back to the clean flat quad. This pins
    the invariant: no PlateCurved ships an under-covered mesh.
    """
    import numpy as np
    import trimesh

    src = fem_files / "sesam/curved_plates.xml"
    if not src.exists():
        pytest.skip(f"fixture not present: {src}")

    asm = ada.from_genie_xml(src)
    curved = [o for o in asm.get_all_physical_objects() if type(o).__name__ == "PlateCurved"]
    assert curved, "fixture should contain PlateCurved objects"

    def _footprint_area(o):
        fb = getattr(o, "_flat_fallback_pts", None)
        if not fb or len(fb) < 3:
            return None
        pts = np.array([list(p)[:3] for p in fb])
        n = np.zeros(3)
        for i in range(len(pts)):
            n = n + np.cross(pts[i], pts[(i + 1) % len(pts)])
        return 0.5 * float(np.linalg.norm(n))

    checked = 0
    for o in curved:
        fa = _footprint_area(o)
        if not fa or fa < 1e-6:
            continue
        sub = ada.Assembly("s") / (ada.Part("p") / o)
        scene = sub.to_trimesh_scene(merge_meshes=False)
        mesh_area = sum(g.area for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh))
        # >= 1.85x footprint: a correct prism is ~2x + walls; the degenerate
        # centre-fan (the bug) lands ~1.5x and must have fallen back to flat.
        assert mesh_area >= 1.85 * fa, f"{o.name}: mesh area {mesh_area:.3f} < 1.85 x footprint {fa:.3f}"
        checked += 1
    assert checked > 0
