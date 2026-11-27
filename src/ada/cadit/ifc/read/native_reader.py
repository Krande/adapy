"""Native IFC reader: parse via adacpp's C++ IfcResolver (IfcNgeomStream), no ifcopenshell/OCC.

The geometry-shapes counterpart of ``ada.cadit.step.read.native_reader`` for IFC: iterate the native
per-product ``IfcNgeomStream`` (blob + meta = guid/color/transforms/instance_paths) and build the
Assembly's Part/ShapeProxy tree directly — lazy ShapeStore blobs, colour + spatial hierarchy resolved
in C++. Beams and plates the native member scan reads fully (``native_members``) come back as the
typed ``Beam`` / ``Plate`` the ifcopenshell reader would build; every other product is a shape.
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


def _native_ifc_products(ifc_path: str | pathlib.Path) -> Iterator[tuple]:
    """Yield ``(ngeom_blob, name, guid, color, world_matrices, instance_paths)`` per IFC product.

    ``name`` is the product's own ``IfcRoot.Name`` (the deepest instance-path level, as the STEP
    reader names a solid after its owning product), falling back to the stream's id; ``guid`` is
    its ``GlobalId`` (empty when the file states none)."""
    import adacpp

    from ada.cadit.step.read.native_reader import decode_step_root_meta

    for nbytes, meta in adacpp.cad.IfcNgeomStream(str(ifc_path)):
        name, color, mats, paths = decode_step_root_meta(meta)
        yield nbytes, name, (meta.guid or ""), color, mats, paths


def native_stream_read_ifc_blobs(ifc_path: str | pathlib.Path) -> Iterator[tuple]:
    """Yield ``(ngeom_blob, gid, color, world_matrices, instance_paths)`` per IFC product WITHOUT
    hydrating the geometry — the foundation of the lazy ShapeStore native IFC import. The C++
    ``StepRootMeta`` is shared with the STEP path, so ``decode_step_root_meta`` decodes it; the IFC
    GlobalId (``meta.guid``) is preferred as the shape id when present."""
    for nbytes, name, guid, color, mats, paths in _native_ifc_products(ifc_path):
        yield nbytes, (guid or name), color, mats, paths


def _native_members_by_guid(ifc_path) -> dict[str, dict]:
    """The member scan's beam/plate records by GlobalId, or {} where the installed adacpp scan
    cannot state a member fully (no structural fields): a member rebuilt from its axis alone would
    be a lesser object than the shape it replaces."""
    from ada.cadit.ifc.read.native_members import (
        _BEAM_CLASSES,
        _PLATE_CLASSES,
        native_members_available,
        scan_ifc_members,
    )

    if not native_members_available():
        return {}
    out: dict[str, dict] = {}
    for rec in scan_ifc_members(ifc_path):
        if "cuts" not in rec:
            return {}
        cls = (rec.get("ifc_class") or "").upper()
        if rec.get("guid") and (cls in _BEAM_CLASSES or cls in _PLATE_CLASSES):
            out[rec["guid"]] = rec
    return out


def native_read_ifc_into(
    assembly, ifc_path: str | pathlib.Path, *, product_tree: bool = True, members: bool = True
) -> int:
    """Populate ``assembly`` with a Part/ShapeProxy tree from the native IFC reader. Returns the
    number of objects added. Lazy ShapeStore by default (``Config().cad_lazy_shape_store``); the eager
    fallback hydrates each blob to a ``Geometry``.

    ``members``: a beam or plate the native member scan reads fully (its section, cuts, material and
    property sets) is added as that ``Beam`` / ``Plate`` -- the object the ifcopenshell reader would
    build -- in place of its shape. A product the scan cannot rebuild stays a shape."""
    from ada.api.shapes import ShapeProxy, ShapeStore
    from ada.api.spatial import Part
    from ada.cadit.ifc.read.native_members import member_object
    from ada.config import Config, logger

    by_guid = _native_members_by_guid(ifc_path) if members else {}
    materials: dict = {}

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
    for i, (blob, pname, guid, color, mats, paths) in enumerate(_native_ifc_products(ifc_path)):
        parent = _tree_parent(paths) if product_tree else assembly
        rec = by_guid.pop(guid, None) if guid else None
        if rec is not None:
            try:
                obj = member_object(rec, materials)
            except Exception as exc:  # noqa: BLE001 - not rebuildable: keep the product as its shape
                logger.debug(f"native IFC reader: member {rec.get('name')!r} kept as a shape ({exc})")
                obj = None
            if obj is not None:
                parent.add_object(obj)
                n += 1
                continue
        # Named after the product, as the ifcopenshell reader names it, so an object keeps its name
        # through a write/read round-trip; the GlobalId rides as the shape's guid. A product the
        # file leaves unnamed falls back to its GlobalId, then to a positional name.
        name = pname or guid or f"{assembly.name}_{i}"
        ident = {"guid": guid} if guid else {}
        if store is not None:
            idx = store.add_blob(blob, gid=name, color=color, transforms=(mats or None), instance_paths=(paths or None))
            shp = ShapeProxy(name, store, idx, color=color, **ident)
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
                **ident,
            )
        parent.add_shape(shp)
        n += 1
    return n
