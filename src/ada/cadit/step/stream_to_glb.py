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

from ada.config import logger


def stream_step_to_glb(step_path: str | Path, glb_path: str | Path, *, tolerant: bool = True) -> dict:
    """Stream-convert a STEP file to a GLB without holding the whole model in memory.

    With ``tolerant`` (default) a solid using an unsupported surface (spherical /
    rational B-spline) is skipped rather than aborting the file; degenerate, empty-mesh
    and build/tessellate failures are skipped and logged (per-reason summary at
    warning) so a conversion never silently drops geometry.

    Returns ``{"meshed", "total", "skipped", "materials", "reasons"}``.
    """
    from ada.visit.scene_converter import SceneConverter
    from ada.visit.scene_handling.scene_from_step_stream import StepStreamSource

    converter = SceneConverter(source=StepStreamSource(step_path, tolerant=tolerant))
    data = converter.build_glb()  # colour-merged + ADA-ext, built by streaming
    stats = dict((converter.build_scene().metadata or {}).get("ada_stream_stats", {}))

    if stats.get("meshed", 0) == 0:
        raise ValueError(f"stream_step_to_glb: no solids tessellated from {step_path} (all skipped)")

    Path(glb_path).write_bytes(data)
    logger.info(
        "stream_step_to_glb: %s -> %s — meshed %d/%d solids, %d material group(s)",
        step_path, glb_path, stats.get("meshed", 0), stats.get("total", 0), stats.get("materials", 0),
    )
    return stats
