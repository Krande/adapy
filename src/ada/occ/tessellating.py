from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterable

import numpy as np
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.Tesselator import ShapeTesselator
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Shape
from OCC.Extend.TopologyUtils import discretize_edge

from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.cad import active_backend, is_shape_handle
from ada.cadit.ifc.utils import default_settings
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

    from ada.api.spatial import Part
    from ada.cadit.ifc.store import IfcStore


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
    points = discretize_edge(shape, deflection=deflection)

    np_edge_vertices = np.array(points, dtype=np.float32)
    np_edge_indices = np.arange(np_edge_vertices.shape[0], dtype=np.uint32)
    return LineMesh(np_edge_vertices, np_edge_indices)


def tessellate_advanced_face(face: TopoDS_Shape, linear_deflection=0.1, angular_deflection=0.1, relative=True) -> None:
    # Perform tessellation using BRepMesh_IncrementalMesh
    mesh = BRepMesh_IncrementalMesh(face, linear_deflection, relative, angular_deflection)
    if not mesh.IsDone():
        raise RuntimeError("Tessellation failed")

    mesh.Perform()


def tessellate_shape(shape: TopoDS_Shape, quality=1.0, render_edges=False, parallel=True) -> TriangleMesh:
    # Backend dispatch: a pythonocc TopoDS uses the rich ShapeTesselator
    # (normals + edges). Any other handle (e.g. an adacpp ShapeHandle) is
    # tessellated through the active backend's tessellate verb (adacpp's native
    # ShapeTesselator port) and adapted to a TriangleMesh with computed normals.
    if not isinstance(shape, TopoDS_Shape):
        from ada.cad import active_backend

        mesh = active_backend().tessellate(shape)
        positions = np.ascontiguousarray(mesh.positions, dtype="float32")
        faces = np.ascontiguousarray(mesh.indices, dtype="uint32")
        return TriangleMesh(positions, faces, None, _vertex_normals(positions, faces))

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
    np_faces = np.arange(number_of_triangles * 3, dtype="uint32")
    np_normals = np.array(tess.GetNormalsAsTuple(), dtype="float32")
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

    def tessellate_geom(
        self,
        geom: Geometry,
        obj: BackendGeom,
        graph_store: GraphStore = None,
        mesh_type: MeshType = MeshType.TRIANGLES,
    ) -> MeshStore:
        try:
            # Construction seam: build through the active CAD backend rather
            # than calling geom_to_occ_geom directly (= OccBackend.build under
            # the default backend). Same funnel as ada.occ.geom.cache.
            occ_geom = active_backend().build(geom)
        except Exception as e:
            print("\n================ GLB TESSELLATION ERROR ================")
            print(f"Beam name: {getattr(obj, 'name', None)}")
            print(f"Beam guid: {getattr(obj, 'guid', None)}")
            print(f"Beam type: {type(obj)}")

            # If it has section
            if hasattr(obj, "section"):
                print(f"Section: {obj.section}")

            print("Exception:", e)
            print("========================================================\n")
            raise

        if graph_store is not None:
            node_ref = graph_store.hash_map.get(obj.guid)
        else:
            node_ref = getattr(obj, "guid", geom.id)

        return self.tessellate_occ_geom(occ_geom, node_ref, geom.color, mesh_type)

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
                node_ref = (
                    graph_store.hash_map.get(obj.guid)
                    if graph_store is not None
                    else getattr(obj, "guid", None)
                )

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
                    ms_curved = None
                    try:
                        shape = obj.extruded_solid_occ()
                        ms_curved = self.tessellate_occ_geom(shape, node_ref, obj.color)
                    except UnableToCreateTesselationFromSolidOCCGeom as e:
                        logger.error(e)
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
                                            float(mesh_ext[worst]), worst,
                                            float(flat_ext[worst]), float(limit[worst]),
                                        )
                                        mesh_ok = False
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
                            from ada.cadit.gxml.read.helpers import _project_to_best_fit_plane
                            fb = Plate.from_3d_points(
                                getattr(ada_obj, "name", "fallback"),
                                _project_to_best_fit_plane(fallback_pts),
                                getattr(ada_obj, "t", None) or 0.0,
                                mat=getattr(ada_obj, "material", None),
                                metadata=dict(props=dict(
                                    gxml_flat_fallback_for=getattr(ada_obj, "name", None),
                                )),
                                parent=getattr(ada_obj, "parent", None),
                            )
                            yield self.tessellate_geom(
                                fb.solid_geom(), ada_obj, graph_store=graph_store,
                            )
                            logger.warning(
                                "PlateCurved %r: BSpline tessellation failed, rendered as flat fallback",
                                getattr(ada_obj, "name", "?"),
                            )
                        except Exception as fb_err:
                            logger.error(
                                "PlateCurved %r: flat fallback also failed (%s); plate dropped",
                                getattr(ada_obj, "name", "?"), fb_err,
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
                    try:
                        yield self.tessellate_occ_geom(raw, node_ref, obj.color, MeshType.TRIANGLES)
                    except UnableToCreateTesselationFromSolidOCCGeom as e:
                        logger.error(e)
                    continue
                if geom_repr == GeomRepr.SOLID:
                    geom = obj.solid_geom()
                    mesh_type = MeshType.TRIANGLES
                elif geom_repr == GeomRepr.SHELL:
                    geom = obj.shell_geom()
                    mesh_type = MeshType.TRIANGLES
                else:
                    geom = obj.line_geom()
                    mesh_type = MeshType.LINES
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
                    getattr(ada_obj, "name", "?"), pos_n, idx_n,
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
                    metadata=dict(props=dict(
                        gxml_flat_fallback_for=getattr(ada_obj, "name", None),
                    )),
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
                    getattr(ada_obj, "name", "?"), fb_err,
                )

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
