from __future__ import annotations

import os
from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.geom.curves import CURVE_GEOM_TUPLE as _CURVE_GEOM_TUPLE
from ada.cad import active_backend, is_shape_handle
from ada.config import logger
from ada.geom import Geometry
from ada.occ.exceptions import (
    UnableToCreateCurveOCCGeom,
    UnableToCreateTesselationFromSolidOCCGeom,
)
from ada.visit.colors import Color
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.gltf.meshes import MeshStore, MeshType
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene
from ada.visit.render_params import RenderParams

if TYPE_CHECKING:
    import trimesh
    from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Shape

    from ada.api.spatial import Part
    from ada.cadit.ifc.store import IfcStore


def _is_topods_shape(shape) -> bool:
    """True if ``shape`` is a raw pythonocc ``TopoDS_Shape``. Returns False when
    pythonocc isn't installed (e.g. the adacpp-only environment) so the
    tessellation dispatch falls through to the active backend's tessellate verb
    rather than blowing up on the import. See dap plan/v3 Phase 2."""
    try:
        from OCC.Core.TopoDS import TopoDS_Shape
    except ModuleNotFoundError:
        return False
    return isinstance(shape, TopoDS_Shape)


def _mesh_store_area(ms) -> float:
    """Total triangle area of a tessellated ``MeshStore`` (position soup +
    indices). Used to gauge whether a curved-plate prism actually covered its
    surface vs under-meshed it."""
    pos = getattr(ms, "position", None)
    idx = getattr(ms, "indices", None)
    if pos is None or idx is None:
        return 0.0
    verts = np.asarray(pos, dtype=float).reshape(-1, 3)
    faces = np.asarray(idx, dtype=np.int64).reshape(-1, 3)
    if len(faces) == 0 or len(verts) == 0:
        return 0.0
    tris = verts[faces]
    return float(np.sum(0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)))


def _natural_bound_curved_solid(ada_obj):
    """Rebuild a PlateCurved's solid from its surface over the face's *natural*
    UV bounds, discarding the trim wire.

    A handful of imported B-spline plate faces carry malformed pcurves (the
    wire backtracks / duplicates an edge in UV), so BRepMesh grids only ~half
    the face — the "missing triangles". The underlying surface is sound and its
    UV box equals the plate's (rectangular) boundary, so a face built straight
    from the surface over ``UVBounds`` re-meshes fully. Returns an OCC solid
    (prism by thickness ``t`` along the surface normal), or the bare face when
    ``t == 0``, or ``None`` if the rebuild can't be done."""
    face_fn = getattr(ada_obj, "_face_occ", None)
    if not callable(face_fn):
        return None
    try:
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCC.Core.BRepTools import breptools
        from OCC.Core.GeomLProp import GeomLProp_SLProps
        from OCC.Core.gp import gp_Vec

        face = face_fn()
        if not _is_topods_shape(face):
            return None
        surf = BRep_Tool.Surface(face)
        umin, umax, vmin, vmax = breptools.UVBounds(face)
        mf = BRepBuilderAPI_MakeFace(surf, umin, umax, vmin, vmax, 1e-6)
        if not mf.IsDone():
            return None
        nb = mf.Face()
        t = getattr(ada_obj, "t", None) or 0.0
        if not t:
            return nb
        props = GeomLProp_SLProps(surf, (umin + umax) / 2.0, (vmin + vmax) / 2.0, 1, 1e-6)
        if not props.IsNormalDefined():
            return nb
        n = props.Normal()
        return BRepPrimAPI_MakePrism(nb, gp_Vec(n.X(), n.Y(), n.Z()).Multiplied(t)).Shape()
    except Exception as e:  # OCC missing / pathological surface
        logger.debug("natural-bound curved rebuild failed: %s", e)
        return None


def _shapefix_for_mesh(occ_geom):
    """Run ``ShapeFix_Shape`` on an OCC body so faces that lack p-curves become meshable,
    returning the fixed shape (or ``None`` if pythonocc is absent / the fix fails or no-ops).
    Used as a last-ditch retry when BRepMesh produced zero triangles."""
    if not _is_topods_shape(occ_geom):
        return None
    try:
        from OCC.Core.ShapeFix import ShapeFix_Shape

        sf = ShapeFix_Shape(occ_geom)
        sf.Perform()
        fixed = sf.Shape()
        return fixed if fixed is not None and not fixed.IsNull() else None
    except Exception as e:  # OCC missing, or ShapeFix raised on a pathological body
        logger.debug(f"ShapeFix mesh-retry unavailable/failed: {e}")
        return None


@dataclass
class TriangleMesh:
    positions: np.ndarray
    faces: np.ndarray
    edges: np.ndarray = None
    normals: np.ndarray = None


@dataclass
class LineMesh:
    positions: np.ndarray
    indices: np.ndarray
    normals: np.ndarray = None


