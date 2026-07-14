"""Serialize ``ada.geom`` geometry into the NGEOM binary buffer (spec v1).

The buffer is the contract with adacpp's neutral geometry layer (no adacpp import here). See
dap/plan/v3/spec_neutral_geometry_schema.md for the wire format and tag catalog.
"""

from __future__ import annotations

import struct
from typing import Iterable

import numpy as np

import ada.geom.curves as cu
import ada.geom.surfaces as su

NGEOM_VERSION = 1

# Arrays shorter than this serialize via per-scalar ``struct.pack`` (faster than building a numpy
# array); longer arrays go through ``numpy.tobytes()`` (the B-spline / polyline bulk path).
_BULK_MIN = 16


def _sample_arc(start, mid, end, n: int = 24) -> list:
    """Sample a 3-point circular arc (IFC ArcLine) into an ordered polyline start->mid->end.
    Falls back to the 3 raw points if the points are (near-)collinear."""
    p0, p1, p2 = np.asarray(start, float), np.asarray(mid, float), np.asarray(end, float)
    a, b = p1 - p0, p2 - p0
    n_vec = np.cross(a, b)
    nn = np.linalg.norm(n_vec)
    if nn < 1e-12:
        return [start, mid, end]  # collinear -> straight
    # circumcenter (in 3D, via the perpendicular-bisector formula)
    a2, b2 = a @ a, b @ b
    center = p0 + (np.cross((a2 * b - b2 * a), n_vec)) / (2.0 * nn * nn)
    nhat = n_vec / nn
    u = p0 - center
    r = np.linalg.norm(u)
    if r < 1e-12:
        return [start, mid, end]
    uhat = u / r
    vhat = np.cross(nhat, uhat)

    def ang(p):
        d = np.asarray(p, float) - center
        return np.arctan2(d @ vhat, d @ uhat)

    t0, tm, t1 = ang(p0), ang(p1), ang(p2)
    tau = 2.0 * np.pi
    # choose the sweep direction that passes through the midpoint
    t1f = t1 + (tau if t1 < t0 else 0.0)
    tmf = tm + (tau if tm < t0 else 0.0)
    if not (t0 <= tmf <= t1f):  # mid not on the CCW side -> go clockwise
        t1f = t1 - (tau if t1 > t0 else 0.0)
    pts = []
    for i in range(n + 1):
        t = t0 + (t1f - t0) * i / n
        pts.append(center + r * (np.cos(t) * uhat + np.sin(t) * vhat))
    pts[0], pts[-1] = np.asarray(start, float), np.asarray(end, float)
    return pts


# tag catalog (spec §3)
_PLACEMENT3 = 1
_PLACEMENT1 = 2
_LINE = 10
_POLYLINE = 11
_HYPERBOLA = 12
_PARABOLA = 13
_COMPOSITE_CURVE = 14
_CIRCLE = 20
_ELLIPSE = 21
_BSPLINE_CURVE = 22
_TRIMMED_CURVE = 24
_PLANE = 40
_CYLINDER = 41
_CONE = 42
_SPHERE = 43
_TORUS = 44
_BSPLINE_SURFACE = 45
_SURF_LIN_EXTRUSION = 46
_SURF_REVOLUTION = 47
# solids (50-59) — swept/CSG solids mapped to ifcopenshell taxonomy items
_EXTRUDED_AREA_SOLID = 50
_REVOLVED_AREA_SOLID = 51
_BOOLEAN_RESULT = 52
_SPHERE_SOLID = 53
_FIXED_REF_SWEPT_SOLID = 54
_EDGE_CURVE = 60
_ORIENTED_EDGE = 61
_EDGE_LOOP = 62
_POLY_LOOP = 63
_FACE_BOUND = 64
_FACE_SURFACE = 65
_CONNECTED_FACE_SET = 66


def _xyz(p) -> tuple[float, float, float]:
    # IFC readers occasionally emit a 2D point — the z is dropped when it's 0, e.g. a polyline
    # edge lying in the z=0 plane (one edge of a face whose other edges came through 3D). Treat
    # the missing component as 0 (it was 0) instead of IndexError-crashing the whole serialize.
    try:
        z = float(p[2])
    except IndexError:
        z = 0.0
    return (float(p[0]), float(p[1]), z)


