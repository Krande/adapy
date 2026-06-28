"""Decode an NGEOM buffer back into ``ada.geom`` geometry — the exact inverse of ``serialize.py``.

Wire format (see ``serialize.py`` / dap/plan/v3/spec_neutral_geometry_schema.md): a header
``b"ADANGEOM"`` + ``i32 version`` + ``i32 num_records``, then ``num_records`` records each
``i32 tag, i32 nbytes, payload``, then a roots trailer ``i32 root_count`` followed by
``i32 geom_record_index, i32 id_len, id_utf8`` per root. Records reference earlier records by their
0-based index, so a single forward pass decodes the whole tree.

Used by the native STEP reader (``read_step_file(reader="native")``): ``adacpp.cad.stream_step_to_ngeom``
emits one NGEOM root per solid (a B-rep ``ConnectedFaceSet``), and this rebuilds each as an
``ada.geom`` geometry. Also the round-trip oracle for ``serialize_geometries``.

Swept/CSG solids (extruded/revolved/boolean/sphere primitives) are serialized as synthesized profile
faces; the native STEP path never emits them (STEP solids are explicit B-reps), so those tags raise.
"""

from __future__ import annotations

import struct

import numpy as np

import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.geom.direction import Direction
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.points import Point

_MAGIC = b"ADANGEOM"
NGEOM_VERSION = 1

# tag catalog — must match serialize.py
_PLACEMENT3, _PLACEMENT1 = 1, 2
_LINE, _POLYLINE, _HYPERBOLA, _PARABOLA, _COMPOSITE_CURVE = 10, 11, 12, 13, 14
_CIRCLE, _ELLIPSE, _BSPLINE_CURVE, _TRIMMED_CURVE = 20, 21, 22, 24
_PLANE, _CYLINDER, _CONE, _SPHERE, _TORUS, _BSPLINE_SURFACE = 40, 41, 42, 43, 44, 45
_SURF_LIN_EXTRUSION, _SURF_REVOLUTION = 46, 47
_EDGE_CURVE, _ORIENTED_EDGE, _EDGE_LOOP, _POLY_LOOP, _FACE_BOUND, _FACE_SURFACE, _CONNECTED_FACE_SET = (
    60, 61, 62, 63, 64, 65, 66,
)


class NgeomDecodeError(Exception):
    pass


class _Cur:
    """Little-endian cursor over one record's payload (mirrors _Encoder's scalar/array helpers)."""

    __slots__ = ("b", "o")

    def __init__(self, b: memoryview):
        self.b = b
        self.o = 0

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.b, self.o)[0]
        self.o += 4
        return v

    def f64(self) -> float:
        v = struct.unpack_from("<d", self.b, self.o)[0]
        self.o += 8
        return v

    def v3(self) -> tuple[float, float, float]:
        t = struct.unpack_from("<ddd", self.b, self.o)
        self.o += 24
        return t

    def f64a(self, n: int) -> list[float]:
        a = np.frombuffer(self.b, "<f8", n, self.o).tolist()
        self.o += 8 * n
        return a

    def i32a(self, n: int) -> list[int]:
        a = np.frombuffer(self.b, "<i4", n, self.o).tolist()
        self.o += 4 * n
        return a

    def pts(self, n: int) -> list[Point]:
        a = np.frombuffer(self.b, "<f8", 3 * n, self.o).reshape(n, 3)
        self.o += 24 * n
        return [Point(*row) for row in a.tolist()]


