"""How far apart two meshes' SURFACES are: a sampled, symmetric surface distance.

Each mesh is sampled -- its vertices plus points spread over its triangles in proportion to their
area (a fixed seed, so a rerun gives the same numbers) -- and every sample of one side is measured to
the nearest point of the OTHER side's triangles, both ways (exact point-to-triangle distance,
vectorised). Reported: the largest such distance (a sampled Hausdorff distance), the 95th percentile
(robust to one stray triangle) and the mean.

Only the sampling is approximate: a gap narrower than the sample spacing (``spacing``, reported) can
be missed, never invented -- the distances measured are exact. A mesh of more than
``max_triangles`` triangles is measured against the other side's SAMPLES instead (overstating by up
to the spacing, which is then what ``spacing`` says), so one huge element cannot stall a run.

Needs numpy; scipy's KD-tree is used for the sample fallback when present.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SurfaceDistance", "point_mesh_distance", "sample_surface", "surface_distance"]

#: Samples per side, at most.
DEFAULT_SAMPLES = 2000
#: Above this many triangles a side is measured against samples, not triangles.
MAX_TRIANGLES = 20000


@dataclass(frozen=True)
class SurfaceDistance:
    """In the meshes' own units (scale before calling for mm)."""

    hausdorff: float
    p95: float
    mean: float
    #: The mean distance between neighbouring samples of the sparser side: the resolution of the above.
    spacing: float
    samples: tuple[int, int]


def sample_surface(positions, indices, n: int = DEFAULT_SAMPLES, seed: int = 0):
    """``(m, 3)`` points on a triangle mesh: its vertices (at most ``n // 2``) and area-weighted points
    on its triangles, ``n`` in all at most. ``positions`` flat or ``(k, 3)``; ``indices`` flat."""
    import numpy as np

    p = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    if len(p) == 0:
        return np.zeros((0, 3))
    rng = np.random.default_rng(seed)
    verts = p if len(p) <= n // 2 else p[rng.choice(len(p), n // 2, replace=False)]
    if len(t) == 0:
        return verts
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = float(area.sum())
    k = max(0, n - len(verts))
    if total <= 0.0 or k == 0:
        return verts
    pick = rng.choice(len(t), k, p=area / total)
    u, v = rng.random(k), rng.random(k)
    flip = u + v > 1.0
    u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
    pts = a[pick] + u[:, None] * (b[pick] - a[pick]) + v[:, None] * (c[pick] - a[pick])
    return np.vstack([verts, pts])


def _segment_distance(p, a, b):
    """Distance from points ``p`` (n,1,3) to segments ``a``-``b`` (1,m,3): (n, m)."""
    import numpy as np

    ab = b - a
    denom = (ab * ab).sum(axis=-1)
    t = np.clip(((p - a) * ab).sum(axis=-1) / np.where(denom > 0, denom, 1.0), 0.0, 1.0)
    closest = a + t[..., None] * ab
    return np.linalg.norm(p - closest, axis=-1)


def point_mesh_distance(points, positions, indices, chunk: int = 64):
    """Distance from each of ``points`` (n, 3) to the nearest point of the triangle mesh."""
    import numpy as np

    pos = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    tri = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    A, B, C = pos[tri[:, 0]][None], pos[tri[:, 1]][None], pos[tri[:, 2]][None]
    e0, e1 = B - A, C - A
    n = np.cross(e0, e1)
    nlen = np.linalg.norm(n, axis=-1)
    unit = n / np.where(nlen > 0, nlen, 1.0)[..., None]
    d00, d01, d11 = (e0 * e0).sum(-1), (e0 * e1).sum(-1), (e1 * e1).sum(-1)
    det = d00 * d11 - d01 * d01
    out = np.empty(len(points))
    pts = np.asarray(points, dtype=np.float64)
    for i in range(0, len(pts), chunk):
        p = pts[i : i + chunk][:, None, :]
        w = p - A
        h = (w * unit).sum(-1)  # signed height above each triangle's plane
        q = w - h[..., None] * unit  # the projection, relative to A
        d20, d21 = (q * e0).sum(-1), (q * e1).sum(-1)
        safe = np.where(det > 0, det, 1.0)
        v = (d11 * d20 - d01 * d21) / safe
        u = (d00 * d21 - d01 * d20) / safe
        inside = (det > 0) & (v >= 0) & (u >= 0) & (u + v <= 1)
        edge = np.minimum(
            np.minimum(_segment_distance(p, A, B), _segment_distance(p, B, C)), _segment_distance(p, C, A)
        )
        best = np.where(inside, np.abs(h), edge)
        out[i : i + chunk] = best.min(axis=1)
    return out


def _nearest_sample(src, dst):
    import numpy as np

    try:
        from scipy.spatial import cKDTree

        return np.asarray(cKDTree(dst).query(src)[0])
    except ImportError:
        out = np.empty(len(src))
        for i in range(0, len(src), 512):
            chunk = src[i : i + 512]
            out[i : i + 512] = np.sqrt(((chunk[:, None, :] - dst[None, :, :]) ** 2).sum(axis=2).min(axis=1))
        return out


def surface_distance(
    a_positions,
    a_indices,
    b_positions,
    b_indices,
    *,
    samples: int = DEFAULT_SAMPLES,
    max_triangles: int = MAX_TRIANGLES,
) -> SurfaceDistance:
    """The sampled symmetric surface distance between mesh A and mesh B."""
    import numpy as np

    pa = sample_surface(a_positions, a_indices, samples, seed=1)
    pb = sample_surface(b_positions, b_indices, samples, seed=2)
    if len(pa) == 0 or len(pb) == 0:
        return SurfaceDistance(float("inf"), float("inf"), float("inf"), 0.0, (len(pa), len(pb)))

    def to(points, positions, indices, other_samples):
        if 0 < len(indices) // 3 <= max_triangles:
            return point_mesh_distance(points, positions, indices)
        return _nearest_sample(points, other_samples)

    d = np.concatenate([to(pa, b_positions, b_indices, pb), to(pb, a_positions, a_indices, pa)])
    sparse = pa if len(pa) <= len(pb) else pb
    return SurfaceDistance(
        float(d.max()), float(np.percentile(d, 95)), float(d.mean()), _spacing(sparse), (len(pa), len(pb))
    )


def _spacing(points) -> float:
    """Mean distance from a point to its nearest OTHER point, over (up to) 2000 of them."""
    import numpy as np

    pts = points[: min(len(points), 2000)]
    if len(pts) < 2:
        return 0.0
    try:
        from scipy.spatial import cKDTree

        d, _ = cKDTree(pts).query(pts, k=2)
        return float(np.mean(d[:, 1]))
    except ImportError:
        d2 = ((pts[:, None, :] - pts[None, :, :]) ** 2).sum(axis=2)
        np.fill_diagonal(d2, np.inf)
        return float(np.mean(np.sqrt(d2.min(axis=1))))