def _vertex_normals(positions: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Per-vertex normals (flat float32, len == len(positions)) from triangle
    faces by area-weighted accumulation + normalization. Used for backend
    tessellators that return positions + indices but no normals."""
    verts = positions.reshape(-1, 3)
    tris = faces.reshape(-1, 3).astype(np.int64)
    normals = np.zeros_like(verts)
    fn = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]])
    for k in range(3):
        np.add.at(normals, tris[:, k], fn)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return (normals / lengths).reshape(-1).astype("float32")


def tessellate_edges(shape: TopoDS_Edge, deflection=0.01) -> LineMesh:
    """Discretize an edge / wire (or any shape's edges) into a GL_LINES mesh.

    Indices are emitted as endpoint PAIRS (``i0,i1, i1,i2, ...``) — the glTF store reshapes
    them to ``(n/2, 2)`` segments — not a sequential strip, so a wire of several edges renders
    as one connected polyline rather than disjoint hops."""
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Extend.TopologyUtils import TopologyExplorer, discretize_edge

    if shape.ShapeType() == TopAbs_EDGE:
        edges = [shape]
    else:
        edges = list(TopologyExplorer(shape).edges()) or [shape]

    verts: list = []
    indices: list = []
    for edge in edges:
        pts = discretize_edge(edge, deflection=deflection)
        if len(pts) < 2:
            continue
        base = len(verts) // 3
        verts.extend(c for p in pts for c in (float(p[0]), float(p[1]), float(p[2])))
        for i in range(len(pts) - 1):
            indices.append(base + i)
            indices.append(base + i + 1)
    # Flat xyz buffer (the gltf store does position.reshape(len/3, 3)).
    return LineMesh(np.array(verts, dtype=np.float32), np.array(indices, dtype=np.uint32))


def _tessellate_brepmesh(shape, linear_deflection: float, angular_deflection: float, relative: bool) -> "TriangleMesh":
    """Tessellate via BRepMesh with an EXPLICIT linear + angular deflection and extract
    the per-face triangles (unwelded, 3 verts/tri, with area-weighted vertex normals).

    Unlike ShapeTesselator — which only exposes a single relative ``mesh_quality`` and no
    angular control — this gives curvature-adaptive smoothness: the angular deflection
    sets a minimum facet count around any curve regardless of size, and an *absolute*
    linear deflection adds facets in proportion to a curve's radius (matching step2glb's
    1 mm + 25° model). Used when ADA_TESS_LINEAR_DEFLECTION is configured."""
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopLoc import TopLoc_Location
    from OCC.Core.TopoDS import topods

    # Runaway clip reference: the shape's TIGHT geometric bbox, taken BEFORE meshing (the
    # shape arrives unmeshed; useTriangulation=True then yields the true extent). At a fine
    # absolute deflection an over-covered B-spline face — a param-extent / natural-bound
    # rebuild that spans more UV than its true trim — meshes well outside the real solid
    # and "explodes" the part. Triangle vertices on a healthy face sit on the surface, so
    # they cannot leave the tight bbox by more than a small margin; anything past 10% of
    # the diagonal is a phantom and is dropped. NB: must use the tight bbox, not an edge
    # hull — brepbndlib over-estimates B-spline EDGE bounds 5-6x (control polygon), which
    # silently defeats any edge-based guard.
    ref = Bnd_Box()
    try:
        brepbndlib.Add(shape, ref, True)
    except Exception:  # noqa: BLE001
        pass
    clip_lo = clip_hi = None
    if not ref.IsVoid():
        rx0, ry0, rz0, rx1, ry1, rz1 = ref.Get()
        pad = 0.1 * ((rx1 - rx0) ** 2 + (ry1 - ry0) ** 2 + (rz1 - rz0) ** 2) ** 0.5 + 1e-6
        clip_lo = (rx0 - pad, ry0 - pad, rz0 - pad)
        clip_hi = (rx1 + pad, ry1 + pad, rz1 + pad)

    BRepMesh_IncrementalMesh(shape, linear_deflection, relative, angular_deflection, True)
    verts: list = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = topods.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            rev = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if rev:
                    a, c = c, a
                pts = [tri.Node(ni).Transformed(trsf) for ni in (a, b, c)]
                if clip_lo is not None and any(
                    p.X() < clip_lo[0]
                    or p.X() > clip_hi[0]
                    or p.Y() < clip_lo[1]
                    or p.Y() > clip_hi[1]
                    or p.Z() < clip_lo[2]
                    or p.Z() > clip_hi[2]
                    for p in pts
                ):
                    continue  # phantom triangle outside the real solid — drop it
                for p in pts:
                    verts.extend((p.X(), p.Y(), p.Z()))
        exp.Next()
    positions = np.asarray(verts, dtype="float32")
    if not positions.size:
        return TriangleMesh(positions, np.arange(0, dtype="uint32"), None, positions)
    faces = np.arange(positions.size // 3, dtype="uint32")
    normals = _vertex_normals(positions, faces)
    return TriangleMesh(positions, faces, None, normals)


def tessellate_advanced_face(face: TopoDS_Shape, linear_deflection=0.1, angular_deflection=0.1, relative=True) -> None:
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh

    # Perform tessellation using BRepMesh_IncrementalMesh
    mesh = BRepMesh_IncrementalMesh(face, linear_deflection, relative, angular_deflection)
    if not mesh.IsDone():
        raise RuntimeError("Tessellation failed")

    mesh.Perform()


def _edge_hull_box(shape):
    """Union bbox of the shape's edge curves plus its bounded closed surfaces
    (sphere/torus). Face interiors are excluded on purpose: a face whose wire
    failed to trim an infinite surface (cylinder/cone) poisons any face-inclusive
    bbox, while its boundary edges remain finite. Returns (xmin..zmax) or None."""
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.GeomAbs import GeomAbs_Sphere, GeomAbs_Torus
    from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer

    box = Bnd_Box()
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        try:
            brepbndlib.Add(exp.Current(), box, False)
        except Exception:  # noqa: BLE001 - one bad edge must not void the hull
            pass
        exp.Next()
    fexp = TopExp_Explorer(shape, TopAbs_FACE)
    while fexp.More():
        try:
            ad = BRepAdaptor_Surface(fexp.Current())
            t = ad.GetType()
            if t in (GeomAbs_Sphere, GeomAbs_Torus):
                if t == GeomAbs_Sphere:
                    s = ad.Sphere()
                    c, r = s.Location(), s.Radius()
                else:
                    s = ad.Torus()
                    c, r = s.Location(), s.MajorRadius() + s.MinorRadius()
                box.Update(c.X() - r, c.Y() - r, c.Z() - r, c.X() + r, c.Y() + r, c.Z() + r)
        except Exception:  # noqa: BLE001
            pass
        fexp.Next()
    if box.IsVoid():
        return None
    return box.Get()


def _drop_runaway_triangles(shape, np_vertices, np_normals):
    """ShapeTesselator output is an unindexed triangle soup. A face whose wire
    failed to trim an infinite surface can mesh with interior vertices flying
    kilometres out (worst on sewn solids, where ShapeFix/sewing rebuilds pcurves) —
    a single such face explodes the converted model's bounding box. Drop every
    triangle with a vertex outside the shape's edge hull inflated by 10x its
    diagonal; legitimate surface bulge between edges is well under 1x."""
    hull = _edge_hull_box(shape)
    if hull is None:
        return np_vertices, np_normals
    xmin, ymin, zmin, xmax, ymax, zmax = hull
    lo = np.array([xmin, ymin, zmin], dtype="float64")
    hi = np.array([xmax, ymax, zmax], dtype="float64")
    pad = 10.0 * max(float(np.linalg.norm(hi - lo)), 1e-6)
    tri = np_vertices.reshape(-1, 3, 3)
    ok = ((tri >= (lo - pad)) & (tri <= (hi + pad))).all(axis=(1, 2))
    if bool(ok.all()):
        return np_vertices, np_normals
    np_vertices = np.ascontiguousarray(tri[ok].reshape(-1))
    if np_normals is not None and np_normals.size:
        np_normals = np.ascontiguousarray(np_normals.reshape(-1, 3, 3)[ok].reshape(-1))
    return np_vertices, np_normals


def _empty_triangle_mesh() -> TriangleMesh:
    e = np.empty(0, dtype="float32")
    return TriangleMesh(e, np.empty(0, dtype="uint32"), None, e)


def _has_meshable_extent(shape: TopoDS_Shape) -> bool:
    """False when ``shape`` has nothing to triangulate — an empty body or a
    void / zero-extent bounding box.

    ``ShapeTesselator.Compute`` derives a *relative* linear deflection from the
    shape's bounding box, so a void/zero-extent box yields deflection 0 and OCC
    raises ``std::invalid_argument("The deviation must be greater than 0")``.
    That's a plain C++ exception (not a ``Standard_Failure``), so pythonocc
    doesn't translate it to a Python ``RuntimeError`` — it escapes the
    try/except in :func:`tessellate_shape` and ``std::terminate``\\s the whole
    process. Empty bodies turn up from round-trips that drop geometry (e.g. a
    FEM deck whose elements don't convert exports an empty STEP compound), so
    guard rather than crash."""
    from OCC.Core.Bnd import Bnd_Box

    try:
        from OCC.Core.BRepBndLib import brepbndlib

        _add = brepbndlib.Add
    except (ImportError, AttributeError):
        from OCC.Core.BRepBndLib import brepbndlib_Add as _add

    box = Bnd_Box()
    try:
        _add(shape, box)
    except Exception:
        return False
    if box.IsVoid():
        return False
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    diag2 = (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2
    return diag2 > 1e-18  # ~1 nm extent floor


def _backend_has_meshable_extent(shape) -> bool:
    """Backend-agnostic mirror of :func:`_has_meshable_extent` for a non-OCC
    handle. An empty body crashes adacpp's native tessellator the same way it
    crashes OCC's (the C++ exception can't cross nanobind), so probe the
    backend's bbox first — it raises / reports a void box for a geometry-less
    shape."""
    from ada.cad import active_backend

    try:
        # optimal=False: we only need "does it have non-zero extent", not a tight
        # box. AddOptimal samples every BSpline/B-rep face (~ms each) — on a model
        # with thousands of curved faces that empty-probe alone dominated the
        # conversion (e.g. a hull skin: ~29s of bbox just to confirm non-empty).
        xmin, ymin, zmin, xmax, ymax, zmax = active_backend().bbox(shape, optimal=False)
    except Exception:
        return False  # "empty bounding box (shape has no geometry)" etc.
    diag2 = (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2
    return diag2 > 1e-18  # ~1 nm extent floor


def tessellate_shape(shape: TopoDS_Shape, quality=1.0, render_edges=False, parallel=True) -> TriangleMesh:
    # Backend dispatch: a pythonocc TopoDS uses the rich ShapeTesselator
    # (normals + edges). Any other handle (e.g. an adacpp ShapeHandle) is
    # tessellated through the active backend's tessellate verb (adacpp's native
    # ShapeTesselator port) and adapted to a TriangleMesh with computed normals.
    if not _is_topods_shape(shape):
        # Empty / void-bbox shape: adacpp's native tessellate crashes on it the
        # same way OCC's does, so guard here too (see _has_meshable_extent).
        if not _backend_has_meshable_extent(shape):
            return _empty_triangle_mesh()
        from ada.cad import active_backend

        mesh = active_backend().tessellate(shape)
        positions = np.ascontiguousarray(mesh.positions, dtype="float32")
        faces = np.ascontiguousarray(mesh.indices, dtype="uint32")
        return TriangleMesh(positions, faces, None, _vertex_normals(positions, faces))

    # Nothing to mesh (empty body / void bbox) → return empty rather than let
    # OCC SIGABRT on a zero relative deflection. See _has_meshable_extent.
    if not _has_meshable_extent(shape):
        return _empty_triangle_mesh()

    # Configurable quality (Config ``occ_tess`` section / env ADA_OCC_TESS_*): when
    # linear_deflection > 0, tessellate with an explicit chordal + angular deflection via
    # BRepMesh — curvature-adaptive smoothness, à la step2glb (1 mm + ~25°). Default
    # (0) keeps the lean ShapeTesselator(quality) path so production GLB size / mobile
    # perf are unchanged unless a caller opts in. Read fresh from the singleton so a
    # spawned tessellation worker honours the per-job env the orchestrator set.
    import math as _math
    import os as _os_tess

    from ada.config import Config as _Config

    # Per-job env (set by the worker / orchestrator, inherited by spawned workers) wins;
    # the Config singleton supplies the startup/config-file default.
    _cfg = _Config()

    def _opt(env_name, attr, cast):
        raw = _os_tess.environ.get(env_name)
        if raw is not None and raw.strip() != "":
            return cast(raw)
        return cast(getattr(_cfg, attr, None))

    try:
        ld = float(_opt("ADA_OCC_TESS_LINEAR_DEFLECTION", "occ_tess_linear_deflection", float) or 0.0)
    except (ValueError, TypeError):
        ld = 0.0
    if ld > 0:
        try:
            ang = _math.radians(float(_opt("ADA_OCC_TESS_ANGULAR_DEG", "occ_tess_angular_deg", float)))
            rel_raw = _os_tess.environ.get("ADA_OCC_TESS_RELATIVE")
            relative = (
                rel_raw.strip().lower() in {"1", "true", "yes", "on"}
                if rel_raw is not None and rel_raw.strip() != ""
                else bool(getattr(_cfg, "occ_tess_relative", False))
            )
            return _tessellate_brepmesh(shape, ld, ang, relative)
        except (ValueError, TypeError):
            pass

    from OCC.Core.Tesselator import ShapeTesselator

    # first, compute the tesselation
    try:
        tess = ShapeTesselator(shape)
        tess.Compute(compute_edges=render_edges, mesh_quality=quality, parallel=parallel)
    except RuntimeError as e:
        raise UnableToCreateTesselationFromSolidOCCGeom(f'Failed to tessellate OCC geometry due to "{e}"')

    # get vertices and normals
    vertices_position = tess.GetVerticesPositionAsTuple()
    number_of_triangles = tess.ObjGetTriangleCount()
    number_of_vertices = len(vertices_position)

    # number of vertices should be a multiple of 3
    if number_of_vertices % 3 != 0:
        raise AssertionError("Wrong number of vertices")
    if number_of_triangles * 9 != number_of_vertices:
        raise AssertionError("Wrong number of triangles")

    # then we build the vertex and faces collections as numpy ndarrays
    np_vertices = np.array(vertices_position, dtype="float32")
    np_normals = np.array(tess.GetNormalsAsTuple(), dtype="float32")
    np_vertices, np_normals = _drop_runaway_triangles(shape, np_vertices, np_normals)
    np_faces = np.arange(np_vertices.size // 3, dtype="uint32")
    edges = np.array(
        map(
            lambda i_edge: [tess.GetEdgeVertex(i_edge, i_vert) for i_vert in range(tess.ObjEdgeGetVertexCount(i_edge))],
            range(tess.ObjGetEdgeCount()),
        )
    )

    return TriangleMesh(np_vertices, np_faces, edges, np_normals)


def shape_to_tri_mesh(shape: TopoDS_Shape, rgba_color: Iterable[float, float, float, float] = None) -> trimesh.Trimesh:
    import trimesh.visual

    tm = tessellate_shape(shape)
    positions = tm.positions.reshape(len(tm.positions) // 3, 3)
    faces = tm.faces.reshape(len(tm.faces) // 3, 3)
    mesh = trimesh.Trimesh(vertices=positions, faces=faces, process=False)
    mesh.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(baseColorFactor=rgba_color)
    )
    return mesh


class TessellationFallbackError(RuntimeError):
    """Raised in strict mode (``ADA_STREAM_TESS_STRICT``) when a geometry can't be tessellated
    by the selected OCC-free stream pipeline (libtess2/adacpp-*) and would otherwise silently
    fall back to OCC. Lets a conversion *enforce* 100% stream-kernel coverage — it fails loudly,
    naming the geometry + reason, instead of completing on the OCC path."""


@dataclass
class BatchTessellator:
    quality: float = 1.0
    render_edges: bool = False
    parallel: bool = False
    material_store: dict[Color, int] = field(default_factory=dict)
    _geom_id: int = 0

    def add_color(self, color: Color) -> int:
        mat_id = self.material_store.get(color, None)
        if mat_id is None:
            mat_id = len(self.material_store)
            self.material_store[color] = mat_id
        return mat_id

    def tessellate_occ_geom(
        self,
        occ_geom: TopoDS_Shape,
        geom_ref: GraphNode | int | str,
        geom_color,
        mesh_type: MeshType = MeshType.TRIANGLES,
    ) -> MeshStore:
        if geom_ref is None:
            geom_ref = self._geom_id
            self._geom_id += 1

        # The kind (line vs triangle mesh) is passed in by the caller rather
        # than sniffed off the OCC handle type — an opaque ShapeHandle under
        # a non-OCC backend can't be isinstance-checked. See dap plan/v3
        # notes_occ_backend_abstraction (Phase 1).
        if mesh_type == MeshType.LINES:
            tess_shape = tessellate_edges(occ_geom)
            indices = tess_shape.indices
        else:
            tess_shape = tessellate_shape(occ_geom, self.quality, self.render_edges, self.parallel)
            indices = tess_shape.faces
            # BRepMesh grids nothing when a face is missing its p-curves (the 2D
            # parametric representation it needs to triangulate) — common for imported
            # B-reps: a bspline/NURBS face trimmed by 3D-only boundary curves
            # (IfcPolyline pcurves, IfcIntersectionCurve, SAT sheet bodies). The face is
            # valid (it exports to STEP) but renders empty. ShapeFix builds the missing
            # p-curves; retry the mesh once before giving up. Guarded so a non-OCC handle
            # or a fix failure just falls through to the empty result.
            if len(indices) == 0:
                fixed = _shapefix_for_mesh(occ_geom)
                if fixed is not None:
                    tess_shape = tessellate_shape(fixed, self.quality, self.render_edges, self.parallel)
                    indices = tess_shape.faces

        mat_id = self.material_store.get(geom_color, None)
        if mat_id is None:
            mat_id = len(self.material_store)
            self.material_store[geom_color] = mat_id

        return MeshStore(
            geom_ref,
            None,
            tess_shape.positions,
            indices,
            tess_shape.normals,
            mat_id,
            mesh_type,
            geom_ref,
        )

    def _direct_line_meshstore(self, geom: Geometry, node_ref) -> MeshStore | None:
        """Build a GL_LINES MeshStore from a curve geometry WITHOUT OCC or libtess2: straight
        segments are their endpoints, arcs/circles are sampled by chord deflection (the native
        ada.geom.curve_discretize sampler — parity-tested against OCC discretize_edge). Also lets
        line geometry render on wasm (no pythonocc). Returns None for curve kinds with no native
        sampler (e.g. B-spline), which fall through to the OCC discretization path."""
        from ada.geom.curve_discretize import discretize_curve

        deflection = float(os.environ.get("ADA_LINE_DEFLECTION", "0.01"))
        pts = discretize_curve(geom.geometry, deflection=deflection)
        if not pts or len(pts) < 2:
            return None

        # Flat xyz buffer (the gltf store does position.reshape(len/3, 3)).
        position = np.array([c for p in pts for c in (float(p[0]), float(p[1]), float(p[2]))], dtype=np.float32)
        # GL_LINES endpoint pairs: (0,1),(1,2),... — connected polyline (the glTF store reshapes
        # indices to (n/2, 2) segments).
        idx: list = []
        for i in range(len(pts) - 1):
            idx.extend((i, i + 1))
        mat_id = self.material_store.get(geom.color, None)
        if mat_id is None:
            mat_id = len(self.material_store)
            self.material_store[geom.color] = mat_id
        return MeshStore(node_ref, None, position, np.array(idx, dtype=np.uint32), None, mat_id, MeshType.LINES, node_ref)

    def tessellate_geom(
        self,
        geom: Geometry,
        obj: BackendGeom,
        graph_store: GraphStore = None,
        mesh_type: MeshType = MeshType.TRIANGLES,
    ) -> MeshStore:
        if graph_store is not None:
            node_ref = graph_store.hash_map.get(obj.guid)
        else:
            node_ref = getattr(obj, "guid", geom.id)

        # OCC-free fast path: discretize the curve natively (straight = endpoints; arc/circle =
        # chord-deflection sampling) and emit the GL_LINES mesh directly — no OCC build, no
        # libtess2. Also lets line geometry render on wasm (no pythonocc). Only curve kinds
        # without a native sampler (e.g. B-spline) return None and fall to the OCC path below.
        if mesh_type == MeshType.LINES:
            direct = self._direct_line_meshstore(geom, node_ref)
            if direct is not None:
                return direct

        # NGEOM stream path (opt-in via ADA_STREAM_TESS_PIPELINE=libtess2|occ|cgal|hybrid):
        # serialize ada.geom + tessellate in one adacpp call instead of the OCC build +
        # BRepMesh below. Returns None when the env is unset or the active backend has no
        # tessellate_stream, so the default path runs UNCHANGED.
        if mesh_type != MeshType.LINES:
            stream_ms = self._tessellate_geom_via_stream(geom, node_ref)
            if stream_ms is not None:
                return stream_ms

        try:
            # Construction seam: build through the active CAD backend rather
            # than calling geom_to_occ_geom directly (= OccBackend.build under
            # the default backend). Same funnel as ada.occ.geom.cache.
            occ_geom = active_backend().build(geom)
        except Exception as e:
            section = getattr(obj, "section", None)
            logger.error(
                "tessellation failed for %s name=%r guid=%r section=%r: %s",
                type(obj).__name__,
                getattr(obj, "name", None),
                getattr(obj, "guid", None),
                section,
                e,
            )
            raise

        return self.tessellate_occ_geom(occ_geom, node_ref, geom.color, mesh_type)

    @staticmethod
    def _log_tess_fallback(node_ref, pipeline: str, reason: str, geom: Geometry | None = None) -> None:
        """Audit a silent NGEOM->OCC tessellation fallback. Normally DEBUG; set
        ADA_TESS_FALLBACK_DEBUG=1 to surface every fallback as a WARNING (so a
        coarse/odd object that quietly dropped off the selected kernel is caught)."""
        gt = "?"
        if geom is not None:
            inner = getattr(geom, "geometry", geom)
            gt = type(getattr(inner, "geometry", inner)).__name__
        msg = f"NGEOM pipeline {pipeline!r} fell back to OCC for {node_ref!r} (geom={gt}): {reason}"
        # Strict mode: a fall back to OCC is a hard failure, so a conversion can enforce/measure
        # 100% stream-kernel (libtess2/adacpp-*) coverage rather than silently completing on OCC.
        if os.environ.get("ADA_STREAM_TESS_STRICT", "").strip().lower() not in ("", "0", "false", "no", "off"):
            logger.warning(msg)
            raise TessellationFallbackError(msg)
        if os.environ.get("ADA_TESS_FALLBACK_DEBUG"):
            logger.warning(msg)
        else:
            logger.debug(msg)

    def _tessellate_geom_via_stream(self, geom: Geometry, node_ref) -> MeshStore | None:
        pipeline = os.environ.get("ADA_STREAM_TESS_PIPELINE")
        if not pipeline:
            return None
        be = active_backend()
        if not hasattr(be, "tessellate_stream"):
            self._log_tess_fallback(node_ref, pipeline, "active backend has no tessellate_stream", geom)
            return None
        gi = geom.geometry.geometry if hasattr(geom.geometry, "geometry") else geom.geometry
        defl = float(os.environ.get("ADA_STREAM_TESS_DEFLECTION", "2.0"))
        ang = float(os.environ.get("ADA_STREAM_TESS_ANGULAR", "20.0"))
        try:
            bm = be.tessellate_stream([(str(node_ref), gi)], pipeline=pipeline, deflection=defl, angular_deg=ang)
        except Exception as e:  # noqa: BLE001 - fall back to the OCC build path on any stream failure
            self._log_tess_fallback(node_ref, pipeline, f"tessellate_stream raised {type(e).__name__}: {e}", geom)
            return None
        pos = getattr(bm, "positions", None)
        idx = getattr(bm, "indices", None)
        if pos is None or idx is None or len(idx) == 0:
            self._log_tess_fallback(node_ref, pipeline, "empty mesh (geom type not NGEOM-serializable)", geom)
            return None
        pos = np.ascontiguousarray(pos, dtype=np.float32)
        idx = np.ascontiguousarray(idx, dtype=np.uint32)
        nrm = getattr(bm, "normals", None)
        nrm = np.ascontiguousarray(nrm, dtype=np.float32) if nrm is not None and len(nrm) else None
        mat_id = self.material_store.get(geom.color, None)
        if mat_id is None:
            mat_id = len(self.material_store)
            self.material_store[geom.color] = mat_id
        return MeshStore(node_ref, None, pos, idx, nrm, mat_id, MeshType.TRIANGLES, node_ref)

    def batch_tessellate(
        self,
        objects: Iterable[Geometry | BackendGeom],
        render_override: dict[str, GeomRepr] = None,
        graph_store: GraphStore = None,
    ) -> Iterable[MeshStore]:
        if render_override is None:
            render_override = dict()

        for obj in objects:
            if isinstance(obj, BackendGeom):
                ada_obj = obj
                geom_repr = render_override.get(obj.guid, GeomRepr.SOLID)
                # A Shape carrying a bare curve geometry (sectionless SAT wire body, open
                # wireframe) has no solid/shell — render it as glTF line geometry.
                if geom_repr == GeomRepr.SOLID:
                    _g = getattr(obj, "geom", None)
                    if _g is not None and isinstance(getattr(_g, "geometry", None), _CURVE_GEOM_TUPLE):
                        geom_repr = GeomRepr.LINE
                node_ref = graph_store.hash_map.get(obj.guid) if graph_store is not None else getattr(obj, "guid", None)

                # PlateCurved: prism-extrude the BSpline face by
                # thickness so the GLB ships a solid (matching what
                # a flat Plate produces) rather than a thin shell.
                # Falls back to the bare face if the prism fails
                # (handled inside extruded_solid_occ), and falls
                # through to the generic flat-fallback path further
                # below if the result tessellates to nothing.
                if (
                    geom_repr == GeomRepr.SOLID
                    and hasattr(obj, "extruded_solid_occ")
                    and callable(getattr(obj, "extruded_solid_occ"))
                ):
                    # Stream-first: a curved plate's solid_geom() can be serialized to NGEOM and
                    # tessellated by the selected OCC-free kernel. Only if that yields nothing do we
                    # use the OCC prism-extrude path below — routed through _log_tess_fallback so it
                    # is logged and strict mode fails instead of silently completing on OCC. (This
                    # path previously always used OCC, so curved plates never reached libtess2.)
                    if os.environ.get("ADA_STREAM_TESS_PIPELINE"):
                        cng = None
                        try:
                            cng = obj.solid_geom()
                        except Exception:  # noqa: BLE001 - no parametric geom → OCC prism path
                            cng = None
                        if cng is not None:
                            ms_cs = self._tessellate_geom_via_stream(cng, node_ref)
                            if ms_cs is not None:
                                yield ms_cs
                                continue
                        else:
                            self._log_tess_fallback(
                                node_ref,
                                os.environ["ADA_STREAM_TESS_PIPELINE"],
                                "PlateCurved has no parametric solid_geom for the stream kernel",
                                None,
                            )
                    ms_curved = None
                    try:
                        shape = obj.extruded_solid_occ()
                        ms_curved = self.tessellate_occ_geom(shape, node_ref, obj.color)
                    except UnableToCreateTesselationFromSolidOCCGeom as e:
                        logger.error(e)
                    except Exception as e:
                        # A backend that can't build this curved face's wire
                        # (e.g. adacpp ``build_advanced_face: wire build failed``)
                        # must not crash the whole model render — fall through to
                        # the flat-fallback path below for this one plate.
                        logger.warning(
                            "PlateCurved %r: solid build failed (%s); using flat representation",
                            getattr(ada_obj, "name", "?"),
                            e,
                        )
                    if ms_curved is not None:
                        pos = getattr(ms_curved, "position", None)
                        idx = getattr(ms_curved, "indices", None)
                        pos_n = 0 if pos is None else (len(pos) if hasattr(pos, "__len__") else 0)
                        idx_n = 0 if idx is None else (len(idx) if hasattr(idx, "__len__") else 0)
                        if pos_n > 0 and idx_n > 0:
                            # Trust-but-verify: compare the rendered
                            # mesh bbox against the flat reference.
                            # The exppc surface peel can land on the
                            # right neighbourhood (centroid passes
                            # the AABB containment check upstream)
                            # while the wire's pcurves point at a
                            # different UV region — OCC happily
                            # tessellates over the wrong patch and
                            # produces a mesh that's 5-15× the
                            # plate's actual size. Catch those by
                            # checking the actual mesh extent.
                            fb_pts = getattr(ada_obj, "_flat_fallback_pts", None)
                            mesh_ok = True
                            if fb_pts and len(fb_pts) >= 3:
                                try:
                                    import numpy as _np

                                    verts = _np.asarray(pos, dtype=float).reshape(-1, 3)
                                    flat_arr = _np.array([list(p)[:3] for p in fb_pts])
                                    flat_ext = flat_arr.max(axis=0) - flat_arr.min(axis=0)
                                    mesh_ext = verts.max(axis=0) - verts.min(axis=0)
                                    # Allow 3× per axis; thickness
                                    # axis (flat_ext ≈ 0) gets a 1 m
                                    # absolute floor since prism
                                    # extrusion adds at most ``t`` on
                                    # that axis (≪ 1 m).
                                    floor = 1.0
                                    limit = _np.maximum(3.0 * flat_ext, floor)
                                    over = mesh_ext > limit
                                    if bool(over.any()):
                                        worst = int(_np.argmax(mesh_ext / _np.maximum(flat_ext, 1e-3)))
                                        logger.warning(
                                            "PlateCurved %r: mesh extent %.1f m on axis %d vs flat %.2f m"
                                            " (limit %.2f m) — using flat representation",
                                            getattr(ada_obj, "name", "?"),
                                            float(mesh_ext[worst]),
                                            worst,
                                            float(flat_ext[worst]),
                                            float(limit[worst]),
                                        )
                                        mesh_ok = False

                                    # Under-coverage guard. Some trimmed B-spline
                                    # faces defeat BRepMesh: a malformed trim wire
                                    # (pcurves that backtrack/duplicate in UV) makes
                                    # it grid only ~half the face, regardless of the
                                    # deflection — the "missing triangles" on a few
                                    # hull-skin plates. The mesh extent is right, so
                                    # the bbox check above passes it. But a correct
                                    # *prism* of a (curved or flat) plate has
                                    # top+bottom faces each at least the flat
                                    # footprint area, so its mesh area is always
                                    # >= ~2x the footprint plus the side walls.
                                    # Healthy plates measure ~2.05x; the broken ones
                                    # ~1.55x. When under-covered, first try a
                                    # natural-bound rebuild (keeps the curve — the
                                    # surface is sound, only the wire is bad); fall
                                    # back to the flat quad only if that fails too.
                                    if mesh_ok and idx_n >= 3:
                                        mesh_area = _mesh_store_area(ms_curved)
                                        # Newell area of the (planar) flat-footprint loop.
                                        nrm = _np.zeros(3)
                                        for _i in range(len(flat_arr)):
                                            nrm = nrm + _np.cross(flat_arr[_i], flat_arr[(_i + 1) % len(flat_arr)])
                                        flat_area = 0.5 * float(_np.linalg.norm(nrm))
                                        if flat_area > 1e-6 and mesh_area < 1.85 * flat_area:
                                            mesh_ok = False
                                            rebuilt = _natural_bound_curved_solid(ada_obj)
                                            if rebuilt is not None:
                                                ms_rb = self.tessellate_occ_geom(rebuilt, node_ref, obj.color)
                                                rb_area = _mesh_store_area(ms_rb)
                                                # Accept the repaired curve when it now
                                                # covers (>=1.85x) without overshooting
                                                # the trim (<=3.5x rules out a surface
                                                # whose natural box exceeds the real
                                                # plate — those keep the flat fallback).
                                                if 1.85 * flat_area <= rb_area <= 3.5 * flat_area:
                                                    logger.warning(
                                                        "PlateCurved %r: trim-wire tessellation under-covered "
                                                        "(%.2f m^2 < %.2f); rebuilt from surface natural bounds "
                                                        "(%.2f m^2)",
                                                        getattr(ada_obj, "name", "?"),
                                                        mesh_area,
                                                        2.0 * flat_area,
                                                        rb_area,
                                                    )
                                                    yield ms_rb
                                                    continue
                                            logger.warning(
                                                "PlateCurved %r: curved mesh area %.2f m^2 vs %.2f expected "
                                                "(< 1.85x flat footprint %.2f) — degenerate tessellation, "
                                                "using flat representation",
                                                getattr(ada_obj, "name", "?"),
                                                mesh_area,
                                                2.0 * flat_area,
                                                flat_area,
                                            )
                                except Exception:
                                    pass
                            if mesh_ok:
                                yield ms_curved
                                continue
                    # Empty / failed / oversize — drop to the flat-
                    # fallback path at the bottom of the loop.
                    fallback_pts = getattr(ada_obj, "_flat_fallback_pts", None)
                    if fallback_pts:
                        try:
                            from ada import Plate
                            from ada.cadit.gxml.read.helpers import (
                                _project_to_best_fit_plane,
                            )

                            fb = Plate.from_3d_points(
                                getattr(ada_obj, "name", "fallback"),
                                _project_to_best_fit_plane(fallback_pts),
                                getattr(ada_obj, "t", None) or 0.0,
                                mat=getattr(ada_obj, "material", None),
                                metadata=dict(
                                    props=dict(
                                        gxml_flat_fallback_for=getattr(ada_obj, "name", None),
                                    )
                                ),
                                parent=getattr(ada_obj, "parent", None),
                            )
                            yield self.tessellate_geom(
                                fb.solid_geom(),
                                ada_obj,
                                graph_store=graph_store,
                            )
                            logger.warning(
                                "PlateCurved %r: BSpline tessellation failed, rendered as flat fallback",
                                getattr(ada_obj, "name", "?"),
                            )
                        except Exception as fb_err:
                            logger.error(
                                "PlateCurved %r: flat fallback also failed (%s); plate dropped",
                                getattr(ada_obj, "name", "?"),
                                fb_err,
                            )
                    continue

                # Raw-OCC fast path: STEP/SAT-imported shapes hold a
                # TopoDS_* on the transient ``_occ_cache`` slot
                # (post-refactor — used to live on ``_geom`` but
                # that broke serialisability). Either way, when
                # we have a raw OCC body and no parametric
                # ``solid_geom()`` available, skip the ada.geom
                # round-trip and tessellate the OCC body directly.
                raw = getattr(obj, "_occ_cache", None)
                if raw is None:
                    # Backward-compat: any legacy code path that
                    # still stuffs a backend body into ``_geom`` keeps
                    # working.
                    legacy = getattr(obj, "_geom", None)
                    if is_shape_handle(legacy):
                        raw = legacy
                # STEP/SAT-imported bodies are raw OCC TopoDS_Shapes from the
                # OCC DocBackend reader. Under a non-OCC active backend (adacpp)
                # they aren't active-backend handles yet — adopt across the
                # kernel boundary (same OCCT version → safe) before rendering.
                if raw is not None and not is_shape_handle(raw):
                    try:
                        raw = active_backend().adopt_occ_shape(raw)
                    except (NotImplementedError, AttributeError):
                        raw = None
                if geom_repr == GeomRepr.SOLID and is_shape_handle(raw):
                    # NGEOM pipeline requested: prefer serializing the object's ada.geom +
                    # streaming it through the selected kernel — the raw OCC body (_occ_cache,
                    # set by primitives + raw-OCC imports) alone can't drive a non-OCC kernel.
                    # Objects with no parametric solid_geom (raw-OCC imports) fall back to the
                    # direct OCC tessellation below.
                    if os.environ.get("ADA_STREAM_TESS_PIPELINE"):
                        ng = None
                        try:
                            ng = obj.solid_geom()
                        except Exception:  # noqa: BLE001 - no parametric geom -> OCC fast path
                            ng = None
                        if ng is not None:
                            ms_ng = self._tessellate_geom_via_stream(ng, node_ref)
                            if ms_ng is not None:
                                yield ms_ng
                                continue
                        else:
                            # Raw-OCC body with no parametric solid_geom (e.g. SAT/STEP import):
                            # it can't be serialized to NGEOM, so it only renders via OCC. That IS a
                            # stream→OCC fallback — route it through the choke point so it's logged
                            # and strict mode fails instead of silently completing on OCC.
                            self._log_tess_fallback(
                                node_ref,
                                os.environ["ADA_STREAM_TESS_PIPELINE"],
                                "raw-OCC body has no parametric solid_geom for the stream kernel",
                                None,
                            )
                    try:
                        yield self.tessellate_occ_geom(raw, node_ref, obj.color, MeshType.TRIANGLES)
                    except UnableToCreateTesselationFromSolidOCCGeom as e:
                        logger.error(e)
                    continue
                try:
                    if geom_repr == GeomRepr.SOLID:
                        geom = obj.solid_geom()
                        mesh_type = MeshType.TRIANGLES
                    elif geom_repr == GeomRepr.SHELL:
                        geom = obj.shell_geom()
                        mesh_type = MeshType.TRIANGLES
                    else:
                        geom = obj.line_geom()
                        mesh_type = MeshType.LINES
                except NotImplementedError as e:
                    # A shape that carries no renderable geometry (e.g. an IFC product
                    # imported without a body, or a type with no solid_geom mapping) must
                    # not abort tessellation of the whole scene — skip it.
                    logger.warning(f"Skipping {getattr(obj, 'name', obj)!r}: no tessellable geometry ({e})")
                    continue
            else:
                geom = obj
                ada_obj = None
                mesh_type = MeshType.TRIANGLES

            # resolve transform based on parent transforms
            ms = None
            try:
                ms = self.tessellate_geom(geom, ada_obj, graph_store=graph_store, mesh_type=mesh_type)
            except UnableToCreateTesselationFromSolidOCCGeom as e:
                logger.error(e)
            except UnableToCreateCurveOCCGeom as e:
                logger.error(e)
            if ms is not None:
                # Treat an empty MeshStore the same as a thrown
                # tessellation error: BRepMesh produced 0 triangles
                # (typically a face that passed every guard but has a
                # degenerate or self-intersecting wire OCC's mesher
                # can't grid). Fall through to the flat-plate fallback
                # rather than emit an orphan node with no geometry —
                # which the user perceives as "the plate vanished".
                pos = getattr(ms, "position", None)
                idx = getattr(ms, "indices", None)
                pos_n = 0 if pos is None else (len(pos) if hasattr(pos, "__len__") else 0)
                idx_n = 0 if idx is None else (len(idx) if hasattr(idx, "__len__") else 0)
                if pos_n > 0 and idx_n > 0:
                    yield ms
                    continue
                logger.error(
                    "PlateCurved %r: tessellation produced empty mesh (pos=%d idx=%d)",
                    getattr(ada_obj, "name", "?"),
                    pos_n,
                    idx_n,
                )

            # PlateCurved → flat-plate fallback. The gxml reader
            # attaches ``_flat_fallback_pts`` to PlateCurved instances
            # whenever the SAT face also has a planar perimeter loop
            # available (which is essentially all of them). When the
            # BSpline OCC face construction fails — typically the
            # strict ``p-curve update incomplete`` guard in
            # surfaces.py — we degrade to a flat plate using those
            # corner points so the plate at least appears flat
            # instead of vanishing entirely. Restores pre-exppc-fix
            # behaviour where these faces fell back via
            # ``Plate.from_3d_points`` automatically because
            # advanced-face conversion failed earlier.
            fallback_pts = getattr(ada_obj, "_flat_fallback_pts", None)
            if not fallback_pts:
                continue
            try:
                from ada import Plate
                from ada.cadit.gxml.read.helpers import _project_to_best_fit_plane

                fallback = Plate.from_3d_points(
                    getattr(ada_obj, "name", "fallback"),
                    _project_to_best_fit_plane(fallback_pts),
                    getattr(ada_obj, "t", None) or 0.0,
                    mat=getattr(ada_obj, "material", None),
                    metadata=dict(
                        props=dict(
                            gxml_flat_fallback_for=getattr(ada_obj, "name", None),
                        )
                    ),
                    parent=getattr(ada_obj, "parent", None),
                )
                fallback_geom = fallback.solid_geom()
                yield self.tessellate_geom(fallback_geom, ada_obj, graph_store=graph_store)
                logger.warning(
                    "PlateCurved %r: BSpline tessellation failed, rendered as flat fallback",
                    getattr(ada_obj, "name", "?"),
                )
            except Exception as fb_err:
                logger.error(
                    "PlateCurved %r: flat fallback also failed (%s); plate dropped",
                    getattr(ada_obj, "name", "?"),
                    fb_err,
                )

    def batch_tessellate_solids(
        self,
        objects: Iterable[BackendGeom],
        graph_store: GraphStore = None,
        linear_deflection: float = -1.0,
    ) -> Iterable[MeshStore]:
        """Fast path for simple SOLID objects: tessellate them all in ONE backend
        call (ada-cpp's native ``tessellate_batch`` when available, else a loop),
        then split the combined mesh back into per-object ``MeshStore``s using each
        group's vertex range — preserving each object's colour so the downstream
        per-colour merge in :meth:`meshes_to_trimesh` is unchanged.

        Scope: each object must expose ``solid_geom()`` that builds cleanly. Curved
        plates, raw-OCC bodies and line geometry are NOT handled here — route those
        through :meth:`batch_tessellate`. Yields nothing for an empty input.
        """
        backend = active_backend()
        shapes = []
        meta: list = []  # (color, node_ref) parallel to shapes
        for obj in objects:
            geom = obj.solid_geom()
            shapes.append(backend.build(geom))
            node_ref = graph_store.hash_map.get(obj.guid) if graph_store is not None else getattr(obj, "guid", None)
            meta.append((geom.color, node_ref))

        if not shapes:
            return

        bm = backend.tessellate_batch(shapes, linear_deflection)
        for grp, (color, node_ref) in zip(bm.groups, meta):
            mat_id = self.add_color(color)
            pos = np.ascontiguousarray(bm.positions[grp.vstart * 3 : (grp.vstart + grp.vlength) * 3], dtype="float32")
            # rebase the group's indices to this object's local vertex range
            idx = np.ascontiguousarray(
                bm.indices[grp.start : grp.start + grp.length].astype(np.uint32) - np.uint32(grp.vstart),
                dtype="uint32",
            )
            if bm.normals is not None:
                nrm = np.ascontiguousarray(bm.normals[grp.vstart * 3 : (grp.vstart + grp.vlength) * 3], dtype="float32")
            else:
                nrm = _vertex_normals(pos, idx)
            yield MeshStore(node_ref, None, pos, idx, nrm, mat_id, MeshType.TRIANGLES, node_ref)

    def meshes_to_trimesh(
        self, shapes_tess_iter: Iterable[MeshStore], graph=None, merge_meshes: bool = True, apply_transform=False
    ) -> trimesh.Scene:
        import trimesh

        all_shapes = sorted(shapes_tess_iter, key=lambda x: x.material)

        # filter out all shapes associated with an animation,
        base_frame = graph.top_level.name if graph is not None else "root"

        scene = trimesh.Scene(base_frame=base_frame)
        for mat_id, meshes in groupby(all_shapes, lambda x: x.material):
            if merge_meshes:
                merged_store = concatenate_stores(meshes)
                merged_mesh_to_trimesh_scene(
                    scene, merged_store, self.get_mat_by_id(mat_id), mat_id, graph, apply_transform=apply_transform
                )
            else:
                for mesh_store in meshes:
                    merged_mesh_to_trimesh_scene(scene, mesh_store, self.get_mat_by_id(mat_id), mat_id, graph)
        return scene

    def tessellate_part(
        self,
        part: Part,
        params: RenderParams = None,
        graph: GraphStore = None,
    ) -> trimesh.Scene:
        if params is None:
            params = RenderParams()

        # Welds compose in alongside structural objects: Part._welds
        # is intentionally a separate container (IFC/FEM/GXML writers
        # can't process welds), so the GLB path is the one place we
        # explicitly add them via Part.get_all_welds().
        from itertools import chain as _chain

        objects_iter = _chain(
            part.get_all_physical_objects(pipe_to_segments=True, filter_by_guids=params.filter_by_guids),
            part.get_all_welds(),
        )

        shapes_tess_iter = self.batch_tessellate(
            objects=objects_iter,
            render_override=params.render_override,
            graph_store=graph,
        )

        scene = self.meshes_to_trimesh(
            shapes_tess_iter, graph, merge_meshes=params.merge_meshes, apply_transform=params.apply_transform
        )

        return scene

    def get_mat_by_id(self, mat_id: int):
        _data = {value: key for key, value in self.material_store.items()}
        return _data.get(mat_id)

    def _extract_ifc_product_color(self, ifc_store: IfcStore, ifc_product) -> int:
        from ada.cadit.ifc.read.read_color import get_product_color

        color = get_product_color(ifc_product, ifc_store.f)

        mat_id = self.add_color(color)
        return mat_id

    def iter_ifc_store(self, ifc_store: IfcStore, cpus=1, settings=None) -> Iterable[MeshStore]:
        import ifcopenshell.geom
        import ifcopenshell.util.representation

        # Imported lazily (not at module load) so ``ada.occ.tessellating``
        # stays importable under pyodide/WASM, where ifcopenshell may be
        # absent — the beam-solid tessellation path (tessellate_geom →
        # active_backend) needs this module but not its IFC bits.
        from ada.cadit.ifc.utils import default_settings
        from ada.visit.gltf.meshes import MeshStore, MeshType

        # see Ifcopenshell src/ifcgeom/ConversionSettings.h for the various parameters
        # https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/ifcgeom/ConversionSettings.h#L102

        if settings is None:
            settings = default_settings()

        iterator = ifcopenshell.geom.iterator(settings, ifc_store.f, cpus)

        iterator.initialize()
        while True:
            shape = iterator.get()
            product = ifc_store.f.by_id(shape.id)
            if shape and hasattr(shape, "geometry") and not product.is_a("IfcOpeningElement"):
                geom = shape.geometry
                mat_id = self._extract_ifc_product_color(ifc_store, ifc_store.f.by_id(shape.id))
                yield MeshStore(
                    shape.guid,
                    matrix=shape.transformation.matrix,
                    position=np.frombuffer(geom.verts_buffer, "d"),
                    indices=np.frombuffer(geom.faces_buffer, dtype=np.uint32),
                    normal=None,
                    material=mat_id,
                    type=MeshType.TRIANGLES,
                    node_ref=shape.guid,
                )
            else:
                logger.warning(f"{product=} will not be processed")

            if not iterator.next():
                break

    def ifc_to_trimesh_scene(self, ifc_store: IfcStore, merge_meshes=True, graph: GraphStore = None) -> trimesh.Scene:
        shapes_tess_iter = self.iter_ifc_store(ifc_store)

        scene = self.meshes_to_trimesh(shapes_tess_iter, graph, merge_meshes=merge_meshes)
        return scene
