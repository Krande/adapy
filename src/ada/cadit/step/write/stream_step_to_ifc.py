"""Streaming STEP → IFC (IFC4 advanced B-rep) — per-solid, no full Assembly, no OCC.

The native adacpp NGEOM reader (pure-Python stream reader as a drop-in fallback)
yields one analytic ``ada.geom.Geometry`` per solid; each is hand-authored straight
to IFC STEP-physical-file text as an ``IfcAdvancedBrep`` (analytic B-rep incl.
B-spline surfaces/curves and swept surfaces). Peak memory is O(one solid) — the
ifcopenshell.file is only ever the small spatial-structure *preamble*, never the
geometry — so the multi-GB CAD assemblies that OOM/timed out through
``ada.from_step`` → ``to_ifc`` stream through. No tessellation; analytic geometry
is preserved end-to-end.

The hand-authored emitter mirrors the AP242 STEP writer (same B-rep traversal +
instance-placement baking) but emits IFC entities in IFC4 positional schema order.
"""

from __future__ import annotations

import math
import pathlib
from collections import Counter
from typing import Callable

from ada.config import logger

ProgressFn = Callable[[str, float], None]


def _r(x) -> str:
    """IFC SPF real (uppercase E exponent, always a decimal point)."""
    s = "%.12g" % float(x)
    if "e" in s or "E" in s:
        mant, _, exp = s.replace("E", "e").partition("e")
        if "." not in mant:
            mant += "."
        return mant + "E" + exp
    if "." not in s:
        s += "."
    return s


def _b(v) -> str:
    return ".T." if v else ".F."


