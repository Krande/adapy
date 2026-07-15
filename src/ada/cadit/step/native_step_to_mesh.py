"""Fully-native STEP -> STL / OBJ via adacpp (the parallel mesh sibling of ``native_step_to_glb``).

Calls a single adacpp C++ entry point (``stream_step_to_mesh``) that does EVERYTHING natively and
in-process: the same Part-21 reader (offset index + per-statement pread, bounded memory) -> per-solid
resolve -> libtess2 tessellation across a C++ thread pool, but bakes each instance's world placement
and streams the triangles straight to a binary STL or Wavefront OBJ file. No Python reader, no
``ada.geom`` hydrate, no per-solid round-trip, no GIL — so it is ~2.5x faster than the per-solid
Python ``stream_step_to_mesh`` on giant-solid / FEM-export STEP (469826: 72s native vs 180s Python).

Coordinates are scaled to metres (the adapy / viewer unit convention, same as the native GLB path).

OBJ output welds vertices (each instance's unique tessellated verts written once + indexed faces),
~2.3x smaller than per-triangle unshared verts (reference assembly 8.25 GB -> 3.53 GB) — the file size drove the
write + gzip-at-rest + upload time that dominated STEP->obj in the capped worker pod.
"""

from __future__ import annotations

import pathlib

from ada.config import logger


def native_mesh_available() -> bool:
    """True if the adacpp native STEP -> STL/OBJ entry point is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "stream_step_to_mesh")
    except Exception:  # noqa: BLE001
        return False


def native_step_to_mesh(
    step_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    fmt: str,
    deflection: float | None = None,
    angular_deg: float | None = None,
    num_threads: int = 0,
    on_progress=None,
) -> int:
    """Convert ``step_path`` to ``out_path`` as ``fmt`` ('stl' | 'obj') with the native adacpp mesh
    pipeline. ``deflection`` / ``angular_deg`` default to the ``ADA_STREAM_TESS_DEFLECTION`` /
    ``ADA_STREAM_TESS_ANGULAR`` env via ``ada.cad.registry.stream_tess_defaults``. ``num_threads``
    0 = the cgroup-aware streaming allotment.
    Returns the triangle count; raises if adacpp is unavailable or the conversion fails."""
    import adacpp

    fmt = fmt.lower().lstrip(".")
    if fmt not in ("stl", "obj"):
        raise ValueError(f"native_step_to_mesh: unsupported format {fmt!r}")
    from ada.cad.registry import (
        DEFAULT_STREAM_TESS_ADAPTIVE_NATIVE,
        stream_tess_adaptive,
        stream_tess_defaults,
    )

    if deflection is None or angular_deg is None:
        _defl, _ang = stream_tess_defaults()
        deflection = _defl if deflection is None else deflection
        angular_deg = _ang if angular_deg is None else angular_deg
    if num_threads <= 0:
        # Mirror the native GLB path: bound threads to the cgroup-aware allotment, not the node's
        # core count, so we don't oversubscribe a CPU-capped pod or bloat per-thread malloc arenas.
        try:
            from ada.visit.scene_handling.scene_from_step_stream import _stream_workers

            num_threads = _stream_workers()
        except Exception:  # noqa: BLE001
            num_threads = 0

    # Adaptive per-surface density is ON BY DEFAULT for STEP->OBJ/STL too (same rationale as
    # STEP->GLB: dense curved assemblies over-tessellate, and the text OBJ/STL are the largest,
    # slowest products — the reference assembly's 107M-tri OBJ/STL blew the 5-min timeout). ADA_STREAM_TESS_
    # ADAPTIVE=0/false forces the fixed-angle path.
    adaptive = stream_tess_adaptive(default=DEFAULT_STREAM_TESS_ADAPTIVE_NATIVE)
    model_scale = 0.0
    if adaptive:
        from ada.cadit.step.model_scale import estimate_step_model_scale

        model_scale = estimate_step_model_scale(step_path)

    if on_progress is not None:
        on_progress("adacpp-native-mesh", 0.1)
    mesh_kwargs = dict(deflection=deflection, angular_deg=angular_deg, num_threads=num_threads)
    if "model_scale" in (adacpp.cad.stream_step_to_mesh.__doc__ or ""):
        mesh_kwargs["model_scale"] = model_scale
    elif model_scale > 0.0:
        logger.warning("adacpp build predates adaptive tessellation (no model_scale); using fixed angular_deg")
    n = adacpp.cad.stream_step_to_mesh(str(step_path), str(out_path), fmt, **mesh_kwargs)
    if n < 0:
        raise RuntimeError(f"adacpp native stream_step_to_mesh failed for {step_path}")
    # Record the triangle tally for the audit (convert_meta["tri_stats"]) so a tessellation-output
    # change (density drift, the adaptive thread bug, a dropped solid) is caught run-to-run.
    from ada.cadit.step.tess_stats import record_tri_stats

    record_tri_stats(n_tris=n, engine="adacpp:libtess2")
    logger.info("adacpp-native STEP->%s: %s triangles -> %s", fmt.upper(), n, out_path)
    print(
        f"[adacpp-native] {n} triangles -> {out_path} (fmt={fmt}, threads={num_threads}, "
        f"deflection={deflection}, angular={angular_deg})",
        flush=True,
    )
    if on_progress is not None:
        on_progress("ready", 1.0)
    return n
