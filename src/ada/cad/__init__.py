"""Backend-agnostic CAD operations.

A thin abstraction layer that lets adapy swap between pythonocc-core
(native CPython) and adacpp's wasm-compatible kernel (pyodide). Each
backend is lazy-loaded — importing this module does not pull in either
kernel — so the same module file works in environments where only one
of the two is installable.

Selection order (in `select_backend()`):
1. Explicit `prefer` argument
2. `ADAPY_CAD_BACKEND` env var ("adacpp" or "occ")
3. adacpp if importable
4. pythonocc-core if importable
5. raise ImportError

Surface kept intentionally narrow — mirrors `adacpp.cad` (primitives +
tessellate + pyocc bridge). Grow as the migration progresses; do not
add operations here that don't have a working implementation in at
least one backend.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ada.cad.registry import (  # noqa: E402 - registry is stdlib-only, no ada.cad cycle
    DEFAULT_STREAM_TESS_ANGULAR_DEG,
    CadBackendName,
    CadConfig,
    StepReader,
    TessellationPath,
    available_backends,
    available_paths,
    backend_available,
)
from ada.config import logger

if TYPE_CHECKING:
    import numpy as np

    from ada.geom import Geometry
    from ada.geom.booleans import BoolOpEnum
    from ada.geom.direction import Direction
    from ada.geom.points import Point
    from ada.occ.backend import OccBackend  # re-exported at runtime via __getattr__


def _circle_param(pt, loc, axis, ref) -> float:
    """Angular parameter (radians) of ``pt`` on a circle at ``loc`` with normal ``axis``
    and angular origin ``ref`` — measured from ref about axis, matching OCC's gp_Circ. Used
    to recover a circular arc's trim extent from its endpoints for the adacpp edge encoder."""
    ax = [float(x) for x in axis]
    an = math.sqrt(sum(c * c for c in ax)) or 1.0
    ax = [c / an for c in ax]
    r0 = [float(x) for x in ref]
    dp = sum(r0[i] * ax[i] for i in range(3))
    r0 = [r0[i] - dp * ax[i] for i in range(3)]  # ref orthogonalised to axis
    rn = math.sqrt(sum(c * c for c in r0)) or 1.0
    r0 = [c / rn for c in r0]
    perp = [ax[1] * r0[2] - ax[2] * r0[1], ax[2] * r0[0] - ax[0] * r0[2], ax[0] * r0[1] - ax[1] * r0[0]]
    d = [float(pt[i]) - float(loc[i]) for i in range(3)]
    return math.atan2(sum(d[i] * perp[i] for i in range(3)), sum(d[i] * r0[i] for i in range(3)))


class Containment(Enum):
    """Backend-neutral result of a point-in-solid classification.

    Mirrors OCCT's TopAbs_State without exposing the kernel enum."""

    IN = "in"
    OUT = "out"
    ON = "on"
    UNKNOWN = "unknown"


@runtime_checkable
class ShapeHandle(Protocol):
    """Opaque CAD shape handle. Concrete type is backend-private."""


@runtime_checkable
class Mesh(Protocol):
    """Triangle mesh produced by tessellation."""

    positions: Any  # flat float buffer, length = 3 * num_vertices
    indices: Any  # flat int buffer,   length = 3 * num_triangles


@dataclass(frozen=True)
class MeshGroup:
    """One input shape's slice of a combined :class:`BatchMesh`."""

    node_id: int  # index of the source shape in the batch
    start: int  # offset into BatchMesh.indices
    length: int  # number of indices (== 3 * triangles) for this shape
    vstart: int  # offset into BatchMesh.positions, in vertices (start*3 in floats)
    vlength: int  # number of vertices for this shape


@dataclass
class BatchMesh:
    """Several shapes tessellated into one combined buffer (``tessellate_batch``).

    ``positions`` / ``indices`` (and ``normals`` when the backend supplies them)
    are flat NumPy buffers covering every shape; ``groups`` demarcates each source
    shape's index AND vertex range, so callers can slice per-shape geometry out of
    the shared buffer — one GLB scene, or split back into per-object meshes while
    keeping each shape's colour.
    """

    positions: Any  # flat float32, length = 3 * num_vertices
    indices: Any  # flat uint32,  length = 3 * num_triangles
    groups: "list[MeshGroup]"
    normals: Any = None  # flat float32, len == positions, or None if not supplied


def tessellate_batch_via_loop(backend, shapes, linear_deflection: float = -1.0) -> "BatchMesh":
    """Backend-neutral ``tessellate_batch`` fallback: tessellate each shape and
    concatenate into one combined :class:`BatchMesh`. Used by backends without a
    native batch path (OccBackend always; AdacppBackend when the loaded ada-cpp
    build predates ``tessellate_batch``). Indices are offset by the running
    vertex count so the merged buffer is self-consistent. Normals are carried
    through when every per-shape mesh has them."""
    import numpy as np

    pos_chunks: list = []
    idx_chunks: list = []
    nrm_chunks: list = []
    groups: list[MeshGroup] = []
    vbase = 0  # running vertex offset
    istart = 0  # running index offset
    have_normals = True
    for i, sh in enumerate(shapes):
        m = backend.tessellate(sh, linear_deflection)
        pos = np.asarray(m.positions, dtype=np.float32)
        raw = getattr(m, "indices", None)
        if raw is None:
            raw = m.faces  # OccBackend's TriangleMesh names it `faces`
        idx = np.asarray(raw, dtype=np.uint32)
        nrm = getattr(m, "normals", None)
        if nrm is None or len(nrm) != pos.size:
            have_normals = False
        elif have_normals:
            nrm_chunks.append(np.asarray(nrm, dtype=np.float32))
        nverts = pos.size // 3
        pos_chunks.append(pos)
        idx_chunks.append(idx + np.uint32(vbase))
        groups.append(MeshGroup(node_id=i, start=istart, length=int(idx.size), vstart=vbase, vlength=nverts))
        vbase += nverts
        istart += int(idx.size)
    positions = np.concatenate(pos_chunks) if pos_chunks else np.empty(0, np.float32)
    indices = np.concatenate(idx_chunks) if idx_chunks else np.empty(0, np.uint32)
    normals = np.concatenate(nrm_chunks) if (have_normals and nrm_chunks) else None
    return BatchMesh(positions=positions, indices=indices, groups=groups, normals=normals)


