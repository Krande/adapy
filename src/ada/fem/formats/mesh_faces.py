"""Object-free, vectorized FEM-shell → CAD-face source.

The FEM→CAD writers historically rebuilt one ``Plate`` per shell element and
then merged those objects (``concept_merge``). That materialises tens of
thousands of objects just to throw most away — the dominant memory/time cost of
FEM→CAD export.

This module produces the *merged faces* directly from the array-backed mesh
(``MeshArrays``: ``coords`` + per-block ``conn`` row-indices), with **no Plate
or Elem objects built**. Each strategy is a vectorized function over the mesh
arrays that yields lightweight :class:`FaceData` records (raw outline + refs);
a streaming writer turns those into ``<flat_plate>`` text one at a time.

Strategies (``MergeStrategy``):

* ``NONE``     — identity: one face per shell element (non-coplanar quads split
  into two triangles, mirroring the legacy 1:1 conversion).
* ``COPLANAR`` — edge-adjacent coplanar shells sharing material+thickness merged
  into their union polygon. The *grouping* (plane buckets + edge-connected
  components) is computed here vectorized; the union itself reuses the tested
  :func:`merge_coplanar_loops_by_edge_cancellation` core on raw point loops, so
  the result is geometrically equivalent to the object-based merge.

``SURFACE`` (curved-panel reconstruction) and ``PANEL`` (structured-quad region
growing) slot in behind the same :func:`iter_faces` interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

import numpy as np

from ada.config import logger


class MergeStrategy(str, Enum):
    NONE = "none"
    COPLANAR = "coplanar"
    SURFACE = "surface"
    PANEL = "panel"

    @classmethod
    def from_value(cls, value) -> "MergeStrategy":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.NONE
        if isinstance(value, bool):  # legacy merge=True/False
            return cls.COPLANAR if value else cls.NONE
        return cls(str(value).lower())


@dataclass
class FaceData:
    """A single planar CAD face, as raw data (no Plate object)."""

    name: str
    outline: np.ndarray  # (k, 3) global coordinates
    normal: np.ndarray  # (3,) unit normal
    material: str
    thickness: float


# ── substrate ───────────────────────────────────────────────────────────────


@dataclass
class _ShellBlock:
    coords: np.ndarray  # (N, 3) the shared node coords
    conn: np.ndarray  # (M, k) row-indices into coords
    el_ids: np.ndarray  # (M,)
    materials: list  # length-M material names
    thicknesses: np.ndarray  # (M,) float


def _ensure_array_backed(fem):
    from ada.api.mesh.containers import ArrayElements, to_array_backed

    if not isinstance(fem.elements, ArrayElements):
        to_array_backed(fem)
    return fem.elements._store


def _shell_blocks(fem) -> Iterator[_ShellBlock]:
    """Yield one :class:`_ShellBlock` per shell element block, with per-element
    material name + thickness resolved from the block's ``fem_secs``."""
    from ada.fem.shapes.definitions import ShellShapes

    store = _ensure_array_backed(fem)
    coords = store.coords
    for ctype, blk in store.blocks.items():
        if not isinstance(ctype, ShellShapes):
            continue
        m = blk.conn.shape[0]
        fem_secs = blk.fem_secs
        materials: list = [None] * m
        thicknesses = np.zeros(m, dtype=np.float64)
        usable = np.zeros(m, dtype=bool)
        if fem_secs is not None:
            for i, fs in enumerate(fem_secs):
                # No section, or a solid/thickness-less section (2D continuum
                # elements) -> not a plate; skip (mirrors convert_shell_elem).
                t = getattr(fs, "thickness", None) if fs is not None else None
                mat = getattr(fs, "material", None) if fs is not None else None
                if t is None or mat is None:
                    continue
                materials[i] = mat.name
                thicknesses[i] = float(t)
                usable[i] = True
        if not usable.any():
            continue
        rows = np.nonzero(usable)[0]
        yield _ShellBlock(
            coords=coords,
            conn=blk.conn[rows],
            el_ids=blk.el_ids[rows],
            materials=[materials[r] for r in rows],
            thicknesses=thicknesses[rows],
        )


