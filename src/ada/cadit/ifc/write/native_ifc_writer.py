"""Native IFC writer: emit via adacpp's ``blobs_to_ifc`` (C++ ifc_emit), no ifcopenshell/OCC.

Feeds each shape's NGEOM blob + out-of-band record (colour, world transforms, spatial paths) to the
native emitter, which decodes the blob and writes the analytic IFC solid (IfcExtrudedAreaSolid /
IfcSweptDiskSolid / IfcBooleanResult / IfcAdvancedBrep / ...) + IfcStyledItem + the spatial tree — the
inverse of the native IFC reader, so a native round-trip keeps the CSG analytic (never tessellated).

Only shapes that carry an NGEOM blob (lazy ShapeProxy) are emittable; others are skipped (the caller
falls back to the ifcopenshell writer). Best paired with ``from_ifc(reader="native")``.
"""

from __future__ import annotations

import pathlib


def native_ifc_writer_available() -> bool:
    """True if adacpp's native NGEOM-blobs->IFC emitter is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "blobs_to_ifc")
    except Exception:
        return False


def native_write_ifc(assembly, out_path: str | pathlib.Path, schema: str = "IFC4X3_ADD2") -> dict:
    """Write ``assembly`` to an IFC file natively. Returns the losslessness audit dict
    (solids_in/out, faces_in/out/dropped, drop_reasons). Raises if no shape carries an NGEOM blob."""
    import adacpp
    import numpy as np

    from ada.api.shapes import ShapeProxy
    from ada.base.units import Units

    blobs: list[bytes] = []
    colors: list[list[float]] = []
    transforms: list[list[list[float]]] = []
    paths: list[list[list[tuple]]] = []

    for shp in assembly.get_all_physical_objects():
        if not isinstance(shp, ShapeProxy):
            continue  # native writer needs the raw NGEOM blob (lazy store)
        blob = shp.ngeom_blob()
        if blob is None:
            continue
        rec = shp._shape_store.record(shp._store_index)
        blobs.append(bytes(blob))
        col = rec.color if rec.color is not None else shp.color
        colors.append([float(c) for c in col][:4] if col is not None else [0.0, 0.0, 0.0, -1.0])
        # ShapeRecord transforms are 4x4 (column-major-decoded); flatten back to 16-float column-major.
        mats = rec.transforms or []
        transforms.append([list(np.asarray(m, dtype="float32").flatten(order="F")) for m in mats])
        ip = rec.instance_paths or []
        paths.append([[(int(r), str(nm)) for (r, nm) in lvl] for lvl in ip])

    if not blobs:
        raise RuntimeError("native IFC writer: no shape carries an NGEOM blob (need lazy ShapeProxy shapes)")

    unit_scale = 0.001 if assembly.units == Units.MM else 1.0
    return adacpp.cad.blobs_to_ifc(blobs, colors, transforms, paths, str(out_path), schema, unit_scale)
