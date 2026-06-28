"""Streaming STEP → mesh container (OBJ / STL) — per-solid, bounded memory, no OCC.

Tessellates one solid at a time off the native NGEOM stream (libtess2 via the
active CAD backend), applies each instance placement, and writes the triangles
straight to disk — binary STL or Wavefront OBJ — so peak memory is O(one solid's
mesh) instead of the whole-model trimesh.Scene the GLB→trimesh path materialises.
"""

from __future__ import annotations

import pathlib
import struct
from typing import Callable

from ada.config import logger

ProgressFn = Callable[[str, float], None]


def stream_step_to_mesh(
    src_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    fmt: str,
    *,
    deflection: float = 2.0,
    angular_deg: float = 20.0,
    on_progress: ProgressFn | None = None,
) -> dict:
    """Stream a STEP file to ``fmt`` ('stl' | 'obj'), one solid at a time. Returns
    ``{emitted, skipped, total}``."""
    import numpy as np

    from ada.cad import active_backend

    fmt = fmt.lower().lstrip(".")
    if fmt not in ("stl", "obj"):
        raise ValueError(f"stream_step_to_mesh: unsupported format {fmt!r}")
    prog = on_progress or (lambda *_: None)
    be = active_backend()
    if not hasattr(be, "tessellate_stream"):
        raise RuntimeError("active CAD backend has no libtess2 tessellate_stream; cannot stream mesh")

    from ada.cadit.step.read.stream_reader import detect_step_length_unit_scale
    from ada.cadit.step.write._solid_source import read_solids

    emitted = total = ntri = 0
    prog("tessellating", 0.1)
    # Scale to metres (the adapy / viewer unit convention, matching the GLB path and the
    # native C++ mesh writer) — the reader yields geometry in the file's source units.
    usc = float(detect_step_length_unit_scale(src_path))

    # Triangles materialised at once. A solid's full vertex+index buffer (the
    # tessellator output) is unavoidable, but we never expand it to a whole-solid
    # (M,3,3) world array — instead gather + transform ONE batch of triangles at a
    # time, so peak above the tessellator floor stays ~constant regardless of size.
    TRI_BATCH = 500_000

    def _tris(geom):
        """Yield (n,3,3) float32 world-space triangle batches for one solid."""
        gi = geom.geometry.geometry if hasattr(geom.geometry, "geometry") else geom.geometry
        gid = str(geom.id) if geom.id not in (None, "") else "0"
        try:
            bm = be.tessellate_stream([(gid, gi)], pipeline="libtess2", deflection=deflection, angular_deg=angular_deg)
        except Exception as exc:  # noqa: BLE001 - one bad solid shouldn't sink the file
            logger.debug("stream_step_to_mesh: tessellation failed for %s: %s", gid, exc)
            return
        bpos = getattr(bm, "positions", None)
        bidx = getattr(bm, "indices", None)
        if bpos is None or bidx is None or len(bidx) == 0:
            return
        pos = np.asarray(bpos, dtype=np.float32).reshape(-1, 3)
        idx = np.asarray(bidx, dtype=np.uint32).reshape(-1, 3)
        del bm, bpos, bidx  # drop the tessellator's own copy of the mesh ASAP
        for m in geom.transforms if geom.transforms else [None]:
            if m is None:
                R = t = None
            else:
                M = np.asarray(m, dtype=np.float32)
                R, t = M[:3, :3].T, M[:3, 3]
            for s in range(0, len(idx), TRI_BATCH):
                tri = pos[idx[s : s + TRI_BATCH]]  # (n,3,3) — gather only this batch
                world = tri if R is None else (tri @ R + t)
                yield world if usc == 1.0 else (world * usc)

    if fmt == "stl":
        with open(out_path, "wb") as fh:
            fh.write(b"\0" * 80)
            fh.write(struct.pack("<I", 0))  # facet count — patched at the end
            for total, geom in enumerate(read_solids(src_path), start=1):
                any_ok = False
                for tris in _tris(geom):
                    any_ok = True
                    M = len(tris)
                    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
                    ln = np.linalg.norm(n, axis=1, keepdims=True)
                    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
                    floats = np.empty((M, 12), dtype=np.float32)
                    floats[:, 0:3] = n
                    floats[:, 3:6] = tris[:, 0]
                    floats[:, 6:9] = tris[:, 1]
                    floats[:, 9:12] = tris[:, 2]
                    rec = np.zeros((M, 50), dtype=np.uint8)
                    rec[:, :48] = floats.view(np.uint8).reshape(M, 48)
                    fh.write(rec.tobytes())
                    ntri += M
                emitted += 1 if any_ok else 0
                if total % 500 == 0:
                    prog(f"tessellating {total}", 0.1 + 0.8 * min(0.99, total / 10000.0))
            fh.seek(80)
            fh.write(struct.pack("<I", ntri))
    else:  # obj
        with open(out_path, "w") as fh:
            voff = 1
            for total, geom in enumerate(read_solids(src_path), start=1):
                any_ok = False
                for tris in _tris(geom):
                    any_ok = True
                    verts = tris.reshape(-1, 3)  # (3M,3) — one vertex triple per triangle
                    np.savetxt(fh, verts, fmt="v %.6g %.6g %.6g")
                    nfac = len(tris)
                    faces = np.arange(voff, voff + 3 * nfac, dtype=np.int64).reshape(nfac, 3)
                    np.savetxt(fh, faces, fmt="f %d %d %d")
                    voff += 3 * nfac
                    ntri += nfac
                emitted += 1 if any_ok else 0
                if total % 500 == 0:
                    prog(f"tessellating {total}", 0.1 + 0.8 * min(0.99, total / 10000.0))

    skipped = max(0, total - emitted)
    logger.info("stream STEP->%s: emitted=%d skipped=%d total=%d tris=%d", fmt, emitted, skipped, total, ntri)
    prog("ready", 1.0)
    return {"emitted": emitted, "skipped": skipped, "total": total}
