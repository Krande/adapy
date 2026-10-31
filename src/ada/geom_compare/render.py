"""Side-by-side pictures of one element as two sources draw it.

Each side's triangles are written as a small GLB (``glb.write``) and rendered by adapy's offscreen
renderer (``ada.visit.rendering.offscreen.glb_to_image``) -- the one the search export renders its
thumbnails with. Both sides are framed by the SAME box (the union of the two), so a part that is
missing or displaced on one side shows as such rather than being re-fitted away: each mesh is written
together with an invisible-size frame of that union.

The renderer is an optional dependency of a worker; without it :func:`render_pair` says why and the
comparison goes on without pictures.
"""

from __future__ import annotations

import pathlib
import tempfile
from typing import Optional

from . import glb as _glb

__all__ = ["THUMB_SIZE", "render_pair", "unavailable"]

THUMB_SIZE = 256

_REASON: list[Optional[str]] = []


def unavailable() -> Optional[str]:
    """Why this process cannot render, or None when it can. Asked once."""
    if not _REASON:
        reason = None
        try:
            from ada.visit.rendering.adacpp_offscreen import is_available

            if not is_available():
                reason = "no offscreen renderer in this worker (adacpp without _render)"
        except Exception as exc:  # noqa: BLE001
            reason = f"no offscreen renderer ({type(exc).__name__}: {exc})"
        _REASON.append(reason)
    return _REASON[0]


def _framed(mesh: _glb.ElementMesh, lo, hi) -> tuple[list, list]:
    """The mesh plus two degenerate triangles at the union box's corners: the renderer fits its camera
    to the geometry, and these make that the union's box for both sides."""
    pos = list(mesh.positions)
    tris = list(mesh.indices)
    base = len(pos) // 3
    eps = 1e-6
    for corner in (lo, hi):
        pos.extend(
            [
                corner[0],
                corner[1],
                corner[2],
                corner[0] + eps,
                corner[1],
                corner[2],
                corner[0],
                corner[1] + eps,
                corner[2],
            ]
        )
        tris.extend([base, base + 1, base + 2])
        base += 3
    return pos, tris


def render_pair(
    a: Optional[_glb.ElementMesh], b: Optional[_glb.ElementMesh], size: int = THUMB_SIZE
) -> tuple[Optional[bytes], Optional[bytes], Optional[str]]:
    """``(png of A, png of B, reason)`` -- a side that has no mesh has no picture; ``reason`` is set
    when nothing could be rendered."""
    reason = unavailable()
    if reason is not None:
        return None, None, reason
    from ada.visit.rendering.offscreen import glb_to_image

    meshes = [m for m in (a, b) if m is not None and m.indices]
    if not meshes:
        return None, None, "neither side has triangles"
    lo = [min(min(m.positions[i::3]) for m in meshes) for i in range(3)]
    hi = [max(max(m.positions[i::3]) for m in meshes) for i in range(3)]
    out: list[Optional[bytes]] = []
    with tempfile.TemporaryDirectory(prefix="geom-compare-") as tmp:
        for i, m in enumerate((a, b)):
            if m is None or not m.indices:
                out.append(None)
                continue
            path = pathlib.Path(tmp) / f"side-{i}.glb"
            _glb.write({m.name: _framed(m, lo, hi)}, path)
            png = pathlib.Path(tmp) / f"side-{i}.png"
            try:
                glb_to_image(path, size=(size, size)).save(png, format="PNG")
                out.append(png.read_bytes())
            except Exception as exc:  # noqa: BLE001 - a picture is a nicety
                return None, None, f"render failed ({type(exc).__name__}: {exc})"
    return out[0], out[1], None