class _IfcBrepEmitter:
    """Hand-authors the IFC4 advanced-B-rep SPF lines for one solid, allocating
    ids from a running counter and appending to a ``lines`` buffer (flushed in
    chunks). Bakes an optional 4x4 instance placement (rotation+translation) into
    every point/direction so an instanced solid lands at its world pose — numpy
    free, so it runs in the slim worker."""

    def __init__(self, start_id: int):
        self.nid = start_id
        self._tf = None  # active 16-tuple row-major 4x4, or None (identity)
        self._vcache: dict = {}
        self._ecache: dict = {}
        # Optional sink that drains the `lines` buffer to disk once it grows past
        # _flush_at, so a single multi-million-entity solid can't spike RSS (IFC is
        # emitted children-before-parents, so flushing complete entities mid-solid
        # is safe — SPF tolerates any reference order). Set by the driver.
        self._flush = None
        self._flush_at = 50000

    # -- low level ---------------------------------------------------------- #
    def _emit(self, lines, body) -> int:
        self.nid += 1
        lines.append(f"#{self.nid}={body};")
        if self._flush is not None and len(lines) >= self._flush_at:
            self._flush(lines)
        return self.nid

    def _tp(self, p):
        m = self._tf
        x, y, z = float(p[0]), float(p[1]), float(p[2])
        if m is None:
            return (x, y, z)
        return (
            m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11],
        )

    def _td(self, v):
        m = self._tf
        x, y, z = float(v[0]), float(v[1]), float(v[2])
        if m is None:
            return (x, y, z)
        rx, ry, rz = m[0] * x + m[1] * y + m[2] * z, m[4] * x + m[5] * y + m[6] * z, m[8] * x + m[9] * y + m[10] * z
        n = math.sqrt(rx * rx + ry * ry + rz * rz)
        return (rx / n, ry / n, rz / n) if n else (x, y, z)

    def _p3(self, p):
        return "(" + ",".join(_r(c) for c in p) + ")"

    def _pt(self, lines, p):
        return self._emit(lines, f"IfcCartesianPoint({self._p3(self._tp(p))})")

    def _dir(self, lines, v):
        return self._emit(lines, f"IfcDirection({self._p3(self._td(v))})")

    def _vec(self, lines, v, mag=1.0):
        return self._emit(lines, f"IfcVector(#{self._dir(lines, v)},{_r(mag)})")

    def _vertex(self, lines, p):
        key = tuple(round(c, 9) for c in self._tp(p))
        vid = self._vcache.get(key)
        if vid is None:
            vid = self._emit(lines, f"IfcVertexPoint(#{self._pt(lines, p)})")
            self._vcache[key] = vid
        return vid

    def _axis2(self, lines, loc, axis, ref):
        return self._emit(
            lines,
            f"IfcAxis2Placement3D(#{self._pt(lines, loc)},#{self._dir(lines, axis)},#{self._dir(lines, ref)})",
        )

    def _axis1(self, lines, loc, axis):
        return self._emit(lines, f"IfcAxis1Placement(#{self._pt(lines, loc)},#{self._dir(lines, axis)})")

    def _refs(self, ids):
        return "(" + ",".join(f"#{i}" for i in ids) + ")"

    # -- curves ------------------------------------------------------------- #
    def _ilist(self, vs):
        return "(" + ",".join(str(int(v)) for v in vs) + ")"

    def _rlist(self, vs):
        return "(" + ",".join(_r(v) for v in vs) + ")"

    def _bspline_curve(self, lines, c):
        cps = self._refs([self._pt(lines, p) for p in c.control_points_list])
        common = (
            f"{c.degree},{cps},.{c.curve_form.value}.,{_b(c.closed_curve)},{_b(c.self_intersect)},"
            f"{self._ilist(c.knot_multiplicities)},{self._rlist(c.knots)},.{c.knot_spec.value}."
        )
        weights = getattr(c, "weights_data", None)
        if weights:
            return self._emit(lines, f"IfcRationalBSplineCurveWithKnots({common},{self._rlist(weights)})")
        return self._emit(lines, f"IfcBSplineCurveWithKnots({common})")

    def _curve(self, lines, g):
        """A bare 3D geometric curve (IfcLine/Circle/Ellipse/BSpline), or None."""
        import ada.geom.curves as cu

        if isinstance(g, cu.Line):
            return self._emit(lines, f"IfcLine(#{self._pt(lines, g.pnt)},#{self._vec(lines, g.dir)})")
        if isinstance(g, cu.Circle):
            pos = g.position
            a2 = self._axis2(lines, pos.location, _axis_or(pos.axis), _axis_or(pos.ref_direction, (1, 0, 0)))
            return self._emit(lines, f"IfcCircle(#{a2},{_r(g.radius)})")
        if isinstance(g, cu.Ellipse):
            pos = g.position
            a2 = self._axis2(lines, pos.location, _axis_or(pos.axis), _axis_or(pos.ref_direction, (1, 0, 0)))
            return self._emit(lines, f"IfcEllipse(#{a2},{_r(g.semi_axis1)},{_r(g.semi_axis2)})")
        if isinstance(g, cu.BSplineCurveWithKnots):
            return self._bspline_curve(lines, g)
        return None

    def _edge_curve(self, lines, ec):
        g = ec.edge_geometry
        import ada.geom.curves as cu

        crv = self._curve(lines, g) if g is not None and not isinstance(g, cu.Line) else None
        if crv is None:
            # a straight edge: IfcLine through the two endpoints
            p0, p1 = ec.start, ec.end
            d = _unit(_sub(p1, p0))
            crv = self._emit(lines, f"IfcLine(#{self._pt(lines, p0)},#{self._vec(lines, d)})")
        v0, v1 = self._vertex(lines, ec.start), self._vertex(lines, ec.end)
        return self._emit(lines, f"IfcEdgeCurve(#{v0},#{v1},#{crv},{_b(ec.same_sense)})")

    def _oriented_edge(self, lines, oe):
        ec = getattr(oe, "edge_element", oe)
        key = id(ec)
        edge_id = self._ecache.get(key)
        if edge_id is None:
            edge_id = self._edge_curve(lines, ec)
            self._ecache[key] = edge_id
        return self._emit(lines, f"IfcOrientedEdge($,$,#{edge_id},{_b(oe.orientation)})")

    def _loop(self, lines, loop):
        import ada.geom.curves as cu

        if isinstance(loop, cu.EdgeLoop):
            oe = [self._oriented_edge(lines, e) for e in loop.edge_list]
            if not oe:
                return None
            return self._emit(lines, f"IfcEdgeLoop({self._refs(oe)})")
        if isinstance(loop, cu.PolyLoop):
            pts = self._refs([self._pt(lines, p) for p in loop.polygon])
            return self._emit(lines, f"IfcPolyLoop({pts})")
        return None

    # -- surfaces ----------------------------------------------------------- #
    def _surface(self, lines, s):
        import ada.geom.surfaces as su

        if isinstance(s, su.BSplineSurfaceWithKnots):
            rows = list(s.control_points_list)
            grid = "(" + ",".join(self._refs([self._pt(lines, p) for p in row]) for row in rows) + ")"
            common = (
                f"{s.u_degree},{s.v_degree},{grid},.{s.surface_form.value}.,"
                f"{_b(s.u_closed)},{_b(s.v_closed)},{_b(s.self_intersect)},"
                f"{self._ilist(s.u_multiplicities)},{self._ilist(s.v_multiplicities)},"
                f"{self._rlist(s.u_knots)},{self._rlist(s.v_knots)},.{s.knot_spec.value}."
            )
            weights = getattr(s, "weights_data", None)
            if weights:
                wgrid = "(" + ",".join(self._rlist(row) for row in weights) + ")"
                return self._emit(lines, f"IfcRationalBSplineSurfaceWithKnots({common},{wgrid})")
            return self._emit(lines, f"IfcBSplineSurfaceWithKnots({common})")
        if isinstance(s, su.SurfaceOfLinearExtrusion):
            crv = self._curve(lines, s.swept_curve)
            if crv is None:
                return None
            prof = self._emit(lines, f"IfcArbitraryOpenProfileDef(.CURVE.,$,#{crv})")
            ed = self._dir(lines, s.extrusion_direction)
            return self._emit(lines, f"IfcSurfaceOfLinearExtrusion(#{prof},$,#{ed},{_r(s.depth or 1.0)})")
        if isinstance(s, su.SurfaceOfRevolution):
            crv = self._curve(lines, s.swept_curve)
            if crv is None:
                return None
            prof = self._emit(lines, f"IfcArbitraryOpenProfileDef(.CURVE.,$,#{crv})")
            ap = s.axis_position
            ax1 = self._axis1(lines, ap.location, _axis_or(ap.axis))
            return self._emit(lines, f"IfcSurfaceOfRevolution(#{prof},$,#{ax1})")

        p = getattr(s, "position", None)
        if p is None:
            return None
        a2 = self._axis2(lines, p.location, _axis_or(p.axis), _axis_or(p.ref_direction, (1, 0, 0)))
        if isinstance(s, su.Plane):
            return self._emit(lines, f"IfcPlane(#{a2})")
        if isinstance(s, su.CylindricalSurface):
            return self._emit(lines, f"IfcCylindricalSurface(#{a2},{_r(s.radius)})")
        if isinstance(s, su.ConicalSurface):
            return self._emit(lines, f"IfcConicalSurface(#{a2},{_r(s.radius)},{_r(s.semi_angle)})")
        if isinstance(s, su.SphericalSurface):
            return self._emit(lines, f"IfcSphericalSurface(#{a2},{_r(s.radius)})")
        if isinstance(s, su.ToroidalSurface):
            return self._emit(lines, f"IfcToroidalSurface(#{a2},{_r(s.major_radius)},{_r(s.minor_radius)})")
        return None

    def _face(self, lines, face):
        surf = self._surface(lines, face.face_surface)
        if surf is None:
            return None
        bounds = []
        for i, fb in enumerate(face.bounds):
            loop = self._loop(lines, fb.bound)
            if loop is None:
                return None
            kw = "IfcFaceOuterBound" if i == 0 else "IfcFaceBound"
            bounds.append(self._emit(lines, f"{kw}(#{loop},{_b(fb.orientation)})"))
        if not bounds:
            return None
        return self._emit(lines, f"IfcAdvancedFace({self._refs(bounds)},#{surf},{_b(face.same_sense)})")

    def _shell_faces(self, lines, faces):
        out = []
        for fc in faces:
            fid = self._face(lines, fc)
            if fid is None:
                return None
            out.append(fid)
        return out or None

    def solid(self, lines, geom, *, transform=None):
        """Emit one solid/shell; return ``(item_id, representation_type)`` or
        ``(None, None)`` if any face uses geometry not yet emittable (skipped
        wholesale). Closed B-rep solids become ``IfcAdvancedBrep``; open shells /
        surface models become ``IfcShellBasedSurfaceModel`` (analytic faces either
        way)."""
        import ada.geom.surfaces as su

        self._tf = tuple(float(x) for x in transform) if transform is not None else None
        self._vcache = {}
        self._ecache = {}

        if isinstance(geom, (su.ClosedShell, su.ConnectedFaceSet)):
            faces = self._shell_faces(lines, geom.cfs_faces)
            if faces is None:
                return None, None
            shell = self._emit(lines, f"IfcClosedShell({self._refs(faces)})")
            return self._emit(lines, f"IfcAdvancedBrep(#{shell})"), "AdvancedBrep"

        if isinstance(geom, su.ShellBasedSurfaceModel):
            shell_ids = []
            for sh in geom.sbsm_boundary:
                faces = self._shell_faces(lines, sh.cfs_faces)
                if faces is None:
                    return None, None
                kw = "IfcClosedShell" if isinstance(sh, su.ClosedShell) else "IfcOpenShell"
                shell_ids.append(self._emit(lines, f"{kw}({self._refs(faces)})"))
            if not shell_ids:
                return None, None
            return self._emit(lines, f"IfcShellBasedSurfaceModel({self._refs(shell_ids)})"), "SurfaceModel"

        if isinstance(geom, (su.OpenShell,)):
            faces = self._shell_faces(lines, geom.cfs_faces)
            if faces is None:
                return None, None
            shell = self._emit(lines, f"IfcOpenShell({self._refs(faces)})")
            return self._emit(lines, f"IfcShellBasedSurfaceModel((#{shell}))"), "SurfaceModel"

        if isinstance(geom, (su.AdvancedFace, su.FaceSurface)):
            fid = self._face(lines, geom)
            if fid is None:
                return None, None
            shell = self._emit(lines, f"IfcOpenShell((#{fid}))")
            return self._emit(lines, f"IfcShellBasedSurfaceModel((#{shell}))"), "SurfaceModel"

        return None, None

    def solid_streaming(self, lines, face_iter, *, transform=None):
        """Emit a ConnectedFaceSet B-rep (``IfcAdvancedBrep``) from a face ITERATOR —
        faces are decoded + emitted + freed one at a time (only their small entity ids
        are kept), so a giant solid (millions of faces) never holds its whole ada.geom
        tree. The memory-bounded counterpart to :meth:`solid` for the giant-solid OOM
        (e.g. the 67 MB single solid in 469826). Returns ``(item_id, rep_type)`` or
        ``(None, None)`` if any face uses non-emittable geometry (solid skipped)."""
        self._tf = tuple(float(x) for x in transform) if transform is not None else None
        self._vcache = {}
        self._ecache = {}
        face_ids: list[int] = []
        for fc in face_iter:
            fid = self._face(lines, fc)
            if fid is None:
                return None, None
            face_ids.append(fid)
        if not face_ids:
            return None, None
        shell = self._emit(lines, f"IfcClosedShell({self._refs(face_ids)})")
        return self._emit(lines, f"IfcAdvancedBrep(#{shell})"), "AdvancedBrep"