class _Decoder:
    def __init__(self, records: list[tuple[int, memoryview]]):
        self._records = records
        self._cache: dict[int, object] = {}

    def get(self, idx: int):
        # A negative index is the encoder's "no record here" sentinel (e.g. an
        # absent surface/position/curve). Return None rather than letting Python's
        # negative-index wrap resolve it to the last record (which recurses into a
        # cycle on the root ConnectedFaceSet). Callers already accept None geometry.
        if idx < 0:
            return None
        if idx in self._cache:
            return self._cache[idx]
        tag, payload = self._records[idx]
        fn = _DISPATCH.get(tag)
        if fn is None:
            raise NgeomDecodeError(f"unsupported NGEOM tag {tag} at record {idx}")
        obj = fn(self, _Cur(payload))
        self._cache[idx] = obj
        return obj

    # --- placements ---
    def _placement3(self, c: _Cur):
        return Axis2Placement3D(Point(*c.v3()), Direction(*c.v3()), Direction(*c.v3()))

    def _placement1(self, c: _Cur):
        return Axis1Placement(Point(*c.v3()), Direction(*c.v3()))

    # --- curves ---
    def _line(self, c: _Cur):
        return cu.Line(Point(*c.v3()), Direction(*c.v3()))

    def _polyline(self, c: _Cur):
        return cu.PolyLine(c.pts(c.i32()))

    def _circle(self, c: _Cur):
        return cu.Circle(self.get(c.i32()), c.f64())

    def _ellipse(self, c: _Cur):
        return cu.Ellipse(self.get(c.i32()), c.f64(), c.f64())

    def _hyperbola(self, c: _Cur):
        return cu.Hyperbola(self.get(c.i32()), c.f64(), c.f64())

    def _parabola(self, c: _Cur):
        return cu.Parabola(self.get(c.i32()), c.f64())

    def _composite_curve(self, c: _Cur):
        n = c.i32()
        segs = [cu.CompositeCurveSegment(self.get(c.i32()), bool(c.i32())) for _ in range(n)]
        return cu.CompositeCurve(segs, self_intersect=False)

    def _bspline_curve(self, c: _Cur):
        degree, closed, self_int, n_ctrl = c.i32(), c.i32(), c.i32(), c.i32()
        ctrl = c.pts(n_ctrl)
        n_knots = c.i32()
        knots = c.f64a(n_knots)
        mults = c.i32a(n_knots)
        has_w = c.i32()
        common = dict(
            degree=degree,
            control_points_list=ctrl,
            curve_form=cu.BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=bool(closed),
            self_intersect=bool(self_int),
            knot_multiplicities=mults,
            knots=knots,
            knot_spec=cu.KnotType.UNSPECIFIED,
        )
        if has_w:
            return cu.RationalBSplineCurveWithKnots(weights_data=c.f64a(n_ctrl), **common)
        return cu.BSplineCurveWithKnots(**common)

    def _trimmed_curve(self, c: _Cur):
        basis = self.get(c.i32())
        t1, t2 = c.f64(), c.f64()
        sense = bool(c.i32())
        master = {0: "PARAMETER", 1: "CARTESIAN", 2: "UNSPECIFIED"}.get(c.i32(), "PARAMETER")
        return cu.TrimmedCurve(basis, t1, t2, sense_agreement=sense, master_representation=master)

    # --- surfaces ---
    def _plane(self, c: _Cur):
        return su.Plane(self.get(c.i32()))

    def _cylinder(self, c: _Cur):
        return su.CylindricalSurface(self.get(c.i32()), c.f64())

    def _cone(self, c: _Cur):
        return su.ConicalSurface(self.get(c.i32()), c.f64(), c.f64())

    def _sphere(self, c: _Cur):
        return su.SphericalSurface(self.get(c.i32()), c.f64())

    def _torus(self, c: _Cur):
        return su.ToroidalSurface(self.get(c.i32()), c.f64(), c.f64())

    def _surf_lin_extrusion(self, c: _Cur):
        sc = self.get(c.i32())
        pos = self.get(c.i32())
        return su.SurfaceOfLinearExtrusion(sc, pos, Direction(*c.v3()), c.f64())

    def _surf_revolution(self, c: _Cur):
        sc = self.get(c.i32())
        ax = self.get(c.i32())
        c.i32()  # reserved (-1)
        return su.SurfaceOfRevolution(sc, ax)

    def _bspline_surface(self, c: _Cur):
        u_deg, v_deg = c.i32(), c.i32()
        u_closed, v_closed, self_int = c.i32(), c.i32(), c.i32()
        nu, nv = c.i32(), c.i32()
        flat = c.pts(nu * nv)
        grid = [flat[u * nv : (u + 1) * nv] for u in range(nu)]  # row-major: u outer, v inner
        n_uk = c.i32()
        u_knots = c.f64a(n_uk)
        u_mults = c.i32a(n_uk)
        n_vk = c.i32()
        v_knots = c.f64a(n_vk)
        v_mults = c.i32a(n_vk)
        has_w = c.i32()
        common = dict(
            u_degree=u_deg,
            v_degree=v_deg,
            control_points_list=grid,
            surface_form=su.BSplineSurfaceForm.UNSPECIFIED,
            u_closed=bool(u_closed),
            v_closed=bool(v_closed),
            self_intersect=bool(self_int),
            u_multiplicities=u_mults,
            v_multiplicities=v_mults,
            u_knots=u_knots,
            v_knots=v_knots,
            knot_spec=cu.KnotType.UNSPECIFIED,
        )
        if has_w:
            wf = c.f64a(nu * nv)
            wgrid = [wf[u * nv : (u + 1) * nv] for u in range(nu)]
            return su.RationalBSplineSurfaceWithKnots(weights_data=wgrid, **common)
        return su.BSplineSurfaceWithKnots(**common)

    # --- topology ---
    def _edge_curve(self, c: _Cur):
        start, end = Point(*c.v3()), Point(*c.v3())
        geom_ref = c.i32()
        same_sense = bool(c.i32())
        geom = self.get(geom_ref) if geom_ref >= 0 else None
        return cu.EdgeCurve(start=start, end=end, edge_geometry=geom, same_sense=same_sense)

    def _oriented_edge(self, c: _Cur):
        edge = self.get(c.i32())
        orientation = bool(c.i32())
        c.i32()  # has_pcurve (always 0 in v1)
        has_params = c.i32()
        t_start = t_end = None
        if has_params:
            t_start, t_end = c.f64(), c.f64()
        # the oriented edge's own vertices follow the orientation of its underlying edge
        s, e = (edge.start, edge.end) if orientation else (edge.end, edge.start)
        return cu.OrientedEdge(
            start=s, end=e, edge_element=edge, orientation=orientation, pcurve=None, t_start=t_start, t_end=t_end
        )

    def _edge_loop(self, c: _Cur):
        n = c.i32()
        return cu.EdgeLoop([self.get(c.i32()) for _ in range(n)])

    def _poly_loop(self, c: _Cur):
        return cu.PolyLoop(c.pts(c.i32()))

    def _face_bound(self, c: _Cur):
        loop = self.get(c.i32())
        return su.FaceBound(loop, bool(c.i32()))

    def _face_surface(self, c: _Cur):
        surf = self.get(c.i32())
        same_sense = bool(c.i32())
        n = c.i32()
        bounds = [self.get(c.i32()) for _ in range(n)]
        return su.FaceSurface(bounds=bounds, face_surface=surf, same_sense=same_sense)

    def _connected_face_set(self, c: _Cur):
        n = c.i32()
        return su.ConnectedFaceSet([self.get(i) for i in c.i32a(n)])


