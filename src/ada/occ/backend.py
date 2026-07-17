"""OccBackend — the pythonocc-core implementation of ada.cad.CadBackend.

Relocated out of ada.cad so the OCC kernel dependency lives under ada.occ (the
backend boundary). ``ada.cad.select_backend`` lazy-imports this; ``ada.cad`` also
re-exports ``OccBackend`` via module ``__getattr__`` for back-compat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ada.cad import Containment, StepShapeData

if TYPE_CHECKING:
    import numpy as np

    from ada.cad import Mesh, PlanarImprint, ShapeHandle
    from ada.geom import Geometry
    from ada.geom.booleans import BoolOpEnum
    from ada.geom.direction import Direction
    from ada.geom.points import Point


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
        from OCC.Core.BRepAlgoAPI import (
            BRepAlgoAPI_Common,
            BRepAlgoAPI_Cut,
            BRepAlgoAPI_Fuse,
        )
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
        from OCC.Core.TCollection import (
            TCollection_AsciiString,
            TCollection_ExtendedString,
        )
        from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
        from OCC.Core.TDocStd import TDocStd_Document
        from OCC.Core.TopoDS import TopoDS_Shape
        from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
        from OCC.Extend.TopologyUtils import TopologyExplorer

        from ada.occ.tessellating import tessellate_shape
        from ada.occ.utils import make_box_by_points
        from ada.occ.utils import make_cylinder as _occ_make_cylinder
        from ada.occ.utils import make_sphere as _occ_make_sphere

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
        # Polyline wire through all points (open). Mirrors adacpp.cad.make_wire;
        # the old single-edge helper only handled 2 points.
        from OCC.Core.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeWire,
        )
        from OCC.Core.gp import gp_Pnt

        pts = []
        for p in points:
            c = list(p)
            pts.append(gp_Pnt(float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0))
        if len(pts) < 2:
            raise ValueError("make_wire: need at least 2 points")
        wm = BRepBuilderAPI_MakeWire()
        for a, b in zip(pts, pts[1:]):
            wm.Add(BRepBuilderAPI_MakeEdge(a, b).Edge())
        wm.Build()
        return wm.Wire()

    def polygon_face(self, points: "list") -> ShapeHandle:
        # Planar face from a closed polygon of points (auto-closed). Used to feed
        # internal divider faces into make_volumes_from_faces so a single lofted
        # solid partitions into one cell per section band.
        from OCC.Core.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
        )
        from OCC.Core.gp import gp_Pnt

        poly = BRepBuilderAPI_MakePolygon()
        n = 0
        for p in points:
            c = list(p)
            poly.Add(gp_Pnt(float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0))
            n += 1
        if n < 3:
            raise ValueError("polygon_face: need at least 3 points")
        poly.Close()
        return BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()

    def loft_profiles(
        self, profiles: "list[list[tuple[float, float, float]]]", ruled: bool = True, solid: bool = True
    ) -> ShapeHandle:
        from OCC.Core.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeWire,
        )
        from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCC.Core.gp import gp_Pnt

        if len(profiles) < 2:
            raise ValueError(f"loft_profiles needs at least 2 profiles, got {len(profiles)}")
        ts = BRepOffsetAPI_ThruSections(solid, ruled)
        for prof in profiles:
            pts = [gp_Pnt(float(p[0]), float(p[1]), float(p[2])) for p in prof]
            wm = BRepBuilderAPI_MakeWire()
            for a, b in zip(pts, pts[1:] + pts[:1]):
                wm.Add(BRepBuilderAPI_MakeEdge(a, b).Edge())
            ts.AddWire(wm.Wire())
        ts.Build()
        if not ts.IsDone():
            raise RuntimeError("BRepOffsetAPI_ThruSections.Build failed")
        return ts.Shape()

    def section_with_plane(self, shape: ShapeHandle, origin, normal, size: float = 1000.0) -> ShapeHandle:
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt

        pln = gp_Pln(
            gp_Pnt(float(origin[0]), float(origin[1]), float(origin[2])),
            gp_Dir(float(normal[0]), float(normal[1]), float(normal[2])),
        )
        face = BRepBuilderAPI_MakeFace(pln, -size, size, -size, size).Face()
        common = BRepAlgoAPI_Common(shape, face)
        common.Build()
        if not common.IsDone():
            raise RuntimeError("BRepAlgoAPI_Common.Build failed")
        return common.Shape()

    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle:
        # Centered axis-aligned box: matches adacpp.cad.make_box semantics
        # (corner at -d/2, opposite corner at +d/2). adapy's helper expects
        # tuple/list/ndarray, not gp_Pnt.
        return self._make_box_by_points((-dx / 2, -dy / 2, -dz / 2), (dx / 2, dy / 2, dz / 2))

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

    def tessellate_batch(self, shapes: "list", linear_deflection: float = -1.0):
        # No native batch path under pythonocc; concatenate per-shape meshes into
        # one combined BatchMesh via the shared helper.
        from ada.cad import tessellate_batch_via_loop

        return tessellate_batch_via_loop(self, shapes, linear_deflection)

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

    def area(self, shape: ShapeHandle) -> float:
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.GProp import GProp_GProps

        props = GProp_GProps()
        brepgprop.SurfaceProperties(shape, props)
        return props.Mass()

    def shape_type(self, shape: ShapeHandle) -> str:
        from OCC.Core.TopAbs import (
            TopAbs_COMPOUND,
            TopAbs_COMPSOLID,
            TopAbs_EDGE,
            TopAbs_FACE,
            TopAbs_SHELL,
            TopAbs_SOLID,
            TopAbs_VERTEX,
            TopAbs_WIRE,
        )

        return {
            TopAbs_COMPOUND: "compound",
            TopAbs_COMPSOLID: "compsolid",
            TopAbs_SOLID: "solid",
            TopAbs_SHELL: "shell",
            TopAbs_FACE: "face",
            TopAbs_WIRE: "wire",
            TopAbs_EDGE: "edge",
            TopAbs_VERTEX: "vertex",
        }.get(shape.ShapeType(), "shape")

    def face_surface_type(self, shape: ShapeHandle) -> str:
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.TopAbs import TopAbs_FACE
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopoDS import topods

        if shape.ShapeType() == TopAbs_FACE:
            face = topods.Face(shape)
        else:
            exp = TopExp_Explorer(shape, TopAbs_FACE)
            if not exp.More():
                raise ValueError("face_surface_type: shape has no face")
            face = topods.Face(exp.Current())
        name = BRep_Tool.Surface(face).DynamicType().Name()
        return {
            "Geom_Plane": "plane",
            "Geom_CylindricalSurface": "cylinder",
            "Geom_ConicalSurface": "cone",
            "Geom_SphericalSurface": "sphere",
            "Geom_ToroidalSurface": "torus",
            "Geom_BSplineSurface": "bspline",
            "Geom_BezierSurface": "bezier",
            "Geom_SurfaceOfLinearExtrusion": "linear_extrusion",
            "Geom_SurfaceOfRevolution": "revolution",
        }.get(name, name)

    def extrude_face_along_normal(self, face: ShapeHandle, thickness: float) -> ShapeHandle:
        from ada.occ.plate_curved import extrude_face_along_normal

        return extrude_face_along_normal(face, thickness)

    def face_to_advanced_face(self, shape: ShapeHandle):
        from ada.occ.step.geom.surfaces import occ_face_to_ada_face

        return occ_face_to_ada_face(shape)

    def build_bspline_advanced_face_from_grid(self, grid: "list", tol: float):
        # Fit a NURBS surface through a structured node grid and return it as a
        # backend-neutral ada.geom AdvancedFace. The transient OCC face never
        # leaves the fit helper — callers receive only serialised ada.geom.
        from ada.occ.fem.surface_fit import fit_advanced_face_from_grid

        return fit_advanced_face_from_grid(grid, tol)

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

    def face_id(self, shape: ShapeHandle) -> int:
        # Orientation-independent topological identity: two sub-shapes that are
        # the same face of a non-manifold complex (shared by two solids, with
        # opposite orientation) hash equal here. This lets the cell graph detect
        # shared faces by true topological identity rather than geometry.
        try:
            loc_hash = shape.Location().HashCode()
        except Exception:
            loc_hash = 0
        return hash((shape.TShape().__hash__(), loc_hash))

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
            writer.ChangeCoordinateSystemConverter().SetInputCoordinateSystem(self._RWMesh_CoordinateSystem_Zup)
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

    def read_step_shapes(self, data: bytes, unit: str = "M") -> list:
        # Mirror of adacpp's read_step_shapes: STEPCAFControl_Reader (not the plain
        # STEPControl_Reader read_step_bytes uses) is what resolves the presentation-style
        # tree, so this is the only read that recovers per-shape names and colours.
        import os
        import tempfile

        from OCC.Core.Interface import Interface_Static
        from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
        from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
        from OCC.Core.TDataStd import TDataStd_Name
        from OCC.Core.TDF import TDF_LabelSequence
        from OCC.Core.XCAFDoc import (
            XCAFDoc_ColorCurv,
            XCAFDoc_ColorGen,
            XCAFDoc_ColorSurf,
        )

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            reader = STEPCAFControl_Reader()
            reader.SetColorMode(True)
            reader.SetNameMode(True)
            # Set AFTER the ctor (which resets the static), before ReadFile — same order
            # as adacpp's read_step_shapes and StepStore.create_step_reader.
            Interface_Static.SetCVal("xstep.cascade.unit", unit)
            if reader.ReadFile(path) != self._IFSelect_RetDone:
                raise RuntimeError("read_step_shapes: STEPCAFControl_Reader could not parse the input")
            doc = self._TDocStd_Document(self._TCollection_ExtendedString("MDTV-XCAF"))
            if not reader.Transfer(doc):
                raise RuntimeError("read_step_shapes: transfer to OCAF document failed")
        finally:
            os.unlink(path)

        shape_tool = self._XCAFDoc_DocumentTool.ShapeTool(doc.Main())
        color_tool = self._XCAFDoc_DocumentTool.ColorTool(doc.Main())
        out: list[StepShapeData] = []

        def read_one(lab, raw):
            name = ""
            nm = TDataStd_Name()
            if lab.FindAttribute(TDataStd_Name.GetID(), nm):
                name = nm.Get().ToExtString()
            c = Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB)
            has_color = False
            for target in (lab, raw):
                if any(
                    color_tool.GetColor(target, t, c) for t in (XCAFDoc_ColorGen, XCAFDoc_ColorSurf, XCAFDoc_ColorCurv)
                ):
                    has_color = True
                    break
            out.append(StepShapeData(raw, name, (c.Red(), c.Green(), c.Blue()), has_color))

        def collect(lab):
            if shape_tool.IsAssembly(lab):
                comps = TDF_LabelSequence()
                shape_tool.GetComponents(lab, comps)
                for i in range(1, comps.Length() + 1):
                    collect(comps.Value(i))
            elif shape_tool.IsSimpleShape(lab):
                read_one(lab, shape_tool.GetShape(lab))
                # Sub-shape labels carry the per-face/per-solid overrides XCAF split out of
                # the parent — without these a solid-coloured assembly reads as one colour.
                subs = TDF_LabelSequence()
                shape_tool.GetSubShapes(lab, subs)
                for i in range(1, subs.Length() + 1):
                    sl = subs.Value(i)
                    read_one(sl, shape_tool.GetShape(sl))

        free = TDF_LabelSequence()
        shape_tool.GetFreeShapes(free)
        for i in range(1, free.Length() + 1):
            collect(free.Value(i))
        return out

    def step_bytes_to_glb_bytes(
        self, data: bytes, linear_deflection: float = 0.1, angular_deg: float = 20.0, unit: str = "M"
    ) -> bytes:
        # Mirror of adacpp's step_bytes_to_glb_bytes: keep the OCAF document from reader to
        # writer so names, the assembly tree and colours (solid-level AND per-face) reach the
        # GLB's materials. Flattening to a shape list instead would re-emit a solid AND each of
        # its faces; read_step_bytes + write_glb_bytes would drop colour entirely.
        import math
        import os
        import tempfile

        from OCC.Core.Interface import Interface_Static
        from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
        from OCC.Core.TDF import TDF_LabelSequence

        if linear_deflection <= 0.0:
            linear_deflection = 0.1

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            f.write(data)
            src = f.name
        try:
            reader = STEPCAFControl_Reader()
            reader.SetColorMode(True)
            reader.SetNameMode(True)
            reader.SetLayerMode(True)
            # Set AFTER the ctor (which resets the static), before ReadFile — same order as
            # read_step_shapes and StepStore.create_step_reader.
            Interface_Static.SetCVal("xstep.cascade.unit", unit)
            if reader.ReadFile(src) != self._IFSelect_RetDone:
                raise RuntimeError("step_bytes_to_glb_bytes: STEPCAFControl_Reader could not parse the input")
            doc = self._TDocStd_Document(self._TCollection_ExtendedString("MDTV-XCAF"))
            if not reader.Transfer(doc):
                raise RuntimeError("step_bytes_to_glb_bytes: transfer to OCAF document failed")
        finally:
            os.unlink(src)

        shape_tool = self._XCAFDoc_DocumentTool.ShapeTool(doc.Main())
        labels = TDF_LabelSequence()
        shape_tool.GetFreeShapes(labels)
        for i in range(1, labels.Length() + 1):
            shape = shape_tool.GetShape(labels.Value(i))
            if shape is None or shape.IsNull():
                continue
            self._BRepMesh_IncrementalMesh(shape, linear_deflection, False, math.radians(angular_deg), True)

        with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as f:
            path = f.name
        try:
            writer = self._RWGltf_CafWriter(self._TCollection_AsciiString(path), True)
            writer.ChangeCoordinateSystemConverter().SetInputCoordinateSystem(self._RWMesh_CoordinateSystem_Zup)
            writer.SetTransformationFormat(self._RWGltf_WriterTrsfFormat_Compact)
            file_info = self._TColStd_IndexedDataMapOfStringString()
            file_info.Add(
                self._TCollection_AsciiString("Authors"),
                self._TCollection_AsciiString("adacpp"),
            )
            progress = self._Message_ProgressRange()
            if not writer.Perform(doc, file_info, progress):
                raise RuntimeError("step_bytes_to_glb_bytes: RWGltf_CafWriter::Perform failed")
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    def step_to_face_tagged_meshes(
        self,
        step_path: str,
        linear_deflection: float = 0.1,
        angular_deflection: float = 0.5,
        store_units: str = "m",
    ) -> "list[tuple]":
        """OCAF-read a STEP file and tessellate it *per source face*, tagging each face with
        its STEP entity id and its own colour.

        This is the OCC-tessellation equivalent of the adacpp native ``face_regions`` path.
        Two properties are read straight off the OCAF document so the OCC clickable model
        lines up with native and with OCC's own step2glb benchmark:

        * **face id** — resolved via the STEP transfer map
          (``TransferReader.EntityFromShapeResult(face, 1)`` -> ``StepModel.IdentLabel``), which
          returns the ``#NNNN`` ADVANCED_FACE entity id. This is the SAME id native stamps as
          ``face->src_id``, so a face id means the same thing across both kernels. Faces that
          fail to resolve (a handful) get a negative synthetic id so they stay clickable.
        * **colour** — read per face with ``ColorTool.GetColor(shape, XCAFDoc_ColorSurf, c)``
          (Surf, then Gen), falling back to the owning solid's colour. This recovers the full
          per-face palette; ``GetInstanceColor`` (what the generic reader uses) returns grey
          for style-per-face files like AP214 assemblies.

        Faces MUST be meshed off the original document shapes — not the
        ``BRepBuilderAPI_Transform`` copies ``read_step_file_with_names_colors`` hands out,
        whose faces no longer match the transfer binding (0% id resolution). The document has
        no assembly instancing to place here, so the originals are already world-positioned.

        Nodes group faces into pickable bodies: a face's owning solid, else its owning free
        shell, else the loose face itself. Colour is carried per FACE (a node may therefore
        span several materials, exactly as merge-by-colour already produces).

        Returns one tuple per node::

            (name: str | None,
             faces: list[tuple[int, tuple[float, float, float] | None, np.ndarray, np.ndarray]])

        where each face tuple is ``(face_id, rgb_0_1_or_None, positions, indices)`` — positions
        a flat float32 xyz triangle soup (3 verts/triangle) and indices a flat uint32 arange.
        """
        import numpy as np
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRep import BRep_Builder, BRep_Tool
        from OCC.Core.BRepBndLib import brepbndlib
        from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
        from OCC.Core.TDF import TDF_LabelSequence
        from OCC.Core.TopAbs import (
            TopAbs_FACE,
            TopAbs_REVERSED,
            TopAbs_SHELL,
            TopAbs_SOLID,
        )
        from OCC.Core.TopExp import TopExp_Explorer, topexp
        from OCC.Core.TopLoc import TopLoc_Location
        from OCC.Core.TopoDS import TopoDS_Compound, topods
        from OCC.Core.TopTools import (
            TopTools_IndexedDataMapOfShapeListOfShape,
            TopTools_IndexedMapOfShape,
        )
        from OCC.Core.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf

        from ada.occ.step.store import StepStore

        store = StepStore(step_path, verbosity=False, store_units=store_units)
        store.create_step_reader(use_ocaf=True)
        shape_tool = store.shape_tool
        color_tool = store.color_tool
        # STEP transfer map (shape -> source entity) for per-face #NNNN ids.
        inner = store.step_reader.ChangeReader()
        transfer_reader = inner.WS().TransferReader()
        step_model = inner.StepModel()

        roots = TDF_LabelSequence()
        shape_tool.GetFreeShapes(roots)
        root_shapes = [shape_tool.GetShape(roots.Value(i)) for i in range(1, roots.Length() + 1)]
        if not root_shapes:
            return []
        if len(root_shapes) == 1:
            root = root_shapes[0]
        else:
            root = TopoDS_Compound()
            builder = BRep_Builder()
            builder.MakeCompound(root)
            for s in root_shapes:
                builder.Add(root, s)

        # Model geometric bbox (no triangulation needed) — drives both the phantom-triangle
        # clip box and, when the caller asks for it, an auto-scaled tessellation deflection.
        ref = Bnd_Box()
        try:
            brepbndlib.Add(root, ref, True)
        except Exception:  # noqa: BLE001
            pass
        clip_lo = clip_hi = None
        diag = 0.0
        if not ref.IsVoid():
            rx0, ry0, rz0, rx1, ry1, rz1 = ref.Get()
            diag = ((rx1 - rx0) ** 2 + (ry1 - ry0) ** 2 + (rz1 - rz0) ** 2) ** 0.5
            pad = 0.1 * diag + 1e-6
            clip_lo = (rx0 - pad, ry0 - pad, rz0 - pad)
            clip_hi = (rx1 + pad, ry1 + pad, rz1 + pad)

        # linear_deflection <= 0 means "auto": scale it to the model so quality is unit-agnostic
        # (a metres model and the same model in mm both get a sensible chord tolerance).
        if linear_deflection <= 0:
            linear_deflection = max(diag * 5e-4, 1e-6) if diag > 0 else 0.1

        # Mesh the whole model once; per-face triangulation is read back below.
        self._BRepMesh_IncrementalMesh(root, linear_deflection, False, angular_deflection, True)

        # Ancestry for node grouping: which solid / shell a face belongs to.
        solid_map = TopTools_IndexedMapOfShape()
        topexp.MapShapes(root, TopAbs_SOLID, solid_map)
        shell_map = TopTools_IndexedMapOfShape()
        topexp.MapShapes(root, TopAbs_SHELL, shell_map)
        face_solid = TopTools_IndexedDataMapOfShapeListOfShape()
        topexp.MapShapesAndAncestors(root, TopAbs_FACE, TopAbs_SOLID, face_solid)
        face_shell = TopTools_IndexedDataMapOfShapeListOfShape()
        topexp.MapShapesAndAncestors(root, TopAbs_FACE, TopAbs_SHELL, face_shell)

        def _color(shape) -> "tuple[float, float, float] | None":
            for typ in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
                c = Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB)
                try:
                    if color_tool.GetColor(shape, typ, c):
                        return (float(c.Red()), float(c.Green()), float(c.Blue()))
                except Exception:  # noqa: BLE001
                    pass
            return None

        def _face_id(face) -> "int | None":
            ent = transfer_reader.EntityFromShapeResult(face, 1)
            if ent is not None:
                try:
                    n = step_model.IdentLabel(ent)
                    if n and n > 0:
                        return int(n)
                except Exception:  # noqa: BLE001
                    pass
            return None

        # node_key -> {"name": str, "faces": [(face_id, rgb|None, pos, idx)]}
        nodes: dict = {}
        order: list = []
        fallback = 0

        exp = TopExp_Explorer(root, TopAbs_FACE)
        while exp.More():
            face = topods.Face(exp.Current())

            owning_solid = None
            if face_solid.Contains(face):
                lst = face_solid.FindFromKey(face)
                if lst.Size() > 0:
                    owning_solid = lst.First()
            owning_shell = None
            if owning_solid is None and face_shell.Contains(face):
                lst = face_shell.FindFromKey(face)
                if lst.Size() > 0:
                    owning_shell = lst.First()

            fid = _face_id(face)
            if owning_solid is not None:
                node_key = ("solid", solid_map.FindIndex(owning_solid))
                node_name = f"solid_{node_key[1]}"
            elif owning_shell is not None:
                node_key = ("shell", shell_map.FindIndex(owning_shell))
                node_name = f"shell_{node_key[1]}"
            else:
                node_key = ("face", fid if fid is not None else -(fallback + 1))
                node_name = f"face_{node_key[1]}"

            color = _color(face)
            if color is None and owning_solid is not None:
                color = _color(owning_solid)

            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation(face, loc)
            if tri is None:
                exp.Next()
                continue
            trsf = loc.Transformation()
            rev = face.Orientation() == TopAbs_REVERSED
            verts: list[float] = []
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
                    continue  # phantom triangle outside the real model — drop it
                for p in pts:
                    verts.extend((p.X(), p.Y(), p.Z()))
            if not verts:
                exp.Next()
                continue

            if fid is None:
                fallback += 1
                fid = -fallback  # negative marks an unresolved face; still clickable

            positions = np.asarray(verts, dtype="float32")
            indices = np.arange(positions.size // 3, dtype="uint32")

            entry = nodes.get(node_key)
            if entry is None:
                entry = {"name": node_name, "faces": []}
                nodes[node_key] = entry
                order.append(node_key)
            entry["faces"].append((fid, color, positions, indices))
            exp.Next()

        return [(nodes[k]["name"], nodes[k]["faces"]) for k in order]

    def write_step(
        self, shapes: list, names: list, colors: list, filename: str, unit: str = "m", schema: str = "AP214"
    ) -> None:
        # OCAF/XCAF STEP write via pythonocc's STEPCAFControl_Writer.
        from ada.base.units import Units
        from ada.occ.step.writer import StepSchema, StepWriter

        sw = StepWriter("Assembly", units=Units.from_str(unit), schema=StepSchema(schema.upper()))
        for shape, name, color in zip(shapes, names, colors):
            sw.add_shape(shape, str(name), rgb_color=tuple(color))
        sw.export(filename)

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
            float(m[0][0]),
            float(m[0][1]),
            float(m[0][2]),
            float(m[0][3]),
            float(m[1][0]),
            float(m[1][1]),
            float(m[1][2]),
            float(m[1][3]),
            float(m[2][0]),
            float(m[2][1]),
            float(m[2][2]),
            float(m[2][3]),
        )
        return self._BRepBuilderAPI_Transform(shape, trsf, copy).Shape()

    def adopt_occ_shape(self, occ_shape: Any) -> ShapeHandle:
        # Under this backend a ShapeHandle IS a TopoDS_Shape, so a raw OCC
        # body from the DocBackend reader is already a native handle.
        return occ_shape

    def make_halfspace(self, origin, normal, flip: bool) -> ShapeHandle:
        from ada.occ.cut_surfaces_occ import occ_make_halfspace

        return occ_make_halfspace(origin, normal, flip)

    def cut_surfaces(self, solid: ShapeHandle, cutters: list, deflection: float, tol: float) -> list:
        # Sequential BRepAlgoAPI_Cut with boolean history; all the per-face /
        # per-edge OCCT loops stay inside this backend (the boundary crosses
        # once, returning plain data — never inside a per-element loop).
        from ada.occ.cut_surfaces_occ import occ_cut_surfaces

        return occ_cut_surfaces(solid, cutters, deflection, tol)

    # --- topology-kernel verbs ---------------------------------------------
    # The non-manifold core ada.topology needs. Every per-face / per-solid
    # OCCT loop stays inside the backend (the boundary crosses once, returning
    # opaque handles or backend-neutral ada.geom data — never inside a loop).

    def make_volumes_from_faces(self, faces: list[ShapeHandle], tolerance: float = 1e-6) -> list[ShapeHandle]:
        # Partition space into solids from a face soup. BOPAlgo_MakerVolume
        # builds every enclosed cell; faces interior to the soup come out shared
        # between the two solids they separate, which free_faces() later relies on.
        from OCC.Core.BOPAlgo import BOPAlgo_MakerVolume
        from OCC.Core.TopTools import TopTools_ListOfShape

        mv = BOPAlgo_MakerVolume()
        args = TopTools_ListOfShape()
        for f in faces:
            args.Append(f)
        mv.SetArguments(args)
        mv.SetIntersect(True)  # imprint the faces against each other first
        if tolerance and tolerance > 0:
            mv.SetFuzzyValue(tolerance)
        mv.Perform()
        if mv.HasErrors():
            raise RuntimeError("make_volumes_from_faces: BOPAlgo_MakerVolume reported errors")
        return list(self._TopologyExplorer(mv.Shape()).solids())

    def merge_cells(self, solids: list[ShapeHandle], tolerance: float = 0.0) -> list[ShapeHandle]:
        # Faithful port of topologic's Topology::Merge over solids (the old
        # topologicpy partition: pairwise Topology.Merge of space-box prisms).
        # BOPAlgo_CellsBuilder general-fuses the solids, then each operand is
        # taken into the result (AddToResult) and MakeContainers() assembles the
        # non-manifold CellComplex: each input solid survives as a cell and every
        # mutual interface becomes ONE shared face referenced by both cells (so
        # face_id-based sharing in the extractor links them). This differs from
        # make_volumes_from_faces (BOPAlgo_MakerVolume over a face soup), which
        # rebuilds minimal volumes and loses operand identity / imprints.
        #
        # A single all-at-once CellsBuilder is used rather than topologic's
        # balanced *pairwise* merge tree: both were measured to produce a
        # byte-identical cell complex on hvdc_lean (same cells/faces/members),
        # since CellsBuilder is order-independent — so we keep the cheaper one.
        from OCC.Core.BOPAlgo import BOPAlgo_CellsBuilder
        from OCC.Core.TopTools import TopTools_ListOfShape

        # CellsBuilder is a general-fuse of >= 2 operands; handed a single solid
        # (or none) it reports an error rather than a no-op. Merging one cell has
        # nothing to fuse, so return the operands unchanged — this is what a
        # single-space model (one PrimBox) needs.
        if len(solids) <= 1:
            return list(solids)

        cb = BOPAlgo_CellsBuilder()
        args = TopTools_ListOfShape()
        for s in solids:
            args.Append(s)
        cb.SetArguments(args)
        if tolerance and tolerance > 0:
            cb.SetFuzzyValue(tolerance)
        cb.Perform()
        if cb.HasErrors():
            raise RuntimeError("merge_cells: BOPAlgo_CellsBuilder reported errors")
        # Take each operand's region into the result (mirrors Topology::Merge's
        # per-operand AddToResult with an empty avoid-list).
        for s in solids:
            take = TopTools_ListOfShape()
            take.Append(s)
            cb.AddToResult(take, TopTools_ListOfShape())
        cb.MakeContainers()
        return list(self._TopologyExplorer(cb.Shape()).solids())

    def non_manifold_merge(self, shapes: list[ShapeHandle], tolerance: float = 1e-6, glue: bool = True) -> ShapeHandle:
        # Non-manifold fuse keeping internal walls as shared faces.
        # BOPAlgo_Builder glues coincident faces so adjacent cells share one face
        # rather than duplicating it; plain BRepAlgoAPI_Fuse would dissolve the
        # partitions.
        from OCC.Core.BOPAlgo import BOPAlgo_Builder, BOPAlgo_GlueEnum
        from OCC.Core.TopTools import TopTools_ListOfShape

        builder = BOPAlgo_Builder()
        args = TopTools_ListOfShape()
        for s in shapes:
            args.Append(s)
        builder.SetArguments(args)
        if glue:
            builder.SetGlue(BOPAlgo_GlueEnum.BOPAlgo_GlueShift)
        if tolerance and tolerance > 0:
            builder.SetFuzzyValue(tolerance)
        builder.Perform()
        if builder.HasErrors():
            raise RuntimeError("non_manifold_merge: BOPAlgo_Builder reported errors")
        return builder.Shape()

    def free_faces(self, solids: list[ShapeHandle]) -> list[ShapeHandle]:
        # Faces belonging to exactly one solid — the outer envelope. Build a
        # compound, map FACE→SOLID ancestors, keep the faces with a single owner.
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID
        from OCC.Core.TopExp import topexp
        from OCC.Core.TopoDS import TopoDS_Compound
        from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

        builder = BRep_Builder()
        comp = TopoDS_Compound()
        builder.MakeCompound(comp)
        for s in solids:
            builder.Add(comp, s)
        amap = TopTools_IndexedDataMapOfShapeListOfShape()
        topexp.MapShapesAndAncestors(comp, TopAbs_FACE, TopAbs_SOLID, amap)
        out = []
        for i in range(1, amap.Size() + 1):
            if amap.FindFromIndex(i).Size() == 1:
                out.append(amap.FindKey(i))
        return out

    def imprint_advanced_faces(
        self,
        advanced_faces: "list",
        imprint_curves: "list[list[tuple[float, float, float]]]",
        tolerance: float = 1e-6,
    ) -> "tuple[list, list]":
        # Curved-plate imprint (OCC General Fuse). The whole per-plate/per-edge loop stays
        # kernel-side in ada.occ.imprint_faces_occ; only backend-neutral ada.geom data returns.
        from ada.occ.imprint_faces_occ import imprint_advanced_faces_occ

        return imprint_advanced_faces_occ(advanced_faces, imprint_curves, tolerance)

    def imprint_planar_faces(
        self,
        outlines: "list[list[tuple[float, float, float]]]",
        imprint_curves: "list[list[tuple[float, float, float]]] | None" = None,
        tolerance: float = 1e-6,
    ) -> "PlanarImprint":
        # General Fuse (BOPAlgo_Builder, same kernel as non_manifold_merge) splits
        # every outline against the others and welds coincident topology, so a
        # deck crossed by a bulkhead comes back as two faces sharing the bulkhead
        # line as ONE edge. Unlike non_manifold_merge this keeps the Modified()
        # history, which is what maps an input outline to the faces it became.
        #
        # `imprint_curves` (e.g. beam axes) join the fuse as wire arguments: a
        # stiffener lying on a plate splits it along its axis, and one merely
        # crossing it drops a vertex on the boundary. That is where most of
        # Genie's face count comes from — a 3.9 m panel with stiffeners every
        # 0.65 m arrives as 6 faces, not 1.
        #
        # The whole model is imprinted and extracted in this single call: the
        # abstraction boundary must not land inside a per-face/per-edge loop, so
        # everything below stays kernel-side and only plain data is returned.
        from OCC.Core.BOPAlgo import BOPAlgo_Builder
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
        )
        from OCC.Core.BRepTools import BRepTools_WireExplorer, breptools
        from OCC.Core.GeomAbs import GeomAbs_Plane
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.TopAbs import (
            TopAbs_EDGE,
            TopAbs_FACE,
            TopAbs_FORWARD,
            TopAbs_REVERSED,
            TopAbs_VERTEX,
            TopAbs_WIRE,
        )
        from OCC.Core.TopExp import TopExp_Explorer, topexp
        from OCC.Core.TopoDS import topods
        from OCC.Core.TopTools import (
            TopTools_IndexedDataMapOfShapeListOfShape,
            TopTools_IndexedMapOfShape,
        )

        from ada.cad import ImprintedEdge, ImprintedFace, PlanarImprint

        def _pnt(p):
            c = list(p)
            return gp_Pnt(float(c[0]), float(c[1]), float(c[2]))

        def _mkface(pts):
            poly = BRepBuilderAPI_MakePolygon()
            for p in pts:
                poly.Add(_pnt(p))
            poly.Close()
            return BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()

        def _mkedges(pts):
            # One TopoDS_Edge per segment rather than a wire: BOPAlgo's history is
            # per-argument, and edges give a direct Modified(edge) -> split edges
            # map, which is what resolves a beam axis to the edges it became.
            out = []
            for a, b in zip(pts, pts[1:]):
                pa, pb = _pnt(a), _pnt(b)
                if pa.Distance(pb) <= max(tolerance, 1e-12):
                    continue  # a zero-length segment would fail the edge build
                out.append(BRepBuilderAPI_MakeEdge(pa, pb).Edge())
            return out

        inputs = [_mkface(o) for o in outlines]
        if not inputs:
            return PlanarImprint(vertices=[], edges=[], faces=[], sources=[], curve_sources=[])

        # per curve, the argument edges it contributed
        curve_edges = [_mkedges(c) for c in (imprint_curves or [])]
        cutters = [e for edges_ in curve_edges for e in edges_]

        if len(inputs) + len(cutters) < 2:
            # General Fuse needs at least two arguments (it raises
            # TooFewArguments otherwise). A lone outline has nothing to imprint
            # against, so it passes straight through.
            builder = None
            res = inputs[0]
        else:
            builder = BOPAlgo_Builder()
            for shape in inputs + cutters:
                builder.AddArgument(shape)
            if tolerance and tolerance > 0:
                builder.SetFuzzyValue(tolerance)
            builder.SetRunParallel(True)
            builder.Perform()
            if builder.HasErrors():
                raise RuntimeError("imprint_planar_faces: BOPAlgo_Builder reported errors")
            res = builder.Shape()

        # Index every unique sub-shape. The map's hasher keys on TShape+Location
        # and ignores orientation, so a FORWARD and a REVERSED use of the same
        # edge collapse to one index — which is exactly the sharing we want.
        #
        # Index the whole result, faces and free edges alike: an imprint curve
        # with no face under it survives as a free edge, and the caller still
        # needs geometry for it (ACIS carries those as wire bodies). Which is
        # which is reported via free_edges.
        fmap, vmap, emap = (TopTools_IndexedMapOfShape() for _ in range(3))
        topexp.MapShapes(res, TopAbs_FACE, fmap)
        topexp.MapShapes(res, TopAbs_VERTEX, vmap)
        topexp.MapShapes(res, TopAbs_EDGE, emap)

        edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
        topexp.MapShapesAndAncestors(res, TopAbs_EDGE, TopAbs_FACE, edge_faces)
        free_edges = []
        for i in range(1, emap.Size() + 1):
            e = emap.FindKey(i)
            if not edge_faces.Contains(e) or edge_faces.FindFromKey(e).Size() == 0:
                free_edges.append(i - 1)

        vertices = []
        for i in range(1, vmap.Size() + 1):
            p = BRep_Tool.Pnt(topods.Vertex(vmap.FindKey(i)))
            vertices.append((p.X(), p.Y(), p.Z()))

        edges = []
        for i in range(1, emap.Size() + 1):
            e = topods.Edge(emap.FindKey(i)).Oriented(TopAbs_FORWARD)
            v0 = topexp.FirstVertex(e, True)
            v1 = topexp.LastVertex(e, True)
            edges.append(ImprintedEdge(start=vmap.FindIndex(v0) - 1, end=vmap.FindIndex(v1) - 1))

        faces = []
        for i in range(1, fmap.Size() + 1):
            f = topods.Face(fmap.FindKey(i))
            surf = BRepAdaptor_Surface(f, True)
            if surf.GetType() != GeomAbs_Plane:
                raise ValueError("imprint_planar_faces: result contains a non-planar face")
            pln = surf.Plane()
            loc, ax, xd = pln.Location(), pln.Axis().Direction(), pln.XAxis().Direction()
            normal = (ax.X(), ax.Y(), ax.Z())
            # A REVERSED face's true outward normal is the opposite of its
            # surface's; flipping here keeps every loop below wound
            # counter-clockwise about the normal we report.
            if f.Orientation() == TopAbs_REVERSED:
                normal = (-normal[0], -normal[1], -normal[2])

            outer = breptools.OuterWire(f)
            wires = []
            wexp = TopExp_Explorer(f, TopAbs_WIRE)
            while wexp.More():
                wires.append(topods.Wire(wexp.Current()))
                wexp.Next()
            wires.sort(key=lambda w: 0 if w.IsSame(outer) else 1)  # outer loop first

            loops = []
            for w in wires:
                loop = []
                we = BRepTools_WireExplorer(w, f)
                while we.More():
                    edge = we.Current()
                    loop.append((emap.FindIndex(edge) - 1, edge.Orientation() == TopAbs_FORWARD))
                    we.Next()
                loops.append(loop)
            faces.append(
                ImprintedFace(
                    origin=(loc.X(), loc.Y(), loc.Z()),
                    normal=normal,
                    ref_direction=(xd.X(), xd.Y(), xd.Z()),
                    loops=loops,
                )
            )

        def _history(shape, index_map):
            """The result sub-shapes ``shape`` became, as indices into ``index_map``.

            Only ones present in the map count: an imprint curve with no plate
            under it survives as a free edge, which bounds no face and is
            deliberately absent from the map (and from the emitted body).
            """
            if builder is None:
                return [index_map.FindIndex(shape) - 1] if index_map.Contains(shape) else []
            mods = builder.Modified(shape)
            if mods is not None and mods.Size() > 0:
                return [index_map.FindIndex(s) - 1 for s in mods if index_map.Contains(s)]
            if not builder.IsDeleted(shape) and index_map.Contains(shape):
                return [index_map.FindIndex(shape) - 1]  # untouched: passes through as itself
            return []

        sources = [_history(f, fmap) for f in inputs]

        curve_sources = []
        for edges_ in curve_edges:
            got = []
            for e in edges_:
                got.extend(i for i in _history(e, emap) if i not in got)
            curve_sources.append(got)

        return PlanarImprint(
            vertices=vertices,
            edges=edges,
            faces=faces,
            sources=sources,
            curve_sources=curve_sources,
            free_edges=free_edges,
        )

    def point_in_solid(self, solid: ShapeHandle, point, tolerance: float = 1e-6) -> "Containment":
        from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT

        clf = BRepClass3d_SolidClassifier(solid)
        clf.Perform(gp_Pnt(float(point[0]), float(point[1]), float(point[2])), tolerance)
        state = clf.State()
        if state == TopAbs_IN:
            return Containment.IN
        if state == TopAbs_OUT:
            return Containment.OUT
        if state == TopAbs_ON:
            return Containment.ON
        return Containment.UNKNOWN

    def center_of_mass(self, shape: ShapeHandle) -> "Point":
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.GProp import GProp_GProps
        from OCC.Core.TopAbs import (
            TopAbs_COMPOUND,
            TopAbs_COMPSOLID,
            TopAbs_FACE,
            TopAbs_SHELL,
            TopAbs_SOLID,
        )

        from ada.geom.points import Point

        props = GProp_GProps()
        st = shape.ShapeType()
        if st in (TopAbs_SOLID, TopAbs_COMPSOLID, TopAbs_COMPOUND):
            brepgprop.VolumeProperties(shape, props)
        elif st in (TopAbs_SHELL, TopAbs_FACE):
            brepgprop.SurfaceProperties(shape, props)
        else:
            brepgprop.LinearProperties(shape, props)
        com = props.CentreOfMass()
        return Point(com.X(), com.Y(), com.Z())

    def shells(self, shape: ShapeHandle) -> list[ShapeHandle]:
        return list(self._TopologyExplorer(shape).shells())

    def wires(self, shape: ShapeHandle) -> list[ShapeHandle]:
        return list(self._TopologyExplorer(shape).wires())

    def wire_points(self, shape: ShapeHandle) -> list[tuple[float, float, float]]:
        # Ordered boundary vertices. For a FACE, walk its outer wire; for a WIRE,
        # walk it directly. BRepTools_WireExplorer yields them in connection
        # order (unlike vertex_points, which is unordered) — needed to rebuild a
        # face as an ordered polygon.
        from OCC.Core.BRepTools import BRepTools_WireExplorer, breptools
        from OCC.Core.TopAbs import TopAbs_FACE
        from OCC.Core.TopoDS import topods

        if shape.ShapeType() == TopAbs_FACE:
            wire = breptools.OuterWire(topods.Face(shape))
        else:
            wire = shape
        pts = []
        exp = BRepTools_WireExplorer(wire)
        while exp.More():
            p = self._BRep_Tool.Pnt(exp.CurrentVertex())
            pts.append((p.X(), p.Y(), p.Z()))
            exp.Next()
        return pts

    def unify_coplanar_faces(self, shape: ShapeHandle) -> ShapeHandle:
        # Merge adjacent same-surface (coplanar) faces into single faces
        # (ShapeUpgrade_UnifySameDomain). On real geometry a cell wall is often
        # split into several coplanar faces; unifying makes it one face so the
        # shared-face match between adjacent cells (by centroid) is robust.
        from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

        unify = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
        unify.Build()
        return unify.Shape()
