"""IFC backends: which library reads, writes, tessellates and queries an IFC file.

Two backends implement one operation-level protocol (:class:`IfcBackend`):

- ``native``: adacpp's C++ IFC reader and writer (``IfcNgeomStream``, the member scan,
  ``stream_ngeom_to_ifc``, ``stream_ifc_to_glb``). No ifcopenshell and no OCC, so it runs in the
  browser (Pyodide) as well as in CPython.
- ``ifcopenshell``: adapy's full typed reader and writer on top of ifcopenshell. It is the
  reference the native backend is measured against, and the only one that can ``author`` (hand
  out an editable ``ifcopenshell.file``).

Each backend declares what it can do as capability flags (the ``CAP_*`` names below). A caller
asks :func:`select_ifc_backend` for an operation and the capabilities it needs:

1. ``prefer`` (an argument, e.g. ``Assembly.read_ifc(reader=...)``) wins outright.
2. Else the ``ADAPY_IFC_BACKEND`` environment variable (``native`` or ``ifcopenshell``), used when
   that backend is available and can do the operation; otherwise the selection falls through.
3. Else auto-detection: the native backend first (always first under emscripten), then
   ifcopenshell, taking the first that is available and has every required capability. When none
   has them all, the first available backend that can do the operation at all is used, with a
   warning, so a runtime without ifcopenshell still reads and writes what the native side covers.

The native writer writes members only where ``adacpp.cad.ifc_member_records_version() >= 1``.
"""

from __future__ import annotations

import io
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from ada.config import logger

ENV_VAR = "ADAPY_IFC_BACKEND"

# Operations.
OP_READ = "read"
OP_WRITE = "write"
OP_TESSELLATE = "tessellate"
OP_QUERY = "query"
OP_AUTHOR = "author"
OPERATIONS = (OP_READ, OP_WRITE, OP_TESSELLATE, OP_QUERY, OP_AUTHOR)

# Read capabilities.
CAP_READ_SHAPES = "read.shapes"  # every product with a body, named, with its GlobalId and colour
CAP_READ_MEMBERS = "read.members"  # straight extruded beams and flat plates as Beam / Plate
CAP_READ_SPATIAL_TREE = "read.spatial_tree"  # the Part tree from the spatial structure
CAP_READ_TYPED = "read.typed"  # every element kind adapy models (walls, pipes, fasteners, swept members...)
CAP_READ_FILE_OBJECT = "read.file_object"  # reads an in-memory ifcopenshell.file, not only a path
# Write capabilities.
CAP_WRITE_SHAPES = "write.shapes"  # every object with a solid, as a body
CAP_WRITE_MEMBERS = "write.members"  # straight beams and flat plates as IfcBeam / IfcPlate
CAP_WRITE_TYPED = "write.typed"  # every element kind, the spatial tree, nested property sets
CAP_WRITE_FILE_OBJECT = "write.file_object"  # returns an in-memory file instead of writing a path
# Tessellation and queries.
CAP_TESSELLATE_GLB = "tessellate.glb"  # IFC file -> GLB
CAP_QUERY_PRODUCTS = "query.products"  # products with a body: guid, name, spatial parent
CAP_QUERY_ALL_PRODUCTS = "query.all_products"  # also products without a body, and every class
CAP_QUERY_PSETS = "query.psets"  # property and quantity sets of every product
CAP_AUTHOR = "author"  # an editable ifcopenshell.file


@dataclass
class IfcProductRecord:
    """One product of an IFC file, as :meth:`IfcBackend.query` returns it.

    ``ifc_class`` is None where the backend cannot tell it; ``parent`` is the name of the spatial
    element or assembly the product sits in (None at the root); ``psets`` maps a property or
    quantity set name to its values."""

    guid: str
    name: str | None
    ifc_class: str | None = None
    parent: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    psets: dict[str, dict[str, Any]] = field(default_factory=dict)
    material: str | None = None


@runtime_checkable
class IfcBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def capabilities(self) -> frozenset[str]: ...

    def read(self, assembly, ifc_file, **kwargs) -> None: ...

    def write(self, assembly, destination, **kwargs): ...

    def tessellate(self, ifc_file, glb_path, **kwargs) -> dict: ...

    def query(self, ifc_file) -> Iterator[IfcProductRecord]: ...

    def author(self, assembly): ...