class _Encoder:
    def __init__(self):
        self._records: list[tuple[int, bytes]] = []
        self._memo: dict[int, int] = {}  # id(obj) -> record index

    def _add(self, tag: int, payload: bytes) -> int:
        idx = len(self._records)
        self._records.append((tag, payload))
        return idx

    # --- scalar/array helpers ----------------------------------------------------------
    @staticmethod
    def i32(v: int) -> bytes:
        return struct.pack("<i", int(v))

    @staticmethod
    def f64(v: float) -> bytes:
        return struct.pack("<d", float(v))

    def v3(self, p) -> bytes:
        x, y, z = _xyz(p)
        return struct.pack("<ddd", x, y, z)

    def f64s(self, xs: Iterable[float]) -> bytes:
        xs = list(xs)
        return self.i32(len(xs)) + self._f64_raw(xs)

    def i32s(self, xs: Iterable[int]) -> bytes:
        xs = list(xs)
        return self.i32(len(xs)) + self._i32_raw(xs)

    # --- bulk array helpers ------------------------------------------------------------
    # Vectorize the large geometry arrays (B-spline control grids, knots, polylines, big
    # connected-face-set lists) with ``numpy.tobytes()`` instead of per-scalar ``struct.pack`` +
    # ``b"".join`` (~600 calls for a 100-pt B-spline). Wire format is unchanged: numpy ``<f8``/
    # ``<i4`` little-endian bytes are byte-for-byte identical to ``struct.pack("<d"/"<i")``. Arrays
    # below ``_BULK_MIN`` keep the per-scalar path. Crucially, the *per-face* tiny index lists
    # (a face's 1-2 bounds, an edge-loop's handful of edge refs) are joined inline at the call site
    # rather than via these helpers — on a B-rep model that path runs millions of times and the
    # helper's call+guard overhead was measured to slightly *regress* the crane (the bulk win only
    # materializes on genuinely large arrays / NURBS-heavy geometry).
    @staticmethod
    def _f64_raw(xs) -> bytes:
        """Raw little-endian f64 bytes (no count prefix) for a 1-D float sequence."""
        xs = xs if isinstance(xs, list) else list(xs)
        if len(xs) < _BULK_MIN:
            return b"".join(struct.pack("<d", float(x)) for x in xs)
        return np.ascontiguousarray(np.asarray(xs, dtype="<f8")).tobytes()

    @staticmethod
    def _i32_raw(xs) -> bytes:
        """Raw little-endian i32 bytes (no count prefix) for a 1-D int sequence."""
        xs = xs if isinstance(xs, list) else list(xs)
        if len(xs) < _BULK_MIN:
            return b"".join(struct.pack("<i", int(x)) for x in xs)
        return np.ascontiguousarray(np.asarray(xs, dtype="<i4")).tobytes()

    def _v3_raw(self, pts) -> bytes:
        """Raw little-endian f64 bytes for a sequence of 3D points, ``(n,3)`` row-major.
        Falls back to the per-point ``v3`` path for small, ragged or 2D inputs (IFC sometimes
        drops a zero z) — byte-for-byte identical to the old ``b"".join(v3(p) ...)``."""
        pts = pts if isinstance(pts, list) else list(pts)
        if len(pts) < _BULK_MIN:
            return b"".join(self.v3(p) for p in pts)
        try:
            arr = np.asarray(pts, dtype=np.float64)
        except (ValueError, TypeError):
            arr = None
        if arr is None or arr.ndim != 2 or arr.shape[1] != 3:
            return b"".join(self.v3(p) for p in pts)  # ragged / 2D → exact old path
        return np.ascontiguousarray(arr, dtype="<f8").tobytes()

    # --- placements --------------------------------------------------------------------
    def placement3(self, pos: su.Axis2Placement3D) -> int:
        return self._add(_PLACEMENT3, self.v3(pos.location) + self.v3(pos.axis) + self.v3(pos.ref_direction))

    def placement1(self, axis1) -> int:
        return self._add(_PLACEMENT1, self.v3(axis1.location) + self.v3(axis1.axis))

    # --- curves ------------------------------------------------------------------------
    def curve(self, c) -> int:
        key = id(c)
        if key in self._memo:
            return self._memo[key]
        if isinstance(c, cu.Line):
            d = c.dir
            idx = self._add(_LINE, self.v3(c.pnt) + self.v3(d))
        elif isinstance(c, cu.Circle):
            pl = self.placement3(c.position)
            idx = self._add(_CIRCLE, self.i32(pl) + self.f64(c.radius))
        elif isinstance(c, cu.Ellipse):
            pl = self.placement3(c.position)
            idx = self._add(_ELLIPSE, self.i32(pl) + self.f64(c.semi_axis1) + self.f64(c.semi_axis2))
        elif isinstance(c, cu.BSplineCurveWithKnots):
            idx = self._bspline_curve(c)
        elif isinstance(c, cu.TrimmedCurve):
            idx = self._trimmed_curve(c)
        elif isinstance(c, cu.PolyLine):
            pts = list(c.points)
            idx = self._add(_POLYLINE, self.i32(len(pts)) + self._v3_raw(pts))
        elif isinstance(c, cu.Hyperbola):
            pl = self.placement3(c.position)
            idx = self._add(_HYPERBOLA, self.i32(pl) + self.f64(c.semi_axis) + self.f64(c.semi_imag_axis))
        elif isinstance(c, cu.Parabola):
            pl = self.placement3(c.position)
            idx = self._add(_PARABOLA, self.i32(pl) + self.f64(c.focal_dist))
        elif isinstance(c, cu.CompositeCurve):
            segs = list(c.segments)
            body = self.i32(len(segs))
            for s in segs:
                body += self.i32(self.curve(s.parent_curve)) + self.i32(1 if s.same_sense else 0)
            idx = self._add(_COMPOSITE_CURVE, body)
        elif isinstance(c, cu.ArcLine):
            # 3-point arc (no STEP entity): sample to a polyline through start->mid->end
            pts = _sample_arc(c.start, c.midpoint, c.end)
            idx = self._add(_POLYLINE, self.i32(len(pts)) + self._v3_raw(pts))
        elif isinstance(c, cu.OffsetCurve3D):
            idx = self.curve(c.basis_curve)  # offset approximated by its basis (step2glb has no offset arm)
        else:
            raise _Unsupported(f"curve {type(c).__name__}")
        self._memo[key] = idx
        return idx

    def _bspline_curve(self, c: cu.BSplineCurveWithKnots) -> int:
        weights = list(getattr(c, "weights_data", None) or [])
        body = self.i32(c.degree) + self.i32(1 if c.closed_curve else 0) + self.i32(1 if c.self_intersect else 0)
        cps = list(c.control_points_list)
        body += self.i32(len(cps)) + self._v3_raw(cps)
        body += self.f64s(c.knots) + self._i32_raw(c.knot_multiplicities)
        body += self.i32(1 if weights else 0)
        if weights:
            body += self._f64_raw(weights)
        return self._add(_BSPLINE_CURVE, body)

    def _trimmed_curve(self, c: cu.TrimmedCurve) -> int:
        basis = self.curve(c.basis_curve)
        t1, t2 = c.trim1, c.trim2  # serializer resolves to parameters upstream
        master = {"PARAMETER": 0, "CARTESIAN": 1}.get(getattr(c, "master_representation", "PARAMETER"), 2)
        return self._add(
            _TRIMMED_CURVE,
            self.i32(basis) + self.f64(t1) + self.f64(t2) + self.i32(1 if c.sense_agreement else 0) + self.i32(master),
        )

    # --- surfaces ----------------------------------------------------------------------
    def surface(self, s) -> int:
        key = id(s)
        if key in self._memo:
            return self._memo[key]
        if isinstance(s, su.Plane):
            idx = self._add(_PLANE, self.i32(self.placement3(s.position)))
        elif isinstance(s, su.CylindricalSurface):
            idx = self._add(_CYLINDER, self.i32(self.placement3(s.position)) + self.f64(s.radius))
        elif isinstance(s, su.ConicalSurface):
            idx = self._add(_CONE, self.i32(self.placement3(s.position)) + self.f64(s.radius) + self.f64(s.semi_angle))
        elif isinstance(s, su.SphericalSurface):
            idx = self._add(_SPHERE, self.i32(self.placement3(s.position)) + self.f64(s.radius))
        elif isinstance(s, su.ToroidalSurface):
            idx = self._add(
                _TORUS, self.i32(self.placement3(s.position)) + self.f64(s.major_radius) + self.f64(s.minor_radius)
            )
        elif isinstance(s, su.BSplineSurfaceWithKnots):
            idx = self._bspline_surface(s)
        elif isinstance(s, su.SurfaceOfLinearExtrusion):
            sc = self.curve(s.swept_curve)
            idx = self._add(
                _SURF_LIN_EXTRUSION,
                self.i32(sc)
                + self.i32(self.placement3(s.position))
                + self.v3(s.extrusion_direction)
                + self.f64(s.depth),
            )
        elif isinstance(s, su.SurfaceOfRevolution):
            sc = self.curve(s.swept_curve)
            ax = self.placement1(s.axis_position)
            idx = self._add(_SURF_REVOLUTION, self.i32(sc) + self.i32(ax) + self.i32(-1))
        elif isinstance(s, su.RectangularTrimmedSurface):
            idx = self.surface(s.basis_surface)  # rectangular trim lives in UV; face bounds trim it
        elif isinstance(s, su.OffsetSurface):
            idx = self.surface(s.basis_surface)  # offset approximated by its basis surface
        elif isinstance(s, su.CurveBoundedPlane):
            idx = self.surface(s.basis_surface)  # the bounding curves come through the face bounds
        else:
            raise _Unsupported(f"surface {type(s).__name__}")
        self._memo[key] = idx
        return idx

    def _bspline_surface(self, s: su.BSplineSurfaceWithKnots) -> int:
        rows = list(s.control_points_list)  # list[list[Point]] (u rows x v cols)
        nu, nv = len(rows), len(rows[0])
        weights = getattr(s, "weights_data", None)
        body = self.i32(s.u_degree) + self.i32(s.v_degree)
        body += (
            self.i32(1 if s.u_closed else 0) + self.i32(1 if s.v_closed else 0) + self.i32(1 if s.self_intersect else 0)
        )
        body += self.i32(nu) + self.i32(nv)
        # row-major: u outer, v inner — flatten then bulk-serialize the control grid
        body += self._v3_raw([p for row in rows for p in row])
        body += self.f64s(s.u_knots) + self._i32_raw(s.u_multiplicities)
        body += self.f64s(s.v_knots) + self._i32_raw(s.v_multiplicities)
        flat_w = []
        if weights:
            for row in weights:
                flat_w.extend(row)
        body += self.i32(1 if flat_w else 0)
        if flat_w:
            body += self._f64_raw(flat_w)
        return self._add(_BSPLINE_SURFACE, body)

    # --- topology ----------------------------------------------------------------------
    def _edge_curve(self, ec) -> int:
        geom = -1
        same_sense = getattr(ec, "same_sense", True)
        eg = getattr(ec, "edge_geometry", None)
        if eg is not None and not isinstance(eg, cu.Line):
            try:
                geom = self.curve(eg)
            except _Unsupported:
                geom = -1
        return self._add(
            _EDGE_CURVE,
            self.v3(ec.start) + self.v3(ec.end) + self.i32(geom) + self.i32(1 if same_sense else 0),
        )

    def oriented_edge(self, oe: cu.OrientedEdge) -> int:
        elem = oe.edge_element
        eref = self._edge_curve(elem)
        has_params = oe.t_start is not None and oe.t_end is not None
        body = self.i32(eref) + self.i32(1 if oe.orientation else 0) + self.i32(0)  # has_pcurve=0 (v1)
        body += self.i32(1 if has_params else 0)
        if has_params:
            body += self.f64(oe.t_start) + self.f64(oe.t_end)
        return self._add(_ORIENTED_EDGE, body)

    def loop(self, lp) -> int:
        if isinstance(lp, cu.PolyLoop):
            pts = list(lp.polygon)
            return self._add(_POLY_LOOP, self.i32(len(pts)) + self._v3_raw(pts))
        edges = list(lp.edge_list)
        # one inline join per face-loop (few edges, ~always < _BULK_MIN); the helper's guard
        # overhead would dominate on B-rep models with millions of these tiny loops
        return self._add(_EDGE_LOOP, self.i32(len(edges)) + b"".join(self.i32(self.oriented_edge(e)) for e in edges))

    def face_bound(self, fb: su.FaceBound) -> int:
        lp = self.loop(fb.bound)
        return self._add(_FACE_BOUND, self.i32(lp) + self.i32(1 if fb.orientation else 0))

    def face_surface(self, f: su.FaceSurface) -> int:
        surf = self.surface(f.face_surface)
        bounds = [self.face_bound(b) for b in f.bounds]
        body = self.i32(surf) + self.i32(1 if f.same_sense else 0) + self.i32(len(bounds))
        body += b"".join(self.i32(b) for b in bounds)  # 1-2 bounds/face: inline (per-face hot path)
        return self._add(_FACE_SURFACE, body)

    def connected_face_set(self, cfs) -> int:
        # ConnectedFaceSet / ClosedShell / OpenShell all expose ``cfs_faces`` (FaceSurface or
        # the structurally-identical AdvancedFace). Skip any face that can't be mapped.
        faces = []
        for f in cfs.cfs_faces:
            try:
                faces.append(self.face_surface(f))
            except Exception:  # noqa: BLE001 - skip any face that can't be mapped (robustness)
                continue
        return self._add(_CONNECTED_FACE_SET, self.i32(len(faces)) + self._i32_raw(faces))

    # --- solids ------------------------------------------------------------------------
    @staticmethod
    def _loop_points_3d(curve) -> list[tuple[float, float, float]]:
        """Ordered boundary points of a closed profile curve, lifted to z=0
        (the profile lives in its local XY plane). IndexedPolyCurve / Polyline
        expose ``get_points()``; fall back to ``.points``."""
        getp = getattr(curve, "get_points", None)
        raw = getp() if callable(getp) else getattr(curve, "points", None)
        if raw is None:
            raise _Unsupported(f"profile curve {type(curve).__name__}")
        pts: list[tuple[float, float, float]] = []
        for p in raw:
            p = list(p)
            pts.append((float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0))
        if len(pts) > 1 and pts[0] == pts[-1]:  # POLY_LOOP closes implicitly
            pts.pop()
        return pts

    def _poly_face_bound(self, curve, outer: bool) -> int:
        pts = self._loop_points_3d(curve)
        loop = self._add(_POLY_LOOP, self.i32(len(pts)) + self._v3_raw(pts))
        return self._add(_FACE_BOUND, self.i32(loop) + self.i32(1 if outer else 0))

    def _conic_edge_loop(self, curve) -> int:
        """EDGE_LOOP with one full conic (circle/ellipse) edge, parameter [0, 2pi].
        Lets round profiles (pipes, circular columns) tessellate at the requested
        deflection instead of being dropped to a polyline."""
        import math

        import numpy as np

        cref = self.curve(curve)  # CIRCLE/ELLIPSE record carries the curve's own position
        pos = curve.position
        loc = np.asarray(pos.location, dtype=float)
        ref = np.asarray(pos.ref_direction, dtype=float)
        r = float(getattr(curve, "radius", 0.0) or getattr(curve, "semi_axis1", 0.0))
        start = loc + ref * r  # point at parameter 0
        s = (float(start[0]), float(start[1]), float(start[2]))
        edge = self._add(_EDGE_CURVE, self.v3(s) + self.v3(s) + self.i32(cref) + self.i32(1))
        oedge = self._add(
            _ORIENTED_EDGE,
            self.i32(edge) + self.i32(1) + self.i32(0) + self.i32(1) + self.f64(0.0) + self.f64(2 * math.pi),
        )
        return self._add(_EDGE_LOOP, self.i32(1) + self.i32(oedge))

    def _curve_face_bound(self, curve, outer: bool) -> int:
        """FACE_BOUND for one profile boundary curve: Circle/Ellipse -> a conic edge
        loop; polyline / IndexedPolyCurve -> a POLY_LOOP. The hole (inner) bound is
        emitted with orientation=0 so the decoder reverses it into a proper hole."""
        import ada.geom.curves as _cu

        conic = tuple(t for t in (getattr(_cu, "Circle", None), getattr(_cu, "Ellipse", None)) if t is not None)
        if conic and isinstance(curve, conic):
            loop = self._conic_edge_loop(curve)
            return self._add(_FACE_BOUND, self.i32(loop) + self.i32(1 if outer else 0))
        return self._poly_face_bound(curve, outer)

    def _planar_face(self, bounds: list[int]) -> int:
        """A planar FACE_SURFACE in the local XY plane (z=0) from boundary refs."""
        from ada.geom.placement import Axis2Placement3D

        plane = self._add(_PLANE, self.i32(self.placement3(Axis2Placement3D())))
        return self._add(
            _FACE_SURFACE,
            self.i32(plane) + self.i32(1) + self.i32(len(bounds)) + b"".join(self.i32(b) for b in bounds),
        )

    def _profile_face(self, profile) -> int:
        """Planar profile FACE (local XY) from an ArbitraryProfileDef's outer +
        inner boundary loops (polyline profiles)."""
        # Parametric sections (I/H/T/U/L/C/…) carry no outer_curve — only dims. Normalize them to
        # an ArbitraryProfileDef outline (the same backend-neutral conversion the OCC build uses)
        # so the stream kernel gets a real profile instead of an empty extrusion → 0 triangles.
        if getattr(profile, "outer_curve", None) is None:
            try:
                from ada.api.beams.geom_beams import parametric_profile_to_arbitrary

                profile = parametric_profile_to_arbitrary(profile)
            except Exception:  # noqa: BLE001 - unconvertible profile → reported as unsupported below
                pass
        outer = getattr(profile, "outer_curve", None)
        if outer is None:
            raise _Unsupported(f"profile {type(profile).__name__}")
        bounds = [self._curve_face_bound(outer, True)]
        for ic in getattr(profile, "inner_curves", None) or []:
            bounds.append(self._curve_face_bound(ic, False))
        return self._planar_face(bounds)

    def extruded_area_solid(self, eas) -> int:
        """ExtrudedAreaSolid -> profile FACE + extrude direction + depth +
        placement. Decoded by adacpp into an ifcopenshell taxonomy::extrusion."""
        face = self._profile_face(eas.swept_area)
        body = (
            self.i32(face)
            + self.i32(self.placement3(eas.position))
            + self.v3(eas.extruded_direction)
            + self.f64(eas.depth)
        )
        return self._add(_EXTRUDED_AREA_SOLID, body)

    def box_solid(self, box) -> int:
        """Box -> an x*y rectangle profile extruded by z_length (reuses the
        EXTRUDED_AREA_SOLID record; no taxonomy box primitive exists)."""
        x, y = float(box.x_length), float(box.y_length)
        pts = [(0.0, 0.0, 0.0), (x, 0.0, 0.0), (x, y, 0.0), (0.0, y, 0.0)]
        loop = self._add(_POLY_LOOP, self.i32(4) + self._v3_raw(pts))
        bound = self._add(_FACE_BOUND, self.i32(loop) + self.i32(1))
        face = self._planar_face([bound])
        body = (
            self.i32(face) + self.i32(self.placement3(box.position)) + self.v3((0.0, 0.0, 1.0)) + self.f64(box.z_length)
        )
        return self._add(_EXTRUDED_AREA_SOLID, body)

    def revolved_area_solid(self, ras) -> int:
        """RevolvedAreaSolid -> profile FACE + revolution axis (placement1) +
        angle + placement. Decoded by adacpp into a BRepPrimAPI_MakeRevol."""
        face = self._profile_face(ras.swept_area)
        body = (
            self.i32(face)
            + self.i32(self.placement3(ras.position))
            + self.i32(self.placement1(ras.axis))
            + self.f64(ras.angle)
        )
        return self._add(_REVOLVED_AREA_SOLID, body)

    def fixed_reference_swept_area_solid(self, frs) -> int:
        """FixedReferenceSweptAreaSolid (IFC4x3 alignment sweep) -> profile FACE + a precomputed
        field of per-station frames (origin, dir_x, dir_y). The analytic directrix (line/clothoid/
        arc + vertical gradient + fixed-reference frame) is evaluated here; adacpp's tessellate_sweep
        rings + caps the frames via libtess2 (no OCC)."""
        from ada.cadit.ngeom._alignment_sweep import directrix_frames

        face = self._profile_face(frs.swept_area)
        origins, dir_x, dir_y = directrix_frames(frs)
        n = len(origins)
        body = (
            self.i32(face)
            + self.i32(self.placement3(frs.position))
            + self.i32(n)
            + self._f64_raw(origins.ravel())
            + self._f64_raw(dir_x.ravel())
            + self._f64_raw(dir_y.ravel())
        )
        return self._add(_FIXED_REF_SWEPT_SOLID, body)

    def _xz_planar_face(self, pts3d) -> int:
        """Planar FACE_SURFACE in the local XZ plane (y=0; normal=+Y, ref=+X)."""
        place = self._add(_PLACEMENT3, self.v3((0.0, 0.0, 0.0)) + self.v3((0.0, 1.0, 0.0)) + self.v3((1.0, 0.0, 0.0)))
        plane = self._add(_PLANE, self.i32(place))
        loop = self._add(_POLY_LOOP, self.i32(len(pts3d)) + self._v3_raw(pts3d))
        bound = self._add(_FACE_BOUND, self.i32(loop) + self.i32(1))
        return self._add(_FACE_SURFACE, self.i32(plane) + self.i32(1) + self.i32(1) + self.i32(bound))

    def _revolve_z(self, face: int, position) -> int:
        """Emit a REVOLVED_AREA_SOLID: ``face`` revolved 360deg about local Z,
        placed by ``position``."""
        import math

        axis = self._add(_PLACEMENT1, self.v3((0.0, 0.0, 0.0)) + self.v3((0.0, 0.0, 1.0)))
        body = self.i32(face) + self.i32(self.placement3(position)) + self.i32(axis) + self.f64(2 * math.pi)
        return self._add(_REVOLVED_AREA_SOLID, body)

    def cylinder_solid(self, cyl) -> int:
        """Cylinder -> a circle profile (radius, local XY) extruded by height.
        A pure taxonomy::extrusion (no raw OCC; taxonomy has no cylinder solid,
        only a cylindrical *surface*)."""
        import math

        r, h = float(cyl.radius), float(cyl.height)
        circ_pl = self._add(_PLACEMENT3, self.v3((0.0, 0.0, 0.0)) + self.v3((0.0, 0.0, 1.0)) + self.v3((1.0, 0.0, 0.0)))
        circle = self._add(_CIRCLE, self.i32(circ_pl) + self.f64(r))
        start = (r, 0.0, 0.0)  # circle param t=0
        edge = self._add(_EDGE_CURVE, self.v3(start) + self.v3(start) + self.i32(circle) + self.i32(1))
        # full-circle oriented edge: orientation=1, has_pcurve=0, has_params=1, t in [0, 2pi]
        oedge = self._add(
            _ORIENTED_EDGE,
            self.i32(edge) + self.i32(1) + self.i32(0) + self.i32(1) + self.f64(0.0) + self.f64(2 * math.pi),
        )
        loop = self._add(_EDGE_LOOP, self.i32(1) + self.i32(oedge))
        bound = self._add(_FACE_BOUND, self.i32(loop) + self.i32(1))
        face = self._planar_face([bound])
        body = self.i32(face) + self.i32(self.placement3(cyl.position)) + self.v3((0.0, 0.0, 1.0)) + self.f64(h)
        return self._add(_EXTRUDED_AREA_SOLID, body)

    def sphere_solid(self, sphere) -> int:
        """Sphere -> a SPHERE_SOLID record (centre placement + radius). Decoded
        by adacpp into an analytic UV sphere (libtess2) / BRepPrimAPI_MakeSphere
        (occ/cgal); taxonomy has no sphere solid (only a spherical surface)."""
        from ada.geom.placement import Axis2Placement3D, Point

        c = sphere.center
        pos = Axis2Placement3D(location=Point(float(c[0]), float(c[1]), float(c[2])))
        body = self.i32(self.placement3(pos)) + self.f64(float(sphere.radius))
        return self._add(_SPHERE_SOLID, body)

    def cone_solid(self, cone) -> int:
        """Cone -> a base-radius/height triangle (XZ plane) revolved about Z."""
        r, h = float(cone.bottom_radius), float(cone.height)
        face = self._xz_planar_face([(0.0, 0.0, 0.0), (r, 0.0, 0.0), (0.0, 0.0, h)])
        return self._revolve_z(face, cone.position)

    def _planar_face_from_loop(self, bound) -> int:
        """Synthesize a planar FACE_SURFACE from a closed 3D loop (a fitted plane
        through its points). Beam.shell_geom puts bare FaceBound/PolyLoops in its
        ConnectedFaceSet (no surface), so we plane-fit them here."""
        import numpy as np

        poly = getattr(bound, "polygon", None)
        raw = (
            poly
            if poly is not None
            else (bound.get_points() if hasattr(bound, "get_points") else getattr(bound, "points", None))
        )
        if raw is None:
            raise _Unsupported(f"loop {type(bound).__name__}")
        pts = []
        for p in raw:
            p = list(p)
            pts.append((float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0))
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        if len(pts) < 3:
            raise _Unsupported("degenerate loop")
        a, b, c = np.array(pts[0]), np.array(pts[1]), np.array(pts[2])
        n = np.cross(b - a, c - a)
        nn = float(np.linalg.norm(n))
        rxn = float(np.linalg.norm(b - a))
        if nn < 1e-12 or rxn < 1e-12:
            raise _Unsupported("collinear loop")
        n = n / nn
        rx = (b - a) / rxn
        place = self._add(_PLACEMENT3, self.v3(a) + self.v3(n) + self.v3(rx))
        plane = self._add(_PLANE, self.i32(place))
        loop = self._add(_POLY_LOOP, self.i32(len(pts)) + self._v3_raw(pts))
        bnd = self._add(_FACE_BOUND, self.i32(loop) + self.i32(1))
        return self._add(_FACE_SURFACE, self.i32(plane) + self.i32(1) + self.i32(1) + self.i32(bnd))

    def _any_face(self, f) -> int:
        # FaceSurface / AdvancedFace -> normal path; bare FaceBound (planar loop,
        # no surface — Beam.shell_geom) -> synthesize a planar face.
        if hasattr(f, "face_surface") and hasattr(f, "bounds"):
            return self.face_surface(f)
        bound = getattr(f, "bound", None)
        if bound is not None:
            return self._planar_face_from_loop(bound)
        raise _Unsupported(f"face {type(f).__name__}")

    def face_based_surface_model(self, fbsm) -> int:
        """FaceBasedSurfaceModel -> flatten its ConnectedFaceSets' faces into one
        CONNECTED_FACE_SET (so face-based shell reps tessellate via NGEOM)."""
        faces = []
        for cfs in fbsm.fbsm_faces:
            for f in getattr(cfs, "cfs_faces", []):
                try:
                    faces.append(self._any_face(f))
                except Exception:  # noqa: BLE001
                    continue
        return self._add(_CONNECTED_FACE_SET, self.i32(len(faces)) + self._i32_raw(faces))

    def shell_based_surface_model(self, sbsm) -> int:
        """ShellBasedSurfaceModel -> flatten its open/closed shells' faces into one
        CONNECTED_FACE_SET (so shell-based reps — e.g. FEA/abaqus-exported flat & curved
        plates, which were silently dropped → empty mesh — tessellate via NGEOM)."""
        faces = []
        for shell in sbsm.sbsm_boundary:
            for f in getattr(shell, "cfs_faces", []):
                try:
                    faces.append(self._any_face(f))
                except Exception:  # noqa: BLE001
                    continue
        return self._add(_CONNECTED_FACE_SET, self.i32(len(faces)) + self._i32_raw(faces))

    def boolean_result(self, br) -> int:
        """BooleanResult -> operator + two operand records (recursively
        serialized). adacpp builds each operand's TopoDS_Shape and applies
        BRepAlgoAPI_Cut/Fuse/Common."""
        op_name = getattr(br.operator, "value", br.operator)
        op = {"DIFFERENCE": 0, "UNION": 1, "INTERSECTION": 2}.get(str(op_name).upper(), 0)
        a = self._dispatch(br.first_operand)
        b = self._dispatch(br.second_operand)
        return self._add(_BOOLEAN_RESULT, self.i32(op) + self.i32(a) + self.i32(b))

    # --- root + finish -----------------------------------------------------------------
    def _dispatch(self, geom) -> int:
        """Serialize one geometry instance to its record index (used for both
        roots and boolean operands)."""
        from ada.geom import booleans as _bo
        from ada.geom import solids as _so

        if isinstance(geom, _bo.BooleanResult):
            return self.boolean_result(geom)
        if isinstance(geom, _so.ExtrudedAreaSolid) and not isinstance(geom, _so.ExtrudedAreaSolidTapered):
            return self.extruded_area_solid(geom)
        if isinstance(geom, _so.RevolvedAreaSolid):
            return self.revolved_area_solid(geom)
        if isinstance(geom, _so.FixedReferenceSweptAreaSolid):
            return self.fixed_reference_swept_area_solid(geom)
        if isinstance(geom, _so.Box):
            return self.box_solid(geom)
        if isinstance(geom, _so.Cylinder):
            return self.cylinder_solid(geom)
        if isinstance(geom, _so.Cone):
            return self.cone_solid(geom)
        if isinstance(geom, _so.Sphere):
            return self.sphere_solid(geom)
        if isinstance(geom, su.FaceBasedSurfaceModel):
            return self.face_based_surface_model(geom)
        if isinstance(geom, su.ShellBasedSurfaceModel):
            return self.shell_based_surface_model(geom)
        # AdvancedFace is structurally identical to FaceSurface (same face_surface / bounds /
        # same_sense) but NOT a subclass, so it needs its own root check — without it a bare
        # AdvancedFace (e.g. PlateCurved.solid_geom()) is rejected and the curved plate silently
        # falls back to OCC. face_surface() handles both (the cfs path already mixes them).
        if isinstance(geom, (su.FaceSurface, su.AdvancedFace)):
            return self.face_surface(geom)
        if isinstance(geom, (su.ConnectedFaceSet, su.ClosedShell, su.OpenShell)):
            return self.connected_face_set(geom)
        raise _Unsupported(f"geometry {type(geom).__name__}")

    def root(self, geom) -> int:
        """Serialize a top-level geometry instance, returning its record index."""
        return self._dispatch(geom)

    def finish(self, roots: list[tuple[int, str]]) -> bytes:
        out = bytearray(b"ADANGEOM")
        out += self.i32(NGEOM_VERSION) + self.i32(len(self._records))
        for tag, payload in self._records:
            out += self.i32(tag) + self.i32(len(payload)) + payload
        out += self.i32(len(roots))
        for gidx, rid in roots:
            rb = rid.encode("utf-8")
            out += self.i32(gidx) + self.i32(len(rb)) + rb
        return bytes(out)


class _Unsupported(Exception):
    pass


def serialize_geometries(items: Iterable[tuple[str, object]]) -> bytes:
    """Serialize ``(id, geometry)`` pairs into one NGEOM buffer.

    ``geometry`` is an ``ada.geom`` ``FaceSurface`` or ``ConnectedFaceSet``. Instances whose
    geometry can't be mapped are skipped (logged by the caller via the returned root list — a
    skipped item simply gets no root). Returns the binary buffer.
    """
    enc = _Encoder()
    roots: list[tuple[int, str]] = []
    for rid, geom in items:
        try:
            roots.append((enc.root(geom), rid))
        except _Unsupported:
            continue
    return enc.finish(roots)
