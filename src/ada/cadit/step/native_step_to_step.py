"""Native (adacpp) STEP → AP242 STEP re-export — fully C++, no OCC, no ada Assembly.

Wraps ``adacpp.cad.stream_step_to_step`` (the parallel per-solid native AP242 writer): the same C++
reader the GLB/IFC paths use resolves each solid's analytic B-rep and re-emits it as a
MANIFOLD_SOLID_BREP part (analytic surfaces incl. native CONICAL_SURFACE + rational B-splines;
instances baked). Round-trip-lossless (re-reading the output recovers the same geometry). The
per-solid Python ``Ap242StreamWriter`` stays as the fallback when adacpp lacks the verb.
"""

from __future__ import annotations

import os
import pathlib

from ada.config import logger


def native_step_to_step_available() -> bool:
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "stream_step_to_step")
    except Exception:  # noqa: BLE001
        return False


def native_step_to_step(
    step_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    deflection: float | None = None,
    angular_deg: float | None = None,
    num_threads: int = 0,
    on_progress=None,
) -> dict:
    """Re-export ``step_path`` to AP242 STEP at ``out_path`` with the native adacpp writer.
    ``num_threads`` 0 = the cgroup-aware allotment. Returns the losslessness audit dict; raises if
    adacpp is unavailable or no solid converted."""
    import adacpp

    if deflection is None:
        deflection = float(os.environ.get("ADA_STREAM_TESS_DEFLECTION", "2.0"))
    if angular_deg is None:
        angular_deg = float(os.environ.get("ADA_STREAM_TESS_ANGULAR", "20.0"))
    if num_threads <= 0:
        try:
            from ada.visit.scene_handling.scene_from_step_stream import _stream_workers

            num_threads = _stream_workers()
        except Exception:  # noqa: BLE001
            num_threads = 0
    if on_progress is not None:
        on_progress("adacpp-native-step", 0.1)
    stats = adacpp.cad.stream_step_to_step(
        str(step_path), str(out_path), deflection=deflection, angular_deg=angular_deg, num_threads=num_threads
    )
    if not stats or stats.get("solids_out", 0) <= 0:
        raise RuntimeError(f"adacpp native stream_step_to_step produced no solids for {step_path}: {stats}")
    if stats.get("faces_dropped", 0) or stats.get("drop_reasons"):
        logger.warning("native STEP->STEP dropped geometry: %s", stats.get("drop_reasons"))
    logger.info(
        "adacpp-native STEP->STEP: solids %s/%s -> %s", stats.get("solids_out"), stats.get("solids_in"), out_path
    )
    return stats