def _operation_caps(caps: Iterable[str], operation: str) -> bool:
    caps = set(caps)
    if operation == OP_AUTHOR:
        return CAP_AUTHOR in caps
    return any(c.startswith(operation + ".") for c in caps)


def is_emscripten() -> bool:
    return sys.platform == "emscripten"


class NativeIfcBackend:
    """adacpp's C++ IFC reader and writer."""

    name = "native"

    def available(self) -> bool:
        from ada.cadit.ifc.read.native_reader import native_adacpp_ifc_available

        return native_adacpp_ifc_available()

    @staticmethod
    def member_records_version() -> int:
        from ada.cadit.ifc.write.native_ifc_writer import native_ifc_member_records

        return native_ifc_member_records()

    def capabilities(self) -> frozenset[str]:
        try:
            import adacpp
        except Exception:
            return frozenset()
        cad = adacpp.cad
        caps: set[str] = set()
        if hasattr(cad, "IfcNgeomStream"):
            caps |= {CAP_READ_SHAPES, CAP_READ_SPATIAL_TREE, CAP_QUERY_PRODUCTS}
        from ada.cadit.ifc.read.native_members import native_members_available

        if hasattr(cad, "IfcNgeomStream") and native_members_available() and self.member_records_version() >= 1:
            caps.add(CAP_READ_MEMBERS)
        if hasattr(cad, "stream_ngeom_to_ifc") or hasattr(cad, "blobs_to_ifc"):
            caps.add(CAP_WRITE_SHAPES)
            if self.member_records_version() >= 1:
                caps.add(CAP_WRITE_MEMBERS)
        if hasattr(cad, "stream_ifc_to_glb"):
            caps.add(CAP_TESSELLATE_GLB)
        return frozenset(caps)

    def read(self, assembly, ifc_file, **kwargs) -> None:
        from ada.cadit.ifc.read.native_reader import native_read_ifc_into

        if not isinstance(ifc_file, (str, os.PathLike)):
            raise TypeError("the native IFC reader reads a file path, not an in-memory file")
        if not self.available():
            raise RuntimeError("reader='native' requires adacpp with IfcNgeomStream")
        native_read_ifc_into(assembly, ifc_file)

    def write(self, assembly, destination, **kwargs):
        from ada.cadit.ifc.write.native_ifc_writer import (
            native_ifc_writer_available,
            native_write_ifc,
        )

        if destination is None or destination == "object":
            raise ValueError("to_ifc(writer='native') needs an on-disk destination")
        if not native_ifc_writer_available():
            raise RuntimeError("writer='native' requires adacpp with blobs_to_ifc")
        destination = pathlib.Path(destination)
        os.makedirs(destination.parent, exist_ok=True)
        return native_write_ifc(assembly, destination, **kwargs)

    def tessellate(self, ifc_file, glb_path, **kwargs) -> dict:
        from ada.cadit.ifc.native_ifc_to_glb import native_ifc_to_glb

        return native_ifc_to_glb(ifc_file, glb_path, **kwargs)

    def query(self, ifc_file) -> Iterator[IfcProductRecord]:
        from ada.cadit.ifc.read.native_members import (
            native_members_available,
            scan_ifc_members,
        )
        from ada.cadit.ifc.read.native_reader import _native_ifc_products

        members: dict[str, dict] = {}
        if native_members_available():
            for rec in scan_ifc_members(ifc_file):
                if rec.get("guid"):
                    members[rec["guid"]] = rec
        for _blob, name, guid, _color, _mats, paths in _native_ifc_products(ifc_file):
            path = paths[0] if paths else None
            parent = None
            if path and len(path) > 1:
                level = path[-2]
                parent = level[1] if isinstance(level, (tuple, list)) else str(level)
            rec = members.get(guid, {})
            attributes = {k: rec[k] for k in ("description", "object_type", "tag") if rec.get(k)}
            yield IfcProductRecord(
                guid=guid,
                name=name or None,
                ifc_class=rec.get("ifc_class") or None,
                parent=parent,
                attributes=attributes,
                psets=dict(rec.get("psets") or {}),
                material=rec.get("material") or None,
            )

    def author(self, assembly):
        raise NotImplementedError("the native IFC backend cannot author an ifcopenshell.file; use 'ifcopenshell'")


