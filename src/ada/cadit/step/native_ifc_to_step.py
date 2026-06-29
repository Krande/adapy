"""Native (adacpp) IFC → AP242 STEP — fully C++, no OCC, no ada Assembly.

Wraps ``adacpp.cad.stream_ifc_to_step``: a native IFC advanced-B-rep reader (IfcAdvancedBrep +
analytic surfaces/curves + IfcMappedItem instancing, reusing the STEP reader's Part-21 parser) builds
ng:: neutral geometry, which the native AP242 STEP emitter re-writes (instances baked). The declared
length unit is preserved. Scope: analytic-B-rep IFC (precise-geometry interop + this codebase's own
STEP->IFC output). No native fallback — IFC->STEP is native-only (the OCC path is the registry default
when adacpp lacks the verb).
"""
from __future__ import annotations

import os
import pathlib

from ada.config import logger


def native_ifc_to_step_available() -> bool:
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "stream_ifc_to_step")
    except Exception:  # noqa: BLE001
        return False


def native_ifc_to_step(
    ifc_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    deflection: float | None = None,
    angular_deg: float | None = None,
    on_progress=None,
) -> dict:
    """Convert ``ifc_path`` to AP242 STEP at ``out_path`` with the native adacpp reader+writer.
    Returns the losslessness audit dict; raises if adacpp is unavailable or no solid converted."""
    import adacpp

    if deflection is None:
        deflection = float(os.environ.get("ADA_STREAM_TESS_DEFLECTION", "2.0"))
    if angular_deg is None:
        angular_deg = float(os.environ.get("ADA_STREAM_TESS_ANGULAR", "20.0"))
    if on_progress is not None:
        on_progress("adacpp-native-ifc2step", 0.1)
    stats = adacpp.cad.stream_ifc_to_step(str(ifc_path), str(out_path), deflection=deflection, angular_deg=angular_deg)
    if not stats or stats.get("solids_out", 0) <= 0:
        raise RuntimeError(f"adacpp native stream_ifc_to_step produced no solids for {ifc_path}: {stats}")
    if stats.get("faces_dropped", 0) or stats.get("drop_reasons"):
        logger.warning("native IFC->STEP dropped geometry: %s", stats.get("drop_reasons"))
    logger.info("adacpp-native IFC->STEP: solids %s/%s -> %s", stats.get("solids_out"), stats.get("solids_in"), out_path)
    return stats
