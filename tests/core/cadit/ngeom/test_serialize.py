"""NGEOM serializer: ada.geom -> the neutral-schema binary buffer.

Validates the buffer is spec-shaped (magic/version/record framing/roots). The C++ decoder
in adacpp is tested independently; together they form the adapy<->adacpp contract. A full
cross-package round-trip (serialize here, tessellate in adacpp) is exercised in the adacpp
test env, not here, to keep adapy free of an adacpp dependency.
"""

from __future__ import annotations

import math
import struct

import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.cadit.ngeom import NGEOM_VERSION, serialize_geometries
from ada.geom.placement import Axis2Placement3D, Direction, Point


def _line_oe(s, t):
    ec = cu.EdgeCurve(start=s, end=t, edge_geometry=cu.Line(s, [b - a for a, b in zip(s, t)]), same_sense=True)
    return cu.OrientedEdge(start=s, end=t, edge_element=ec, orientation=True)


def _square_face():
    p = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    loop = cu.EdgeLoop(edge_list=[_line_oe(p[i], p[(i + 1) % 4]) for i in range(4)])
    plane = su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    return su.FaceSurface(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=plane, same_sense=True)


def _parse(buf):
    """Minimal spec-faithful parser: returns (version, [(tag, payload_len)], [(root_idx, id)])."""
    assert buf[:8] == b"ADANGEOM"
    version, rec_count = struct.unpack_from("<ii", buf, 8)
    off = 16
    records = []
    for _ in range(rec_count):
        tag, nbytes = struct.unpack_from("<ii", buf, off)
        off += 8 + nbytes
        records.append((tag, nbytes))
    (root_count,) = struct.unpack_from("<i", buf, off)
    off += 4
    roots = []
    for _ in range(root_count):
        gidx, id_len = struct.unpack_from("<ii", buf, off)
        off += 8
        rid = buf[off : off + id_len].decode("utf-8")
        off += id_len
        roots.append((gidx, rid))
    assert off == len(buf), "trailing bytes / size mismatch"
    return version, records, roots


def test_shell_based_surface_model_serializes():
    """Regression: ShellBasedSurfaceModel was missing from the dispatch, so FEA/abaqus
    plates (a shell of faces) were silently dropped (header-only buffer, 0 roots) and
    libtess2 tessellated them to an empty mesh. It must now flatten to a CONNECTED_FACE_SET."""
    shell = su.OpenShell(cfs_faces=[_square_face()])
    sbsm = su.ShellBasedSurfaceModel(sbsm_boundary=[shell])
    buf = serialize_geometries([("plate", sbsm)])
    _version, records, roots = _parse(buf)
    tags = [t for t, _ in records]
    assert len(roots) == 1, "shell-based surface model must not be dropped"
    assert 66 in tags  # CONNECTED_FACE_SET (flattened shell)
    assert 65 in tags  # FACE_SURFACE (the shell's face survived)


def test_buffer_is_spec_shaped():
    buf = serialize_geometries([("sq", _square_face())])
    version, records, roots = _parse(buf)
    assert version == NGEOM_VERSION
    tags = [t for t, _ in records]
    assert 40 in tags  # PLANE
    assert 65 in tags  # FACE_SURFACE
    assert 62 in tags  # EDGE_LOOP
    assert tags.count(60) == 4  # 4 EDGE_CURVE (one per square side)
    assert roots == [(len(records) - 1, "sq")]  # the FACE_SURFACE is the last record + the root


def test_multiple_roots_and_skip_unsupported():
    # two faces -> two roots; ordering preserved (node_id maps by position)
    buf = serialize_geometries([("a", _square_face()), ("b", _square_face())])
    _, _, roots = _parse(buf)
    assert [rid for _, rid in roots] == ["a", "b"]


def test_dependency_order_no_forward_refs():
    # every record's child refs must point to earlier indices (single-pass decodable)
    buf = serialize_geometries([("sq", _square_face())])
    _, records, _ = _parse(buf)
    # FACE_SURFACE (tag 65) is last; PLANE/loops/edges precede it
    assert records[-1][0] == 65


# --- vectorized bulk-array serialization (Tier 1.5) ------------------------------------
# The serializer packs large geometry arrays with ``numpy.tobytes()`` instead of per-scalar
# ``struct.pack``; the tests below build geometry that exercises every vectorized site (B-spline
# control grids/knots/weights, polylines, poly-loops, edge-loop and face-set index lists) with
# more than ``_BULK_MIN`` elements so the numpy path is actually taken.


def _bspline_surface_face():
    nu, nv = 4, 6  # 24 control points > _BULK_MIN
    rows = [[Point(float(i), float(j), float(i * j) * 0.25) for j in range(nv)] for i in range(nu)]
    # Rational subclass carries the weights as a real field (the geom dataclasses are
    # slotted — ad-hoc attributes on the non-rational class no longer stick).
    surf = su.RationalBSplineSurfaceWithKnots(
        u_degree=3,
        v_degree=3,
        control_points_list=rows,
        surface_form=su.BSplineSurfaceForm.PLANE_SURF,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=[1] * 20,
        v_multiplicities=[2] * 20,
        u_knots=[float(i) for i in range(20)],
        v_knots=[float(i) * 0.5 for i in range(20)],
        knot_spec=cu.KnotType.UNSPECIFIED,
        weights_data=[[1.0 + 0.01 * (i + j) for j in range(nv)] for i in range(nu)],  # >16 flat weights
    )
    poly = cu.PolyLoop(polygon=[Point(math.cos(0.3 * k), math.sin(0.3 * k), 0.1 * k) for k in range(20)])  # >16 pts
    return su.FaceSurface(bounds=[su.FaceBound(bound=poly, orientation=True)], face_surface=surf, same_sense=True)


