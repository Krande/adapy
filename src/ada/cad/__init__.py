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

import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from ada.geom import Geometry
    from ada.geom.booleans import BoolOpEnum
    from ada.geom.direction import Direction
    from ada.geom.points import Point


@runtime_checkable
class ShapeHandle(Protocol):
    """Opaque CAD shape handle. Concrete type is backend-private."""


@runtime_checkable
class Mesh(Protocol):
    """Triangle mesh produced by tessellation."""

    positions: Any  # flat float buffer, length = 3 * num_vertices
    indices: Any    # flat int buffer,   length = 3 * num_triangles


class CadBackend(Protocol):
    """Backend contract. Each method returns a kernel-native value; callers
    treat the returned ShapeHandle as opaque and only consume Mesh fields."""

    name: str

    def build(self, geometry: "Geometry") -> ShapeHandle: ...
    def make_wire(self, points: "list") -> ShapeHandle: ...
    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle: ...
    def make_cylinder(self, radius: float, height: float) -> ShapeHandle: ...
    def make_sphere(self, radius: float) -> ShapeHandle: ...
    def tessellate(self, shape: ShapeHandle, linear_deflection: float = -1.0) -> Mesh: ...
    def bbox(
        self, shape: ShapeHandle, optimal: bool = True, use_mesh: bool = False
    ) -> tuple[float, float, float, float, float, float]: ...
    def obb(
        self, shape: ShapeHandle
    ) -> "tuple[tuple[float, float, float], tuple[float, float, float]]": ...
    def read_step_bytes(self, data: bytes) -> ShapeHandle: ...
    def write_glb_bytes(self, shape: ShapeHandle, linear_deflection: float = 0.1) -> bytes: ...
    def is_handle(self, obj: Any) -> bool: ...
    def boolean(self, op: "BoolOpEnum", a: ShapeHandle, b: ShapeHandle) -> ShapeHandle: ...
    def transform(self, shape: ShapeHandle, matrix: "np.ndarray", copy: bool = True) -> ShapeHandle: ...
    def distance(self, a: ShapeHandle, b: ShapeHandle) -> float: ...
    def serialize(self, shape: ShapeHandle) -> str: ...
    def is_valid(self, shape: ShapeHandle) -> bool: ...
    def volume(self, shape: ShapeHandle) -> float: ...
    def faces(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def solids(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def edges(self, shape: ShapeHandle) -> list[ShapeHandle]: ...
    def vertex_points(self, shape: ShapeHandle) -> list[tuple[float, float, float]]: ...
    def face_plane(self, face: ShapeHandle) -> "tuple[Point, Direction] | None": ...
    def to_topods_pointer(self, shape: ShapeHandle) -> int: ...
    def adopt_occ_shape(self, occ_shape: Any) -> ShapeHandle: ...


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
        import ada.geom.solids as so
        import ada.geom.surfaces as su

        g = geometry.geometry

        def _axis(d, default):
            return list(d) if d is not None else list(default)

        if isinstance(g, so.Box):
            p = g.position
            shape = self._cad.build_box(
                list(p.location), _axis(p.axis, (0, 0, 1)), _axis(p.ref_direction, (1, 0, 0)),
                g.x_length, g.y_length, g.z_length,
            )
        elif isinstance(g, so.Cylinder):
            p = g.position
            shape = self._cad.build_cylinder(list(p.location), _axis(p.axis, (0, 0, 1)), g.radius, g.height)
        elif isinstance(g, so.Sphere):
            shape = self._cad.build_sphere(list(g.center), g.radius)
        elif isinstance(g, so.Cone):
            p = g.position
            shape = self._cad.build_cone(list(p.location), _axis(p.axis, (0, 0, 1)), g.bottom_radius, g.height)
        elif isinstance(g, so.ExtrudedAreaSolid):
            area = g.swept_area
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
                outer, inners, self._xyz(p.location),
                _axis(p.axis, (0, 0, 1)), _axis(p.ref_direction, (1, 0, 0)), g.depth, is_area,
            )
        elif isinstance(g, so.RevolvedAreaSolid):
            area = g.swept_area
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
                outer, inners, self._xyz(p.location),
                _axis(p.axis, (0, 0, 1)), _axis(p.ref_direction, (1, 0, 0)),
                self._xyz(g.axis.location), _axis(g.axis.axis, (0, 0, 1)), float(g.angle), is_area,
            )
        elif isinstance(g, so.FixedReferenceSweptAreaSolid):
            area = g.swept_area
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
                directrix, outer, self._xyz(g.position.location),
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
                outer, inners, self._xyz(pos.location),
                _axis(pos.axis, (0, 0, 1)), _axis(pos.ref_direction, (1, 0, 0)),
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
        raise NotImplementedError(
            f"AdacppBackend.build: profile curve {type(curve).__name__!r} not yet ported to adacpp."
        )

    def make_wire(self, points: "list") -> ShapeHandle:
        return self._cad.make_wire([[float(c) for c in self._xyz(p)] for p in points])

    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle:
        return self._cad.make_box(dx, dy, dz)

    def make_cylinder(self, radius: float, height: float) -> ShapeHandle:
        return self._cad.make_cylinder(radius, height)

    def make_sphere(self, radius: float) -> ShapeHandle:
        return self._cad.make_sphere(radius)

    def tessellate(self, shape: ShapeHandle, linear_deflection: float = -1.0) -> Mesh:
        return self._cad.tessellate(shape, linear_deflection)

    def bbox(
        self, shape: ShapeHandle, optimal: bool = True, use_mesh: bool = False
    ) -> tuple[float, float, float, float, float, float]:
        # adacpp.cad.bbox is analytic only; the optimal/use_mesh OCC-accuracy
        # knobs don't apply and are ignored.
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

    def transform(self, shape: ShapeHandle, matrix: "np.ndarray", copy: bool = True) -> ShapeHandle:
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

    def faces(self, shape: ShapeHandle) -> list[ShapeHandle]:
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


