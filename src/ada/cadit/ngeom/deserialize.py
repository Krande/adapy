"""Decode an NGEOM buffer back into ``ada.geom`` geometry — the exact inverse of ``serialize.py``.

Wire format (see ``serialize.py`` / the neutral-geometry schema spec): a header
``b"ADANGEOM"`` + ``i32 version`` + ``i32 num_records``, then ``num_records`` records each
``i32 tag, i32 nbytes, payload``, then a roots trailer ``i32 root_count`` followed by
``i32 geom_record_index, i32 id_len, id_utf8`` per root. Records reference earlier records by their
0-based index, so a single forward pass decodes the whole tree.

Used by the native STEP reader (``read_step_file(reader="native")``): ``adacpp.cad.stream_step_to_ngeom``
emits one NGEOM root per solid (a B-rep ``ConnectedFaceSet``), and this rebuilds each as an
``ada.geom`` geometry. Also the round-trip oracle for ``serialize_geometries``.

Swept/CSG solids (extruded/revolved/boolean/sphere — tags 50-53) are serialized with a synthesized
profile FACE; they decode back to the ada.geom solid (the profile is rebuilt from the face's local-XY
boundary loops). Only the baked-frame FixedReferenceSweptAreaSolid (tag 54) can't be inverted — its
directrix is gone — so it raises (hydrate that via the adacpp tessellator instead).
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
_EXTRUDED_AREA_SOLID, _REVOLVED_AREA_SOLID, _BOOLEAN_RESULT, _SPHERE_SOLID, _FIXED_REF_SWEPT_SOLID = (
    50,
    51,
    52,
    53,
    54,
)
_EDGE_CURVE, _ORIENTED_EDGE, _EDGE_LOOP, _POLY_LOOP, _FACE_BOUND, _FACE_SURFACE, _CONNECTED_FACE_SET = (
    60,
    61,
    62,
    63,
    64,
    65,
    66,
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

    # --- swept / CSG solids (tags 50-54) ---
    @staticmethod
    def _loop_to_curve(loop) -> cu.IndexedPolyCurve:
        """One profile boundary loop (in the profile's local XY plane, z=0) → a closed 2D
        IndexedPolyCurve. A ``PolyLoop`` is a straight-edge polygon; an ``EdgeLoop`` restores a
        Circle-backed oriented edge as an ``ArcLine`` (fillet round-trip), else a straight ``Edge``."""

        def p2d(p) -> Point:
            return Point(float(p[0]), float(p[1]))

        if isinstance(loop, cu.PolyLoop):
            pts = [p2d(p) for p in loop.polygon]
            return cu.IndexedPolyCurve([cu.Edge(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))])

        segs: list = []
        for oe in loop.edge_list:
            s, e = p2d(oe.start), p2d(oe.end)
            geom = getattr(oe.edge_element, "edge_geometry", None)
            if isinstance(geom, cu.Circle):
                ctr = np.asarray(geom.position.location, dtype=float)[:2]
                a = np.asarray(s, dtype=float) - ctr
                b = np.asarray(e, dtype=float) - ctr
                bis = a + b  # minor-arc midpoint direction (profile fillets are minor arcs)
                if np.linalg.norm(bis) < 1e-12:  # half-circle: rotate `a` 90°
                    bis = np.array([-a[1], a[0]])
                mid = ctr + float(geom.radius) * bis / (np.linalg.norm(bis) or 1.0)
                segs.append(cu.ArcLine(s, Point(float(mid[0]), float(mid[1])), e))
            else:
                segs.append(cu.Edge(s, e))
        return cu.IndexedPolyCurve(segs)

    def _profile_from_face(self, face) -> su.ArbitraryProfileDef:
        """Rebuild the swept-area ProfileDef the encoder flattened into a planar FACE — outer bound
        first, then any inner (void) bounds."""
        bounds = list(getattr(face, "bounds", None) or [])
        if not bounds:
            raise NgeomDecodeError("swept solid profile face has no bounds")
        return su.ArbitraryProfileDef(
            profile_type=su.ProfileType.AREA,
            outer_curve=self._loop_to_curve(bounds[0].bound),
            inner_curves=[self._loop_to_curve(b.bound) for b in bounds[1:]],
        )

    def _extruded_area_solid(self, c: _Cur):
        import ada.geom.solids as so

        profile = self._profile_from_face(self.get(c.i32()))
        position = self.get(c.i32())
        direction = Direction(*c.v3())
        depth = c.f64()
        return so.ExtrudedAreaSolid(swept_area=profile, position=position, depth=depth, extruded_direction=direction)

    def _revolved_area_solid(self, c: _Cur):
        import math

        import ada.geom.solids as so

        profile = self._profile_from_face(self.get(c.i32()))
        position = self.get(c.i32())
        axis_local = self.get(c.i32())  # Axis1Placement in the position-LOCAL frame (encoder transform)
        angle_rad = c.f64()
        # Invert the encoder's world→local axis transform (serialize.revolved_area_solid): rebuild the
        # position frame's rotation and map the local axis back to world. angle is stored in RADIANS;
        # RevolvedAreaSolid.angle is DEGREES.
        xdir = np.asarray(position.ref_direction if position.ref_direction is not None else (1, 0, 0), float)
        zdir = np.asarray(position.axis if position.axis is not None else (0, 0, 1), float)
        xdir = xdir / (np.linalg.norm(xdir) or 1.0)
        zdir = zdir / (np.linalg.norm(zdir) or 1.0)
        rot = np.column_stack([xdir, np.cross(zdir, xdir), zdir])  # local→world
        origin = np.asarray(position.location, float)
        loc_w = rot @ np.asarray(axis_local.location, float) + origin
        dir_w = rot @ np.asarray(axis_local.axis, float)
        axis = Axis1Placement(Point(*loc_w), Direction(*dir_w))
        return so.RevolvedAreaSolid(swept_area=profile, position=position, axis=axis, angle=math.degrees(angle_rad))

    def _sphere_solid(self, c: _Cur):
        import ada.geom.solids as so

        pos = self.get(c.i32())
        radius = c.f64()
        return so.Sphere(center=Point(*pos.location), radius=radius)

    def _boolean_result(self, c: _Cur):
        from ada.geom.booleans import BooleanResult, BoolOpEnum

        op = {0: BoolOpEnum.DIFFERENCE, 1: BoolOpEnum.UNION, 2: BoolOpEnum.INTERSECTION}[c.i32()]
        first = self.get(c.i32())
        second = self.get(c.i32())
        return BooleanResult(first_operand=first, second_operand=second, operator=op)

    def _fixed_ref_swept(self, c: _Cur):
        # tag 54 bakes an alignment sweep into per-station (origin, dir_x, dir_y) frames — the
        # analytic directrix is gone, so it cannot be rebuilt as an ada.geom
        # FixedReferenceSweptAreaSolid. Consumers that need this must decode via adacpp (which
        # tessellates the frames directly); flag it rather than return a wrong solid.
        raise NgeomDecodeError(
            "FIXED_REF_SWEPT_SOLID (tag 54) is a baked per-station frame field with no invertible "
            "directrix — hydrate via the adacpp tessellator, not the Python deserializer"
        )


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
    _EXTRUDED_AREA_SOLID: _Decoder._extruded_area_solid,
    _REVOLVED_AREA_SOLID: _Decoder._revolved_area_solid,
    _BOOLEAN_RESULT: _Decoder._boolean_result,
    _SPHERE_SOLID: _Decoder._sphere_solid,
    _FIXED_REF_SWEPT_SOLID: _Decoder._fixed_ref_swept,
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


def connected_face_set_is_closed(cfs) -> bool:
    """Topological closedness of a decoded ``ConnectedFaceSet``: every (undirected)
    edge is used exactly twice across the faces' bounds — the manifold-closed-shell
    condition. NGEOM tag 66 does not record whether the source shell was closed
    (MANIFOLD_SOLID_BREP) or open (SHELL_BASED_SURFACE_MODEL), so the native read
    paths use this to restore ``ClosedShell`` parity with the Python stream reader.

    Edges are keyed by VALUE, not object identity — the C++ emitter re-encodes an
    edge record per referencing face. Endpoints alone are ambiguous for arcs (two
    halves of one circle share both endpoints), so the key adds the underlying
    curve's signature and the oriented edge's parameter range. Two uses of the same
    key = the edge is interior; any other count = a free or non-manifold edge. A
    model whose adjacent faces don't encode identical edge values conservatively
    reads as open."""
    from collections import Counter

    edge_use: Counter = Counter()
    for f in getattr(cfs, "cfs_faces", []) or []:
        for b in getattr(f, "bounds", []) or []:
            loop = getattr(b, "bound", None)
            edge_list = getattr(loop, "edge_list", None)
            if edge_list is not None:
                for oe in edge_list:
                    e = oe.edge_element
                    a, z = tuple(map(float, e.start)), tuple(map(float, e.end))
                    ts, te = getattr(oe, "t_start", None), getattr(oe, "t_end", None)
                    trange = None if ts is None else (min(ts, te), max(ts, te))
                    key = ((a, z) if a <= z else (z, a), _edge_curve_sig(e.edge_geometry), trange)
                    edge_use[key] += 1
                continue
            polygon = getattr(loop, "polygon", None)
            if polygon is not None:
                pts = [tuple(map(float, p)) for p in polygon]
                for i, a in enumerate(pts):
                    z = pts[(i + 1) % len(pts)]
                    if a == z:
                        continue
                    edge_use[((a, z) if a <= z else (z, a), None, None)] += 1
    if not edge_use:
        return False
    return all(n == 2 for n in edge_use.values())


def _edge_curve_sig(geom):
    """A value signature of an edge's underlying curve, discriminating edges whose
    endpoints coincide (arcs of one circle, seam edges). Decoded floats from equal
    source records are bit-identical, so exact tuples work as keys."""
    if geom is None:
        return None
    tname = type(geom).__name__
    pos = getattr(geom, "position", None)
    loc = tuple(map(float, pos.location)) if pos is not None else None
    if hasattr(geom, "radius"):  # Circle / cylinder-ish
        return (tname, loc, float(geom.radius))
    if hasattr(geom, "semi_axis1"):  # Ellipse
        return (tname, loc, float(geom.semi_axis1), float(geom.semi_axis2))
    cps = getattr(geom, "control_points_list", None)
    if cps is not None:  # B-spline
        return (tname, tuple(tuple(map(float, p)) for p in cps))
    pnt = getattr(geom, "pnt", None)
    if pnt is not None:  # Line
        return (tname,)
    return (tname, loc)


def promote_closed_shell(geometry):
    """Return ``ClosedShell(cfs_faces)`` when ``geometry`` is a bare, topologically
    closed ``ConnectedFaceSet`` root; otherwise return it unchanged. Restores the
    Python stream reader's root form (solid_geom / OCC build / STEP re-emit all key
    on ClosedShell vs open shells) for natively-parsed B-reps."""
    if type(geometry) is su.ConnectedFaceSet and connected_face_set_is_closed(geometry):
        return su.ClosedShell(cfs_faces=geometry.cfs_faces)
    return geometry


def iter_connected_face_set_faces(buffer: bytes):
    """If the NGEOM buffer's single root is a ``ConnectedFaceSet`` (a B-rep solid's
    shell), return ``(rid, n_faces, face_gen)`` where ``face_gen`` yields each
    ``FaceSurface`` ONE AT A TIME, clearing the decoder cache between faces so a
    giant solid (the 67 MB / millions-of-faces case) never has its whole ada.geom
    tree resident. Returns ``None`` if the root isn't a single ConnectedFaceSet —
    the caller then falls back to :func:`deserialize_geometries`.

    Bounded-memory streaming emit (STEP→IFC / STEP→STEP): the caller emits each face
    and keeps only its small entity id, so peak is ~one face's ada.geom, not the
    whole shell.
    """
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
    if n_roots != 1:
        return None
    gidx = struct.unpack_from("<i", mv, o)[0]
    id_len = struct.unpack_from("<i", mv, o + 4)[0]
    o += 8
    rid = bytes(mv[o : o + id_len]).decode("utf-8")
    if gidx < 0 or gidx >= len(records) or records[gidx][0] != _CONNECTED_FACE_SET:
        return None
    cur = _Cur(records[gidx][1])
    n = cur.i32()
    idxs = cur.i32a(n)

    def _gen():
        for fi in idxs:
            dec._cache.clear()  # free the previous face's sub-records (+ its interned Points)
            yield dec.get(fi)

    return rid, n, _gen()
