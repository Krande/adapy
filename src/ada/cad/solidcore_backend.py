"""EXPERIMENTAL: a CadBackend on the solidcore B-rep kernel.

solidcore (https://github.com/tangency-labs/solidcore, Apache-2.0) is a pure-Rust port of
NVIDIA's SMLib B-rep kernel. It ships no Python package, so this backend talks to it through
``solidcore_py``, a thin PyO3 binding built from source in ``ext/solidcore-py`` (see the README
there and the ``tests-solidcore`` pixi environment).

Opt-in only: ``ADAPY_CAD_BACKEND=solidcore`` or ``select_backend(prefer="solidcore")``. It is
never auto-selected.

Units. solidcore works in millimetres -- its tolerances are absolute mm values -- while adapy
models in metres. Every length crossing the boundary is therefore scaled by ``scale`` (default
1000, override with ``ADAPY_SOLIDCORE_SCALE``): lengths in, coordinates/areas/volumes back out.
A model in metres thus meets the kernel at the sizes its tolerances were written for.

Handles. A :class:`SolidcoreShape` owns one kernel body. Sub-shapes (faces, edges, wires,
shells) are light views -- (parent, index) into the body's B-rep record -- answered from the
record the binding hands over as JSON and from the kernel's per-face measures. They cannot be
used as operands of a boolean on their own.

Verbs the kernel (or this prototype) cannot answer raise ``NotImplementedError`` naming why.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ada.cad import Containment, check_similarity_matrix, tessellate_batch_via_loop

if TYPE_CHECKING:
    from ada.geom import Geometry
    from ada.geom.booleans import BoolOpEnum


def _nyi(verb: str, why: str):
    raise NotImplementedError(f"SolidcoreBackend.{verb}: {why}")


# --------------------------------------------------------------------------------------------
# Handles
# --------------------------------------------------------------------------------------------


@dataclass(eq=False)
class SolidcoreShape:
    """One kernel body (solid, sheet or a set of them)."""

    body: Any  # solidcore_py.Body
    # What OCC would call the result, where structure alone cannot say: a planar_faces sheet is
    # a "face", a boolean's sheet result a "compound" (OCC's BRepAlgoAPI results are).
    kind: str | None = None
    _record: dict | None = field(default=None, repr=False)

    @property
    def record(self) -> dict:
        if self._record is None:
            self._record = json.loads(self.body.record_json())
        return self._record


@dataclass(eq=False)
class SolidcoreFace:
    parent: SolidcoreShape
    index: int


@dataclass(eq=False)
class SolidcoreEdge:
    parent: SolidcoreShape
    index: int


@dataclass(eq=False)
class SolidcoreWire:
    parent: SolidcoreShape
    face: int
    loop: int


@dataclass(eq=False)
class SolidcoreShell:
    parent: SolidcoreShape
    index: int


@dataclass
class SolidcoreMesh:
    positions: np.ndarray
    indices: np.ndarray
    normals: np.ndarray
    face_ids: np.ndarray

    @property
    def faces(self):  # the name OccBackend's TriangleMesh uses
        return self.indices


_HANDLE_TYPES = (SolidcoreShape, SolidcoreFace, SolidcoreEdge, SolidcoreWire, SolidcoreShell)

_SURFACE_TYPES = {
    "plane": "plane",
    "plane_through": "plane",
    "cylinder": "cylinder",
    "cone": "cone",
    "sphere": "sphere",
    "torus": "torus",
    "ball_patch": "sphere",
    "spline": "bspline",
    "rational_spline": "bspline",
    "placed_revolution": "revolution",
    "blend": "blend",
}


# --------------------------------------------------------------------------------------------
# Small geometry helpers (plain vector maths on the Python side)
# --------------------------------------------------------------------------------------------


def _v(p) -> np.ndarray:
    c = [float(x) for x in p]
    if len(c) == 2:
        c.append(0.0)
    return np.asarray(c, dtype=float)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("zero-length direction")
    return v / n


def _arc_through(a, m, b) -> tuple:
    """The circle arc from a through m to b, as the binding's ("arc", ...) tuple (world coords)."""
    a, m, b = _v(a), _v(m), _v(b)
    u, w = m - a, b - a
    n = np.cross(u, w)
    nn = float(n @ n)
    if nn < 1e-30:
        raise ValueError("ArcLine: the three points are collinear")
    c = a + (np.cross(n, u) * float(w @ w) + np.cross(w, n) * float(u @ u)) / (2.0 * nn)
    axis = n / math.sqrt(nn)  # a -> m -> b runs counter-clockwise about this axis
    ref = _unit(a - c)
    r = float(np.linalg.norm(a - c))
    y = np.cross(axis, ref)
    rel = b - c
    end = math.atan2(float(rel @ y), float(rel @ ref)) % (2 * math.pi)
    if end <= 1e-12:
        end = 2 * math.pi
    return ("arc", c.tolist(), axis.tolist(), ref.tolist(), r, 0.0, end)


