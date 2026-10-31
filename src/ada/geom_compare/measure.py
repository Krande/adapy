"""Measures of one side of a comparison -- a triangle soup or an adapy object's exact solids -- and the
box-and-volume verdict between two of them.

VOLUME is the exact solid's where a CAD backend builds one, and the signed volume of the triangles
for a mesh. A tessellation under-measures a round hole or a cylinder by the chord, so a volume delta
is always reported with the box delta, and the tolerance applies to the magnitude.

Pure where it can be: :func:`judge`, :func:`mesh_volume_bbox` and :func:`mesh_area` need no CAD
kernel. :func:`shape_measures` needs adapy's active backend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

__all__ = [
    "DEFAULT_BOX_TOL_MM",
    "DEFAULT_VOL_TOL",
    "Measure",
    "Verdict",
    "box_volume",
    "is_open",
    "judge",
    "mesh_area",
    "mesh_volume_bbox",
    "shape_measures",
    "tight_bbox_mm",
]

DEFAULT_BOX_TOL_MM = 1.0
DEFAULT_VOL_TOL = 0.005


@dataclass
class Measure:
    """One side of a comparison, in millimetres."""

    bbox_min: tuple
    bbox_max: tuple
    volume: float  # mm3
    solids: int = 0
    source: str = ""
    notes: list = field(default_factory=list)
    area: float = 0.0  # mm2, the surface; 0 where it was not measured
    #: Every edge of the mesh shared by an even number of triangles (``glb.closure``); None where not
    #: checked.
    closed: Optional[bool] = None


@dataclass
class Verdict:
    name: str
    ours: Optional[Measure]
    theirs: Optional[Measure]
    box_delta_mm: Optional[float]
    vol_ratio: Optional[float]
    passed: bool
    reason: str


def judge(
    name: str,
    ours: Optional[Measure],
    theirs: Optional[Measure],
    *,
    box_tol_mm: float = DEFAULT_BOX_TOL_MM,
    vol_tol: float = DEFAULT_VOL_TOL,
    theirs_label: str = "the other source",
) -> Verdict:
    """Pass when every box face is within ``box_tol_mm`` and the volumes within ``vol_tol`` (relative).
    ``theirs_label`` names the other side in the reasons."""
    if theirs is None:
        return Verdict(name, ours, theirs, None, None, False, f"{theirs_label} has no element of this name")
    if ours is None:
        return Verdict(name, ours, theirs, None, None, False, "not exported")
    box = max(
        max(abs(a - b) for a, b in zip(ours.bbox_min, theirs.bbox_min)),
        max(abs(a - b) for a, b in zip(ours.bbox_max, theirs.bbox_max)),
    )
    reasons = []
    if box > box_tol_mm:
        reasons.append(f"box off by {box:.1f} mm")
    # A mesh that is not closed has no volume: its signed sum is a number, and a meaningless one. One
    # that claims more than its own box is that, and its volume is not compared.
    theirs_open = is_open(theirs)
    ours_open = is_open(ours)
    ratio = ours.volume / theirs.volume if theirs.volume and not theirs_open else None
    if theirs_open:
        pass  # box only
    elif ours_open:
        reasons.append("our mesh is not closed")
    elif ratio is None:
        if abs(ours.volume) > 1.0:
            reasons.append(f"{theirs_label} volume is zero")
    elif abs(ratio - 1.0) > vol_tol:
        reasons.append(f"volume x{ratio:.4f}")
    note = f"ok (box only: {theirs_label}'s mesh is not closed)" if theirs_open else "ok"
    return Verdict(name, ours, theirs, box, ratio, not reasons, "; ".join(reasons) or note)


def is_open(m: Measure) -> bool:
    """A mesh with no meaningful volume: one found not closed (``Measure.closed``), or one whose signed
    volume claims more than its own box."""
    return m.closed is False or m.volume > box_volume(m) * 1.05 + 1.0


def box_volume(m: Measure) -> float:
    return (
        max(0.0, m.bbox_max[0] - m.bbox_min[0])
        * max(0.0, m.bbox_max[1] - m.bbox_min[1])
        * max(0.0, m.bbox_max[2] - m.bbox_min[2])
    )


def mesh_volume_bbox(points: Sequence[Sequence[float]], triangles: Iterable[Sequence[int]]):
    """``(signed volume, (min, max))`` of a triangle soup, taken about its centroid so a model hundreds
    of metres from the origin loses no precision."""
    if not points:
        return 0.0, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    n = len(points)
    c = [sum(p[k] for p in points) / n for k in range(3)]
    vol = 0.0
    for a, b, d in triangles:
        ax, ay, az = (points[a][k] - c[k] for k in range(3))
        bx, by, bz = (points[b][k] - c[k] for k in range(3))
        cx, cy, cz = (points[d][k] - c[k] for k in range(3))
        vol += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    lo = tuple(min(p[k] for p in points) for k in range(3))
    hi = tuple(max(p[k] for p in points) for k in range(3))
    return vol / 6.0, (lo, hi)


def mesh_area(points: Sequence[Sequence[float]], triangles: Iterable[Sequence[int]]) -> float:
    """The surface area of a triangle soup."""
    total = 0.0
    for a, b, d in triangles:
        u = [points[b][k] - points[a][k] for k in range(3)]
        w = [points[d][k] - points[a][k] for k in range(3)]
        total += 0.5 * math.sqrt(
            (u[1] * w[2] - u[2] * w[1]) ** 2 + (u[2] * w[0] - u[0] * w[2]) ** 2 + (u[0] * w[1] - u[1] * w[0]) ** 2
        )
    return total


#: Chord of the fine tessellation :func:`tight_bbox_mm` bounds a solid by, in metres (0.05 mm).
BBOX_DEFLECTION_M = 5.0e-5


def tight_bbox_mm(backend, shape):
    """``(min, max)`` in mm, from a fine tessellation of the solid rather than the backend's box.

    A backend's box of a revolved or swept face is bounded by its control points and can be loose by
    centimetres; a mesh's vertices lie ON the surface, so its box is inside the true one by at most the
    chord. Falls back to the backend's box where it cannot tessellate."""
    try:
        mesh = backend.tessellate(shape, linear_deflection=BBOX_DEFLECTION_M)
        pos = [float(v) for v in mesh.positions]
        if len(pos) >= 3:
            xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
            return (
                (min(xs) * 1000, min(ys) * 1000, min(zs) * 1000),
                (max(xs) * 1000, max(ys) * 1000, max(zs) * 1000),
            )
    except Exception:  # noqa: BLE001 - the backend's box is still an answer
        pass
    x0, y0, z0, x1, y1, z1 = backend.bbox(shape)
    return (x0 * 1000, y0 * 1000, z0 * 1000), (x1 * 1000, y1 * 1000, z1 * 1000)


