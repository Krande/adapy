"""Triangle-mesh operations behind the mesh linework backend (numpy only).

* :func:`weld` merges coincident vertices so neighbouring triangles share edges.
* :func:`edge_topology` lists the unique edges and the (up to two) faces on each.
* :func:`clip_mesh` keeps the part of a mesh on one side of a plane.
* :func:`slice_mesh` intersects a mesh with a plane and chains the result into loops.
* :class:`Occluder` answers "is this screen point behind a triangle or a section cap?".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def weld(vertices: np.ndarray, faces: np.ndarray, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices closer than ``tol`` (grid rounding) and drop collapsed triangles."""
    v = np.asarray(vertices, float).reshape(-1, 3)
    f = np.asarray(faces, np.int64).reshape(-1, 3)
    if len(v) == 0 or len(f) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    key = np.round(v / tol).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    v2 = v[first]
    f2 = inv[f]
    ok = (f2[:, 0] != f2[:, 1]) & (f2[:, 1] != f2[:, 2]) & (f2[:, 0] != f2[:, 2])
    f2 = f2[ok]
    # drop zero-area triangles (collinear after welding)
    n = np.cross(v2[f2[:, 1]] - v2[f2[:, 0]], v2[f2[:, 2]] - v2[f2[:, 0]])
    area2 = np.linalg.norm(n, axis=1)
    return v2, f2[area2 > (tol * tol) * 1e-3]


def face_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    n = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.where(ln > 0, ln, 1.0)


@dataclass
class EdgeTopology:
    edges: np.ndarray  # (E, 2) vertex ids, sorted
    face_a: np.ndarray  # (E,) first face
    face_b: np.ndarray  # (E,) second face, -1 on a boundary edge
    count: np.ndarray  # (E,) number of faces on the edge (>2 = non-manifold)

    @property
    def closed(self) -> bool:
        return bool(len(self.count)) and bool(np.all(self.count == 2))