def _newell_normals(pts: np.ndarray) -> np.ndarray:
    """Vectorized Newell normal for a batch of polygons ``pts`` (M, k, 3)."""
    p = pts
    pn = np.roll(p, -1, axis=1)
    nx = np.sum((p[:, :, 1] - pn[:, :, 1]) * (p[:, :, 2] + pn[:, :, 2]), axis=1)
    ny = np.sum((p[:, :, 2] - pn[:, :, 2]) * (p[:, :, 0] + pn[:, :, 0]), axis=1)
    nz = np.sum((p[:, :, 0] - pn[:, :, 0]) * (p[:, :, 1] + pn[:, :, 1]), axis=1)
    n = np.stack([nx, ny, nz], axis=1)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return n / ln


def _canonical_sign(normals: np.ndarray, tol: float) -> np.ndarray:
    """+1/-1 per row so a face and its flip share a plane bucket (first
    significant component made positive)."""
    sign = np.ones(normals.shape[0], dtype=np.float64)
    chosen = np.zeros(normals.shape[0], dtype=bool)
    for c in range(3):
        comp = normals[:, c]
        take = (~chosen) & (np.abs(comp) > tol)
        sign[take] = np.sign(comp[take])
        chosen |= take
    sign[sign == 0] = 1.0
    return sign


# ── strategies ──────────────────────────────────────────────────────────────


def iter_faces(part, strategy=MergeStrategy.COPLANAR, ndigits: int = 6) -> Iterator[FaceData]:
    """Yield merged CAD faces for every FEM mesh under ``part`` (Part or Assembly).

    Object-free: walks each sub-part's array-backed shell mesh, never building
    Plate/Elem objects.
    """
    strategy = MergeStrategy.from_value(strategy)
    if strategy in (MergeStrategy.SURFACE, MergeStrategy.PANEL):
        raise NotImplementedError(f"merge strategy {strategy.value!r} not yet wired into the vectorized face source")

    parts = part.get_all_parts_in_assembly(include_self=True) if hasattr(part, "get_all_parts_in_assembly") else [part]
    for p in parts:
        fem = getattr(p, "fem", None)
        if fem is None or len(fem.elements) == 0:
            continue
        if strategy == MergeStrategy.NONE:
            yield from _none_faces(fem)
        else:
            yield from _coplanar_faces(fem, ndigits=ndigits)


def _is_coplanar_quad(pts: np.ndarray) -> np.ndarray:
    """(M,) bool: vectorized, bit-for-bit replica of ``vector_utils.is_coplanar``
    for a batch of quads ``pts`` (M,4,3).

    The legacy quad-split decision (``convert_shell_elem_to_plates``) uses
    ``is_coplanar``'s *exact* ``== 0`` plane test — not a tolerance — so on a
    curved mesh nearly every warped quad splits into two triangles. To produce
    the same primitive decomposition we must match that exactly. The arithmetic
    and operand order mirror the scalar implementation; IEEE-symmetric negation
    makes the plane-offset association bit-identical."""
    p0, p1, p2, p3 = pts[:, 0], pts[:, 1], pts[:, 2], pts[:, 3]
    v1 = p1 - p0
    v2 = p2 - p0
    a = v1[:, 1] * v2[:, 2] - v2[:, 1] * v1[:, 2]
    b = v2[:, 0] * v1[:, 2] - v1[:, 0] * v2[:, 2]
    c = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
    neg_d = a * p0[:, 0] + b * p0[:, 1] + c * p0[:, 2]
    return a * p3[:, 0] + b * p3[:, 1] + c * p3[:, 2] - neg_d == 0


