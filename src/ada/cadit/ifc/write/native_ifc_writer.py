"""Native IFC writer: emit via adacpp's C++ ifc_emit (no ifcopenshell/OCC).

Feeds each shape's NGEOM blob + out-of-band record (colour, world transforms, spatial paths) to the
native emitter, which decodes the blob and writes the analytic IFC solid (IfcExtrudedAreaSolid /
IfcSweptDiskSolid / IfcBooleanResult / IfcAdvancedBrep / ...) + IfcStyledItem + the spatial tree — the
inverse of the native IFC reader, so a native round-trip keeps the CSG analytic (never tessellated).

Blob source per shape: a lazy ``ShapeProxy`` hands over its stored blob as-is (zero re-encode); any
other physical object is serialized from its parametric ``solid_geom()`` through the NGEOM encoder
(the same wire), so freshly-built models — e.g. a Genie-XML import with thickened curved shells —
take the native path too. Objects with neither (raw-OCC imports) are skipped and logged (the caller
falls back to the ifcopenshell writer for full coverage). Best paired with ``from_ifc(reader="native")``.

Emits through ``stream_ngeom_to_ifc`` (records pulled lazily, bounded memory) when the adacpp build
has it, else the older parallel-list ``blobs_to_ifc``.
"""

from __future__ import annotations

import pathlib

from ada.config import logger


def native_ifc_writer_available() -> bool:
    """True if adacpp's native NGEOM-blobs->IFC emitter is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "blobs_to_ifc") or hasattr(adacpp.cad, "stream_ngeom_to_ifc")
    except Exception:
        return False


def _shape_records(assembly):
    """(name, blob, rgba|None, transforms|None, paths|None) per emittable shape + a skip count."""
    import numpy as np

    from ada.api.mass import MassPoint
    from ada.api.shapes import ShapeProxy
    from ada.cadit.ngeom.serialize import _Encoder, _Unsupported

    records: list[tuple] = []
    skipped = 0
    for shp in assembly.get_all_physical_objects(pipe_to_segments=True):
        if isinstance(shp, MassPoint):
            continue  # no geometry
        name = str(shp.name)
        if isinstance(shp, ShapeProxy):
            blob = shp.ngeom_blob()
            if blob is not None:
                rec = shp._shape_store.record(shp._store_index)
                col = rec.color if rec.color is not None else shp.color
                rgba = [float(c) for c in col][:4] if col is not None else None
                # ShapeRecord transforms are 4x4 (column-major-decoded); flatten back to 16-float
                # column-major for the wire.
                mats = rec.transforms or None
                tfs = [list(np.asarray(m, dtype="float32").flatten(order="F")) for m in mats] if mats else None
                ip = rec.instance_paths or None
                paths = [[(int(r), str(nm)) for (r, nm) in lvl] for lvl in ip] if ip else None
                records.append((name, bytes(blob), rgba, tfs, paths))
                continue
        # Non-proxy (or a proxy without a stored blob): serialize the parametric solid_geom().
        try:
            geom = shp.solid_geom()
            enc = _Encoder()
            idx = enc.root(geom)
            blob = enc.finish([(idx, name)])
        except _Unsupported as ex:
            skipped += 1
            logger.debug(
                "native IFC writer: %s (%s) unsupported by the NGEOM encoder: %s", name, type(shp).__name__, ex
            )
            continue
        except Exception as ex:
            skipped += 1
            logger.debug("native IFC writer: %s (%s) not serializable: %s", name, type(shp).__name__, ex)
            continue
        col = getattr(shp, "color", None)
        rgba = [float(c) for c in col][:4] if col is not None else None
        records.append((name, blob, rgba, None, None))
    return records, skipped


def native_write_ifc(assembly, out_path: str | pathlib.Path, schema: str = "IFC4X3_ADD2") -> dict:
    """Write ``assembly`` to an IFC file natively. Returns the losslessness audit dict
    (solids_in/out, faces_in/out/dropped, drop_reasons). Raises if no shape yields an NGEOM blob."""
    import adacpp

    from ada.base.units import Units

    records, skipped = _shape_records(assembly)
    if not records:
        raise RuntimeError("native IFC writer: no shape yields an NGEOM blob (stored or via solid_geom())")
    if skipped:
        logger.warning("native IFC writer: %d object(s) skipped (no NGEOM-serializable geometry)", skipped)

    unit_scale = 0.001 if assembly.units == Units.MM else 1.0
    if hasattr(adacpp.cad, "stream_ngeom_to_ifc"):
        return adacpp.cad.stream_ngeom_to_ifc(records, str(out_path), schema=schema, unit_scale=unit_scale)
    # Older adacpp: the parallel-list form (colour sentinel alpha=-1 for "no colour").
    blobs = [r[1] for r in records]
    colors = [r[2] if r[2] is not None else [0.0, 0.0, 0.0, -1.0] for r in records]
    transforms = [r[3] or [] for r in records]
    paths = [r[4] or [] for r in records]
    return adacpp.cad.blobs_to_ifc(blobs, colors, transforms, paths, str(out_path), schema, unit_scale)
