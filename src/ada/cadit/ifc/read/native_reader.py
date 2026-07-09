"""Native IFC reader: parse via adacpp's C++ IfcResolver (IfcNgeomStream), no ifcopenshell/OCC.

The geometry-shapes counterpart of ``ada.cadit.step.read.native_reader`` for IFC: iterate the native
per-product ``IfcNgeomStream`` (blob + meta = guid/color/transforms/instance_paths) and build the
Assembly's Part/ShapeProxy tree directly — lazy ShapeStore blobs, colour + spatial hierarchy resolved
in C++. This is a geometry-shapes mode (like the STEP product tree): it does NOT reconstruct typed
Beam/Plate/Pipe objects — the ifcopenshell reader remains the typed-object path.
"""

from __future__ import annotations

import pathlib
from typing import Iterator


def native_adacpp_ifc_available() -> bool:
    """True if adacpp's native per-product IFC->NGEOM stream is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "IfcNgeomStream")
    except Exception:
        return False


def native_stream_read_ifc_blobs(ifc_path: str | pathlib.Path) -> Iterator[tuple]:
    """Yield ``(ngeom_blob, gid, color, world_matrices, instance_paths)`` per IFC product WITHOUT
    hydrating the geometry — the foundation of the lazy ShapeStore native IFC import. The C++
    ``StepRootMeta`` is shared with the STEP path, so ``decode_step_root_meta`` decodes it; the IFC
    GlobalId (``meta.guid``) is preferred as the shape id when present."""
    import adacpp

    from ada.cadit.step.read.native_reader import decode_step_root_meta

    for nbytes, meta in adacpp.cad.IfcNgeomStream(str(ifc_path)):
        gid, color, mats, paths = decode_step_root_meta(meta)
        gid = meta.guid or gid
        yield nbytes, gid, color, mats, paths


def native_read_ifc_into(assembly, ifc_path: str | pathlib.Path, *, product_tree: bool = True) -> int:
    """Populate ``assembly`` with a Part/ShapeProxy tree from the native IFC reader. Returns the
    number of shapes added. Lazy ShapeStore by default (``Config().cad_lazy_shape_store``); the eager
    fallback hydrates each blob to a ``Geometry``."""
    from ada.api.shapes import ShapeProxy, ShapeStore
    from ada.api.spatial import Part
    from ada.config import Config

    # Lazy store retains each product's NGEOM blob (the zero-copy ndarray view the C++ IfcNgeomStream
    # yields — no memcpy) and, when cad_shape_store_compress is on, zlib-compresses it in place to cut
    # resident memory — same as the STEP native reader (part.py).
    store = ShapeStore(compress=Config().cad_shape_store_compress) if Config().cad_lazy_shape_store else None
    asm_parts: dict[tuple, Part] = {}

    def _tree_parent(paths):
        # Mirror the STEP reader's _tree_parent: intermediate path levels become nested Parts
        # (reusing same-name siblings); the last level is the solid's own product (excluded).
        path = paths[0] if paths else None
        if not path or len(path) <= 1:
            return assembly
        parent = assembly
        name_path: tuple = ()
        for level in path[:-1]:
            pname = (level[1] if level[1] else f"asm_{level[0]}") if isinstance(level, (tuple, list)) else str(level)
            name_path += (pname,)
            p = asm_parts.get(name_path)
            if p is None:
                existing = parent._parts.get(pname)
                p = existing if existing is not None else parent.add_part(Part(pname))
                asm_parts[name_path] = p
            parent = p
        return parent

    n = 0
    for i, (blob, gid, color, mats, paths) in enumerate(native_stream_read_ifc_blobs(ifc_path)):
        name = gid if gid not in (None, "") else f"{assembly.name}_{i}"
        if store is not None:
            idx = store.add_blob(
                blob, gid=name, color=color, transforms=(mats or None), instance_paths=(paths or None)
            )
            shp = ShapeProxy(name, store, idx, color=color)
        else:
            from ada.api.primitives import Shape
            from ada.cadit.ngeom.deserialize import deserialize_geometries
            from ada.geom import Geometry

            dec = deserialize_geometries(blob)
            if not dec:
                continue
            shp = Shape(
                name,
                Geometry(
                    id=name, geometry=dec[0][1], color=color, transforms=(mats or None), instance_paths=(paths or None)
                ),
                color=color,
            )
        parent = _tree_parent(paths) if product_tree else assembly
        parent.add_shape(shp)
        n += 1
    return n