class OccBackend:
    """Backend backed by pythonocc-core. Native CPython only.

    Adapts the existing ada.occ helpers to the CadBackend signature shapes
    declared by adacpp.cad — primitives are centered/origin-anchored to
    match exactly, so a swap between backends produces identical AABBs."""

    name = "pythonocc-core"

    def __init__(self) -> None:
        # Import lazily so this class can be referenced (e.g. by tests
        # checking `name`) in environments where pythonocc-core isn't
        # installable. Raise on first instantiation if unavailable.
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCC.Core.BRepBndLib import brepbndlib
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.BRepCheck import BRepCheck_Analyzer
        from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
        from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
        from OCC.Core.BRepTools import breptools
        from OCC.Core.GeomAbs import GeomAbs_Plane
        from OCC.Core.IFSelect import IFSelect_RetDone
        from OCC.Core.Message import Message_ProgressRange
        from OCC.Core.RWGltf import RWGltf_CafWriter, RWGltf_WriterTrsfFormat
        from OCC.Core.RWMesh import RWMesh_CoordinateSystem_Zup
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.TopoDS import TopoDS_Shape
        from OCC.Core.TCollection import TCollection_AsciiString, TCollection_ExtendedString
        from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
        from OCC.Core.TDocStd import TDocStd_Document
        from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
        from OCC.Extend.TopologyUtils import TopologyExplorer
        from ada.occ.tessellating import tessellate_shape
        from ada.occ.utils import (
            make_box_by_points,
            make_cylinder as _occ_make_cylinder,
            make_sphere as _occ_make_sphere,
        )
        self._Bnd_Box = Bnd_Box
        self._brepbndlib = brepbndlib
        self._BRepAlgoAPI_Common = BRepAlgoAPI_Common
        self._BRepAlgoAPI_Cut = BRepAlgoAPI_Cut
        self._BRepAlgoAPI_Fuse = BRepAlgoAPI_Fuse
        self._BRepBuilderAPI_Transform = BRepBuilderAPI_Transform
        self._BRepCheck_Analyzer = BRepCheck_Analyzer
        self._BRepExtrema_DistShapeShape = BRepExtrema_DistShapeShape
        self._BRepMesh_IncrementalMesh = BRepMesh_IncrementalMesh
        self._breptools = breptools
        self._BRep_Tool = BRep_Tool
        self._BRepAdaptor_Surface = BRepAdaptor_Surface
        self._GeomAbs_Plane = GeomAbs_Plane
        self._TopologyExplorer = TopologyExplorer
        self._IFSelect_RetDone = IFSelect_RetDone
        self._Message_ProgressRange = Message_ProgressRange
        self._RWGltf_CafWriter = RWGltf_CafWriter
        self._RWGltf_WriterTrsfFormat_Compact = RWGltf_WriterTrsfFormat.RWGltf_WriterTrsfFormat_Compact
        self._RWMesh_CoordinateSystem_Zup = RWMesh_CoordinateSystem_Zup
        self._STEPControl_Reader = STEPControl_Reader
        self._TopoDS_Shape = TopoDS_Shape
        self._TCollection_AsciiString = TCollection_AsciiString
        self._TCollection_ExtendedString = TCollection_ExtendedString
        self._TColStd_IndexedDataMapOfStringString = TColStd_IndexedDataMapOfStringString
        self._TDocStd_Document = TDocStd_Document
        self._XCAFDoc_DocumentTool = XCAFDoc_DocumentTool
        self._tessellate_shape = tessellate_shape
        self._make_box_by_points = make_box_by_points
        self._make_cylinder = _occ_make_cylinder
        self._make_sphere = _occ_make_sphere

    def build(self, geometry: "Geometry") -> ShapeHandle:
        # Coarse Layer-B construction seam: the parametric ada.geom.Geometry
        # tree is the backend-neutral construction language. The fine-grained
        # ada.occ.geom builders are OccBackend's private implementation and
        # are never promoted to the CadBackend interface. The returned
        # ShapeHandle is a TopoDS_Shape under this backend.
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(geometry)

    def make_wire(self, points: "list") -> ShapeHandle:
        from ada.occ.utils import make_wire_from_points

        return make_wire_from_points(list(points))

    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle:
        # Centered axis-aligned box: matches adacpp.cad.make_box semantics
        # (corner at -d/2, opposite corner at +d/2). adapy's helper expects
        # tuple/list/ndarray, not gp_Pnt.
        return self._make_box_by_points((-dx / 2, -dy / 2, -dz / 2),
                                        (dx / 2, dy / 2, dz / 2))

    def make_cylinder(self, radius: float, height: float) -> ShapeHandle:
        # adapy's helper takes (origin point, axis vec, height, radius).
        # Match adacpp.cad.make_cylinder: +Z, base at origin.
        return self._make_cylinder((0, 0, 0), (0, 0, 1), height, radius)

    def make_sphere(self, radius: float) -> ShapeHandle:
        return self._make_sphere((0, 0, 0), radius)

    def tessellate(self, shape: ShapeHandle, linear_deflection: float = -1.0) -> Mesh:
        # ada.occ.tessellating uses a `quality` parameter; lower = finer.
        # Map the adacpp linear_deflection convention onto it: <=0 → default.
        if linear_deflection <= 0.0:
            return self._tessellate_shape(shape)
        return self._tessellate_shape(shape, quality=linear_deflection)

    def bbox(
        self, shape: ShapeHandle, optimal: bool = True, use_mesh: bool = False
    ) -> tuple[float, float, float, float, float, float]:
        # optimal=True → AddOptimal(useTriangulation=False, useShapeTolerance=
        # False): analytic geometric extents only, matching adacpp.cad.bbox
        # (both backends give identical AABBs for the same primitive). This is
        # the neutral default. optimal=False → Add(shape, bb, use_mesh): the
        # looser triangulation-aware box some OCC callers want (e.g. curved
        # plates); OCC-accuracy knobs, ignored by adacpp.
        bb = self._Bnd_Box()
        if optimal:
            self._brepbndlib.AddOptimal(shape, bb, False, False)
        else:
            self._brepbndlib.Add(shape, bb, use_mesh)
        if bb.IsVoid():
            raise RuntimeError("bbox: empty bounding box (shape has no geometry)")
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return (xmin, ymin, zmin, xmax, ymax, zmax)

    def obb(self, shape: ShapeHandle) -> "tuple[tuple[float, float, float], tuple[float, float, float]]":
        # Oriented bounding box. Mirrors OCC.Extend.ShapeFactory.
        # get_oriented_boundingbox: optimal OBB via triangulation. The portable
        # result is the world-space barycenter plus the three OBB half-sizes
        # (consumers reconstruct an axis-aligned span from those). The OBB
        # orientation axes themselves are OCC-internal and not exposed.
        from OCC.Core.Bnd import Bnd_OBB

        obb = Bnd_OBB()
        self._brepbndlib.AddOBB(shape, obb, True, True, False)
        c = obb.Center()
        return ((c.X(), c.Y(), c.Z()), (obb.XHSize(), obb.YHSize(), obb.ZHSize()))

    def distance(self, a: ShapeHandle, b: ShapeHandle) -> float:
        # Minimal distance between two bodies. The rich
        # BRepExtrema_DistShapeShape (nearest points, support sub-shapes) is
        # OCC-specific; the portable result is the scalar value, which is all
        # adapy consumers read.
        dss = self._BRepExtrema_DistShapeShape()
        dss.LoadS1(a)
        dss.LoadS2(b)
        dss.Perform()
        if not dss.IsDone():
            raise RuntimeError("distance: BRepExtrema_DistShapeShape failed")
        return dss.Value()

    def serialize(self, shape: ShapeHandle) -> str:
        # BREP text serialization (Clean drops cached triangulation first so
        # the string is geometry-only and deterministic).
        self._breptools.Clean(shape)
        return self._breptools.WriteToString(shape)

    def is_valid(self, shape: ShapeHandle) -> bool:
        # Topological validity (BRepCheck). geom_props=True checks geometric
        # consistency too.
        return self._BRepCheck_Analyzer(shape, True).IsValid()

    def volume(self, shape: ShapeHandle) -> float:
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.GProp import GProp_GProps

        props = GProp_GProps()
        brepgprop.VolumeProperties(shape, props)
        return props.Mass()

    def faces(self, shape: ShapeHandle) -> list[ShapeHandle]:
        # Whole list of face sub-shapes — the boundary crosses once, not per
        # face, so callers iterate without re-entering the backend per element.
        return list(self._TopologyExplorer(shape).faces())

    def solids(self, shape: ShapeHandle) -> list[ShapeHandle]:
        return list(self._TopologyExplorer(shape).solids())

    def edges(self, shape: ShapeHandle) -> list[ShapeHandle]:
        return list(self._TopologyExplorer(shape).edges())

    def to_topods_pointer(self, shape: ShapeHandle) -> int:
        # Under this backend a ShapeHandle IS a TopoDS_Shape; its SWIG pointer
        # is int(shape.this) — the value gmsh's importShapesNativePointer wants.
        return int(shape.this)

    def vertex_points(self, shape: ShapeHandle) -> list[tuple[float, float, float]]:
        # Walk every vertex and return all coordinates as one list. The
        # per-vertex loop stays inside the backend (the abstraction boundary
        # must never land inside a per-vertex loop — see the perf guardrail in
        # dap plan/v3 notes_occ_backend_abstraction Phase 1/5).
        exp = self._TopologyExplorer(shape)
        pts = []
        for v in exp.vertices():
            p = self._BRep_Tool.Pnt(v)
            pts.append((p.X(), p.Y(), p.Z()))
        return pts

    def face_plane(self, face: ShapeHandle) -> "tuple[Point, Direction] | None":
        # The face's plane as (origin Point, normal Direction), or None if the
        # face is not planar. Returns backend-neutral ada.geom data.
        from ada.geom.direction import Direction
        from ada.geom.points import Point

        surf = self._BRepAdaptor_Surface(face, True)
        if surf.GetType() != self._GeomAbs_Plane:
            return None
        pln = surf.Plane()
        location = pln.Location().XYZ().Coord()
        normal = pln.Axis().Direction()
        return Point(*location), Direction(normal.X(), normal.Y(), normal.Z())

    def read_step_bytes(self, data: bytes) -> ShapeHandle:
        # pythonocc-core's STEPControl_Reader reads from filenames only, so
        # we round-trip through a temp file. Same pattern as adacpp's wasm
        # implementation (which uses MEMFS). Native /tmp is real disk; cost
        # is a single file write+read of the input buffer.
        import tempfile
        from OCC.Core.TopoDS import TopoDS_Shape
        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            reader = self._STEPControl_Reader()
            if reader.ReadFile(path) != self._IFSelect_RetDone:
                raise RuntimeError("read_step_bytes: STEPControl_Reader failed to parse the input")
            reader.TransferRoots()
            shape: TopoDS_Shape = reader.OneShape()
            if shape.IsNull():
                raise RuntimeError("read_step_bytes: no transferable shape (empty STEP?)")
            return shape
        finally:
            import os
            os.unlink(path)

    def write_glb_bytes(self, shape: ShapeHandle, linear_deflection: float = 0.1) -> bytes:
        # Mirror of adacpp's wasm write_glb_bytes: tessellate the shape, wrap
        # in a CAF document, write GLB to a temp file via RWGltf_CafWriter,
        # slurp the bytes back. Output identical between backends.
        import os
        import tempfile
        if linear_deflection <= 0.0:
            linear_deflection = 0.1
        self._BRepMesh_IncrementalMesh(shape, linear_deflection, False, 0.5, True)

        doc = self._TDocStd_Document(self._TCollection_ExtendedString("MDTV-XCAF"))
        shape_tool = self._XCAFDoc_DocumentTool.ShapeTool(doc.Main())
        shape_tool.AddShape(shape)

        with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as f:
            path = f.name
        try:
            writer = self._RWGltf_CafWriter(self._TCollection_AsciiString(path), True)
            # Z-up source + compact transforms — same as adapy's ada.occ.gltf_writer.to_gltf
            # so output is byte-compatible with the viewer's existing assumptions.
            writer.ChangeCoordinateSystemConverter().SetInputCoordinateSystem(
                self._RWMesh_CoordinateSystem_Zup
            )
            writer.SetTransformationFormat(self._RWGltf_WriterTrsfFormat_Compact)
            file_info = self._TColStd_IndexedDataMapOfStringString()
            file_info.Add(
                self._TCollection_AsciiString("Authors"),
                self._TCollection_AsciiString("adacpp"),
            )
            progress = self._Message_ProgressRange()
            if not writer.Perform(doc, file_info, progress):
                raise RuntimeError("write_glb_bytes: RWGltf_CafWriter::Perform failed")
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    def is_handle(self, obj: Any) -> bool:
        # Under this backend a ShapeHandle IS a TopoDS_Shape. Centralising
        # the isinstance here lets callers (e.g. the tessellator's raw-import
        # fast path) ask "is this a backend body?" without importing OCC
        # types — keeping handle-type introspection out of portable code.
        return isinstance(obj, self._TopoDS_Shape)

    def boolean(self, op: "BoolOpEnum", a: ShapeHandle, b: ShapeHandle) -> ShapeHandle:
        # CSG on two opaque bodies. op is an ada.geom BoolOpEnum (backend-
        # neutral); the kernel ops (Cut/Fuse/Common) are OccBackend-private.
        from ada.geom.booleans import BoolOpEnum

        if op == BoolOpEnum.DIFFERENCE:
            return self._BRepAlgoAPI_Cut(a, b).Shape()
        elif op == BoolOpEnum.UNION:
            return self._BRepAlgoAPI_Fuse(a, b).Shape()
        elif op == BoolOpEnum.INTERSECTION:
            return self._BRepAlgoAPI_Common(a, b).Shape()
        raise NotImplementedError(f"Boolean operation {op} not implemented")

    def transform(self, shape: ShapeHandle, matrix: "np.ndarray", copy: bool = True) -> ShapeHandle:
        # Apply a 4x4 affine transform (the backend-neutral currency) to an
        # opaque body. The top 3 rows feed gp_Trsf.SetValues (the implicit
        # bottom row is [0,0,0,1]); this is lossless for the rigid + uniform-
        # scale transforms adapy composes (no shear). `copy` mirrors
        # BRepBuilderAPI_Transform's copy flag.
        from OCC.Core.gp import gp_Trsf

        m = matrix
        trsf = gp_Trsf()
        trsf.SetValues(
            float(m[0][0]), float(m[0][1]), float(m[0][2]), float(m[0][3]),
            float(m[1][0]), float(m[1][1]), float(m[1][2]), float(m[1][3]),
            float(m[2][0]), float(m[2][1]), float(m[2][2]), float(m[2][3]),
        )
        return self._BRepBuilderAPI_Transform(shape, trsf, copy).Shape()

    def adopt_occ_shape(self, occ_shape: Any) -> ShapeHandle:
        # Under this backend a ShapeHandle IS a TopoDS_Shape, so a raw OCC
        # body from the DocBackend reader is already a native handle.
        return occ_shape


def select_backend(prefer: str | None = None) -> CadBackend:
    """Pick a CAD backend.

    `prefer` overrides everything; "adacpp" or "occ"/"pythonocc-core".
    Otherwise consults ADAPY_CAD_BACKEND, then auto-detects (adacpp first
    because pyodide-capable; pythonocc-core as native fallback)."""
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
]
