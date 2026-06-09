"""Memory-bounded STEP -> GLB conversion.

The worker's default STEP path (``from_step`` -> Assembly -> ``to_gltf``) loads every
solid into an OCC compound up front, which OOMs a worker pod on large CAD (the
778 MB CAD assembly needs >7 GB through that route).

``stream_step_to_glb`` instead streams the kernel-free reader one solid at a time:
parse -> tessellate -> append the triangle mesh to the output scene -> drop the
solid's geometry before reading the next. Peak memory is the reader's offset index
(~170 MB on 11 M entities) plus the accumulated *meshes* (flat float/int buffers,
far lighter than the B-rep geometry), never the whole model as live ada objects.
"""

from __future__ import annotations

from pathlib import Path

from ada.config import logger


def _mesh_arrays(mesh):
    """Return (positions Nx3 float32, faces Mx3 int) from a backend tessellation,
    tolerating both the ``.indices`` protocol name and OCC's ``.faces``."""
    import numpy as np

    idx = getattr(mesh, "indices", None)
    if idx is None:
        idx = getattr(mesh, "faces", None)
    pos = getattr(mesh, "positions", None)
    if pos is None or idx is None:
        return None, None
    pos = np.asarray(pos, dtype=np.float32).reshape(-1, 3)
    faces = np.asarray(idx, dtype=np.int64).reshape(-1, 3)
    return pos, faces


def stream_step_to_glb(step_path: str | Path, glb_path: str | Path, *, tolerant: bool = True) -> dict:
    """Stream-convert a STEP file to a GLB without holding the whole model in memory.

    Each solid is read (kernel-free), tessellated via the active CAD backend, and its
    triangle mesh appended to the GLB scene; the solid's geometry is released before
    the next is read. With ``tolerant`` (default) a solid using an unsupported surface
    (spherical / rational B-spline) is skipped rather than aborting the file.

    Every solid that is skipped (degenerate, empty mesh, or a build/tessellate error)
    is logged — each one at debug, a per-reason summary at warning — so a conversion
    never silently drops geometry. The streaming reader's own per-solid skips
    (unsupported surfaces) are logged by ``stream_read_step``.

    Returns ``{"meshed", "total", "skipped", "reasons"}``.
    """
    import collections

    import trimesh

    from ada.cad import active_backend

    from .read.stream_reader import stream_read_step

    be = active_backend()
    scene = trimesh.Scene()
    n_ok = n_total = 0
    reasons: collections.Counter = collections.Counter()
    skipped_ids: list[str] = []

    def _skip(gid: str, reason: str) -> None:
        reasons[reason] += 1
        if len(skipped_ids) < 50:  # capped sample for the summary log
            skipped_ids.append(gid)
        logger.debug("stream_step_to_glb: skipped %s — %s", gid, reason)

    for i, geom in enumerate(stream_read_step(step_path, local_pool=False, tolerant=tolerant)):
        n_total += 1
        gid = str(geom.id) if geom.id not in (None, "") else f"solid_{i}"
        try:
            shape = be.build(geom)
            # A zero-extent (collapsed) solid makes OCC's relative mesher throw
            # "deviation must be greater than 0" — a fatal, uncatchable C++ terminate
            # that would kill the whole conversion. Skip it via the analytic bbox.
            try:
                bb = be.bbox(shape)
                diag = ((bb[3] - bb[0]) ** 2 + (bb[4] - bb[1]) ** 2 + (bb[5] - bb[2]) ** 2) ** 0.5
            except Exception:
                diag = 0.0
            if diag < 1e-7:
                _skip(gid, "degenerate (zero-extent solid)")
                continue
            mesh = be.tessellate(shape)
            pos, faces = _mesh_arrays(mesh)
            if pos is not None and len(pos) and len(faces):
                scene.add_geometry(trimesh.Trimesh(vertices=pos, faces=faces, process=False), node_name=gid)
                n_ok += 1
            else:
                _skip(gid, "empty mesh (no triangles)")
        except Exception as exc:  # noqa: BLE001 - one bad solid must not abort the file
            _skip(gid, f"{type(exc).__name__}: {str(exc)[:100]}")
        # geom / mesh become unreferenced here and are freed before the next solid.

    n_skipped = sum(reasons.values())
    if n_skipped:
        sample = ", ".join(skipped_ids)
        more = f" (+{n_skipped - len(skipped_ids)} more)" if n_skipped > len(skipped_ids) else ""
        logger.warning(
            "stream_step_to_glb: %s — skipped %d/%d solids by reason %s; ids: %s%s",
            step_path, n_skipped, n_total, dict(reasons), sample, more,
        )

    if n_ok == 0:
        raise ValueError(f"stream_step_to_glb: no solids tessellated from {step_path} ({n_total} read, all skipped)")
    scene.export(str(glb_path))
    logger.info(
        "stream_step_to_glb: %s -> %s — meshed %d/%d solids (skipped %d)",
        step_path, glb_path, n_ok, n_total, n_skipped,
    )
    return {"meshed": n_ok, "total": n_total, "skipped": n_skipped, "reasons": dict(reasons)}
