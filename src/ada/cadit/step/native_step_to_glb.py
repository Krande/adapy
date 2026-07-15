"""Fully-native STEP->GLB via adacpp (the ``adacpp-native`` viewer pipeline).

Unlike the default ``libtess2`` pipeline (Python streaming reader + a multiprocess worker pool that
tessellates through adacpp), this calls a single adacpp C++ entry point that does EVERYTHING natively
and in-process: a Part-21 reader (offset index + per-statement pread, bounded memory) -> per-solid
resolve -> libtess2 tessellation across a C++ thread pool -> a merge-by-colour GLB writer with on-disk
spill. No Python reader, no pickle, no worker pool, no GIL.

On the large reference assembly (778 MB, 7291 solids, 26 M tris) this is ~2.9x faster than the Python 6-worker path at
~20% lower peak memory, in one process. It honours the same ``ADA_STREAM_TESS_DEFLECTION`` /
``ADA_STREAM_TESS_ANGULAR`` env as the streaming path so deflection options carry over.

The native GLB carries the full viewer picking contract: merge-by-colour materials + per-material
``draw_ranges_node<matidx>`` and a per-instance, product-named ``id_hierarchy`` in
``scenes[0].extras``, plus an ``ADA_EXT_data`` extension. Each placement is individually pickable and
the assembly tree is reconstructed from the reader's instance paths — validated 1:1 with the Python
streaming path on the large reference assembly (same products, placements, triangle counts, names, and full tree).
"""

from __future__ import annotations

import pathlib

from ada.config import logger


# The track the native binding runs when ``pipeline`` is left unset — adacpp's own default
# ("" parses to libtess2). Named here only so the capability probe and the refusal below agree on
# which single track an older, pipeline-less build can still be trusted to deliver.
_NATIVE_DEFAULT_TRACK = "libtess2"