# --------------------------------------------------------------------------------------------
# The backend
# --------------------------------------------------------------------------------------------


class SolidcoreBackend:
    """CadBackend on solidcore (EXPERIMENTAL prototype)."""

    name = "solidcore"

    def __init__(self, scale: float | None = None) -> None:
        import solidcore_py

        self._sc = solidcore_py
        if scale is None:
            scale = float(os.environ.get("ADAPY_SOLIDCORE_SCALE", "1000"))
        self.scale = float(scale)
        # relative accuracy handed to the kernel's integrators
        self.accuracy = 1e-10

    # --- unit conversion ---------------------------------------------------------------
    def _in(self, p) -> list[float]:
        return [float(x) * self.scale for x in _v(p)]

    def _out(self, p) -> tuple[float, float, float]:
        return tuple(float(x) / self.scale for x in p)

    def _curves_in(self, curves: list[tuple]) -> list[tuple]:
        """World-coordinate curve tuples (model units) -> kernel units."""
        s = self.scale
        out = []
        for c in curves:
            if c[0] == "line":
                out.append(("line", [x * s for x in c[1]], [x * s for x in c[2]]))
            elif c[0] == "arc":
                out.append(("arc", [x * s for x in c[1]], list(c[2]), list(c[3]), c[4] * s, c[5], c[6]))
            else:
                raise ValueError(f"curve kind {c[0]!r}")
        return out

    def _wrap(self, body) -> SolidcoreShape:
        return SolidcoreShape(body)

    @staticmethod
    def _body_of(shape) -> SolidcoreShape:
        if isinstance(shape, SolidcoreShape):
            return shape
        raise TypeError(f"expected a solidcore body, got {type(shape).__name__}")

    @staticmethod
    def _polygon(points) -> list[tuple]:
        pts = [_v(p) for p in points]
        if len(pts) > 2 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        if len(pts) < 3:
            raise ValueError("need at least 3 points for a closed polygon")
        return [("line", a.tolist(), b.tolist()) for a, b in zip(pts, pts[1:] + pts[:1])]

    # --- primitives -------------------------------------------------------------------------
    def make_box(self, dx: float, dy: float, dz: float) -> SolidcoreShape:
        s = self.scale
        return self._wrap(self._sc.make_box([-dx * s / 2, -dy * s / 2, -dz * s / 2], [dx * s, dy * s, dz * s]))

    def make_cylinder(self, radius: float, height: float) -> SolidcoreShape:
        return self._wrap(self._sc.make_cylinder([0.0, 0.0, 0.0], radius * self.scale, height * self.scale))

    def make_sphere(self, radius: float) -> SolidcoreShape:
        return self._wrap(self._sc.make_sphere([0.0, 0.0, 0.0], radius * self.scale))

    def make_wire(self, points: list) -> Any:
        _nyi("make_wire", "solidcore-ops has no wire-body constructor exposed by the prototype binding")

    def polygon_face(self, points: list) -> SolidcoreShape:
        return SolidcoreShape(self._sc.planar_faces(self._curves_in(self._polygon(points))), kind="face")

    def _as_face(self, shape) -> SolidcoreFace:
        """A face handle, or a one-face sheet body read as its face."""
        if isinstance(shape, SolidcoreFace):
            return shape
        faces = self.faces(shape)
        if not faces:
            raise ValueError("shape has no face")
        return faces[0]

    def _face_loops(self, face: SolidcoreFace) -> list[list[tuple]]:
        """The face's loops as curve tuples in MODEL units (outer first). Lines only."""
        rec = face.parent.record
        loops = []
        for lp in rec["faces"][face.index]["loops"]:
            if lp.get("kind") != "edges":
                continue
            out = []
            for oe in lp["edges"]:
                e = rec["edges"][oe["edge"]]
                if e["curve"]["kind"] != "line":
                    _nyi("_face_loops", f"edge curve {e['curve']['kind']!r} not supported (lines only)")
                v0, v1 = (rec["vertices"][i]["point"] for i in e["vertices"])
                a, b = (v0, v1) if oe["forward"] else (v1, v0)
                out.append(("line", list(self._out(a)), list(self._out(b))))
            loops.append(out)
        return loops

    def extrude_face_along_normal(self, face, thickness: float) -> SolidcoreShape:
        f = self._as_face(face)
        plane = self.face_plane(f)
        if plane is None:
            _nyi("extrude_face_along_normal", "only planar faces")
        normal = [float(c) for c in plane[1]]
        loops = self._face_loops(f)
        shape = self.extrude_profile(loops[0], normal, thickness)
        for hole in loops[1:]:
            shape = self.boolean_named("subtract", shape, self.extrude_profile(hole, normal, thickness))
        return shape

    def extrude_profile(self, curves: list[tuple], direction, distance: float, draft: float = 0.0) -> SolidcoreShape:
        """Extra verb: a closed planar profile (world-coordinate curve tuples, model units) swept
        ``distance`` along ``direction``. ``draft`` (radians) tapers the walls."""
        return self._wrap(
            self._sc.extrude(self._curves_in(curves), list(_unit(direction)), distance * self.scale, draft, True)
        )

    def loft_profiles(self, profiles, ruled: bool = True, solid: bool = True) -> SolidcoreShape:
        if len(profiles) < 2:
            raise ValueError(f"loft_profiles needs at least 2 profiles, got {len(profiles)}")
        sections = [self._curves_in(self._polygon(p)) for p in profiles]
        return self.loft_curves(sections, ruled=ruled, solid=solid, _scaled=True)

    def loft_curves(self, sections: list[list[tuple]], ruled: bool = True, solid: bool = True, _scaled=False):
        """Extra verb: loft through sections given as curve tuples (lines/arcs), so a section
        may be exactly circular rather than a polygon."""
        if not _scaled:
            sections = [self._curves_in(s) for s in sections]
        degree = 1 if ruled else min(3, len(sections) - 1)
        return self._wrap(self._sc.loft(sections, degree, solid))

    def build(self, geometry: "Geometry") -> SolidcoreShape:
        import ada.geom.curves as cu
        import ada.geom.solids as so
        import ada.geom.surfaces as su
        from ada.api.beams.geom_beams import parametric_profile_to_arbitrary

        g = geometry.geometry
        s = self.scale

        def frame(p):
            loc = _v(p.location)
            z = _unit(_v(p.axis) if p.axis is not None else (0, 0, 1))
            x = _unit(_v(p.ref_direction) if p.ref_direction is not None else (1, 0, 0))
            x = _unit(x - (x @ z) * z)
            return loc, x, np.cross(z, x), z

        if isinstance(g, so.Box):
            loc, x, y, z = frame(g.position)
            body = self._sc.make_box([0.0, 0.0, 0.0], [g.x_length * s, g.y_length * s, g.z_length * s])
            shape = self._wrap(body)
            self._place(shape, loc, x, y, z)
        elif isinstance(g, so.Cylinder):
            loc, x, y, z = frame(g.position)
            shape = self._wrap(self._sc.make_cylinder([0.0, 0.0, 0.0], g.radius * s, g.height * s))
            self._place(shape, loc, x, y, z)
        elif isinstance(g, so.Sphere):
            shape = self._wrap(self._sc.make_sphere(self._in(g.center), g.radius * s))
        elif isinstance(g, so.Cone):
            loc, x, y, z = frame(g.position)
            # IFC right circular cone: apex at height; solidcore needs a positive top radius
            shape = self._wrap(self._sc.make_cone([0.0, 0.0, 0.0], g.bottom_radius * s, 1e-6, g.height * s))
            self._place(shape, loc, x, y, z)
        elif isinstance(g, so.ExtrudedAreaSolid) and not isinstance(g, so.ExtrudedAreaSolidTapered):
            area = (
                parametric_profile_to_arbitrary(g.swept_area)
                if isinstance(g.swept_area, su.ProfileDef)
                else g.swept_area
            )
            if not isinstance(area, su.ArbitraryProfileDef):
                _nyi("build", f"ExtrudedAreaSolid swept_area {type(area).__name__} not supported")
            if area.profile_type != su.ProfileType.AREA:
                _nyi("build", "a CURVE (sheet) profile extrusion is not supported by the prototype")
            loc, x, y, z = frame(g.position)

            def to_world(p):
                q = _v(p)
                return (loc + q[0] * x + q[1] * y + q[2] * z).tolist()

            def loop(curve) -> list[tuple]:
                if isinstance(curve, cu.Circle):
                    cl, cx, _, cz = frame(curve.position)
                    c = to_world(cl)
                    ax = (cz[0] * x + cz[1] * y + cz[2] * z).tolist()
                    rf = (cx[0] * x + cx[1] * y + cx[2] * z).tolist()
                    return [("arc", c, ax, rf, float(curve.radius), 0.0, 2 * math.pi)]
                if isinstance(curve, cu.IndexedPolyCurve):
                    out = []
                    for seg in curve.segments:
                        if isinstance(seg, cu.ArcLine):
                            out.append(_arc_through(to_world(seg.start), to_world(seg.midpoint), to_world(seg.end)))
                        else:
                            out.append(("line", to_world(seg.start), to_world(seg.end)))
                    return out
                if isinstance(curve, cu.PolyLine):
                    return self._polygon([to_world(p) for p in curve.points])
                _nyi("build", f"profile curve {type(curve).__name__} not supported")

            d = _v(g.extruded_direction)
            direction = _unit(d[0] * x + d[1] * y + d[2] * z)
            shape = self.extrude_profile(loop(area.outer_curve), direction, g.depth)
            for inner in area.inner_curves:
                hole = self.extrude_profile(loop(inner), direction, g.depth)
                shape = self.boolean_named("subtract", shape, hole)
        else:
            _nyi("build", f"ada.geom type {type(g).__name__!r} not ported to the solidcore prototype")

        for op in geometry.bool_operations:
            shape = self.boolean(op.operator, shape, self.build(op.second_operand))
        return shape

    def _place(self, shape: SolidcoreShape, loc, x, y, z) -> None:
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, loc
        self._transform_in_place(shape, m)

    # --- booleans / transforms --------------------------------------------------------------
    def boolean_named(self, op: str, a, b) -> SolidcoreShape:
        r = self._sc.boolean(self._body_of(a).body, self._body_of(b).body, op)
        if r is None:
            raise ValueError(f"solidcore boolean {op}: empty result")
        return SolidcoreShape(r, kind=None if r.solid_count() else "compound")

    def boolean(self, op: "BoolOpEnum", a, b) -> SolidcoreShape:
        from ada.geom.booleans import BoolOpEnum

        name = {
            BoolOpEnum.DIFFERENCE: "subtract",
            BoolOpEnum.UNION: "union",
            BoolOpEnum.INTERSECTION: "intersect",
        }.get(op)
        if name is None:
            raise NotImplementedError(f"Boolean operation {op} not implemented")
        return self.boolean_named(name, a, b)

    def _transform_in_place(self, shape: SolidcoreShape, m: np.ndarray) -> None:
        k = np.array(m, dtype=float)
        k[:3, 3] *= self.scale
        # kernel convention: row vector, [x y z 1] * T
        shape.body.transform(k.T.tolist())
        shape._record = None

    def transform(self, shape, matrix, copy: bool = True) -> SolidcoreShape:
        m = check_similarity_matrix(matrix, "transform")
        sh = self._body_of(shape)
        out = SolidcoreShape(sh.body.copy(), kind=sh.kind) if copy else sh
        self._transform_in_place(out, m)
        return out

    # --- measures ---------------------------------------------------------------------------
    def volume(self, shape) -> float:
        if not isinstance(shape, SolidcoreShape):
            return 0.0
        return shape.body.volume(self.accuracy) / self.scale**3

    def area(self, shape) -> float:
        if isinstance(shape, SolidcoreFace):
            return shape.parent.body.face_area(shape.index, self.accuracy) / self.scale**2
        if isinstance(shape, SolidcoreShape):
            return shape.body.area(self.accuracy) / self.scale**2
        _nyi("area", f"not defined for {type(shape).__name__}")

    def center_of_mass(self, shape):
        from ada.geom.points import Point

        if isinstance(shape, SolidcoreFace):
            _, c = shape.parent.body.face_centroid(shape.index, self.accuracy)
        elif isinstance(shape, SolidcoreShape):
            _, _, c = shape.body.mass_properties(self.accuracy)
        else:
            _nyi("center_of_mass", f"not defined for {type(shape).__name__}")
        return Point(*self._out(c))

    def bbox(self, shape, optimal: bool = True, use_mesh: bool = False):
        lo, hi = self._body_of(shape).body.bounds()
        return (*self._out(lo), *self._out(hi))

    def obb(self, shape):
        _nyi("obb", "solidcore-ops exposes no oriented bounding box")

    def distance(self, a, b) -> float:
        _nyi(
            "distance",
            "solidcore-ops has body/body contact distance (contact::Gap) but the prototype binding does not expose it yet",
        )

    def is_valid(self, shape) -> bool:
        body = self._body_of(shape).body
        failures, _ = body.check(1)
        if failures:
            return False
        if body.solid_count() > 0:
            return body.check_solid() is None
        return True

    def validity_report(self, shape) -> str:
        """Extra verb: the kernel's check text (empty when valid) and the solid test's verdict."""
        body = self._body_of(shape).body
        failures, text = body.check(2)
        solid = body.check_solid() if body.solid_count() > 0 else None
        return f"failures={failures}\n{text}" + (f"\nsolid: {solid}" if solid else "")

    def point_in_solid(self, solid, point, tolerance: float = 1e-6) -> Containment:
        r = self._body_of(solid).body.classify_point(self._in(point))
        return {"inside": Containment.IN, "outside": Containment.OUT, "on": Containment.ON}.get(r, Containment.UNKNOWN)

    # --- topology ---------------------------------------------------------------------------
    def is_handle(self, obj: Any) -> bool:
        return isinstance(obj, _HANDLE_TYPES)

    def shape_type(self, shape) -> str:
        if isinstance(shape, SolidcoreFace):
            return "face"
        if isinstance(shape, SolidcoreEdge):
            return "edge"
        if isinstance(shape, SolidcoreWire):
            return "wire"
        if isinstance(shape, SolidcoreShell):
            return "shell"
        if shape.kind is not None:
            return shape.kind
        n = shape.body.solid_count()
        if n == 1:
            return "solid"
        if n > 1:
            return "compsolid"
        shells = shape.record["shells"]
        if len(shells) == 1 and len(shape.record["faces"]) == 1:
            return "face"
        return "shell" if len(shells) == 1 else "compound"

    def solids(self, shape) -> list:
        sh = self._body_of(shape)
        return (
            [sh] * sh.body.solid_count()
            if sh.body.solid_count() <= 1
            else _nyi("solids", "splitting a multi-region body into separate solids is not exposed")
        )

    def shells(self, shape) -> list:
        sh = self._parent(shape)
        shells = sh.record["shells"]
        return [SolidcoreShell(sh, i) for i, s in enumerate(shells) if s.get("kind") == "faces"]

    def _parent(self, shape) -> SolidcoreShape:
        return shape if isinstance(shape, SolidcoreShape) else shape.parent

    def _face_indices(self, shape) -> list[int]:
        if isinstance(shape, SolidcoreFace):
            return [shape.index]
        if isinstance(shape, SolidcoreShell):
            return [of["face"] for of in shape.parent.record["shells"][shape.index]["faces"]]
        if isinstance(shape, SolidcoreShape):
            return list(range(len(shape.record["faces"])))
        return []

    def faces(self, shape) -> list:
        sh = self._parent(shape)
        return [SolidcoreFace(sh, i) for i in self._face_indices(shape)]

    def wires(self, shape) -> list:
        sh = self._parent(shape)
        out = []
        for fi in self._face_indices(shape):
            for li, lp in enumerate(sh.record["faces"][fi]["loops"]):
                if lp.get("kind") == "edges":
                    out.append(SolidcoreWire(sh, fi, li))
        return out

    def _edge_indices(self, shape) -> list[int]:
        rec = self._parent(shape).record
        if isinstance(shape, SolidcoreEdge):
            return [shape.index]
        if isinstance(shape, SolidcoreWire):
            return [oe["edge"] for oe in rec["faces"][shape.face]["loops"][shape.loop]["edges"]]
        if isinstance(shape, SolidcoreShape):
            return list(range(len(rec["edges"])))
        seen, out = set(), []
        for fi in self._face_indices(shape):
            for lp in rec["faces"][fi]["loops"]:
                for oe in lp.get("edges", []):
                    if oe["edge"] not in seen:
                        seen.add(oe["edge"])
                        out.append(oe["edge"])
        return out

    def edges(self, shape) -> list:
        sh = self._parent(shape)
        return [SolidcoreEdge(sh, i) for i in self._edge_indices(shape)]

    def vertex_points(self, shape) -> list[tuple[float, float, float]]:
        rec = self._parent(shape).record
        if isinstance(shape, SolidcoreShape):
            idx = range(len(rec["vertices"]))
        else:
            seen: list[int] = []
            for e in self._edge_indices(shape):
                for v in rec["edges"][e]["vertices"]:
                    if v not in seen:
                        seen.append(v)
            idx = seen
        return [self._out(rec["vertices"][i]["point"]) for i in idx]

    def wire_points(self, shape) -> list[tuple[float, float, float]]:
        rec = self._parent(shape).record
        if isinstance(shape, SolidcoreShape):
            shape = self._as_face(shape)
        if isinstance(shape, SolidcoreFace):
            wires = self.wires(shape)
            if not wires:
                return []
            shape = wires[0]
        if not isinstance(shape, SolidcoreWire):
            _nyi("wire_points", f"not defined for {type(shape).__name__}")
        pts = []
        for oe in rec["faces"][shape.face]["loops"][shape.loop]["edges"]:
            v0, v1 = rec["edges"][oe["edge"]]["vertices"]
            pts.append(self._out(rec["vertices"][v0 if oe["forward"] else v1]["point"]))
        return pts

    def face_surface_type(self, shape) -> str:
        face = self._as_face(shape)
        kind = face.parent.record["faces"][face.index]["surface"]["kind"]
        return _SURFACE_TYPES.get(kind, kind)

    def is_planar_face(self, shape, tol: float = 1e-6) -> bool:
        face = self._as_face(shape)
        return face.parent.body.face_plane_normal(face.index) is not None

    def _face_forward(self, face: SolidcoreFace) -> bool:
        for sh in face.parent.record["shells"]:
            for of in sh.get("faces", []):
                if of["face"] == face.index:
                    return bool(of["forward"])
        return True

    def face_plane(self, face):
        from ada.geom.direction import Direction
        from ada.geom.points import Point

        face = self._as_face(face)
        surf = face.parent.record["faces"][face.index]["surface"]
        if surf["kind"] == "plane":
            o, u, v = _v(surf["origin"]), _v(surf["u_axis"]), _v(surf["v_axis"])
        elif surf["kind"] == "plane_through":
            o = _v(surf["origin"])
            u, v = _v(surf["u_point"]) - o, _v(surf["v_point"]) - o
        else:
            return None
        n = _unit(np.cross(u, v))  # the surface's natural normal ...
        if not self._face_forward(face):  # ... turned to the side the face is used on
            n = -n
        return Point(*self._out(o)), Direction(*[float(c) for c in n])

    def face_id(self, shape) -> int | None:
        if isinstance(shape, SolidcoreFace):
            return hash((id(shape.parent), "f", shape.index))
        if isinstance(shape, SolidcoreEdge):
            return hash((id(shape.parent), "e", shape.index))
        return None

    # --- tessellation / export -------------------------------------------------------------
    def tessellate(self, shape, linear_deflection: float = -1.0) -> SolidcoreMesh:
        sh = self._body_of(shape)
        chord = linear_deflection * self.scale if linear_deflection > 0 else -1.0
        pos, idx, nrm, tri_face, refused = sh.body.tessellate(chord)
        if refused:
            from ada.config import logger

            logger.warning(f"solidcore tessellation refused {refused} face(s)")
        return SolidcoreMesh(
            positions=(np.asarray(pos, dtype=np.float64) / self.scale).astype(np.float32),
            indices=np.asarray(idx, dtype=np.uint32),
            normals=np.asarray(nrm, dtype=np.float32),
            face_ids=np.asarray(tri_face, dtype=np.uint32),
        )

    def tessellate_batch(self, shapes, linear_deflection: float = -1.0):
        return tessellate_batch_via_loop(self, shapes, linear_deflection)

    def write_step(self, shapes, names, colors, filename, unit: str = "m", schema: str = "AP214") -> None:
        # solidcore writes AP242 in millimetres; with scale=1000 the kernel coordinates ARE mm.
        if abs(self.scale - 1000.0) > 1e-12:
            _nyi("write_step", "solidcore writes millimetres; only scale=1000 maps model metres to that")
        text = self._sc.write_step(
            [(str(n), self._body_of(s).body) for s, n in zip(shapes, names)], os.path.basename(filename)
        )
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)

    def step_text(self, shapes, names) -> str:
        """Extra verb: AP242 Part 21 text (mm)."""
        return self._sc.write_step([(str(n), self._body_of(s).body) for s, n in zip(shapes, names)], "")

    def serialize(self, shape) -> str:
        _nyi("serialize", "solidcore's native body file is .smb, not OCCT BREP text; use step_text() / to_smb")

    def to_smb(self, shape) -> bytes:
        return self._body_of(shape).body.to_smb()

    # --- not (yet) available -----------------------------------------------------------------
    def section_with_plane(self, shape, origin, normal, size: float = 1000.0):
        # As OccBackend: the common of the body with a square planar sheet on the plane.
        o, n = _v(origin), _unit(normal)
        x = np.cross(n, (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0))
        x = _unit(x)
        y = np.cross(n, x)
        sq = [o + size * (sx * x + sy * y) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        return self.boolean_named("intersect", shape, self.polygon_face(sq))

    def read_step_bytes(self, data: bytes):
        _nyi("read_step_bytes", "solidcore-step can read Part 21 into records; not bound in the prototype")

    def read_step_shapes(self, data: bytes, unit: str = "M", matrix=None) -> list:
        _nyi("read_step_shapes", "not bound in the prototype")

    def write_glb_bytes(self, shape, linear_deflection: float = 0.1) -> bytes:
        _nyi("write_glb_bytes", "no GLB writer; use tessellate() and adapy's own GLB writer")

    def step_bytes_to_glb_bytes(self, data, linear_deflection=0.1, angular_deg=20.0, unit="M") -> bytes:
        _nyi("step_bytes_to_glb_bytes", "no STEP reader bound")

    def step_to_face_tagged_meshes(self, step_path, linear_deflection=0.1, angular_deflection=0.5, store_units="m"):
        _nyi("step_to_face_tagged_meshes", "no STEP reader bound")

    def face_to_advanced_face(self, shape):
        _nyi("face_to_advanced_face", "no ada.geom AdvancedFace export from the record yet")

    def build_bspline_advanced_face_from_grid(self, grid, tol: float):
        _nyi("build_bspline_advanced_face_from_grid", "not bound")

    def to_topods_pointer(self, shape) -> int:
        _nyi("to_topods_pointer", "solidcore is not OCCT: there is no TopoDS_Shape")

    def adopt_occ_shape(self, occ_shape):
        _nyi("adopt_occ_shape", "solidcore is not OCCT; a route would be OCC STEP -> solidcore-step reader")

    def make_halfspace(self, origin, normal, flip: bool):
        _nyi("make_halfspace", "the kernel has no infinite half-space; a large box cutter would be the substitute")

    def cut_surfaces(self, solid, cutters, deflection, tol):
        _nyi("cut_surfaces", "not ported")

    def make_volumes_from_faces(self, faces, tolerance: float = 1e-6):
        _nyi("make_volumes_from_faces", "solidcore has stitch(); the non-manifold cell builder is not ported")

    def merge_cells(self, solids, tolerance: float = 0.0):
        _nyi("merge_cells", "non-manifold cell merge is not ported")

    def non_manifold_merge(self, shapes, tolerance: float = 1e-6, glue: bool = True):
        _nyi("non_manifold_merge", "not ported")

    def free_faces(self, solids):
        _nyi("free_faces", "not ported")

    def unify_coplanar_faces(self, shape):
        _nyi("unify_coplanar_faces", "not bound")

    def imprint_planar_faces(self, outlines, imprint_curves=None, tolerance: float = 1e-6):
        _nyi("imprint_planar_faces", "solidcore has imprint(); the planar-imprint data model is not ported")

    def imprint_advanced_faces(self, advanced_faces, imprint_curves, tolerance: float = 1e-6):
        _nyi("imprint_advanced_faces", "not ported")