def _bspline_curve_edge_face():
    cps = [Point(float(i), float(i % 3), 0.0) for i in range(20)]  # >16 control points
    bc = cu.RationalBSplineCurveWithKnots(
        degree=3,
        control_points_list=cps,
        curve_form=cu.BSplineCurveFormEnum.POLYLINE_FORM,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[1] * 24,  # >16 multiplicities
        knots=[float(i) for i in range(24)],  # >16 knots
        knot_spec=cu.KnotType.UNSPECIFIED,
        weights_data=[1.0 + 0.001 * i for i in range(20)],  # >16 weights (real field; slotted classes)
    )
    s, t = (0.0, 0.0, 0.0), (19.0, 0.0, 0.0)
    ec = cu.EdgeCurve(start=s, end=t, edge_geometry=bc, same_sense=True)
    oe = cu.OrientedEdge(start=s, end=t, edge_element=ec, orientation=True)
    loop = cu.EdgeLoop(edge_list=[oe])
    plane = su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    return su.FaceSurface(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=plane, same_sense=True)


def _ngon_edge_face(n=20):
    # EDGE_LOOP with >16 oriented edges -> exercises the vectorized edge-ref list
    pts = [(math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n), 0.0) for k in range(n)]
    loop = cu.EdgeLoop(edge_list=[_line_oe(pts[i], pts[(i + 1) % n]) for i in range(n)])
    plane = su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    return su.FaceSurface(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=plane, same_sense=True)


def test_vectorized_serialize_byte_identical_to_scalar(monkeypatch):
    """The numpy bulk path must be byte-for-byte identical to the per-scalar ``struct`` path.
    Forcing ``_BULK_MIN`` above any array length makes every helper fall back to the exact
    pre-vectorization code, so equal buffers prove the wire format is unchanged at every site."""
    items = [
        ("bsurf", _bspline_surface_face()),
        ("bcurve", _bspline_curve_edge_face()),
        ("ngon", _ngon_edge_face(20)),
        ("shell", su.OpenShell(cfs_faces=[_square_face() for _ in range(20)])),  # >16 faces in the set
    ]
    fast = serialize_geometries(items)  # default: numpy for arrays >= _BULK_MIN

    import ada.cadit.ngeom.serialize as ser

    monkeypatch.setattr(ser, "_BULK_MIN", 10**9)  # force the per-scalar struct path everywhere
    slow = serialize_geometries(items)

    assert fast == slow
    _, _, roots = _parse(fast)
    assert [rid for _, rid in roots] == ["bsurf", "bcurve", "ngon", "shell"]


def test_vectorized_buffer_decodes_spec_shaped():
    # the vectorized buffer must still parse cleanly (framing/roots intact)
    buf = serialize_geometries([("bsurf", _bspline_surface_face())])
    version, records, roots = _parse(buf)
    assert version == NGEOM_VERSION
    tags = [t for t, _ in records]
    assert 45 in tags  # BSPLINE_SURFACE
    assert 63 in tags  # POLY_LOOP (the >16-pt bound)
    assert len(roots) == 1


def _linear_extrusion_face(position):
    """A STEP-shaped SURFACE_OF_LINEAR_EXTRUSION: a swept curve + a sweep vector, no placement."""
    p = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    loop = cu.EdgeLoop(edge_list=[_line_oe(p[i], p[(i + 1) % 4]) for i in range(4)])
    surf = su.SurfaceOfLinearExtrusion(
        swept_curve=cu.Line(Point(0, 0, 0), Direction(1, 0, 0)),
        position=position,
        extrusion_direction=Direction(0, 0, 1),
        depth=1.0,
    )
    return su.FaceSurface(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=True)


def test_linear_extrusion_without_position_reaches_the_wire():
    """A STEP SURFACE_OF_LINEAR_EXTRUSION has no Position — the swept curve is already placed —
    so a STEP-sourced ada.geom surface carries ``position=None``. The serializer must emit the
    negative "no record" sentinel for it (the decoder ignores the field entirely) rather than
    dereferencing None: ``placement3(None)`` raised AttributeError, and connected_face_set's
    blanket ``except`` turned that into a silently DROPPED face. On the Ventilator that was
    26/305 faces — 32% of its surface — reaching adacpp as nothing, with both tessellation
    tracks equally blind because neither was ever handed the geometry."""
    shell = su.OpenShell(cfs_faces=[_linear_extrusion_face(position=None)])
    buf = serialize_geometries([("shell", shell)])

    _, records, roots = _parse(buf)
    tags = [t for t, _ in records]
    assert 46 in tags, "SURF_LIN_EXTRUSION missing — the position=None face was dropped"
    assert 65 in tags  # FACE_SURFACE — the face itself survived
    assert len(roots) == 1


def test_linear_extrusion_with_position_still_emits_placement():
    """The IFC form does carry a Position; it must still be serialized (sentinel only when absent)."""
    pos = Axis2Placement3D(Point(1, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0))
    shell = su.OpenShell(cfs_faces=[_linear_extrusion_face(position=pos)])
    buf = serialize_geometries([("shell", shell)])

    _, records, roots = _parse(buf)
    tags = [t for t, _ in records]
    assert 46 in tags
    assert 1 in tags, "expected a PLACEMENT3 record for the IFC-form surface"
    assert len(roots) == 1
