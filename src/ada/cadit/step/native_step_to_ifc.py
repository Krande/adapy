"""Native (adacpp) STEP → IFC4X3_ADD2 advanced-B-rep writer — fully C++, no OCC, no ada Assembly.

Wraps ``adacpp.cad.stream_step_to_ifc`` (the parallel per-solid native writer): the same C++ STEP
reader the GLB/mesh paths use resolves each solid's analytic B-rep, emits it as an IfcAdvancedBrep
(IfcSurfaceOfRevolution for cones, IfcBSplineSurface for splines, etc.), and places instances via
IfcMappedItem. Geometry is lossless (every solid/face/edge analytic) and the output validates against
ifcopenshell.validate. Single global id space is handled internally (atomic-reserve + renumber), so
the writer parallelizes across the cgroup-aware thread allotment. Peak memory is O(one solid) per
worker. The per-solid Python ``stream_step_to_ifc`` stays as the fallback when adacpp lacks the verb.

Output is ifcopenshell.validate-clean (IFC4X3_ADD2) across flat + instanced models — verified on
fixtures, 469826 (1308 solids) and the instanced crane (proxy/header GlobalIds are disjoint). The
file declares its real length unit (mm files → MILLIMETRE) and names instanced proxies by their
assembly path (navigable hierarchy in IFC viewers).
"""

from __future__ import annotations

import os
import pathlib

from ada.config import logger


def native_ifc_available() -> bool:
    """True if the adacpp native STEP -> IFC entry point is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "stream_step_to_ifc")
    except Exception:  # noqa: BLE001
        return False


def native_step_to_ifc(
    step_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    schema: str = "IFC4X3_ADD2",
    deflection: float | None = None,
    angular_deg: float | None = None,
    num_threads: int = 0,
    on_progress=None,
) -> dict:
    """Convert ``step_path`` to an IFC file at ``out_path`` with the native adacpp writer.

    ``schema`` is ``"IFC4X3_ADD2"`` (default) or ``"IFC4"`` — the B-rep entities are identical in
    both. ``deflection`` / ``angular_deg`` (the ``ADA_STREAM_TESS_*`` envs) only drive the
    discretize→IfcPolyline fallback for curves with no analytic IFC entity (hyperbola/parabola/...).
    ``num_threads`` 0 = the cgroup-aware streaming allotment. Returns the losslessness audit dict
    (solids_in/out, faces_in/out/dropped, drop_reasons); raises if adacpp is unavailable or no solid
    converted.
    """
    import adacpp

    if deflection is None:
        deflection = float(os.environ.get("ADA_STREAM_TESS_DEFLECTION", "2.0"))
    if angular_deg is None:
        from ada.cad.registry import DEFAULT_STREAM_TESS_ANGULAR_DEG
        angular_deg = float(os.environ.get("ADA_STREAM_TESS_ANGULAR", str(DEFAULT_STREAM_TESS_ANGULAR_DEG)))
    if num_threads <= 0:
        # Bound to the cgroup-aware allotment (not the node's core count) so we don't oversubscribe a
        # CPU-capped pod — same rule as the native GLB/mesh paths.
        try:
            from ada.visit.scene_handling.scene_from_step_stream import _stream_workers

            num_threads = _stream_workers()
        except Exception:  # noqa: BLE001
            num_threads = 0

    if on_progress is not None:
        on_progress("adacpp-native-ifc", 0.1)
    stats = adacpp.cad.stream_step_to_ifc(
        str(step_path),
        str(out_path),
        schema=schema,
        deflection=deflection,
        angular_deg=angular_deg,
        num_threads=num_threads,
    )
    if not stats or stats.get("solids_out", 0) <= 0:
        raise RuntimeError(f"adacpp native stream_step_to_ifc produced no solids for {step_path}: {stats}")
    if stats.get("faces_dropped", 0) or stats.get("drop_reasons"):
        # Surface (never silently swallow) any geometry the writer couldn't represent.
        logger.warning("native STEP->IFC dropped geometry: %s", stats.get("drop_reasons"))
    logger.info(
        "adacpp-native STEP->IFC (%s): solids %s/%s faces %s/%s -> %s (threads=%s)",
        schema,
        stats.get("solids_out"),
        stats.get("solids_in"),
        stats.get("faces_out"),
        stats.get("faces_in"),
        out_path,
        num_threads,
    )
    return stats
