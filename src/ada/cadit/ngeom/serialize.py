"""Serialize ``ada.geom`` geometry into the NGEOM binary buffer (spec v1).

The buffer is the contract with adacpp's neutral geometry layer (no adacpp import here). See
dap/plan/v3/spec_neutral_geometry_schema.md for the wire format and tag catalog.
"""

from __future__ import annotations

import struct
from typing import Iterable

import ada.geom.curves as cu
import ada.geom.surfaces as su

NGEOM_VERSION = 1

# tag catalog (spec §3)
_PLACEMENT3 = 1
_PLACEMENT1 = 2
_LINE = 10
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
_EDGE_CURVE = 60
_ORIENTED_EDGE = 61
_EDGE_LOOP = 62
_POLY_LOOP = 63
_FACE_BOUND = 64
_FACE_SURFACE = 65
_CONNECTED_FACE_SET = 66


def _xyz(p) -> tuple[float, float, float]:
    return (float(p[0]), float(p[1]), float(p[2]))


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
        return self.i32(len(xs)) + b"".join(self.f64(x) for x in xs)

    def i32s(self, xs: Iterable[int]) -> bytes:
        xs = list(xs)
        return self.i32(len(xs)) + b"".join(self.i32(x) for x in xs)

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
        else:
            raise _Unsupported(f"curve {type(c).__name__}")
        self._memo[key] = idx
        return idx

    def _bspline_curve(self, c: cu.BSplineCurveWithKnots) -> int:
        weights = list(getattr(c, "weights_data", None) or [])
        body = self.i32(c.degree) + self.i32(1 if c.closed_curve else 0) + self.i32(1 if c.self_intersect else 0)
        cps = list(c.control_points_list)
        body += self.i32(len(cps)) + b"".join(self.v3(p) for p in cps)
        body += self.f64s(c.knots) + b"".join(self.i32(m) for m in c.knot_multiplicities)
        body += self.i32(1 if weights else 0)
        if weights:
            body += b"".join(self.f64(w) for w in weights)
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
            idx = self._add(
                _CONE, self.i32(self.placement3(s.position)) + self.f64(s.radius) + self.f64(s.semi_angle)
            )
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
                self.i32(sc) + self.i32(self.placement3(s.position)) + self.v3(s.extrusion_direction) + self.f64(s.depth),
            )
        elif isinstance(s, su.SurfaceOfRevolution):
            sc = self.curve(s.swept_curve)
            ax = self.placement1(s.axis_position)
            idx = self._add(_SURF_REVOLUTION, self.i32(sc) + self.i32(ax) + self.i32(-1))
        else:
            raise _Unsupported(f"surface {type(s).__name__}")
        self._memo[key] = idx
        return idx

    def _bspline_surface(self, s: su.BSplineSurfaceWithKnots) -> int:
        rows = list(s.control_points_list)  # list[list[Point]] (u rows x v cols)
        nu, nv = len(rows), len(rows[0])
        weights = getattr(s, "weights_data", None)
        body = self.i32(s.u_degree) + self.i32(s.v_degree)
        body += self.i32(1 if s.u_closed else 0) + self.i32(1 if s.v_closed else 0) + self.i32(1 if s.self_intersect else 0)
        body += self.i32(nu) + self.i32(nv)
        for row in rows:  # row-major: u outer, v inner
            for p in row:
                body += self.v3(p)
        body += self.f64s(s.u_knots) + b"".join(self.i32(m) for m in s.u_multiplicities)
        body += self.f64s(s.v_knots) + b"".join(self.i32(m) for m in s.v_multiplicities)
        flat_w = []
        if weights:
            for row in weights:
                flat_w.extend(row)
        body += self.i32(1 if flat_w else 0)
        if flat_w:
            body += b"".join(self.f64(w) for w in flat_w)
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
            return self._add(_POLY_LOOP, self.i32(len(pts)) + b"".join(self.v3(p) for p in pts))
        edges = list(lp.edge_list)
        return self._add(_EDGE_LOOP, self.i32(len(edges)) + b"".join(self.i32(self.oriented_edge(e)) for e in edges))

    def face_bound(self, fb: su.FaceBound) -> int:
        lp = self.loop(fb.bound)
        return self._add(_FACE_BOUND, self.i32(lp) + self.i32(1 if fb.orientation else 0))

    def face_surface(self, f: su.FaceSurface) -> int:
        surf = self.surface(f.face_surface)
        bounds = [self.face_bound(b) for b in f.bounds]
        body = self.i32(surf) + self.i32(1 if f.same_sense else 0) + self.i32(len(bounds))
        body += b"".join(self.i32(b) for b in bounds)
        return self._add(_FACE_SURFACE, body)

    def connected_face_set(self, cfs: su.ConnectedFaceSet) -> int:
        faces = [self.face_surface(f) for f in cfs.cfs_faces]
        return self._add(_CONNECTED_FACE_SET, self.i32(len(faces)) + b"".join(self.i32(f) for f in faces))

    # --- root + finish -----------------------------------------------------------------
    def root(self, geom) -> int:
        """Serialize a top-level geometry instance, returning its record index."""
        if isinstance(geom, su.FaceSurface):
            return self.face_surface(geom)
        if isinstance(geom, su.ConnectedFaceSet):
            return self.connected_face_set(geom)
        raise _Unsupported(f"root geometry {type(geom).__name__}")

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