# tuple helpers (numpy-free)
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unit(a):
    # tolerant: a degenerate (zero-length) edge in real CAD must not sink the solid
    n = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    return (a[0] / n, a[1] / n, a[2] / n) if n else (0.0, 0.0, 1.0)


def _axis_or(v, default=(0.0, 0.0, 1.0)):
    if v is None:
        return default
    return (float(v[0]), float(v[1]), float(v[2]))


def _iter_stream_solids(src_path):
    """Yield ``(gi, make_face_gen, gid, color_rgb, mats, paths)`` per solid.

    Native path: for a ConnectedFaceSet solid, ``make_face_gen`` is a callable that
    returns a fresh face generator (faces decoded one at a time) so the giant solid
    emits face-by-face (bounded RSS); ``gi`` is None. Other roots / the pure-Python
    fallback yield a fully-hydrated ``gi`` and ``make_face_gen`` None.
    """
    from ada.config import logger

    try:
        from ada.cadit.step.read.native_reader import decode_step_root_meta, native_adacpp_step_available

        native = native_adacpp_step_available()
    except Exception:  # noqa: BLE001
        native = False

    if native:
        import adacpp

        from ada.cadit.ngeom.deserialize import (
            NgeomDecodeError,
            deserialize_geometries,
            iter_connected_face_set_faces,
        )

        try:
            for nbytes, meta in adacpp.cad.StepNgeomStream(str(src_path)):
                gid, color, mats, paths = decode_step_root_meta(meta)
                rgb = color.rgb if color is not None else None
                streamed = iter_connected_face_set_faces(nbytes)
                if streamed is not None:
                    yield (None, (lambda nb=nbytes: iter_connected_face_set_faces(nb)[2]), gid, rgb,
                           (mats or [None]), (paths or None))
                else:
                    dec = deserialize_geometries(nbytes)
                    if not dec:
                        continue
                    yield (dec[0][1], None, gid, rgb, (mats or [None]), (paths or None))
            return
        except (NgeomDecodeError, RecursionError) as exc:
            logger.warning("native STEP stream failed (%s); falling back to pure-Python for %s", exc, src_path)

    # pure-Python fallback (full per-solid deserialize; tolerant skips bad solids).
    from ada.factories import iter_from_step

    for geom in iter_from_step(src_path, reader="tolerant"):
        gi = geom.geometry.geometry if hasattr(geom.geometry, "geometry") else geom.geometry
        rgb = geom.color.rgb if geom.color is not None else None
        gid = str(geom.id) if geom.id not in (None, "") else None
        yield (gi, None, gid, rgb, (geom.transforms or [None]), geom.instance_paths or None)


