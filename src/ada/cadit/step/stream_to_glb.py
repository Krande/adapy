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

    Returns ``{"meshed": int, "skipped": int}``.
    """
    import trimesh

    from ada.cad import active_backend

    from .read.stream_reader import stream_read_step

    be = active_backend()
    scene = trimesh.Scene()
    n_ok = n_fail = 0

    n_degenerate = 0
    for i, geom in enumerate(stream_read_step(step_path, local_pool=False, tolerant=tolerant)):
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
                n_fail += 1
                n_degenerate += 1
                continue
            mesh = be.tessellate(shape)
            pos, faces = _mesh_arrays(mesh)
            if pos is not None and len(pos) and len(faces):
                scene.add_geometry(
                    trimesh.Trimesh(vertices=pos, faces=faces, process=False),
                    node_name=str(geom.id) if geom.id not in (None, "") else f"solid_{i}",
                )
                n_ok += 1
            else:
                n_fail += 1
        except Exception as exc:  # noqa: BLE001 - one bad solid must not abort the file
            logger.debug("stream_step_to_glb: skipping %s: %s", geom.id, exc)
            n_fail += 1
        # geom / mesh become unreferenced here and are freed before the next solid.

    if n_ok == 0:
        raise ValueError(f"stream_step_to_glb: no solids tessellated from {step_path}")
    scene.export(str(glb_path))
    logger.info(
        "stream_step_to_glb: %s -> %s (meshed %d, skipped %d incl. %d degenerate)",
        step_path, glb_path, n_ok, n_fail, n_degenerate,
    )
    return {"meshed": n_ok, "skipped": n_fail, "degenerate": n_degenerate}
