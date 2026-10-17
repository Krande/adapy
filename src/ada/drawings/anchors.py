"""Points on adapy objects to hang annotations on (world coordinates).

    from ada.drawings.anchors import beam_end, bbox_point

    LinearDimension(beam_end(col, "start"), beam_end(col, "end"), "vertical")
    Leader(bbox_point(tank, 1.0, 0.5, 1.0), "tank, 12 m3")
"""

from __future__ import annotations

from typing import Literal

import numpy as np


def beam_end(beam, end: Literal["start", "end"] = "start") -> np.ndarray:
    """A beam's end node (``n1`` for ``"start"``, ``n2`` for ``"end"``) in world coordinates."""
    from ada.drawings.scene import _world

    node = beam.n1 if end == "start" else beam.n2
    return _world(beam, np.asarray(node.p, float).reshape(1, 3))[0]


def beam_point(beam, fraction: float) -> np.ndarray:
    """The point at ``fraction`` (0 = start, 1 = end) along a beam's axis."""
    a, b = beam_end(beam, "start"), beam_end(beam, "end")
    return a + float(fraction) * (b - a)


def bounds(obj) -> tuple[np.ndarray, np.ndarray]:
    """World axis-aligned bounds of an object (a sphere exactly, anything else from its
    tessellation), or of a list of objects / a Part."""
    from ada.drawings.scene import collect

    items = collect(obj, None, "solid", 1e-3, [])
    bs = [it.sphere and (it.sphere[0] - it.sphere[1], it.sphere[0] + it.sphere[1]) or it.bounds() for it in items]
    bs = [b for b in bs if b is not None]
    if not bs:
        raise ValueError(f"{obj!r} has no geometry to take bounds from")
    return np.min([b[0] for b in bs], axis=0), np.max([b[1] for b in bs], axis=0)


def bbox_point(obj, fx: float = 0.5, fy: float = 0.5, fz: float = 0.5) -> np.ndarray:
    """A point in an object's world bounds by fractions along x, y, z (0 = min, 1 = max)."""
    lo, hi = bounds(obj)
    return lo + np.array([fx, fy, fz], float) * (hi - lo)