def stream_step_to_ifc(
    src_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    *,
    on_progress: ProgressFn | None = None,
) -> dict:
    """Stream a STEP file to IFC4, one solid at a time. Returns
    ``{emitted, skipped, total, reasons}``."""
    import numpy as np

    import ada
    from ada.cadit.ifc.utils import create_guid
    from ada.cadit.ifc.write.write_ifc import IfcWriter

    prog = on_progress or (lambda *_: None)
    prog("writing-ifc", 0.1)

    # ── bounded preamble: spatial structure only (no geometry) ──
    asm = ada.Assembly("StepImport")
    container = asm.add_part(ada.Part("Bodies"))
    store = asm.ifc_store
    writer = IfcWriter(store)
    store.writer = writer
    store.update_owner(asm.user)
    writer.sync_spatial_hierarchy()
    f = store.f
    owner_id = store.owner_history.id()
    body_ctx_id = store.get_context("Body").id()
    container_id = f.by_guid(container.guid).id()

    pre_text = f.wrapped_data.to_string()
    cut = pre_text.rindex("ENDSEC;")
    head = pre_text[:cut].rstrip("\n")
    start_id = max((e.id() for e in f), default=0) + 1

    emitter = _IfcBrepEmitter(start_id - 1)
    # one shared identity placement (geometry is baked to world coords)
    pre_lines: list[str] = []
    ident_axis = emitter._axis2(pre_lines, (0, 0, 0), (0, 0, 1), (1, 0, 0))
    ident_place = emitter._emit(pre_lines, f"IfcLocalPlacement($,#{ident_axis})")

    emitted = skipped = total = 0
    reasons: Counter = Counter()
    # Nested assembly tree rebuilt from instance_paths: each node (shared by rep_id)
    # becomes an IfcElementAssembly; children aggregate under it (IfcRelAggregates),
    # top-level nodes + flat leaves are contained in the storey. Bounded by node count.
    asm_nodes: dict = {}  # rep_id -> name
    asm_parent: dict = {}  # rep_id -> parent rep_id (None = root)
    node_children: dict = {}  # rep_id -> list of ("node", rep_id) | ("leaf", proxy_id)
    root_members: list = []  # ("node", rep_id) | ("leaf", proxy_id) directly under the storey

    def _register_path(parent_path):
        """Register the breadcrumb's assembly nodes + parent edges; return the deepest
        rep_id (the leaf's parent), or None for a flat solid."""
        if not parent_path:
            return None
        prev = None
        for level in parent_path:
            rep_id = level[0] if isinstance(level, (tuple, list)) else level
            nm = level[1] if (isinstance(level, (tuple, list)) and level[1]) else f"asm_{rep_id}"
            if rep_id not in asm_nodes:
                asm_nodes[rep_id] = nm
                asm_parent[rep_id] = prev
                node_children.setdefault(rep_id, [])
                (node_children[prev] if prev is not None else root_members).append(("node", rep_id))
            prev = rep_id
        return prev

    out = open(out_path, "w")
    try:
        out.write(head + "\n")
        out.write("\n".join(pre_lines) + "\n")
        lines: list[str] = []
        # Drain `lines` to disk as soon as it grows large — even within one solid —
        # so peak RSS stays bounded regardless of any single solid's complexity.
        emitter._flush = lambda buf: (out.write("\n".join(buf) + "\n"), buf.clear())
        for gi, make_face_gen, gid, color, mats, paths in _iter_stream_solids(src_path):
            total += 1
            base = gid if gid not in (None, "") else f"solid_{total}"
            any_ok = False
            for k, m in enumerate(mats):
                name = base if k == 0 else f"{base}/{k + 1}"
                tf = None if m is None else [float(v) for v in np.asarray(m).reshape(-1)]
                try:
                    # IFC entities are emitted children-before-parents, so a mid-solid
                    # failure leaves only orphan (unreferenced) lines — harmless SPF.
                    # Giant ConnectedFaceSet solids stream face-by-face (bounded RSS);
                    # everything else emits from the hydrated geom.
                    if make_face_gen is not None:
                        brep, rep_type = emitter.solid_streaming(lines, make_face_gen(), transform=tf)
                    else:
                        brep, rep_type = emitter.solid(lines, gi, transform=tf)
                    if brep is None:
                        continue
                    rep = emitter._emit(lines, f"IfcShapeRepresentation(#{body_ctx_id},'Body','{rep_type}',(#{brep}))")
                    pds = emitter._emit(lines, f"IfcProductDefinitionShape($,$,(#{rep}))")
                    nm = "'" + name.replace("'", "''") + "'"
                    pid = emitter._emit(
                        lines,
                        f"IfcBuildingElementProxy('{create_guid()}',#{owner_id},{nm},$,$,#{ident_place},#{pds},$,$)",
                    )
                    # nest: aggregate under the leaf's parent assembly node, else storey
                    pp = list(paths[k][:-1]) if (paths and k < len(paths) and paths[k]) else None
                    parent_rep = _register_path(pp)
                    (node_children[parent_rep] if parent_rep is not None else root_members).append(("leaf", pid))
                    if color is not None:
                        cid = emitter._emit(lines, f"IfcColourRgb($,{_r(color[0])},{_r(color[1])},{_r(color[2])})")
                        sh = emitter._emit(lines, f"IfcSurfaceStyleShading(#{cid},0.)")
                        st = emitter._emit(lines, f"IfcSurfaceStyle($,.BOTH.,(#{sh}))")
                        emitter._emit(lines, f"IfcStyledItem(#{brep},(#{st}),$)")
                    any_ok = True
                except Exception as exc:  # noqa: BLE001 - one bad solid shouldn't sink the file
                    logger.warning("stream_step_to_ifc skipped %r: %s", name, exc)
            if any_ok:
                emitted += 1
            else:
                skipped += 1
                reasons[_unsupported_kind(gi) if gi is not None else "ConnectedFaceSet(streamed)"] += 1
            # Flush by buffer size (not solid count) so a few huge solids can't spike RSS.
            if len(lines) >= 20000:
                out.write("\n".join(lines) + "\n")
                lines.clear()
                prog(f"writing-ifc {total}", 0.1 + 0.8 * min(0.99, total / 10000.0))
        if lines:
            out.write("\n".join(lines) + "\n")
            lines.clear()

        # ── trailing: assembly tree (IfcElementAssembly + IfcRelAggregates) + the
        # storey containment of every top-level element ──
        tail: list[str] = []

        def _e(body):
            emitter.nid += 1
            tail.append(f"#{emitter.nid}={body};")
            return emitter.nid

        # one IfcElementAssembly per node, then resolve child refs to entity ids
        node_entity = {
            rep: _e(f"IfcElementAssembly('{create_guid()}',#{owner_id},'{nm}',$,$,#{ident_place},$,$,$,$)")
            for rep, nm in asm_nodes.items()
        }

        def _resolve(members):
            return [node_entity[rep] if kind == "node" else rep for kind, rep in members]

        for rep, kids in node_children.items():
            ids = _resolve(kids)
            if ids:
                refs = "(" + ",".join(f"#{i}" for i in ids) + ")"
                _e(f"IfcRelAggregates('{create_guid()}',#{owner_id},$,$,#{node_entity[rep]},{refs})")

        roots = _resolve(root_members)
        if roots:
            refs = "(" + ",".join(f"#{i}" for i in roots) + ")"
            _e(
                f"IfcRelContainedInSpatialStructure('{create_guid()}',#{owner_id},"
                f"'Physical model',$,{refs},#{container_id})"
            )
        if tail:
            out.write("\n".join(tail) + "\n")
        out.write("ENDSEC;\nEND-ISO-10303-21;\n")
    finally:
        out.close()

    if skipped:
        logger.warning("stream_step_to_ifc: %d/%d solids skipped (unsupported): %s", skipped, total, dict(reasons))
    prog("ready", 1.0)
    return {"emitted": emitted, "skipped": skipped, "total": total, "reasons": dict(reasons)}


def _unsupported_kind(gi) -> str:
    import ada.geom.curves as cu
    import ada.geom.surfaces as su

    emittable_surf = (
        su.Plane,
        su.CylindricalSurface,
        su.ConicalSurface,
        su.SphericalSurface,
        su.ToroidalSurface,
        su.BSplineSurfaceWithKnots,
        su.SurfaceOfLinearExtrusion,
        su.SurfaceOfRevolution,
    )
    emittable_curve = (cu.Line, cu.Circle, cu.Ellipse, cu.BSplineCurveWithKnots)
    faces = getattr(gi, "cfs_faces", None) or ([gi] if hasattr(gi, "face_surface") else [])
    for fc in faces:
        s = getattr(fc, "face_surface", None)
        if s is not None and not isinstance(s, emittable_surf):
            return f"surface:{type(s).__name__}"
        for fb in getattr(fc, "bounds", []):
            for oe in getattr(fb.bound, "edge_list", []):
                ec = getattr(oe, "edge_element", oe)
                eg = getattr(ec, "edge_geometry", None)
                if eg is not None and not isinstance(eg, emittable_curve):
                    return f"curve:{type(eg).__name__}"
    return f"geometry:{type(gi).__name__}"