def native_adacpp_available() -> bool:
    """True if the adacpp native STEP->GLB entry point is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "stream_step_to_glb")
    except Exception:
        return False


def native_track_selection_available() -> bool:
    """True if THIS adacpp build's native binding accepts a tessellation track.

    The threaded C++ core always could; the binding only started forwarding ``pipeline`` in
    adacpp 0.16. Callers must gate on this before OFFERING a track choice for the native path —
    an older build ignores the absent kwarg and runs libtess2, so advertising 'cdt' against it
    would promise a track the conversion never runs.
    """
    if not native_adacpp_available():
        return False
    try:
        import adacpp

        return "pipeline" in (adacpp.cad.stream_step_to_glb.__doc__ or "")
    except Exception:
        return False


def native_step_to_glb(
    step_path: str | pathlib.Path,
    glb_path: str | pathlib.Path,
    deflection: float | None = None,
    angular_deg: float | None = None,
    num_threads: int = 0,
    meshopt: bool = True,
    on_progress=None,
    pipeline: str | None = None,
) -> dict:
    """Convert ``step_path`` to a GLB at ``glb_path`` with the native adacpp pipeline.

    ``deflection`` / ``angular_deg`` default to the ``ADA_STREAM_TESS_DEFLECTION`` /
    ``ADA_STREAM_TESS_ANGULAR`` env via ``ada.cad.registry.stream_tess_defaults`` (the single source
    of the corpus defaults), matching the streaming path. ``num_threads`` 0 = auto
    (hardware concurrency). ``meshopt`` (default on) bakes ``EXT_meshopt_compression`` inline in the
    C++ writer — no Python re-pack of the (potentially GB-scale) GLB, and the worker's compress_glb
    detects the already-packed GLB and skips it (gzip-at-rest still applies on upload). Returns a
    stats dict ``{solids, total, skipped}``. Raises if adacpp is unavailable or the conversion fails
    (the converter then falls back per its fallback chain).
    """
    import adacpp

    from ada.cad.registry import (
        DEFAULT_STREAM_TESS_ADAPTIVE_NATIVE,
        stream_tess_adaptive,
        stream_tess_defaults,
    )

    if deflection is None or angular_deg is None:
        _defl, _ang = stream_tess_defaults()
        deflection = _defl if deflection is None else deflection
        angular_deg = _ang if angular_deg is None else angular_deg

    # Adaptive per-surface angular density is ON BY DEFAULT for STEP->GLB: large curved CAD
    # assemblies (reference assembly: 7291 solids, thousands of sub-cm bolts/pins) over-tessellate at a fixed
    # fine angle, and the GLB is the transfer-size-sensitive product. We estimate a model reference
    # scale so adacpp coarsens tiny features while keeping large surfaces fine. ADA_STREAM_TESS_
    # ADAPTIVE=0/false forces the fixed-angle path (model_scale 0 => angular_deg governs everything).
    adaptive = stream_tess_adaptive(default=DEFAULT_STREAM_TESS_ADAPTIVE_NATIVE)
    model_scale = 0.0
    if adaptive:
        from ada.cadit.step.model_scale import estimate_step_model_scale

        model_scale = estimate_step_model_scale(step_path)

    if num_threads <= 0:
        # ``num_threads=0`` lets the C++ pick std::thread::hardware_concurrency(), which reports the
        # NODE's core count (e.g. 16) — NOT the pod's cgroup CPU quota. 16 threads on a 4-CPU /
        # 3.2 GB-capped pod oversubscribes the CPUs AND bloats glibc's per-thread malloc arenas past
        # the RSS watchdog (observed: 3.12 GB peak → reaped at 31 s). Bound it to the streaming path's
        # cgroup-aware allotment (reads ADA_STEP_STREAM_WORKERS, else the cgroup cpu.max quota → cpu-1,
        # capped at 3) — the same allotment libtess2 runs the reference assembly under at ~1 GB. Falls back to the
        # C++ auto-pick only if that helper can't be imported.
        try:
            from ada.visit.scene_handling.scene_from_step_stream import _stream_workers

            num_threads = _stream_workers()
        except Exception:
            num_threads = 0

    if on_progress is not None:
        on_progress("adacpp-native", 0.1)

    glb_kwargs = dict(deflection=deflection, angular_deg=angular_deg, num_threads=num_threads, meshopt=meshopt)
    # Only forward model_scale to an adacpp build that accepts it (nanobind embeds the signature in
    # __doc__); an older extension would raise on the unknown kwarg. Off (0.0) behaves as before.
    if "model_scale" in (adacpp.cad.stream_step_to_glb.__doc__ or ""):
        glb_kwargs["model_scale"] = model_scale
    elif model_scale > 0.0:
        logger.warning("adacpp build predates adaptive tessellation (no model_scale); using fixed angular_deg")
    # Opt-in per-face clickable regions (scenes[0].extras face_ranges_node<m>): ADA_STREAM_TESS_FACE_REGIONS=1.
    # Off by default — it bloats the GLB and forces serial face tessellation. Only forward to an adacpp
    # build whose binding accepts it (older extensions would raise on the unknown kwarg).
    from ada.cad.registry import stream_tess_face_regions

    if stream_tess_face_regions() and "face_regions" in (adacpp.cad.stream_step_to_glb.__doc__ or ""):
        glb_kwargs["face_regions"] = True
    # Tessellation track (adacpp's own vocabulary — see adacpp.cad.tess_tracks()). Same __doc__
    # capability probe as above. REFUSING is deliberate when the build predates the parameter: it
    # ignores the kwarg's absence and runs libtess2, so quietly proceeding would render a 'cdt'
    # request as libtess2 and report success — the caller would have no way to tell. Only the
    # default track is safe to leave implicit.
    if pipeline:
        if "pipeline" not in (adacpp.cad.stream_step_to_glb.__doc__ or ""):
            if pipeline != _NATIVE_DEFAULT_TRACK:
                raise RuntimeError(
                    f"adacpp build predates native track selection (stream_step_to_glb takes no "
                    f"'pipeline'); cannot honour tessellator {pipeline!r} — it would silently run "
                    f"{_NATIVE_DEFAULT_TRACK!r}"
                )
        else:
            # Non-neutral (taxonomy) tracks need ifcopenshell geometry the C++ STEP reader never
            # builds. adacpp doesn't reject them here — it meshes as if no track were selected — so
            # refuse rather than return a GLB labelled with a kernel that never ran. The API filters
            # these out of the cpp dropdown; this catches a stored config or a direct call.
            from ada.cad.registry import tess_track_by_name

            track = tess_track_by_name(f"adacpp:{pipeline}")
            if track is not None and not track.neutral:
                raise RuntimeError(
                    f"tessellator {pipeline!r} is a taxonomy track and cannot run on the native "
                    f"STEP path (it would silently mesh as if untracked); use the 'python' "
                    f"serializer for it, or a neutral track here"
                )
            glb_kwargs["pipeline"] = pipeline
    n = adacpp.cad.stream_step_to_glb(str(step_path), str(glb_path), **glb_kwargs)
    if n < 0:
        raise RuntimeError(f"adacpp native stream_step_to_glb failed for {step_path}")

    logger.info("adacpp-native STEP->GLB: %s solids -> %s", n, glb_path)
    # Always emit a one-line summary to stdout — captured at the fd level regardless of the ada log
    # level — so a clean native run positively shows up in the audit Log tab instead of an empty
    # capture (the worker adds wall-time + peak RSS to the row's metrics separately).
    print(
        f"[adacpp-native] {n} solids -> {glb_path} "
        f"(threads={num_threads}, deflection={deflection}, angular={angular_deg}, meshopt={meshopt}, "
        f"adaptive={'off' if model_scale <= 0 else f'model_scale={model_scale:.0f}'})",
        flush=True,
    )
    if on_progress is not None:
        on_progress("ready", 1.0)
    # Native coverage is 100% on the reference assembly (all surface types + BREP_WITH_VOIDS resolved); the binding
    # returns solids actually written, so skipped is reported 0 here. (A future binding return of the
    # total-root count would let this report exact skips.)
    return {"solids": n, "total": n, "skipped": 0}