class CadBackend(Protocol):
    """Backend contract. Each method returns a kernel-native value; callers
    treat the returned ShapeHandle as opaque and only consume Mesh fields."""

    name: str

    def build(self, geometry: "Geometry") -> ShapeHandle: ...
    def make_wire(self, points: "list") -> ShapeHandle: ...
    def polygon_face(self, points: "list") -> ShapeHandle: ...
    def loft_profiles(
        self, profiles: "list[list[tuple[float, float, float]]]", ruled: bool = True, solid: bool = True
    ) -> ShapeHandle: ...
    def section_with_plane(self, shape: ShapeHandle, origin, normal, size: float = 1000.0) -> ShapeHandle: ...
    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle: ...
    def make_cylinder(self, radius: float, height: float) -> ShapeHandle: ...
    def make_sphere(self, radius: float) -> ShapeHandle: ...
    def tessellate(self, shape: ShapeHandle, linear_deflection: float = -1.0) -> Mesh: ...
    def tessellate_batch(self, shapes: "list[ShapeHandle]", linear_deflection: float = -1.0) -> "BatchMesh": ...
    def bbox(
        self, shape: ShapeHandle, optimal: bool = True, use_mesh: bool = False
    ) -> tuple[float, float, float, float, float, float]: ...
    def obb(self, shape: ShapeHandle) -> "tuple[tuple[float, float, float], tuple[float, float, float]]": ...
    def read_step_bytes(self, data: bytes) -> ShapeHandle: ...
    def write_glb_bytes(self, shape: ShapeHandle, linear_deflection: float = 0.1) -> bytes: ...
    def write_step(
        self, shapes: list, names: list, colors: list, filename: str, unit: str = "m", schema: str = "AP214"
    ) -> None: ...
    def is_handle(self, obj: Any) -> bool: ...
    def boolean(self, op: "BoolOpEnum", a: ShapeHandle, b: ShapeHandle) -> ShapeHandle: ...
    def transform(self, shape: ShapeHandle, matrix: "np.ndarray", copy: bool = True) -> ShapeHandle: ...
    def distance(self, a: ShapeHandle, b: ShapeHandle) -> float: ...
    def serialize(self, shape: ShapeHandle) -> str: ...
    def is_valid(self, shape: ShapeHandle) -> bool: ...
    def volume(self, shape: ShapeHandle) -> float: ...
    def area(self, shape: ShapeHandle) -> float: ...
    def shape_type(self, shape: ShapeHandle) -> str: ...
    def face_surface_type(self, shape: ShapeHandle) -> str: ...
    def extrude_face_along_normal(self, face: ShapeHandle, thickness: float) -> ShapeHandle: ...
    def face_to_advanced_face(self, shape: ShapeHandle): ...
    def build_bspline_advanced_face_from_grid(self, grid: "list", tol: float): ...
    def faces(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def solids(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def edges(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def vertex_points(self, shape: ShapeHandle) -> list[tuple[float, float, float]]: ...
    def face_plane(self, face: ShapeHandle) -> "tuple[Point, Direction] | None": ...
    def to_topods_pointer(self, shape: ShapeHandle) -> int: ...
    def face_id(self, shape: ShapeHandle) -> "int | None": ...
    def adopt_occ_shape(self, occ_shape: Any) -> ShapeHandle: ...
    def make_halfspace(self, origin, normal, flip: bool) -> ShapeHandle: ...
    def cut_surfaces(self, solid: ShapeHandle, cutters: list, deflection: float, tol: float) -> list: ...

    # --- topology-kernel verbs (the non-manifold core for ada.topology) ---
    def make_volumes_from_faces(self, faces: list[ShapeHandle], tolerance: float = 1e-6) -> list[ShapeHandle]: ...
    def merge_cells(self, solids: list[ShapeHandle], tolerance: float = 0.0) -> list[ShapeHandle]: ...
    def non_manifold_merge(
        self, shapes: list[ShapeHandle], tolerance: float = 1e-6, glue: bool = True
    ) -> ShapeHandle: ...
    def free_faces(self, solids: list[ShapeHandle]) -> list[ShapeHandle]: ...
    def point_in_solid(self, solid: ShapeHandle, point, tolerance: float = 1e-6) -> "Containment": ...
    def center_of_mass(self, shape: ShapeHandle) -> "Point": ...
    def shells(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def wires(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def wire_points(self, shape: ShapeHandle) -> list[tuple[float, float, float]]: ...
    def unify_coplanar_faces(self, shape: ShapeHandle) -> ShapeHandle: ...


class AdacppBackend:
    """Backend backed by adacpp.cad — works in native CPython AND pyodide.
    The wasm build is the only path that works under pyodide today; the
    native build links real OCCT and is functionally equivalent to OccBackend
    for the operations we support so far."""

    name = "adacpp"

    def __init__(self) -> None:
        from adacpp import cad

        self._cad = cad

    def build(self, geometry: "Geometry") -> ShapeHandle:
        # Native adacpp construction — NO pythonocc fallback. adacpp and the
        # pythonocc backend must work independently (adacpp also targets wasm,
        # where pythonocc does not exist). The ada.geom construction funnel is
        # being ported to adacpp C++ incrementally; types not yet ported raise
        # NotImplementedError rather than borrowing pythonocc. End goal: full
        # parity with OccBackend. See dap plan/v3 Phase 7.
        import ada.geom.curves as gcu
        import ada.geom.solids as so
        import ada.geom.surfaces as su
        from ada.api.beams.geom_beams import parametric_profile_to_arbitrary

        g = geometry.geometry

        def _axis(d, default):
            return list(d) if d is not None else list(default)

        def _arbitrary(area):
            # Parametric profile defs (I/T/...) -> buildable arbitrary outline. Shared with
            # the OCC backend; leaves ArbitraryProfileDef untouched.
            return parametric_profile_to_arbitrary(area) if isinstance(area, su.ProfileDef) else area

        if isinstance(g, so.Box):
            p = g.position
            shape = self._cad.build_box(
                list(p.location),
                _axis(p.axis, (0, 0, 1)),
                _axis(p.ref_direction, (1, 0, 0)),
                g.x_length,
                g.y_length,
                g.z_length,
            )
        elif isinstance(g, so.Cylinder):
            p = g.position
            shape = self._cad.build_cylinder(list(p.location), _axis(p.axis, (0, 0, 1)), g.radius, g.height)
        elif isinstance(g, so.Sphere):
            shape = self._cad.build_sphere(list(g.center), g.radius)
        elif isinstance(g, so.Cone):
            p = g.position
            shape = self._cad.build_cone(list(p.location), _axis(p.axis, (0, 0, 1)), g.bottom_radius, g.height)
        elif isinstance(g, so.ExtrudedAreaSolidTapered):
            # Must precede ExtrudedAreaSolid (subclass). Loft between the start
            # and end profiles' outer wires — matches OccBackend's
            # make_extruded_area_shape_tapered_from_geom (ThruSections).
            area = _arbitrary(g.swept_area)
            end_area = _arbitrary(g.end_swept_area)
            if not isinstance(area, su.ArbitraryProfileDef) or not isinstance(end_area, su.ArbitraryProfileDef):
                raise NotImplementedError(
                    f"AdacppBackend.build: ExtrudedAreaSolidTapered swept_area "
                    f"{type(area).__name__!r}/{type(end_area).__name__!r} not yet ported to adacpp."
                )
            p = g.position
            shape = self._cad.build_extruded_area_solid_tapered(
                self._encode_curve(area.outer_curve),
                self._encode_curve(end_area.outer_curve),
                self._xyz(p.location),
                _axis(p.axis, (0, 0, 1)),
                _axis(p.ref_direction, (1, 0, 0)),
                g.depth,
            )
        elif isinstance(g, so.ExtrudedAreaSolid):
            area = _arbitrary(g.swept_area)
            if not isinstance(area, su.ArbitraryProfileDef):
                raise NotImplementedError(
                    f"AdacppBackend.build: ExtrudedAreaSolid swept_area {type(area).__name__!r} "
                    f"not yet ported to adacpp."
                )
            is_area = area.profile_type == su.ProfileType.AREA
            outer = self._encode_curve(area.outer_curve)
            inners = [self._encode_curve(c) for c in area.inner_curves]
            p = g.position
            shape = self._cad.build_extruded_area_solid(
                outer,
                inners,
                self._xyz(p.location),
                _axis(p.axis, (0, 0, 1)),
                _axis(p.ref_direction, (1, 0, 0)),
                g.depth,
                is_area,
            )
        elif isinstance(g, so.RevolvedAreaSolid):
            area = _arbitrary(g.swept_area)
            if not isinstance(area, su.ArbitraryProfileDef):
                raise NotImplementedError(
                    f"AdacppBackend.build: RevolvedAreaSolid swept_area {type(area).__name__!r} "
                    f"not yet ported to adacpp."
                )
            is_area = area.profile_type == su.ProfileType.AREA
            outer = self._encode_curve(area.outer_curve)
            inners = [self._encode_curve(c) for c in area.inner_curves]
            p = g.position
            shape = self._cad.build_revolved_area_solid(
                outer,
                inners,
                self._xyz(p.location),
                _axis(p.axis, (0, 0, 1)),
                _axis(p.ref_direction, (1, 0, 0)),
                self._xyz(g.axis.location),
                _axis(g.axis.axis, (0, 0, 1)),
                float(g.angle),
                is_area,
            )
        elif isinstance(g, so.FixedReferenceSweptAreaSolid):
            area = _arbitrary(g.swept_area)
            if not isinstance(area, su.ArbitraryProfileDef):
                raise NotImplementedError(
                    f"AdacppBackend.build: FixedReferenceSweptAreaSolid swept_area "
                    f"{type(area).__name__!r} not yet ported to adacpp."
                )
            # MakePipeShell sweeps the profile *wire* (already positioned in 3D
            # at the directrix start) along the directrix spine.
            directrix = self._encode_curve(g.directrix)
            outer = self._encode_curve(area.outer_curve)
            shape = self._cad.build_fixed_reference_swept_area_solid(
                directrix,
                outer,
                self._xyz(g.position.location),
            )
        elif isinstance(g, su.HalfSpaceSolid):
            # Infinite half-space cutter (boolean second operand, e.g. an IFC
            # IfcHalfSpaceSolid clipping a beam). ``flip`` selects which side is the solid
            # material that DIFFERENCE removes; verified against OccBackend (apply_geom_
            # booleans) by solid-volume parity on half_space_beam -> flip == agreement_flag.
            plane = g.base_surface
            pos = plane.position
            shape = self._cad.make_halfspace(
                self._xyz(pos.location),
                _axis(pos.axis, (0, 0, 1)),
                bool(g.agreement_flag),
            )
        elif isinstance(g, su.CurveBoundedPlane):
            import ada.geom.curves as cu

            if not isinstance(g.outer_boundary, cu.IndexedPolyCurve):
                raise NotImplementedError(
                    f"AdacppBackend.build: CurveBoundedPlane outer_boundary "
                    f"{type(g.outer_boundary).__name__!r} not yet ported to adacpp."
                )
            outer = self._encode_curve(g.outer_boundary)
            inners = [self._encode_curve(c) for c in g.inner_boundaries]
            pos = g.basis_surface.position
            shape = self._cad.build_planar_face(
                outer,
                inners,
                self._xyz(pos.location),
                _axis(pos.axis, (0, 0, 1)),
                _axis(pos.ref_direction, (1, 0, 0)),
            )
        elif isinstance(g, su.FaceBasedSurfaceModel):
            import ada.geom.curves as cu

            polygons = []
            for cfs in g.fbsm_faces:
                for fb in cfs.cfs_faces:
                    if not isinstance(fb.bound, cu.PolyLoop):
                        raise NotImplementedError(
                            f"AdacppBackend.build: FaceBasedSurfaceModel bound "
                            f"{type(fb.bound).__name__!r} not yet ported to adacpp."
                        )
                    polygons.append([self._xyz(p) for p in fb.bound.polygon])
            shape = self._cad.build_face_based_surface_model(polygons)
        elif isinstance(g, (su.ShellBasedSurfaceModel, su.OpenShell, su.ClosedShell, su.ConnectedFaceSet)):
            # Sew the member faces into one shell handle. Each face is built through the
            # AdvancedFace path; open shells (IfcShellBasedSurfaceModel) don't bound a
            # volume, so sew_faces (BRepBuilderAPI_Sewing) is used rather than
            # make_volumes_from_faces. Mirrors OccBackend's make_shell_from_shell_based_
            # surface_geom / make_open_shell_from_geom. Bare ConnectedFaceSet is the
            # native NGEOM reader's B-rep root form (closedness not recorded in the
            # buffer; hydration promotes verified-closed sets to ClosedShell, the rest
            # arrive here) — same face-sew as the shells.
            from ada.geom import Geometry as _Geometry

            boundary_shells = g.sbsm_boundary if isinstance(g, su.ShellBasedSurfaceModel) else [g]
            face_handles = [self.build(_Geometry(geometry.id, face)) for sh in boundary_shells for face in sh.cfs_faces]
            if not face_handles:
                raise NotImplementedError("AdacppBackend.build: shell model has no faces")
            shape = self._cad.sew_faces(face_handles)
        elif isinstance(g, (su.AdvancedFace, su.FaceSurface)):
            # B-spline (PlateCurved / loft-derived / SAT) faces. With bounds, the
            # surface is trimmed to the boundary wire(s) — each OrientedEdge with
            # a supplied pcurve drives its edge from surface(pcurve(t)) (the
            # SAT-pcurve path of OccBackend.make_face_from_geom). Without bounds,
            # the natural-UV face. Analytic surfaces aren't ported yet.
            # FaceSurface is AdvancedFace's structurally-identical sibling (same
            # face_surface/bounds/same_sense, NOT a subclass) — it is what NGEOM
            # hydration yields for native-read B-rep faces.
            surf = g.face_surface
            if isinstance(surf, su.Plane) and g.bounds:
                # Planar AdvancedFace (flat SAT/IFC plates): plane inferred from the boundary
                # wire. The bspline boundary edge now carries start/end so it's trimmed to the
                # real segment (see _encode_oriented_edge), giving a correctly-bounded face.
                pos = surf.position
                shape = self._cad.build_advanced_face_planar(
                    self._xyz(pos.location),
                    _axis(pos.axis, (0, 0, 1)),
                    _axis(pos.ref_direction, (1, 0, 0)),
                    [self._encode_face_bound(fb) for fb in g.bounds],
                )
            elif (
                isinstance(surf, su.CylindricalSurface)
                and g.bounds
                and hasattr(self._cad, "build_advanced_face_cylindrical")
            ):
                # Cylindrical AdvancedFace (tube/pipe walls). hasattr-guarded so an
                # older ada-cpp without the builder falls through to NotImplementedError
                # below (callers tessellating tubes use ADAPY_CAD_BACKEND=occ); it
                # activates automatically once ada-cpp ships build_advanced_face_cylindrical.
                pos = surf.position
                shape = self._cad.build_advanced_face_cylindrical(
                    self._xyz(pos.location),
                    _axis(pos.axis, (0, 0, 1)),
                    _axis(pos.ref_direction, (1, 0, 0)),
                    float(surf.radius),
                    [self._encode_face_bound(fb) for fb in g.bounds],
                )
            elif isinstance(surf, su.ConicalSurface) and g.bounds and hasattr(self._cad, "build_advanced_face_conical"):
                # Cone AdvancedFace (e.g. PrimCone). hasattr-guarded like the cylinder path.
                pos = surf.position
                shape = self._cad.build_advanced_face_conical(
                    self._xyz(pos.location),
                    _axis(pos.axis, (0, 0, 1)),
                    _axis(pos.ref_direction, (1, 0, 0)),
                    float(surf.radius),
                    float(surf.semi_angle),
                    [self._encode_face_bound(fb) for fb in g.bounds],
                )
            elif (
                isinstance(surf, su.ToroidalSurface) and g.bounds and hasattr(self._cad, "build_advanced_face_toroidal")
            ):
                # Torus AdvancedFace (pipe elbows). hasattr-guarded like the cylinder path.
                pos = surf.position
                shape = self._cad.build_advanced_face_toroidal(
                    self._xyz(pos.location),
                    _axis(pos.axis, (0, 0, 1)),
                    _axis(pos.ref_direction, (1, 0, 0)),
                    float(surf.major_radius),
                    float(surf.minor_radius),
                    [self._encode_face_bound(fb) for fb in g.bounds],
                )
            elif (
                isinstance(surf, su.SurfaceOfRevolution)
                and g.bounds
                and hasattr(self._cad, "build_advanced_face_surface_of_revolution")
            ):
                # Revolution AdvancedFace (e.g. Ventilator contoured surfaces): revolve the
                # generatrix (B-spline meridian) about the axis, trim to bounds. libtess2 covers
                # this OCC-free; this is the OCC build path for B-rep export (ifc/step).
                ax = surf.axis_position
                shape = self._cad.build_advanced_face_surface_of_revolution(
                    self._xyz(ax.location),
                    _axis(ax.axis, (0, 0, 1)),
                    self._encode_generatrix(surf.swept_curve),
                    [self._encode_face_bound(fb) for fb in g.bounds],
                )
            elif (
                isinstance(surf, su.SurfaceOfLinearExtrusion)
                and g.bounds
                and hasattr(self._cad, "build_advanced_face_surface_of_linear_extrusion")
            ):
                # Linear-extrusion AdvancedFace: extrude the swept curve along the direction, trim
                # to bounds. (OCC-free via libtess2's SURF_LIN_EXTRUSION; OCC path for B-rep export.)
                shape = self._cad.build_advanced_face_surface_of_linear_extrusion(
                    _axis(surf.extrusion_direction, (0, 0, 1)),
                    self._encode_generatrix(surf.swept_curve),
                    [self._encode_face_bound(fb) for fb in g.bounds],
                )
            elif not isinstance(surf, su.BSplineSurfaceWithKnots):
                raise NotImplementedError(
                    f"AdacppBackend.build: AdvancedFace surface {type(surf).__name__!r} "
                    "not yet ported to adacpp (only BSplineSurfaceWithKnots / Plane)."
                )
            else:
                cps = [[self._xyz(p) for p in row] for row in surf.control_points_list]
                weights = list(surf.weights_data) if isinstance(surf, su.RationalBSplineSurfaceWithKnots) else []
                surf_args = (
                    surf.u_degree,
                    surf.v_degree,
                    cps,
                    list(surf.u_knots),
                    list(surf.v_knots),
                    list(surf.u_multiplicities),
                    list(surf.v_multiplicities),
                    weights,
                )
                if g.bounds:
                    bounds = [self._encode_face_bound(fb) for fb in g.bounds]
                    shape = self._cad.build_advanced_face_bspline(*surf_args, bounds)
                else:
                    shape = self._cad.build_bspline_surface_face(*surf_args)
        elif isinstance(g, su.Face) and not isinstance(g, su.FaceSurface):
            # Plain polygonal face (faceted-brep / PolyLoop bound) — a single planar polygon.
            import ada.geom.curves as cu

            bound = g.bounds[0].bound
            if not isinstance(bound, cu.PolyLoop):
                raise NotImplementedError(
                    f"AdacppBackend.build: Face bound {type(bound).__name__!r} not yet ported to adacpp."
                )
            shape = self._cad.polygon_face([self._xyz(p) for p in bound.polygon])
        elif isinstance(g, su.PolygonalFaceSet):
            # n-gon mesh: a planar face per (1-based) index loop, sewn into a shell.
            faces = [
                self._cad.polygon_face([self._xyz(g.coordinates[i - 1]) for i in face_idx]) for face_idx in g.faces
            ]
            shape = self._cad.sew_faces(faces)
        elif isinstance(g, su.TriangulatedFaceSet):
            # Triangle mesh (IfcTriangulatedFaceSet): one planar face per 1-based
            # index triple, sewn into a shell — same treatment as PolygonalFaceSet.
            idx = [int(i) for i in g.indices]
            faces = [
                self._cad.polygon_face([self._xyz(g.coordinates[i - 1]) for i in idx[k : k + 3]])
                for k in range(0, len(idx), 3)
            ]
            shape = self._cad.sew_faces(faces)
        elif isinstance(g, so.RectangularPyramid):
            # Rectangular base + 4 triangular sides (apex centred above), placed at the frame.
            x, y, z = g.x_length, g.y_length, g.z_length
            base = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0)]
            apex = (x / 2.0, y / 2.0, z)
            world = [self._to_world(g.position, p) for p in base] + [self._to_world(g.position, apex)]
            faces = [self._cad.polygon_face(world[:4])]
            for i in range(4):
                faces.append(self._cad.polygon_face([world[i], world[(i + 1) % 4], world[4]]))
            shape = self._cad.sew_faces(faces)
        elif isinstance(g, so.FacetedBrep):
            from ada.geom import Geometry as _Geometry
            from ada.geom.booleans import BoolOpEnum

            shape = self.build(_Geometry(geometry.id, g.outer))
            for void in g.voids:
                shape = self.boolean(BoolOpEnum.DIFFERENCE, shape, self.build(_Geometry(geometry.id, void)))
        elif isinstance(g, so.SweptDiskSolid):
            # Disk swept along the directrix; the native verb extracts the start frame and
            # builds the circular profile in C++ (robust for any directrix kind).
            directrix = self._encode_curve(g.directrix)
            shape = self._cad.build_swept_disk_solid(
                directrix, float(g.radius), float(g.inner_radius) if g.inner_radius else 0.0
            )
        elif isinstance(g, su.WireFilledFace):
            # Interpolate a smooth surface through the boundary edges
            # (BRepOffsetAPI_MakeFilling) — the SAT exppc fallback face.
            import ada.geom.curves as cu

            if not g.bounds:
                raise NotImplementedError("AdacppBackend.build: WireFilledFace has no bounds")
            bound = g.bounds[0].bound
            edge_list = bound.edge_list if isinstance(bound, cu.EdgeLoop) else []
            if len(edge_list) < 3:
                raise NotImplementedError("AdacppBackend.build: WireFilledFace needs >=3 boundary edges")
            shape = self._cad.build_filled_face([self._encode_oriented_edge(oe) for oe in edge_list])
        elif isinstance(g, gcu.CURVE_GEOM_TUPLE):
            # Bare curve bodies (sectionless SAT wire bodies, construction
            # wireframes): build the wire so B-rep exports carry them — STEP
            # writes wires natively (GEOMETRIC_CURVE_SET). Mirrors
            # geom_to_occ_geom's CURVE_GEOM_TUPLE arm.
            shape = self._cad.build_wire(self._encode_curve(g))
        else:
            raise NotImplementedError(
                f"AdacppBackend.build: ada.geom type {type(g).__name__!r} is not yet ported to "
                "adacpp (no pythonocc fallback by design). Use ADAPY_CAD_BACKEND=occ for it, or "
                "extend adacpp.cad."
            )

        # Apply booleans natively (operands built recursively in adacpp).
        for op in geometry.bool_operations:
            shape = self.boolean(op.operator, shape, self.build(op.second_operand))
        return shape

    @staticmethod
    def _xyz(p) -> list[float]:
        c = list(p)
        return [float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0]

    @staticmethod
    def _to_world(position, local) -> list[float]:
        """Transform a point in an Axis2Placement3D's local frame to world coordinates."""
        import numpy as np

        loc = np.asarray(list(position.location), dtype=float)
        z = np.asarray(list(position.axis) if position.axis is not None else (0, 0, 1), dtype=float)
        x = np.asarray(list(position.ref_direction) if position.ref_direction is not None else (1, 0, 0), dtype=float)
        z = z / (np.linalg.norm(z) or 1.0)
        x = x / (np.linalg.norm(x) or 1.0)
        y = np.cross(z, x)
        lx, ly, lz = local
        w = loc + lx * x + ly * y + lz * z
        return [float(v) for v in w]

    @staticmethod
    def _arc_midpoint(circle, p_start, p_end, sense: bool = True) -> list[float]:
        """A point on ``circle`` at the angle bisecting start→end (along ``sense``) — the
        midpoint an arc edge record (kind 1) needs."""
        import numpy as np

        c = np.asarray(list(circle.position.location), dtype=float)
        z = np.asarray(list(circle.position.axis), dtype=float)
        x = np.asarray(list(circle.position.ref_direction), dtype=float)
        z = z / (np.linalg.norm(z) or 1.0)
        x = x / (np.linalg.norm(x) or 1.0)
        y = np.cross(z, x)
        r = float(circle.radius)

        def ang(p):
            d = np.asarray(list(p), dtype=float) - c
            return np.arctan2(float(d @ y), float(d @ x))

        a0, a1 = ang(p_start), ang(p_end)
        if sense and a1 <= a0:
            a1 += 2.0 * np.pi
        elif not sense and a1 >= a0:
            a1 -= 2.0 * np.pi
        am = 0.5 * (a0 + a1)
        m = c + r * (np.cos(am) * x + np.sin(am) * y)
        return [float(v) for v in m]

    def _encode_curve(self, curve) -> list[list[float]]:
        # Encode an ada.geom profile curve as adacpp edge records:
        #   line=[0, p1, p2], arc=[1, start, mid, end], circle=[2, centre, axis, r].
        import ada.geom.curves as cu

        if isinstance(curve, cu.IndexedPolyCurve):
            edges = []
            for seg in curve.segments:
                if isinstance(seg, cu.ArcLine):
                    edges.append([1.0, *self._xyz(seg.start), *self._xyz(seg.midpoint), *self._xyz(seg.end)])
                else:  # Edge — straight line
                    edges.append([0.0, *self._xyz(seg.start), *self._xyz(seg.end)])
            return edges
        if isinstance(curve, cu.Circle):
            axis = self._xyz(curve.position.axis) if curve.position.axis is not None else [0.0, 0.0, 1.0]
            return [[2.0, *self._xyz(curve.position.location), *axis, float(curve.radius)]]
        if isinstance(curve, cu.Edge) and not isinstance(curve, cu.ArcLine):
            # Bare line segment (sectionless SAT wire body).
            return [[0.0, *self._xyz(curve.start), *self._xyz(curve.end)]]
        if isinstance(curve, cu.ArcLine):
            return [[1.0, *self._xyz(curve.start), *self._xyz(curve.midpoint), *self._xyz(curve.end)]]
        if isinstance(curve, cu.PolyLine):
            pts = curve.points
            return [[0.0, *self._xyz(a), *self._xyz(b)] for a, b in zip(pts[:-1], pts[1:])]
        if isinstance(curve, cu.TrimmedCurve):
            import numpy as _np

            from ada.geom.points import Point as _Point

            b, t1, t2 = curve.basis_curve, curve.trim1, curve.trim2
            if isinstance(b, cu.Line):
                # Parameter trims evaluate on P(t) = pnt + t*dir — the reader
                # keeps the IfcVector magnitude in ``dir`` so t is unscaled.
                def _line_pt(t):
                    if isinstance(t, _Point):
                        return self._xyz(t)
                    p = _np.asarray(self._xyz(b.pnt), dtype=float)
                    d = _np.asarray(self._xyz(b.dir), dtype=float)
                    return [float(v) for v in p + float(t) * d]

                return [[0.0, *_line_pt(t1), *_line_pt(t2)]]
            if isinstance(b, cu.Circle):
                # Parameter trims are angles (radians, normalized at read) in
                # the circle's own x/y frame — the same frame _arc_midpoint
                # measures in, so sense/wrap handling is shared.
                def _circle_pt(t):
                    if isinstance(t, _Point):
                        return self._xyz(t)
                    c = _np.asarray(self._xyz(b.position.location), dtype=float)
                    z = _np.asarray(
                        self._xyz(b.position.axis) if b.position.axis is not None else [0, 0, 1], dtype=float
                    )
                    x = _np.asarray(
                        self._xyz(b.position.ref_direction) if b.position.ref_direction is not None else [1, 0, 0],
                        dtype=float,
                    )
                    z = z / (_np.linalg.norm(z) or 1.0)
                    x = x / (_np.linalg.norm(x) or 1.0)
                    y = _np.cross(z, x)
                    w = c + float(b.radius) * (_np.cos(float(t)) * x + _np.sin(float(t)) * y)
                    return [float(v) for v in w]

                p1, p2 = _circle_pt(t1), _circle_pt(t2)
                mid = self._arc_midpoint(b, p1, p2, curve.sense_agreement)
                return [[1.0, *p1, *mid, *p2]]
            if isinstance(b, cu.Ellipse):
                # adacpp has no ellipse edge record — sample the trimmed arc
                # (parametric angle; trims normalized to radians at read) into
                # a fine polyline. Profile use only, so chord error at 64
                # segments/full-turn is well under tessellation deflection.
                c = _np.asarray(self._xyz(b.position.location), dtype=float)
                z = _np.asarray(self._xyz(b.position.axis) if b.position.axis is not None else [0, 0, 1], dtype=float)
                x = _np.asarray(
                    self._xyz(b.position.ref_direction) if b.position.ref_direction is not None else [1, 0, 0],
                    dtype=float,
                )
                z = z / (_np.linalg.norm(z) or 1.0)
                x = x / (_np.linalg.norm(x) or 1.0)
                y = _np.cross(z, x)
                sa1, sa2 = float(b.semi_axis1), float(b.semi_axis2)

                def _param_of(t):
                    if not isinstance(t, _Point):
                        return float(t)
                    d = _np.asarray(self._xyz(t), dtype=float) - c
                    return float(_np.arctan2(float(d @ y) / sa2, float(d @ x) / sa1))

                a0, a1 = _param_of(t1), _param_of(t2)
                if curve.sense_agreement and a1 <= a0:
                    a1 += 2.0 * _np.pi
                elif not curve.sense_agreement and a1 >= a0:
                    a1 -= 2.0 * _np.pi
                n = max(8, int(abs(a1 - a0) / (2.0 * _np.pi) * 64))
                ts = _np.linspace(a0, a1, n + 1)
                pts = [c + sa1 * _np.cos(t) * x + sa2 * _np.sin(t) * y for t in ts]
                return [
                    [0.0, *[float(v) for v in p], *[float(v) for v in q]] for p, q in zip(pts[:-1], pts[1:])
                ]
            raise NotImplementedError(
                f"AdacppBackend.build: TrimmedCurve basis {type(b).__name__!r} not yet ported to adacpp."
            )
        if isinstance(curve, cu.CompositeCurve):
            edges = []
            for seg in curve.segments:
                edges.extend(self._encode_curve(seg.parent_curve))
            return edges
        if isinstance(curve, cu.GeometricCurveSet):
            # Loose curve collection (STEP wireframe body) — concatenate the
            # member encodings; the records stay independent edges.
            edges = []
            for element in curve.elements:
                edges.extend(self._encode_curve(element))
            return edges
        if isinstance(curve, cu.GradientCurve):
            # Alignment directrix (IFC4x3): clothoid segments have no analytic
            # edge record — encode the sampled polyline (shared with the
            # NGEOM SweepN tessellation path, which uses the same evaluator).
            import numpy as _np

            from ada.cadit.ngeom._alignment_sweep import gradient_curve_points

            pts = gradient_curve_points(curve, n_per=100)
            return [
                [0.0, *[float(v) for v in a], *[float(v) for v in b]]
                for a, b in zip(pts[:-1], pts[1:])
                if float(_np.linalg.norm(b - a)) > 1e-9
            ]
        raise NotImplementedError(
            f"AdacppBackend.build: profile curve {type(curve).__name__!r} not yet ported to adacpp."
        )

    def _encode_generatrix(self, curve) -> list[float]:
        """Encode a bare generatrix/swept curve (the meridian of a SurfaceOfRevolution or
        the swept curve of a SurfaceOfLinearExtrusion) as a single adacpp curve record
        (geom_curve_from_record layout). Full/untrimmed — the face bounds trim the surface.
        B-spline is the STEP case; circle handled too. Mirrors _encode_oriented_edge's curve arms."""
        import ada.geom.curves as cu

        if isinstance(curve, (cu.BSplineCurveWithKnots, cu.RationalBSplineCurveWithKnots)):
            poles = [self._xyz(p) for p in curve.control_points_list]
            knots = [float(k) for k in curve.knots]
            mults = [float(m) for m in curve.knot_multiplicities]
            rational = isinstance(curve, cu.RationalBSplineCurveWithKnots)
            # [kind=3, degree, rational, trim=0, t0, t1, pstart(3), pend(3), n_poles, poles..., n_knots, knots, mults, weights?]
            rec: list[float] = [
                3.0,
                float(curve.degree),
                1.0 if rational else 0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                float(len(poles)),
            ]
            for p in poles:
                rec += [float(p[0]), float(p[1]), float(p[2])]
            rec += [float(len(knots))] + knots + mults
            if rational:
                rec += [float(w) for w in curve.weights_data]
            return rec
        if isinstance(curve, cu.Circle):
            pos = curve.position
            return [2.0, *self._xyz(pos.location), *self._xyz(pos.axis), float(curve.radius)]
        raise NotImplementedError(
            f"AdacppBackend: surface generatrix {type(curve).__name__!r} not yet ported to adacpp"
        )

    def _encode_oriented_edge(self, oe) -> list[float]:
        """Encode an OrientedEdge / Edge as an adacpp edge record (the layout
        adacpp's edge_from_record consumes). Mirrors OccBackend's
        make_edge_from_edge: line / circle (full|trimmed) / ellipse / B-spline
        (rational, full|trimmed). Straight-line fallback when the underlying
        3D curve geometry isn't one of those."""
        import ada.geom.curves as cu

        start, end = self._xyz(oe.start), self._xyz(oe.end)
        closed = all(abs(a - b) <= 1e-6 for a, b in zip(start, end))
        t_start = getattr(oe, "t_start", None)
        t_end = getattr(oe, "t_end", None)
        has_trim = t_start is not None and t_end is not None and not closed

        ee = getattr(oe, "edge_element", None)
        curve = ee.edge_geometry if isinstance(ee, cu.EdgeCurve) else None

        if isinstance(curve, cu.Circle):
            pos = curve.position
            loc, axis = self._xyz(pos.location), self._xyz(pos.axis)
            # ref_direction is the circle's angular origin (param 0). It MUST be carried so the
            # arc/closed-circle vertices land where the adjacent edges (e.g. a cylinder/torus
            # seam line) attach — without it adacpp placed them at OCC's default x-axis and the
            # boundary wire wouldn't close ("wire build failed"). Mirrors the Ellipse branch.
            ref = self._xyz(pos.ref_direction) if pos.ref_direction is not None else [1.0, 0.0, 0.0]
            r = float(curve.radius)
            if closed:
                # Full circle: anchor the edge vertex at the start point so a seam connects there.
                return [2.0, *loc, *axis, *ref, r, *start]
            if has_trim:
                return [5.0, *loc, *axis, *ref, r, float(t_start), float(t_end)]
            # No explicit trim: recover the arc's angular extent from the endpoints (CCW from
            # start to end, matching OccBackend's two-point arc). WITHOUT this the arc collapsed
            # to a chord ([0, start, end]) → the face lost the surface and BRepMesh tessellated it
            # flat (a cylinder wall meshed toward its axis). Emitting the real arc keeps the
            # boundary on the cylinder so the analytic face meshes correctly on the adacpp backend.
            t0 = _circle_param(start, loc, axis, ref)
            t1 = _circle_param(end, loc, axis, ref)
            while t1 <= t0 + 1e-12:
                t1 += 2.0 * math.pi
            return [5.0, *loc, *axis, *ref, r, t0, t1]
        if isinstance(curve, cu.Ellipse):
            pos = curve.position
            loc, axis, ref = self._xyz(pos.location), self._xyz(pos.axis), self._xyz(pos.ref_direction)
            s1, s2 = float(curve.semi_axis1), float(curve.semi_axis2)
            if closed:
                return [4.0, *loc, *axis, *ref, s1, s2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            return [4.0, *loc, *axis, *ref, s1, s2, 1.0, *start, *end]
        if isinstance(curve, (cu.BSplineCurveWithKnots, cu.RationalBSplineCurveWithKnots)):
            poles = [self._xyz(p) for p in curve.control_points_list]
            knots = [float(k) for k in curve.knots]
            mults = [float(m) for m in curve.knot_multiplicities]
            rational = isinstance(curve, cu.RationalBSplineCurveWithKnots)
            rec = [
                3.0,
                float(curve.degree),
                1.0 if rational else 0.0,
                1.0 if has_trim else 0.0,
                float(t_start or 0.0),
                float(t_end or 0.0),
                # start/end points: when the edge carries no parametric trim, the curve is a
                # full b-spline and these points define the segment to keep — adacpp trims by
                # projecting them onto the curve (mirrors OccBackend.make_edge_from_edge).
                *start,
                *end,
                float(len(poles)),
            ]
            for p in poles:
                rec += p
            rec += [float(len(knots)), *knots, *mults]
            if rational:
                rec += [float(w) for w in curve.weights_data]
            return rec
        # Line, no geometry, or unsupported → straight segment.
        return [0.0, *start, *end]

    @staticmethod
    def _encode_pcurve(
        pc,
        t_start: float | None = None,
        t_end: float | None = None,
        p_start=None,
        p_end=None,
    ) -> list[float]:
        """Encode a Pcurve2dBSpline (2D UV curve on the face surface) as a
        kind-6 edge record for adacpp.cad.build_advanced_face_bspline.

        ``t_start``/``t_end`` are the owning edge's parametric trim on the
        underlying curve; ``p_start``/``p_end`` its declared 3D vertices. SAT
        pcurves typically span the FULL underlying curve, not just the edge's
        segment — without a trim adacpp built the edge over the whole pcurve,
        the wire endpoints landed metres apart and the face failed with "wire
        build failed". The t-params alone aren't enough either: an ACIS bs2
        pcurve is a fit approximation with its OWN parameterization, so the
        edge's curve-params land slightly off on the pcurve (cm-scale 3D error
        on this data). The 3D vertices let adacpp trim geometrically
        (point → surface UV → pcurve param). Appended as an optional
        [2.0, t0, t1, sx, sy, sz, ex, ey, ez] tail (flag-detected,
        back-compatible; flag 1.0 = params-only legacy tail)."""
        cps = pc.control_points_2d
        knots = [float(k) for k in pc.knots]
        mults = [float(m) for m in pc.knot_multiplicities]
        rational = bool(pc.weights)
        rec = [6.0, float(pc.degree), 1.0 if rational else 0.0, 1.0 if pc.closed else 0.0, float(len(cps))]
        for cp in cps:
            rec += [float(cp[0]), float(cp[1])]
        rec += [float(len(knots)), *knots, *mults]
        if rational:
            rec += [float(w) for w in pc.weights]
        if t_start is not None and t_end is not None:
            if p_start is not None and p_end is not None:
                rec += [2.0, float(t_start), float(t_end)]
                rec += [float(c) for c in list(p_start)[:3]] + [float(c) for c in list(p_end)[:3]]
            else:
                rec += [1.0, float(t_start), float(t_end)]
        return rec

    def _encode_face_bound(self, fb) -> list[list[float]]:
        """Encode a FaceBound's loop as adacpp edge records. An EdgeLoop maps each
        OrientedEdge (pcurve → kind-6, else its 3D edge); a PolyLoop (a polygon of
        points — how the analytic flat faces + their hole loops arrive) maps to straight
        line edges between consecutive points, closing the loop. Without the PolyLoop arm
        the bound encoded to an EMPTY edge list and build_advanced_face_planar failed the
        wire build (flat faces-with-holes were unbuildable on the adacpp backend)."""
        import ada.geom.curves as cu

        bound = fb.bound
        if isinstance(bound, cu.PolyLoop):
            pts = [self._xyz(p) for p in bound.polygon]
            n = len(pts)
            return [[0.0, *pts[i], *pts[(i + 1) % n]] for i in range(n)]
        edge_list = bound.edge_list if isinstance(bound, cu.EdgeLoop) else []
        out = []
        for oe in edge_list:
            pc = getattr(oe, "pcurve", None)
            if pc is not None:
                out.append(
                    self._encode_pcurve(
                        pc,
                        getattr(oe, "t_start", None),
                        getattr(oe, "t_end", None),
                        getattr(oe, "start", None),
                        getattr(oe, "end", None),
                    )
                )
            else:
                out.append(self._encode_oriented_edge(oe))
        return out

    def make_wire(self, points: "list") -> ShapeHandle:
        return self._cad.make_wire([[float(c) for c in self._xyz(p)] for p in points])

    def polygon_face(self, points: "list") -> ShapeHandle:
        return self._cad.polygon_face([[float(c) for c in self._xyz(p)] for p in points])

    def loft_profiles(
        self, profiles: "list[list[tuple[float, float, float]]]", ruled: bool = True, solid: bool = True
    ) -> ShapeHandle:
        return self._cad.loft_profiles(
            [[[float(c) for c in self._xyz(p)] for p in prof] for prof in profiles], ruled, solid
        )

    def section_with_plane(self, shape: ShapeHandle, origin, normal, size: float = 1000.0) -> ShapeHandle:
        return self._cad.section_with_plane(shape, self._xyz(origin), list(normal), float(size))

    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle:
        return self._cad.make_box(dx, dy, dz)

    def make_cylinder(self, radius: float, height: float) -> ShapeHandle:
        return self._cad.make_cylinder(radius, height)

    def make_sphere(self, radius: float) -> ShapeHandle:
        return self._cad.make_sphere(radius)

    def tessellate(self, shape: ShapeHandle, linear_deflection: float = -1.0) -> Mesh:
        return self._cad.tessellate(shape, linear_deflection)

    def tessellate_batch(self, shapes: "list[ShapeHandle]", linear_deflection: float = -1.0) -> "BatchMesh":
        # Native single-call batch when the loaded ada-cpp build provides it
        # (returns one combined Mesh with a GroupReference per shape); otherwise
        # fall back to the per-shape loop so the API works on older builds.
        fn = getattr(self._cad, "tessellate_batch", None)
        if fn is None:
            return tessellate_batch_via_loop(self, shapes, linear_deflection)
        import numpy as np

        mesh = fn(list(shapes), linear_deflection)
        groups = [
            MeshGroup(node_id=g.node_id, start=g.start, length=g.length, vstart=g.vstart, vlength=g.vlength)
            for g in mesh.groups
        ]
        nrm = np.asarray(mesh.normals)
        return BatchMesh(
            positions=np.asarray(mesh.positions),
            indices=np.asarray(mesh.indices),
            groups=groups,
            normals=nrm if nrm.size else None,
        )

    def ifc_taxonomy_settings(self) -> "list[dict]":
        """Enumerate the ifcopenshell taxonomy ConversionSettings exposed by
        adacpp as ``[{name, type, default}, ...]`` — for tuning the occ/cgal/
        hybrid kernels (and for the frontend to render dynamically). Empty when
        the adacpp build predates the settings interface."""
        fn = getattr(self._cad, "ifc_taxonomy_settings", None)
        return list(fn()) if fn is not None else []

    def tessellate_stream(
        self,
        items: "list[tuple[str, object]]",
        pipeline: str = "libtess2",
        deflection: float = 0.0,
        angular_deg: float = DEFAULT_STREAM_TESS_ANGULAR_DEG,
        settings: "dict | None" = None,
        threads: int = 1,
    ) -> "BatchMesh":
        """Tessellate a stream of ``(id, ada.geom geometry)`` via adacpp's NGEOM pipeline.

        Serializes the geometry to the NGEOM buffer (the neutral-schema contract — no
        per-object ``build``/ShapeHandle round-trip) and tessellates it in one C++ call,
        returning a combined ``BatchMesh`` with a group per input id (``node_id`` = the
        item's position). ``pipeline``: ``libtess2`` (OCC-free) | ``occ`` | ``cgal``
        (ifcopenshell taxonomy kernels). ``geometry`` is an ``ada.geom`` solid/face-set,
        or a ``core.Geometry`` wrapper — the wrapper's ``bool_operations`` are folded
        into the buffer as a BOOLEAN_RESULT chain (unmappable items are skipped)."""
        from ada.cadit.ngeom import serialize_geometries

        return self.tessellate_stream_buffer(
            serialize_geometries(items),
            pipeline=pipeline,
            deflection=deflection,
            angular_deg=angular_deg,
            settings=settings,
            threads=threads,
        )

    def tessellate_stream_buffer(
        self,
        buffer,
        *,
        pipeline: str = "libtess2",
        deflection: float = 0.0,
        angular_deg: float = DEFAULT_STREAM_TESS_ANGULAR_DEG,
        settings: "dict | None" = None,
        threads: int = 1,
    ) -> "BatchMesh":
        """Tessellate a pre-encoded NGEOM buffer — the fast path for blobs already in
        the neutral form (a lazy ``ShapeStore``'s stored solid, a cached ``.ngeom``
        file): no hydration and no re-serialization, the buffer goes straight to the
        C++ kernel."""
        fn = getattr(self._cad, "tessellate_stream", None)
        if fn is None:
            raise NotImplementedError(
                "this adacpp build has no tessellate_stream — rebuild adacpp (feat/libtess2-tessellator)"
            )
        import numpy as np

        if not isinstance(buffer, (bytes, bytearray)):
            # adacpp's binding currently takes nb::bytes only; drop this coercion once
            # the buffer-protocol/zero-copy adacpp change lands.
            buffer = bytes(buffer)
        # ``settings`` overrides the ifcopenshell ConversionSettings for the taxonomy paths
        # (occ/cgal/hybrid); ignored by libtess2. ``threads`` (>1) parallelises a root's faces
        # in the libtess2 path — opt-in, so the STEP->GLB process pool (which parallelises across
        # solids) stays serial per call and doesn't oversubscribe. The signature grew
        # (settings, then threads); try the fullest form and fall back for older adacpp builds.
        try:
            mesh = fn(buffer, pipeline, deflection, angular_deg, dict(settings or {}), int(threads))
        except TypeError:
            if int(threads) > 1:
                logger.debug("adacpp build has no tessellate_stream threads param; running serial")
            if settings:
                try:
                    mesh = fn(buffer, pipeline, deflection, angular_deg, dict(settings))
                except TypeError:
                    logger.warning("adacpp build has no taxonomy settings param; ignoring %r", settings)
                    mesh = fn(buffer, pipeline, deflection, angular_deg)
            else:
                mesh = fn(buffer, pipeline, deflection, angular_deg)
        groups = [
            MeshGroup(node_id=g.node_id, start=g.start, length=g.length, vstart=g.vstart, vlength=g.vlength)
            for g in mesh.groups
        ]
        nrm = np.asarray(mesh.normals)
        return BatchMesh(
            positions=np.asarray(mesh.positions),
            indices=np.asarray(mesh.indices),
            groups=groups,
            normals=nrm if nrm.size else None,
        )

    def bbox(
        self, shape: ShapeHandle, optimal: bool = True, use_mesh: bool = False
    ) -> tuple[float, float, float, float, float, float]:
        # adacpp.cad.bbox honors `optimal` (optimal=False = fast loose Add box,
        # skipping AddOptimal's per-face BSpline refinement — for rough-extent
        # probes like the empty-body guard). use_mesh doesn't apply (analytic).
        # Older adacpp builds without the `optimal` param fall back to their
        # default (tight) box.
        try:
            return tuple(self._cad.bbox(shape, optimal=optimal))
        except TypeError:
            return tuple(self._cad.bbox(shape))

    def obb(self, shape: ShapeHandle) -> "tuple[tuple[float, float, float], tuple[float, float, float]]":
        fn = getattr(self._cad, "obb", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.obb is not available in this build")
        # adacpp returns (center3, half_dims3) mirroring brepbndlib::AddOBB.
        center, half_dims = fn(shape)
        return tuple(center), tuple(half_dims)

    def read_step_bytes(self, data: bytes) -> ShapeHandle:
        return self._cad.read_step_bytes(data)

    def write_glb_bytes(self, shape: ShapeHandle, linear_deflection: float = 0.1) -> bytes:
        # adacpp returns nb::bytes; bytes(...) coerces it cleanly to a CPython
        # bytes object so callers don't need to know about the underlying type.
        return bytes(self._cad.write_glb_bytes(shape, linear_deflection))

    def serialize_brep(self, shape: ShapeHandle) -> str:
        # OCCT BRepTools_ShapeSet text — the BREP string ifcopenshell.geom.serialise
        # consumes for the IFC tessellation fallback. Lets *→ifc work under the
        # adacpp backend / wasm (ifcopenshell's wasm wheel ships no occ_utils).
        return self._cad.serialize_brep(shape)

    def write_step(
        self, shapes: list, names: list, colors: list, filename: str, unit: str = "m", schema: str = "AP214"
    ) -> None:
        # OCAF/XCAF STEP write via adacpp's bundled OCCT — no pythonocc needed.
        rgb = [[float(c[0]), float(c[1]), float(c[2])] for c in colors]
        self._cad.write_step(list(shapes), [str(n) for n in names], rgb, str(filename), unit, schema)

    def is_handle(self, obj: Any) -> bool:
        # Recognise an adacpp-native shape so callers can keep handle-type
        # introspection out of their own code (e.g. the tessellator's
        # raw-import fast path). adacpp.cad exposes the concrete type as
        # `ShapeHandle`; if a build omits it we conservatively report False.
        handle_t = getattr(self._cad, "ShapeHandle", None)
        return handle_t is not None and isinstance(obj, handle_t)

    def boolean(self, op: "BoolOpEnum", a: ShapeHandle, b: ShapeHandle) -> ShapeHandle:
        fn = getattr(self._cad, "boolean", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.boolean is not available in this build")
        return fn(op.value, a, b)

    def _occ_fallback_for(self, shape: ShapeHandle):
        # A raw pyOCC TopoDS shape (e.g. produced by a STEP/OCC fallback path while adacpp is the active
        # backend) belongs to a different OCC instance than adacpp's embedded one and can't be passed to
        # adacpp.cad.* — return the OCC backend to route it through OCC instead. None => an adacpp shape.
        if not type(shape).__module__.startswith("OCC."):
            return None
        if getattr(self, "_occ_bk", None) is None:
            from ada.occ.backend import OccBackend

            self._occ_bk = OccBackend()
        return self._occ_bk

    def transform(self, shape: ShapeHandle, matrix: "np.ndarray", copy: bool = True) -> ShapeHandle:
        occ = self._occ_fallback_for(shape)
        if occ is not None:
            return occ.transform(shape, matrix, copy)
        fn = getattr(self._cad, "transform", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.transform is not available in this build")
        # adacpp.cad.transform takes the top 3 rows of the 4x4 as 12 row-major
        # doubles (implicit bottom row [0,0,0,1]).
        m = [float(matrix[i][j]) for i in range(3) for j in range(4)]
        return fn(shape, m, copy)

    def distance(self, a: ShapeHandle, b: ShapeHandle) -> float:
        fn = getattr(self._cad, "distance", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.distance is not available in this build")
        return fn(a, b)

    def serialize(self, shape: ShapeHandle) -> str:
        fn = getattr(self._cad, "serialize", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.serialize is not available in this build")
        return fn(shape)

    def is_valid(self, shape: ShapeHandle) -> bool:
        fn = getattr(self._cad, "is_valid", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.is_valid is not available in this build")
        return fn(shape)

    def volume(self, shape: ShapeHandle) -> float:
        fn = getattr(self._cad, "volume", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.volume is not available in this build")
        return fn(shape)

    def area(self, shape: ShapeHandle) -> float:
        fn = getattr(self._cad, "area", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.area is not available in this build")
        return fn(shape)

    def shape_type(self, shape: ShapeHandle) -> str:
        occ = self._occ_fallback_for(shape)
        if occ is not None:
            return occ.shape_type(shape)
        fn = getattr(self._cad, "shape_type", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.shape_type is not available in this build")
        return fn(shape)

    def face_surface_type(self, shape: ShapeHandle) -> str:
        occ = self._occ_fallback_for(shape)
        if occ is not None:
            return occ.face_surface_type(shape)
        fn = getattr(self._cad, "face_surface_type", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.face_surface_type is not available in this build")
        return fn(shape)

    def extrude_face_along_normal(self, face: ShapeHandle, thickness: float) -> ShapeHandle:
        fn = getattr(self._cad, "extrude_face_along_normal", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.extrude_face_along_normal is not available in this build")
        return fn(face, float(thickness))

    def build_bspline_advanced_face_from_grid(self, grid: "list", tol: float):
        # Native grid→NURBS fit not yet ported to adacpp. Raising here makes the
        # surface-reconstruction caller fall back to flat plates (the safe
        # default) rather than borrowing pythonocc. See dap plan/v3 Phase 7.
        raise NotImplementedError("adacpp.cad grid→bspline surface fit is not available yet")

    def face_to_advanced_face(self, shape: ShapeHandle):
        """Decompose a B-spline face handle into an ada.geom AdvancedFace
        (surface + FaceBound/EdgeLoop/OrientedEdge with supplied pcurves) —
        reconstructed from adacpp's AdvancedFaceData. Inverse of build()."""
        occ = self._occ_fallback_for(shape)
        if occ is not None:
            return occ.face_to_advanced_face(shape)
        import ada.geom.curves as cu
        import ada.geom.surfaces as su
        from ada.geom.points import Point

        fn = getattr(self._cad, "face_to_advanced_face", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.face_to_advanced_face is not available in this build")
        d = fn(shape)

        common = dict(
            u_degree=d.u_degree,
            v_degree=d.v_degree,
            control_points_list=[[Point(*p) for p in row] for row in d.poles],
            surface_form=su.BSplineSurfaceForm.UNSPECIFIED,
            u_closed=d.u_closed,
            v_closed=d.v_closed,
            self_intersect=False,
            u_multiplicities=list(d.u_multiplicities),
            v_multiplicities=list(d.v_multiplicities),
            u_knots=list(d.u_knots),
            v_knots=list(d.v_knots),
            knot_spec=cu.KnotType.UNSPECIFIED,
        )
        if d.weights:
            surface = su.RationalBSplineSurfaceWithKnots(weights_data=[list(r) for r in d.weights], **common)
        else:
            surface = su.BSplineSurfaceWithKnots(**common)

        bounds = []
        for wire in d.bounds:
            edges = []
            for pc in wire:
                s, e = Point(*pc.start), Point(*pc.end)
                pcurve = None
                if pc.has_pcurve:
                    pcurve = cu.Pcurve2dBSpline(
                        degree=pc.degree,
                        control_points_2d=[tuple(c) for c in pc.control_points],
                        knots=list(pc.knots),
                        knot_multiplicities=list(pc.multiplicities),
                        weights=list(pc.weights) or None,
                        closed=pc.closed,
                    )
                # edge_element is a placeholder 3D edge — the rebuild path uses
                # the pcurve when present (matches OccBackend's drive-from-pcurve).
                oe = cu.OrientedEdge(s, e, edge_element=cu.Edge(s, e), orientation=True, pcurve=pcurve)
                edges.append(oe)
            bounds.append(su.FaceBound(bound=cu.EdgeLoop(edge_list=edges), orientation=True))
        return su.AdvancedFace(bounds=bounds, face_surface=surface)

    def faces(self, shape: ShapeHandle) -> list[ShapeHandle]:
        occ = self._occ_fallback_for(shape)
        if occ is not None:
            return occ.faces(shape)
        fn = getattr(self._cad, "faces", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.faces is not available in this build")
        return list(fn(shape))

    def solids(self, shape: ShapeHandle) -> list[ShapeHandle]:
        fn = getattr(self._cad, "solids", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.solids is not available in this build")
        return list(fn(shape))

    def edges(self, shape: ShapeHandle) -> list[ShapeHandle]:
        fn = getattr(self._cad, "edges", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.edges is not available in this build")
        return list(fn(shape))

    def to_topods_pointer(self, shape: ShapeHandle) -> int:
        fn = getattr(self._cad, "to_topods_pointer", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.to_topods_pointer is not available in this build")
        return fn(shape)

    def face_id(self, shape: ShapeHandle) -> "int | None":
        # Orientation-independent topological identity (see OccBackend.face_id).
        # Returns None when the native build lacks it, so the cell-graph extractor
        # falls back to geometric face matching.
        fn = getattr(self._cad, "face_id", None)
        return fn(shape) if fn is not None else None

    def vertex_points(self, shape: ShapeHandle) -> list[tuple[float, float, float]]:
        fn = getattr(self._cad, "vertex_points", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.vertex_points is not available in this build")
        return [tuple(p) for p in fn(shape)]

    def face_plane(self, face: ShapeHandle) -> "tuple[Point, Direction] | None":
        fn = getattr(self._cad, "face_plane", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.face_plane is not available in this build")
        res = fn(face)
        if res is None:
            return None
        # adacpp returns ((ox,oy,oz),(nx,ny,nz)); wrap into ada.geom types to
        # match OccBackend.face_plane.
        from ada.geom.direction import Direction
        from ada.geom.points import Point

        origin, normal = res
        return Point(*origin), Direction(*normal)

    def from_topods_pointer(self, ptr: int) -> ShapeHandle:
        """Wrap an OCCT TopoDS_Shape addressed by a raw pointer.
        Native-only; wasm builds do not expose this — adacpp.cad surface
        omits the function entirely there."""
        bridge = getattr(self._cad, "from_topods_pointer", None)
        if bridge is None:
            raise NotImplementedError(
                "from_topods_pointer is unavailable in this adacpp build "
                "(typical for wasm/pyodide — no OCCT to bridge to)"
            )
        return bridge(ptr)

    def adopt_occ_shape(self, occ_shape: Any) -> ShapeHandle:
        """Bring a raw pythonocc-core TopoDS_Shape (produced by the OCC
        DocBackend's STEP/SAT reader) into an adacpp handle. Safe because both
        kernels are the same OCCT version — the TopoDS_Shape ABI is identical,
        so the SWIG pointer can be re-wrapped natively."""
        return self.from_topods_pointer(int(occ_shape.this))

    def make_halfspace(self, origin, normal, flip: bool) -> ShapeHandle:
        fn = getattr(self._cad, "make_halfspace", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.make_halfspace is not available in this build")
        return fn([float(c) for c in origin], [float(c) for c in normal], bool(flip))

    def cut_surfaces(self, solid: ShapeHandle, cutters: list, deflection: float, tol: float) -> list:
        # Native adacpp cut + face/edge extraction (its own OCCT, no pythonocc).
        # Returns the same plain-data contract as OccBackend.cut_surfaces.
        fn = getattr(self._cad, "cut_surfaces", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.cut_surfaces is not available in this build")
        return fn(solid, list(cutters), float(deflection), float(tol))

    # --- topology-kernel verbs (native adacpp, its own OCCT) ---------------

    def make_volumes_from_faces(self, faces: list[ShapeHandle], tolerance: float = 1e-6) -> list[ShapeHandle]:
        fn = getattr(self._cad, "make_volumes_from_faces", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.make_volumes_from_faces is not available in this build")
        return list(fn(list(faces), float(tolerance)))

    def merge_cells(self, solids: list[ShapeHandle], tolerance: float = 0.0) -> list[ShapeHandle]:
        fn = getattr(self._cad, "merge_cells", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.merge_cells is not available in this build")
        return list(fn(list(solids), float(tolerance)))

    def non_manifold_merge(self, shapes: list[ShapeHandle], tolerance: float = 1e-6, glue: bool = True) -> ShapeHandle:
        fn = getattr(self._cad, "non_manifold_merge", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.non_manifold_merge is not available in this build")
        return fn(list(shapes), float(tolerance), bool(glue))

    def free_faces(self, solids: list[ShapeHandle]) -> list[ShapeHandle]:
        fn = getattr(self._cad, "free_faces", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.free_faces is not available in this build")
        return list(fn(list(solids)))

    def point_in_solid(self, solid: ShapeHandle, point, tolerance: float = 1e-6) -> "Containment":
        fn = getattr(self._cad, "point_in_solid", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.point_in_solid is not available in this build")
        # adacpp returns an int matching OCCT TopAbs_State (IN=0, OUT=1, ON=2, UNKNOWN=3).
        state = fn(solid, [float(point[0]), float(point[1]), float(point[2])], float(tolerance))
        return (Containment.IN, Containment.OUT, Containment.ON, Containment.UNKNOWN)[int(state)]

    def center_of_mass(self, shape: ShapeHandle) -> "Point":
        fn = getattr(self._cad, "center_of_mass", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.center_of_mass is not available in this build")
        from ada.geom.points import Point

        return Point(*fn(shape))

    def shells(self, shape: ShapeHandle) -> list[ShapeHandle]:
        occ = self._occ_fallback_for(shape)
        if occ is not None:
            return occ.shells(shape)
        fn = getattr(self._cad, "shells", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.shells is not available in this build")
        return list(fn(shape))

    def wires(self, shape: ShapeHandle) -> list[ShapeHandle]:
        fn = getattr(self._cad, "wires", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.wires is not available in this build")
        return list(fn(shape))

    def wire_points(self, shape: ShapeHandle) -> list[tuple[float, float, float]]:
        fn = getattr(self._cad, "wire_points", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.wire_points is not available in this build")
        return [tuple(p) for p in fn(shape)]

    def unify_coplanar_faces(self, shape: ShapeHandle) -> ShapeHandle:
        fn = getattr(self._cad, "unify_coplanar_faces", None)
        if fn is None:
            raise NotImplementedError("adacpp.cad.unify_coplanar_faces is not available in this build")
        return fn(shape)


def select_backend(prefer: str | None = None) -> CadBackend:
    """Pick a CAD backend.

    `prefer` overrides everything; "adacpp" or "occ"/"pythonocc-core".
    Otherwise consults ADAPY_CAD_BACKEND, then auto-detects (adacpp first
    because pyodide-capable; pythonocc-core as native fallback)."""
    # OccBackend lives in ada.occ (the OCC backend's home); import lazily so
    # ada.cad never pulls the OCC closure at module load.
    from ada.occ.backend import OccBackend

    choice = prefer or os.environ.get("ADAPY_CAD_BACKEND")
    if choice in ("adacpp",):
        return AdacppBackend()
    if choice in ("occ", "pythonocc-core", "pyocc"):
        return OccBackend()
    if choice is not None:
        raise ValueError(f"Unknown ADAPY_CAD_BACKEND: {choice!r}")

    last_err: Exception | None = None
    for cls in (AdacppBackend, OccBackend):
        try:
            return cls()
        except ImportError as e:
            last_err = e
    raise ImportError(
        "No CAD backend available — install `adacpp` (preferred for "
        "pyodide) or `pythonocc-core`. "
        f"Last error: {last_err}"
    )


_ACTIVE_BACKEND: CadBackend | None = None


def active_backend() -> CadBackend:
    """Return the process-wide CAD backend, selecting and memoizing one on
    first use via :func:`select_backend`.

    Call sites that build or convert geometry should go through this rather
    than re-selecting a backend each time. Override the selection up front
    with the ``ADAPY_CAD_BACKEND`` env var, or call
    :func:`reset_active_backend` after changing it at runtime."""
    global _ACTIVE_BACKEND
    if _ACTIVE_BACKEND is None:
        _ACTIVE_BACKEND = select_backend()
    return _ACTIVE_BACKEND


def reset_active_backend() -> None:
    """Drop the memoized backend so the next :func:`active_backend` call
    re-selects. For tests and for switching ``ADAPY_CAD_BACKEND`` at
    runtime."""
    global _ACTIVE_BACKEND
    _ACTIVE_BACKEND = None


def is_shape_handle(obj: Any) -> bool:
    """True if ``obj`` is a shape handle produced by the active backend.

    The portable way to ask "does this object carry a pre-built CAD body?"
    without importing kernel types — the type check lives inside the
    backend (``CadBackend.is_handle``). Under the OCC backend this is an
    ``isinstance(obj, TopoDS_Shape)``; under adacpp it checks the native
    shape type."""
    return active_backend().is_handle(obj)


def is_cad_body(obj: Any) -> bool:
    """True if ``obj`` is a pre-built CAD body of ANY available backend, not just the
    active one.

    A STEP/IFC/SAT reader (the OCC OCAF loader) hands back a pythonocc ``TopoDS_Shape``
    even when ``ADAPY_CAD_BACKEND=adacpp`` is active. Such a body must still be routed to
    the transient OCC-body slot (``Shape._occ_cache``), never to ``_geom`` — which must
    stay an ``ada.geom.Geometry`` / ``None`` (else ``solid_geom()`` does ``self.geom.geometry``
    on a raw ``TopoDS_Compound`` → ``AttributeError``). ``is_shape_handle`` only checks the
    active backend, so it misclassifies a cross-backend body; this checks every importable one.
    """
    from ada.cad.registry import CadBackendName, backend_available

    for name in (CadBackendName.OCC, CadBackendName.ADACPP):
        if not backend_available(name):
            continue
        try:
            if select_backend(prefer=name.value).is_handle(obj):
                return True
        except Exception:
            continue
    return False


def __getattr__(name: str):
    # Back-compat: OccBackend moved to ada.occ.backend. Re-export it lazily on
    # attribute access so ``from ada.cad import OccBackend`` still works without
    # ada.cad eagerly importing the OCC closure (which would also be circular —
    # ada.occ.backend imports ada.cad for the Containment enum).
    if name == "OccBackend":
        from ada.occ.backend import OccBackend

        return OccBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AdacppBackend",
    "CadBackend",
    "Mesh",
    "OccBackend",
    "ShapeHandle",
    "active_backend",
    "is_shape_handle",
    "reset_active_backend",
    "select_backend",
    # registry / config
    "CadBackendName",
    "CadConfig",
    "StepReader",
    "TessellationPath",
    "available_backends",
    "available_paths",
    "backend_available",
]