def edge_topology(faces: np.ndarray) -> EdgeTopology:
    m = len(faces)
    if m == 0:
        z = np.zeros(0, np.int64)
        return EdgeTopology(np.zeros((0, 2), np.int64), z, z, z)
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    fid = np.tile(np.arange(m), 3)
    es = np.sort(e, axis=1)
    uniq, inv, counts = np.unique(es, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    order = np.argsort(inv, kind="stable")
    start = np.concatenate([[0], np.cumsum(counts)[:-1]])
    face_a = fid[order[start]]
    face_b = np.full(len(uniq), -1, np.int64)
    two = counts >= 2
    face_b[two] = fid[order[start[two] + 1]]
    return EdgeTopology(uniq, face_a, face_b, counts)


def clip_mesh(
    vertices: np.ndarray, faces: np.ndarray, origin, normal, eps: float, flags: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Keep the part of the mesh where ``(P - origin) . normal >= 0``.

    Returns (vertices, faces, on_plane) where ``on_plane`` flags vertices lying on the plane
    (the new ones made by the clip, and old ones within ``eps``), or-ed with ``flags`` carried in
    from an earlier clip. Triangles lying in the plane are dropped: they are the cut itself.
    """
    if flags is None:
        flags = np.zeros(len(vertices), bool)
    if len(faces) == 0:
        return vertices, faces, flags
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    s = (vertices - np.asarray(origin, float)) @ n
    s = np.where(np.abs(s) < eps, 0.0, s)
    sf = s[faces]
    keep_all = np.all(sf >= 0, axis=1) & np.any(sf > 0, axis=1)
    mixed = np.any(sf > 0, axis=1) & np.any(sf < 0, axis=1)
    out_faces = [faces[keep_all]]
    new_pts: list[np.ndarray] = []
    cache: dict[tuple[int, int], int] = {}
    nv = len(vertices)

    def cross(i: int, j: int) -> int:
        a, b = (i, j) if i < j else (j, i)
        key = (a, b)
        if key not in cache:
            t = s[a] / (s[a] - s[b])
            new_pts.append(vertices[a] + t * (vertices[b] - vertices[a]))
            cache[key] = nv + len(new_pts) - 1
        return cache[key]

    extra = []
    for tri in faces[mixed]:
        poly: list[int] = []
        for k in range(3):
            i, j = int(tri[k]), int(tri[(k + 1) % 3])
            if s[i] >= 0:
                poly.append(i)
            if (s[i] > 0 and s[j] < 0) or (s[i] < 0 and s[j] > 0):
                poly.append(cross(i, j))
        for k in range(1, len(poly) - 1):
            extra.append((poly[0], poly[k], poly[k + 1]))
    if extra:
        out_faces.append(np.asarray(extra, np.int64))
    v = np.vstack([vertices, np.asarray(new_pts).reshape(-1, 3)]) if new_pts else vertices
    on_plane = np.concatenate([(s == 0.0) | flags, np.ones(len(new_pts), bool)])
    f = np.concatenate(out_faces) if out_faces else np.zeros((0, 3), np.int64)
    # compact: drop unreferenced vertices
    used = np.zeros(len(v), bool)
    used[f.reshape(-1)] = True
    remap = np.cumsum(used) - 1
    return v[used], remap[f], on_plane[used]


def slice_mesh(vertices: np.ndarray, faces: np.ndarray, origin, normal) -> list[np.ndarray]:
    """Intersection of a mesh with a plane as polylines (closed loops repeat their first point)."""
    if len(faces) == 0:
        return []
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    s = (vertices - np.asarray(origin, float)) @ n
    s = np.where(s == 0.0, 1e-300, s)  # vertices on the plane count as the positive side
    pos = s[faces] > 0
    npos = pos.sum(axis=1)
    hit = faces[(npos == 1) | (npos == 2)]
    if len(hit) == 0:
        return []
    point: dict[tuple[int, int], np.ndarray] = {}
    links: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for tri in hit:
        keys = []
        for k in range(3):
            i, j = int(tri[k]), int(tri[(k + 1) % 3])
            if (s[i] > 0) != (s[j] > 0):
                key = (i, j) if i < j else (j, i)
                if key not in point:
                    a, b = key
                    t = s[a] / (s[a] - s[b])
                    point[key] = vertices[a] + t * (vertices[b] - vertices[a])
                keys.append(key)
        if len(keys) == 2:
            links.setdefault(keys[0], []).append(keys[1])
            links.setdefault(keys[1], []).append(keys[0])
    return [np.asarray([point[k] for k in chain]) for chain in _walk_any(links)]


def chain_segments(segments: list[tuple[np.ndarray, np.ndarray]], tol: float) -> list[np.ndarray]:
    """Join 2D segments that share end points into polylines."""
    if not segments:
        return []
    pts: dict[tuple[int, int], np.ndarray] = {}
    links: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for a, b in segments:
        ka = (int(round(a[0] / tol)), int(round(a[1] / tol)))
        kb = (int(round(b[0] / tol)), int(round(b[1] / tol)))
        if ka == kb:
            continue
        pts.setdefault(ka, np.asarray(a, float))
        pts.setdefault(kb, np.asarray(b, float))
        links.setdefault(ka, []).append(kb)
        links.setdefault(kb, []).append(ka)
    out = []
    for chain in _walk_any(links):
        out.append(np.asarray([pts[k] for k in chain]))
    return out


def _walk_any(links: dict) -> list[list]:
    """Chain a graph into paths and loops (loops repeat their first node); chains stop at junctions
    (nodes of degree other than 2)."""
    seen: set = set()

    def ekey(a, b):
        return (a, b) if a <= b else (b, a)

    chains = []
    starts = [k for k, v in links.items() if len(v) != 2] + list(links)
    for st in starts:
        for nb in links[st]:
            if ekey(st, nb) in seen:
                continue
            seen.add(ekey(st, nb))
            chain = [st, nb]
            cur = nb
            while len(links[cur]) == 2 and cur != st:
                nxt = next((c for c in links[cur] if ekey(cur, c) not in seen), None)
                if nxt is None:
                    break
                seen.add(ekey(cur, nxt))
                chain.append(nxt)
                cur = nxt
            chains.append(chain)
    return chains


def points_in_loops(pts: np.ndarray, loops: list[np.ndarray]) -> np.ndarray:
    """Even-odd point-in-region test of 2D points against a set of closed loops."""
    inside = np.zeros(len(pts), bool)
    if len(pts) == 0:
        return inside
    px, py = pts[:, 0], pts[:, 1]
    for loop in loops:
        if len(loop) < 3:
            continue
        lo, hi = loop.min(axis=0), loop.max(axis=0)
        cand = (px >= lo[0]) & (px <= hi[0]) & (py >= lo[1]) & (py <= hi[1])
        if not cand.any():
            continue
        a = loop
        b = np.roll(loop, -1, axis=0)
        x, y = px[cand, None], py[cand, None]
        cond = (a[None, :, 1] > y) != (b[None, :, 1] > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = a[None, :, 0] + (y - a[None, :, 1]) * (b[None, :, 0] - a[None, :, 0]) / (b[None, :, 1] - a[None, :, 1])
        crosses = (cond & (x < xi)).sum(axis=1) % 2 == 1
        idx = np.nonzero(cand)[0]
        inside[idx] ^= crosses
    return inside


class Occluder:
    """Screen-space visibility: a point is hidden when a triangle covers it nearer the viewer, or
    when it lies under a section cap (the cut face of a solid, which sits in front of everything
    the section keeps).

    Triangles are bucketed on a uniform grid; queries test only the triangles in a point's cell.
    A point on a triangle's boundary (within ``eps`` of it) does not count as covered, so an edge
    is never hidden by the faces it belongs to.
    """

    def __init__(self, tris: np.ndarray, depth_tol: float, eps: float, caps: list[list[np.ndarray]] | None = None):
        tris = np.asarray(tris, float).reshape(-1, 3, 3)
        # drop triangles seen edge-on (no screen area)
        a2 = (tris[:, 1, 0] - tris[:, 0, 0]) * (tris[:, 2, 1] - tris[:, 0, 1]) - (tris[:, 2, 0] - tris[:, 0, 0]) * (
            tris[:, 1, 1] - tris[:, 0, 1]
        )
        self.tris = tris[np.abs(a2) > eps * eps]
        self.depth_tol = depth_tol
        self.eps = eps
        self.caps = [c for c in (caps or []) if c]
        self._build_grid()

    def _build_grid(self):
        t = self.tris
        if len(t) == 0:
            self.cells = {}
            return
        lo = t[:, :, :2].min(axis=1)
        hi = t[:, :, :2].max(axis=1)
        glo, ghi = lo.min(axis=0), hi.max(axis=0)
        span = np.maximum(ghi - glo, 1e-9)
        n = int(np.clip(np.sqrt(len(t)) * 1.5, 1, 256))
        self.glo, self.cell = glo, span / n
        self.n = n
        i0 = np.clip(((lo - glo) / self.cell).astype(int), 0, n - 1)
        i1 = np.clip(((hi - glo) / self.cell).astype(int), 0, n - 1)
        cells: dict[int, list[int]] = {}
        for k in range(len(t)):
            for ix in range(i0[k, 0], i1[k, 0] + 1):
                for iy in range(i0[k, 1], i1[k, 1] + 1):
                    cells.setdefault(ix * n + iy, []).append(k)
        self.cells = {c: np.asarray(v, np.int64) for c, v in cells.items()}

    def hidden(self, pts: np.ndarray) -> np.ndarray:
        """``pts`` (N, 3) screen points -> bool (N,) hidden."""
        pts = np.asarray(pts, float).reshape(-1, 3)
        out = np.zeros(len(pts), bool)
        if len(pts) == 0:
            return out
        if self.caps:
            for loops in self.caps:
                out |= points_in_loops(pts[:, :2], loops)
        if not self.cells:
            return out
        ij = ((pts[:, :2] - self.glo) / self.cell).astype(int)
        valid = np.all((ij >= 0) & (ij < self.n), axis=1)
        key = np.where(valid, ij[:, 0] * self.n + ij[:, 1], -1)
        todo = valid & ~out
        if not todo.any():
            return out
        uk, inv = np.unique(key[todo], return_inverse=True)
        idx_todo = np.nonzero(todo)[0]
        for g, k in enumerate(uk):
            tids = self.cells.get(int(k))
            if tids is None:
                continue
            sel = idx_todo[inv == g]
            out[sel] = self._test(pts[sel], self.tris[tids])
        return out

    def _test(self, p: np.ndarray, t: np.ndarray) -> np.ndarray:
        # barycentric coordinates of each point in each triangle (P, T)
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        v0, v1 = b[:, :2] - a[:, :2], c[:, :2] - a[:, :2]
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        d = p[:, None, :2] - a[None, :, :2]
        w1 = (d[..., 0] * v1[None, :, 1] - v1[None, :, 0] * d[..., 1]) / den[None]
        w2 = (v0[None, :, 0] * d[..., 1] - d[..., 0] * v0[None, :, 1]) / den[None]
        w0 = 1.0 - w1 - w2
        # margin in barycentric units: eps over the triangle's size
        size = np.sqrt(np.abs(den))[None]
        m = self.eps / np.maximum(size, 1e-30)
        inside = (w0 > m) & (w1 > m) & (w2 > m)
        z = w0 * a[None, :, 2] + w1 * b[None, :, 2] + w2 * c[None, :, 2]
        return np.any(inside & (z < p[:, None, 2] - self.depth_tol), axis=1)