def shape_measures(obj) -> Optional[Measure]:
    """Every physical object under ``obj`` (an object or a Part), measured by adapy's backend: exact
    solids where it builds them, triangles for a mesh geometry (flagged ``mesh``). None when nothing
    under it has geometry."""
    import ada
    from ada.cad import active_backend

    backend = active_backend()
    objs = list(obj.get_all_physical_objects()) if isinstance(obj, ada.Part) else [obj]
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    vol, solids, notes, area = 0.0, 0, [], 0.0
    cut_away = 0
    for o in objs:
        geom = o.solid_geom()
        g = geom.geometry
        coords = getattr(g, "coordinates", None)
        if coords is not None and getattr(g, "indices", None) is not None:
            pts = [tuple(float(v) * 1000.0 for v in p) for p in coords]
            idx = [i - 1 for i in g.indices]
            tris = [idx[i : i + 3] for i in range(0, len(idx) - 2, 3)]
            v, (mn, mx) = mesh_volume_bbox(pts, tris)
            vol += abs(v)
            area += mesh_area(pts, tris)
            notes.append("mesh")
        else:
            s = backend.build(geom)
            try:
                mn, mx = tight_bbox_mm(backend, s)
            except RuntimeError as exc:
                if "empty bounding box" not in str(exc):
                    raise
                # Its holes remove all of it: a solid with no geometry. It adds nothing.
                cut_away += 1
                continue
            vol += backend.volume(s) * 1e9
            try:
                area += backend.area(s) * 1e6
            except Exception:  # noqa: BLE001 - the area only widens a tolerance
                pass
            solids += 1
        lo = [min(a, b) for a, b in zip(lo, mn)]
        hi = [max(a, b) for a, b in zip(hi, mx)]
    if solids == 0 and not notes:
        if cut_away:
            return Measure((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0, 0, notes=["cut away entirely"])
        return None
    return Measure(tuple(lo), tuple(hi), vol, solids, notes=sorted(set(notes)), area=area)