# ── primitives ──────────────────────────────────────────────────────────────
# The merge operates on *primitive faces*, mirroring the legacy conversion: a
# coplanar quad or a triangle is one primitive; a non-coplanar (warped) quad is
# split into two triangles. Both NONE and COPLANAR build the same primitive set,
# so they agree on geometry — COPLANAR just additionally folds edge-adjacent
# coplanar primitives together.


class _Primitives:
    """Per-block primitive faces, as node-row tuples into the shared coords."""

    def __init__(self, coords, rows, names, mats, ts, normals):
        self.coords = coords
        self.rows = rows  # list[tuple[int, ...]] of node row-indices
        self.names = names
        self.mats = mats
        self.ts = ts
        self.normals = normals  # (P, 3)

    def __len__(self):
        return len(self.rows)

    def outline(self, j) -> np.ndarray:
        return self.coords[list(self.rows[j])]

    def face(self, j, name=None) -> FaceData:
        return FaceData(name or self.names[j], self.outline(j), self.normals[j], self.mats[j], self.ts[j])


def _block_primitives(blk: _ShellBlock) -> _Primitives:
    conn = blk.conn
    coords = blk.coords
    k = conn.shape[1]
    planar = _is_coplanar_quad(coords[conn]) if k == 4 else np.ones(conn.shape[0], dtype=bool)

    rows: list[tuple] = []
    names: list[str] = []
    mats: list = []
    ts: list[float] = []
    for i in range(conn.shape[0]):
        eid = int(blk.el_ids[i])
        mat = blk.materials[i]
        t = float(blk.thicknesses[i])
        ring = conn[i]
        if k == 4 and not planar[i]:
            # warped quad -> two triangles (matches convert_shell_elem_to_plates)
            rows.append((int(ring[0]), int(ring[1]), int(ring[2])))
            names.append(f"sh{eid}")
            mats.append(mat)
            ts.append(t)
            rows.append((int(ring[0]), int(ring[2]), int(ring[3])))
            names.append(f"sh{eid}_1")
            mats.append(mat)
            ts.append(t)
        else:
            rows.append(tuple(int(r) for r in ring))
            names.append(f"sh{eid}")
            mats.append(mat)
            ts.append(t)

    # batched Newell normals, grouped by vertex count (tri vs quad)
    normals = np.zeros((len(rows), 3), dtype=np.float64)
    by_len: dict[int, list[int]] = {}
    for j, r in enumerate(rows):
        by_len.setdefault(len(r), []).append(j)
    for length, idxs in by_len.items():
        batch = coords[np.array([rows[j] for j in idxs], dtype=np.int64)]  # (G, length, 3)
        nb = _newell_normals(batch)
        normals[np.array(idxs)] = nb

    return _Primitives(coords, rows, names, mats, ts, normals)


# ── strategies ──────────────────────────────────────────────────────────────


def faces_from_fem(fem, strategy=MergeStrategy.COPLANAR, ndigits: int = 6) -> Iterator[FaceData]:
    """Yield merged CAD faces for a single ``FEM`` mesh (one part).

    The per-FEM core; :func:`iter_faces` walks an assembly and calls this for
    each part. ``Part.iter_objects_from_fem`` also delegates here so the object
    and object-free streams share one strategy-aware source."""
    strategy = MergeStrategy.from_value(strategy)
    if strategy in (MergeStrategy.SURFACE, MergeStrategy.PANEL):
        raise NotImplementedError(f"merge strategy {strategy.value!r} not yet wired into the vectorized face source")
    if fem is None or len(fem.elements) == 0:
        return
    for blk in _shell_blocks(fem):
        prims = _block_primitives(blk)
        if len(prims) == 0:
            continue
        if strategy == MergeStrategy.NONE:
            for j in range(len(prims)):
                yield prims.face(j)
        else:
            yield from _coplanar_block(prims, ndigits)


