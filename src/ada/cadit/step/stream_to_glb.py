"""Memory-bounded STEP -> GLB conversion.

The worker's default STEP path (``from_step`` -> Assembly -> ``to_gltf``) loads every
solid into an OCC compound up front, which OOMs a worker pod on large CAD (the
778 MB CAD assembly needs >7 GB through that route).

``stream_step_to_glb`` streams the kernel-free reader one solid at a time: parse ->
tessellate -> accumulate the light mesh buffers per material -> drop the solid's
B-rep geometry before reading the next. Peak memory is the reader's offset index
(~170 MB on 11 M entities) plus the accumulated *meshes* (flat float/int buffers, far
lighter than the geometry), never the whole model as live ada objects.

The output matches the normal ``to_gltf``: per-solid STEP colours are extracted by
the reader, meshes are merged by colour (one glTF node per material), and the ADA
design-extension graph is emitted for per-object picking — all built through the
shared ``SceneConverter`` via a ``StepStreamSource`` so there is no second code path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada.cad import CadConfig


def stream_step_to_glb(
    step_path: str | Path,
    glb_path: str | Path,
    *,
    tolerant: bool = True,
    on_progress=None,
    cad_config: "CadConfig | None" = None,
    merge_same_name_siblings: bool = False,
) -> dict:
    """Stream-convert a STEP file to a GLB without holding the whole model in memory.

    With ``tolerant`` (default) a solid using an unsupported surface (spherical /
    rational B-spline) is skipped rather than aborting the file; degenerate, empty-mesh
    and build/tessellate failures are skipped and logged (per-reason summary at
    warning) so a conversion never silently drops geometry.

    ``on_progress(stage, frac)`` is reported through the per-solid tessellation phase
    (the slow part) so a caller can show real progress.

    ``cad_config`` (``ada.cad.CadConfig``) selects the tessellation path (e.g.
    ``TessellationPath.ADACPP_LIBTESS2``) + tolerances; its env vars are applied for the
    conversion (and inherited by the tessellation subprocess pool) and restored afterwards.

    ``merge_same_name_siblings`` (default OFF) merges a product's redundant same-name
    nesting (a product instanced N times, or a lone solid under a same-named group) into one
    node / pickable object. Opt in here or via env ``ADA_MERGE_SAME_NAME_SIBLINGS=1``; left
    off the output is one node per solid (pre-merge behaviour).

    Returns ``{"meshed", "total", "skipped", "materials", "reasons"}``.
    """
    import os

    from ada.visit.scene_handling.scene_from_step_stream import (
        StepStreamSource,
        convert_step_stream_to_glb,
    )

    _saved: dict | None = None
    if cad_config is not None:
        cad_config.validate()
        _env = cad_config.env()
        _saved = {k: os.environ.get(k) for k in _env}
        os.environ.update(_env)

    source = StepStreamSource(
        step_path, tolerant=tolerant, on_progress=on_progress, merge_same_name_siblings=merge_same_name_siblings
    )
    # Spills the per-material merge to disk and streams the GLB straight to ``glb_path``
    # (no in-RAM scene / GLB bytes), so peak memory stays a few hundred MB on assemblies
    # that OOM'd the 2-3x in-memory ``scene.export`` path.
    try:
        stats = convert_step_stream_to_glb(source, glb_path)
    finally:
        if _saved is not None:
            for _k, _v in _saved.items():
                if _v is None:
                    os.environ.pop(_k, None)
                else:
                    os.environ[_k] = _v

    if stats.get("meshed", 0) == 0:
        raise ValueError(f"stream_step_to_glb: no solids tessellated from {step_path} (all skipped)")
    logger.info(
        "stream_step_to_glb: %s -> %s — meshed %d/%d solids, %d material group(s)",
        step_path,
        glb_path,
        stats.get("meshed", 0),
        stats.get("total", 0),
        stats.get("materials", 0),
    )
    return stats