_DISPATCH = {
    _PLACEMENT3: _Decoder._placement3,
    _PLACEMENT1: _Decoder._placement1,
    _LINE: _Decoder._line,
    _POLYLINE: _Decoder._polyline,
    _CIRCLE: _Decoder._circle,
    _ELLIPSE: _Decoder._ellipse,
    _HYPERBOLA: _Decoder._hyperbola,
    _PARABOLA: _Decoder._parabola,
    _COMPOSITE_CURVE: _Decoder._composite_curve,
    _BSPLINE_CURVE: _Decoder._bspline_curve,
    _TRIMMED_CURVE: _Decoder._trimmed_curve,
    _PLANE: _Decoder._plane,
    _CYLINDER: _Decoder._cylinder,
    _CONE: _Decoder._cone,
    _SPHERE: _Decoder._sphere,
    _TORUS: _Decoder._torus,
    _SURF_LIN_EXTRUSION: _Decoder._surf_lin_extrusion,
    _SURF_REVOLUTION: _Decoder._surf_revolution,
    _BSPLINE_SURFACE: _Decoder._bspline_surface,
    _EDGE_CURVE: _Decoder._edge_curve,
    _ORIENTED_EDGE: _Decoder._oriented_edge,
    _EDGE_LOOP: _Decoder._edge_loop,
    _POLY_LOOP: _Decoder._poly_loop,
    _FACE_BOUND: _Decoder._face_bound,
    _FACE_SURFACE: _Decoder._face_surface,
    _CONNECTED_FACE_SET: _Decoder._connected_face_set,
}


def deserialize_geometries(buffer: bytes) -> list[tuple[str, object]]:
    """Decode an NGEOM buffer into ``(id, ada.geom geometry)`` pairs — inverse of
    :func:`ada.cadit.ngeom.serialize.serialize_geometries`. Each root geometry is typically a
    ``ConnectedFaceSet`` (a B-rep solid's shell) or a ``FaceSurface``."""
    mv = memoryview(buffer)
    if bytes(mv[:8]) != _MAGIC:
        raise NgeomDecodeError("not an NGEOM buffer (bad magic)")
    o = 8
    version = struct.unpack_from("<i", mv, o)[0]
    o += 4
    if version != NGEOM_VERSION:
        raise NgeomDecodeError(f"unsupported NGEOM version {version} (decoder is v{NGEOM_VERSION})")
    n_records = struct.unpack_from("<i", mv, o)[0]
    o += 4
    records: list[tuple[int, memoryview]] = []
    for _ in range(n_records):
        tag = struct.unpack_from("<i", mv, o)[0]
        nbytes = struct.unpack_from("<i", mv, o + 4)[0]
        o += 8
        records.append((tag, mv[o : o + nbytes]))
        o += nbytes
    dec = _Decoder(records)
    n_roots = struct.unpack_from("<i", mv, o)[0]
    o += 4
    out: list[tuple[str, object]] = []
    for _ in range(n_roots):
        gidx = struct.unpack_from("<i", mv, o)[0]
        id_len = struct.unpack_from("<i", mv, o + 4)[0]
        o += 8
        rid = bytes(mv[o : o + id_len]).decode("utf-8")
        o += id_len
        out.append((rid, dec.get(gidx)))
    return out