def iter_faces(part, strategy=MergeStrategy.COPLANAR, ndigits: int = 6) -> Iterator[FaceData]:
    """Yield merged CAD faces for every FEM mesh under ``part`` (Part or Assembly).

    Object-free: walks each sub-part's array-backed shell mesh, never building
    Plate/Elem objects.
    """
    parts = part.get_all_parts_in_assembly(include_self=True) if hasattr(part, "get_all_parts_in_assembly") else [part]
    for p in parts:
        yield from faces_from_fem(getattr(p, "fem", None), strategy, ndigits)


def _coplanar_block(prims: _Primitives, ndigits: int) -> Iterator[FaceData]:
    tol = 10.0 ** (-ndigits)
    normals = prims.normals
    sign = _canonical_sign(normals, tol)
    ncanon = np.round(normals * sign[:, None], ndigits)
    p0 = prims.coords[np.array([r[0] for r in prims.rows], dtype=np.int64)]
    # offset from the RAW normal then signed (matches _plate_plane_key: it dots
    # the un-rounded normal with p0, applies the canonical sign, then rounds).
    offset = np.round(sign * np.sum(normals * p0, axis=1), ndigits)
    thick_q = np.round(np.array(prims.ts), ndigits)

    # plane bucket key: (material, thickness, canonical-normal, offset)
    buckets: dict[tuple, list[int]] = {}
    for j in range(len(prims)):
        key = (prims.mats[j], float(thick_q[j]), tuple(ncanon[j]), float(offset[j]))
        buckets.setdefault(key, []).append(j)

    for idxs in buckets.values():
        yield from _merge_plane_bucket(prims, idxs, ndigits)


def _merge_plane_bucket(prims: _Primitives, idxs: list[int], ndigits: int) -> Iterator[FaceData]:
    """Split a plane bucket into edge-connected components, merge each."""
    if len(idxs) == 1:
        yield prims.face(idxs[0])
        return

    parent = list(range(len(idxs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # edge-connectivity by shared node identity (conformal mesh: shared edge ==
    # shared node rows); two primitives sharing an edge are in one component.
    edge_owner: dict[tuple, int] = {}
    for li, j in enumerate(idxs):
        r = prims.rows[j]
        k = len(r)
        for e in range(k):
            a, b = r[e], r[(e + 1) % k]
            ek = (a, b) if a <= b else (b, a)
            if ek in edge_owner:
                union(li, edge_owner[ek])
            else:
                edge_owner[ek] = li

    comps: dict[int, list[int]] = {}
    for li in range(len(idxs)):
        comps.setdefault(find(li), []).append(li)

    for comp in comps.values():
        cj = [idxs[li] for li in comp]
        if len(cj) == 1:
            yield prims.face(cj[0])
        else:
            yield from _merge_component(prims, cj, ndigits)


def _merge_component(prims: _Primitives, cj: list[int], ndigits: int) -> Iterator[FaceData]:
    from ada.core.vector_utils import (
        merge_coplanar_loops_by_edge_cancellation,
        project_points_to_local_2d,
    )
    from ada.fem.formats.concept_merge import _loop_is_simple_2d

    ref = cj[0]
    name = f"{prims.names[ref]}_m"
    loops = [prims.outline(j) for j in cj]
    merged = merge_coplanar_loops_by_edge_cancellation(loops, ndigits=ndigits)
    if merged is not None:
        try:
            pts2d, _ = project_points_to_local_2d(merged)
            if _loop_is_simple_2d(pts2d):
                outline = np.asarray(merged, dtype=float)
                yield FaceData(name, outline, _newell_normals(outline[None])[0], prims.mats[ref], prims.ts[ref])
                return
        except Exception as exc:  # best-effort: degenerate merge -> keep originals
            logger.debug(f"coplanar merge fell back for {name!r}: {exc}")
    # best-effort contract: merge only when it collapses cleanly, else keep all.
    for j in cj:
        yield prims.face(j)