class IfcOpenShellBackend:
    """adapy's typed IFC reader and writer on ifcopenshell."""

    name = "ifcopenshell"

    def available(self) -> bool:
        import importlib.util

        try:
            return importlib.util.find_spec("ifcopenshell") is not None
        except (ImportError, ValueError):
            return False

    def capabilities(self) -> frozenset[str]:
        if not self.available():
            return frozenset()
        return frozenset(
            {
                CAP_READ_SHAPES,
                CAP_READ_MEMBERS,
                CAP_READ_SPATIAL_TREE,
                CAP_READ_TYPED,
                CAP_READ_FILE_OBJECT,
                CAP_WRITE_SHAPES,
                CAP_WRITE_MEMBERS,
                CAP_WRITE_TYPED,
                CAP_WRITE_FILE_OBJECT,
                CAP_TESSELLATE_GLB,
                CAP_QUERY_PRODUCTS,
                CAP_QUERY_ALL_PRODUCTS,
                CAP_QUERY_PSETS,
                CAP_AUTHOR,
            }
        )

    def read(self, assembly, ifc_file, data_only=False, elements2part=None, **kwargs) -> None:
        assembly.ifc_store.load_ifc_content_from_file(ifc_file, data_only=data_only, elements2part=elements2part)

    def write(self, assembly, destination, **kwargs):
        # Assembly.to_ifc owns the ifcopenshell write path (streaming, validation, sync).
        return assembly.to_ifc(destination, writer="ifcopenshell", **kwargs)

    def tessellate(self, ifc_file, glb_path, **kwargs) -> dict:
        glb = ifcopenshell_ifc_to_glb_bytes(str(ifc_file))
        pathlib.Path(glb_path).write_bytes(glb)
        return {"solids": None, "total": None, "skipped": None}

    def query(self, ifc_file) -> Iterator[IfcProductRecord]:
        import ifcopenshell
        import ifcopenshell.util.element as el

        from ada.cadit.ifc.store import open_ifc_file

        f = ifc_file if isinstance(ifc_file, ifcopenshell.file) else open_ifc_file(ifc_file)
        for product in f.by_type("IfcProduct"):
            if product.is_a("IfcOpeningElement"):
                continue
            parent = el.get_container(product) or el.get_aggregate(product)
            mat = el.get_material(product)
            attributes = {
                k: getattr(product, k, None)
                for k in ("Description", "ObjectType", "Tag")
                if getattr(product, k, None) not in (None, "")
            }
            yield IfcProductRecord(
                guid=product.GlobalId,
                name=product.Name,
                ifc_class=product.is_a(),
                parent=getattr(parent, "Name", None) if parent is not None else None,
                attributes=attributes,
                psets=el.get_psets(product),
                material=getattr(mat, "Name", None) if mat is not None else None,
            )

    def author(self, assembly):
        return assembly.ifc_store.f


def ifcopenshell_ifc_to_glb_bytes(src: str) -> bytes:
    """IFC -> GLB through ifcopenshell's geometry iterator and trimesh."""
    import ifcopenshell
    import ifcopenshell.geom
    import numpy as np
    import trimesh

    from ada.cadit.ifc.store import open_ifc_file

    ifc = open_ifc_file(src)
    settings = ifcopenshell.geom.settings()
    try:
        settings.set("use-world-coords", True)
    except Exception:
        pass
    iterator = ifcopenshell.geom.iterator(settings, ifc)
    scene = trimesh.Scene()
    n = 0
    if iterator.initialize():
        while True:
            try:
                shape = iterator.get()
                geom = shape.geometry
                verts = np.asarray(geom.verts, dtype=np.float32).reshape(-1, 3)
                faces = np.asarray(geom.faces, dtype=np.int32).reshape(-1, 3)
                if faces.size:
                    scene.add_geometry(
                        trimesh.Trimesh(vertices=verts, faces=faces, process=False),
                        node_name=(getattr(shape, "name", None) or shape.guid or f"m{n}"),
                    )
                    n += 1
            except Exception:
                pass
            if not iterator.next():
                break
    if n == 0:
        raise RuntimeError("no meshable geometry produced from this IFC")
    buf = io.BytesIO()
    scene.export(buf, file_type="glb")
    return buf.getvalue()


