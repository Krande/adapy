"""Fully-native IFC->GLB via adacpp (no ifcopenshell, no OCC).

Calls a single adacpp C++ entry (``stream_ifc_to_glb``) that does everything in-process: the pure-C++
IfcResolver resolves each product's geometry + presentation colour + spatial-structure path, libtess2
tessellates (welded, crease-angle smooth normals), and the merge-by-colour GLB writer bakes it to
metres. The GLB carries the same viewer picking contract as the native STEP path — merge-by-colour
materials + per-material ``draw_ranges_node<matidx>`` and a per-product ``id_hierarchy`` in
``scenes[0].extras`` — validated field-for-field against ``from_ifc`` -> GLB (geometry + colour +
hierarchy + names; IFC property sets never live in the GLB, they are fetched on selection).

Single-threaded v1 (IfcResolver's colour/rel maps are built per instance); curve-only bodies
(alignment axes) are skipped. Honours the same ``ADA_STREAM_TESS_DEFLECTION`` / ``ADA_STREAM_TESS_
ANGULAR`` env as the STEP native path.
"""

from __future__ import annotations

import os
import pathlib

from ada.config import logger


def native_ifc_glb_available() -> bool:
    """True if the adacpp native IFC->GLB entry point is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "stream_ifc_to_glb")
    except Exception:
        return False


def native_ifc_to_glb(
    ifc_path: str | pathlib.Path,
    glb_path: str | pathlib.Path,
    deflection: float | None = None,
    angular_deg: float | None = None,
    meshopt: bool = True,
    on_progress=None,
) -> dict:
    """Convert ``ifc_path`` to a GLB at ``glb_path`` with the native adacpp IFC pipeline.

    ``deflection`` / ``angular_deg`` default to the ``ADA_STREAM_TESS_DEFLECTION`` (2.0) /
    ``ADA_STREAM_TESS_ANGULAR`` (20.0) env, matching the streaming path. ``meshopt`` (default on) bakes
    ``EXT_meshopt_compression`` inline in the C++ writer. Returns ``{solids, total, skipped}``. Raises
    if adacpp is unavailable or the conversion fails (the converter falls back per its fallback chain).
    """
    import adacpp

    if deflection is None:
        deflection = float(os.environ.get("ADA_STREAM_TESS_DEFLECTION", "2.0"))
    if angular_deg is None:
        from ada.cad.registry import DEFAULT_STREAM_TESS_ANGULAR_DEG

        angular_deg = float(os.environ.get("ADA_STREAM_TESS_ANGULAR", str(DEFAULT_STREAM_TESS_ANGULAR_DEG)))

    if on_progress is not None:
        on_progress("adacpp-native-ifc", 0.1)

    n = adacpp.cad.stream_ifc_to_glb(
        str(ifc_path), str(glb_path), deflection=deflection, angular_deg=angular_deg, meshopt=meshopt
    )
    if n < 0:
        raise RuntimeError(f"adacpp native stream_ifc_to_glb failed for {ifc_path}")

    logger.info("adacpp-native IFC->GLB: %s products -> %s", n, glb_path)
    print(
        f"[adacpp-native-ifc] {n} products -> {glb_path} "
        f"(deflection={deflection}, angular={angular_deg}, meshopt={meshopt})",
        flush=True,
    )
    if on_progress is not None:
        on_progress("ready", 1.0)
    return {"solids": n, "total": n, "skipped": 0}
