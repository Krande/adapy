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
from typing import Any, Protocol, runtime_checkable


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

    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle: ...
    def make_cylinder(self, radius: float, height: float) -> ShapeHandle: ...
    def make_sphere(self, radius: float) -> ShapeHandle: ...
    def tessellate(self, shape: ShapeHandle, linear_deflection: float = -1.0) -> Mesh: ...
    def bbox(self, shape: ShapeHandle) -> tuple[float, float, float, float, float, float]: ...
    def read_step_bytes(self, data: bytes) -> ShapeHandle: ...
    def write_glb_bytes(self, shape: ShapeHandle, linear_deflection: float = 0.1) -> bytes: ...


class AdacppBackend:
    """Backend backed by adacpp.cad — works in native CPython AND pyodide.
    The wasm build is the only path that works under pyodide today; the
    native build links real OCCT and is functionally equivalent to OccBackend
    for the operations we support so far."""

    name = "adacpp"

    def __init__(self) -> None:
        from adacpp import cad
        self._cad = cad

    def make_box(self, dx: float, dy: float, dz: float) -> ShapeHandle:
        return self._cad.make_box(dx, dy, dz)

    def make_cylinder(self, radius: float, height: float) -> ShapeHandle:
        return self._cad.make_cylinder(radius, height)

    def make_sphere(self, radius: float) -> ShapeHandle:
        return self._cad.make_sphere(radius)

    def tessellate(self, shape: ShapeHandle, linear_deflection: float = -1.0) -> Mesh:
        return self._cad.tessellate(shape, linear_deflection)

    def bbox(self, shape: ShapeHandle) -> tuple[float, float, float, float, float, float]:
        return tuple(self._cad.bbox(shape))

    def read_step_bytes(self, data: bytes) -> ShapeHandle:
        return self._cad.read_step_bytes(data)

    def write_glb_bytes(self, shape: ShapeHandle, linear_deflection: float = 0.1) -> bytes:
        # adacpp returns nb::bytes; bytes(...) coerces it cleanly to a CPython
        # bytes object so callers don't need to know about the underlying type.
        return bytes(self._cad.write_glb_bytes(shape, linear_deflection))

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
        from OCC.Core.BRepBndLib import brepbndlib
        from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
        from OCC.Core.IFSelect import IFSelect_RetDone
        from OCC.Core.Message import Message_ProgressRange
        from OCC.Core.RWGltf import RWGltf_CafWriter, RWGltf_WriterTrsfFormat
        from OCC.Core.RWMesh import RWMesh_CoordinateSystem_Zup
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.TCollection import TCollection_AsciiString, TCollection_ExtendedString
        from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
        from OCC.Core.TDocStd import TDocStd_Document
        from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
        from ada.occ.tessellating import tessellate_shape
        from ada.occ.utils import (
            make_box_by_points,
            make_cylinder as _occ_make_cylinder,
            make_sphere as _occ_make_sphere,
        )
        self._Bnd_Box = Bnd_Box
        self._brepbndlib = brepbndlib
        self._BRepMesh_IncrementalMesh = BRepMesh_IncrementalMesh
        self._IFSelect_RetDone = IFSelect_RetDone
        self._Message_ProgressRange = Message_ProgressRange
        self._RWGltf_CafWriter = RWGltf_CafWriter
        self._RWGltf_WriterTrsfFormat_Compact = RWGltf_WriterTrsfFormat.RWGltf_WriterTrsfFormat_Compact
        self._RWMesh_CoordinateSystem_Zup = RWMesh_CoordinateSystem_Zup
        self._STEPControl_Reader = STEPControl_Reader
        self._TCollection_AsciiString = TCollection_AsciiString
        self._TCollection_ExtendedString = TCollection_ExtendedString
        self._TColStd_IndexedDataMapOfStringString = TColStd_IndexedDataMapOfStringString
        self._TDocStd_Document = TDocStd_Document
        self._XCAFDoc_DocumentTool = XCAFDoc_DocumentTool
        self._tessellate_shape = tessellate_shape
        self._make_box_by_points = make_box_by_points
        self._make_cylinder = _occ_make_cylinder
        self._make_sphere = _occ_make_sphere

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

    def bbox(self, shape: ShapeHandle) -> tuple[float, float, float, float, float, float]:
        # AddOptimal(useTriangulation=False, useShapeTolerance=False) matches
        # adacpp.cad.bbox: analytic geometric extents only, ignoring any cached
        # triangulation jitter. Both backends produce identical bbox values
        # for the same primitive.
        bb = self._Bnd_Box()
        self._brepbndlib.AddOptimal(shape, bb, False, False)
        if bb.IsVoid():
            raise RuntimeError("bbox: empty bounding box (shape has no geometry)")
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return (xmin, ymin, zmin, xmax, ymax, zmax)

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


__all__ = [
    "AdacppBackend",
    "CadBackend",
    "Mesh",
    "OccBackend",
    "ShapeHandle",
    "select_backend",
]