def native_read_covers_members(assembly) -> bool:
    """Whether a native read rebuilt every structural member it met: no beam / column / member /
    plate / slab product was left as a plain shape. Products of other classes (proxies, walls, ...)
    are shapes in either reader and do not count."""
    from ada.api.shapes import ShapeProxy
    from ada.cadit.ifc.read.native_members import _BEAM_CLASSES, _PLATE_CLASSES

    classes = assembly.__dict__.get("_native_ifc_classes") or {}
    member_classes = _BEAM_CLASSES | _PLATE_CLASSES
    for obj in assembly.get_all_physical_objects():
        if isinstance(obj, ShapeProxy) and (classes.get(obj.guid) or "").upper() in member_classes:
            return False
    return True


def read_ifc_members_model(ifc_file, prefer: str | None = None):
    """An Assembly with the members of ``ifc_file``, read natively where that is complete.

    For a caller that works on the structural members (clash detail, contacts, a member rebuild):
    the native reader returns every straight beam and flat plate as ``Beam`` / ``Plate`` and every
    other product as a shape. When it left a structural member as a shape (a swept, revolved or
    tapered beam, a curved plate) and ifcopenshell is installed, the file is read again with the
    full reader so that member is typed too. ``prefer`` / ``ADAPY_IFC_BACKEND`` pin the reader."""
    import ada

    backend = select_ifc_backend(prefer, operation=OP_READ, require={CAP_READ_MEMBERS})
    if backend.name != "native":
        return ada.from_ifc(ifc_file, reader=backend.name)
    a = ada.from_ifc(ifc_file, reader="native")
    explicit = bool(prefer) or os.environ.get(ENV_VAR, "").strip().lower() in ("native", "adacpp")
    if explicit or native_read_covers_members(a) or not IfcOpenShellBackend().available():
        return a
    logger.info(f"native IFC read left structural members as shapes in {ifc_file}; reading it with ifcopenshell")
    return ada.from_ifc(ifc_file, reader="ifcopenshell")


_BACKENDS: dict[str, type] = {"native": NativeIfcBackend, "ifcopenshell": IfcOpenShellBackend}
_ALIASES = {"adacpp": "native", "ios": "ifcopenshell", "ifcos": "ifcopenshell"}


def get_ifc_backend(name: str) -> IfcBackend:
    key = _ALIASES.get(name.lower(), name.lower())
    if key not in _BACKENDS:
        raise ValueError(f"Unknown IFC backend {name!r} (expected one of {sorted(_BACKENDS)})")
    return _BACKENDS[key]()


def _auto_order() -> list[str]:
    # Native first everywhere it is available; under emscripten there is no ifcopenshell wheel to
    # prefer unless a job loaded one, and the native reader costs no extra download.
    return ["native", "ifcopenshell"]


def select_ifc_backend(
    prefer: str | None = None, *, operation: str = OP_READ, require: Iterable[str] = ()
) -> IfcBackend:
    """Pick the IFC backend for ``operation`` that has every capability in ``require``.

    ``prefer`` is returned as is (its own call raises if it cannot do the operation). The
    ``ADAPY_IFC_BACKEND`` environment variable is honoured when that backend is available and can do
    the operation; then the backends are tried in auto order. See the module docstring."""
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown IFC operation {operation!r}")
    if prefer:
        return get_ifc_backend(prefer)

    required = frozenset(require)
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        backend = get_ifc_backend(env)
        if backend.available() and _operation_caps(backend.capabilities(), operation):
            return backend
        logger.debug(f"{ENV_VAR}={env!r} cannot {operation} here; selecting automatically")

    candidates = [get_ifc_backend(n) for n in _auto_order()]
    usable = [(b, b.capabilities()) for b in candidates if b.available()]
    for backend, caps in usable:
        if required <= caps and _operation_caps(caps, operation):
            return backend
    for backend, caps in usable:
        if _operation_caps(caps, operation):
            missing = sorted(required - caps)
            logger.warning(f"IFC {operation}: no backend here has {missing}; using {backend.name!r} without them")
            return backend
    raise ImportError(
        f"No IFC backend can {operation} here: install adacpp (native) or ifcopenshell "
        f"(tried {[b.name for b in candidates]})"
    )
