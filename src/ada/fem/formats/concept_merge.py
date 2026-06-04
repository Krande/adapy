"""Merge concept objects rebuilt from a FEM mesh.

``create_objects_from_fem`` produces one Plate per shell element and one
Beam per line element. For a real mesh that's tens of thousands of tiny
objects — heavy to export and unpleasant to work with in CAD. These
helpers fold that back down:

* coplanar shell plates sharing material + thickness + plane are merged
  into their union polygon (edge-connected groups only), and
* colinear beam elements sharing section + material are merged into a
  single beam spanning the chain.

Both are *best-effort and safe*: a group is merged only when it collapses
to a single clean result, otherwise the original objects are kept
untouched. So the worst case is "no merge", never corrupted geometry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada import Beam, Part, Plate


def _round_key(p, ndigits: int) -> tuple:
    return (round(float(p[0]), ndigits), round(float(p[1]), ndigits), round(float(p[2]), ndigits))


def _edge_key(a: tuple, b: tuple) -> tuple:
    return (a, b) if a <= b else (b, a)


class _UnionFind:
    def __init__(self, n: int):
        self._p = list(range(n))

    def find(self, i: int) -> int:
        p = self._p
        while p[i] != i:
            p[i] = p[p[i]]
            i = p[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self._p[ri] = rj

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self._p)):
            out.setdefault(self.find(i), []).append(i)
        return out


# ── Plates ──────────────────────────────────────────────────────────────


def _plate_plane_key(plate: "Plate", ndigits: int):
    """(material, thickness, canonical-normal, plane-offset) or None when
    the plate has no usable normal."""
    try:
        n = plate.normal
        nx, ny, nz = float(n[0]), float(n[1]), float(n[2])
    except Exception:
        return None
    comps = (nx, ny, nz)
    # Canonical sign so a face and its flip land in the same plane bucket.
    sign = 1.0
    for c in comps:
        if abs(c) > 10 ** (-ndigits):
            sign = 1.0 if c > 0 else -1.0
            break
    ncanon = tuple(round(sign * c, ndigits) for c in comps)
    p0 = plate.poly.points3d[0]
    offset = round(sign * (nx * p0[0] + ny * p0[1] + nz * p0[2]), ndigits)
    return (plate.material.name, round(float(plate.t), ndigits), ncanon, offset)


def _merge_coplanar_component(plates: list["Plate"], parent: "Part", ndigits: int) -> list["Plate"]:
    from ada import Plate
    from ada.core.vector_utils import merge_coplanar_loops_by_edge_cancellation

    loops = [list(pl.poly.points3d) for pl in plates]
    merged = merge_coplanar_loops_by_edge_cancellation(loops, ndigits=ndigits)
    if merged is None:
        # Hole, non-manifold vertex, or multiple disjoint loops — not a
        # single clean boundary, so keep the originals.
        return list(plates)
    ref = plates[0]
    try:
        return [Plate.from_3d_points(f"{ref.name}_m", merged, ref.t, mat=ref.material, parent=parent)]
    except Exception as exc:  # geometry the Plate ctor rejects — keep originals
        logger.debug(f"coplanar merge skipped for {ref.name!r}: {exc}")
        return list(plates)


def merge_coplanar_plates(plates: list["Plate"], parent: "Part", ndigits: int = 6) -> list["Plate"]:
    """Merge edge-adjacent coplanar plates sharing material + thickness."""
    if len(plates) < 2:
        return list(plates)

    by_plane: dict[tuple, list["Plate"]] = {}
    out: list["Plate"] = []
    for pl in plates:
        key = _plate_plane_key(pl, ndigits)
        if key is None:
            out.append(pl)  # un-bucketable; pass through
            continue
        by_plane.setdefault(key, []).append(pl)

    for group in by_plane.values():
        if len(group) < 2:
            out.extend(group)
            continue
        # Split the coplanar group into edge-connected components; only
        # plates that actually share an edge should merge (two disjoint
        # decks in the same plane must stay separate).
        uf = _UnionFind(len(group))
        edge_owner: dict[tuple, int] = {}
        for idx, pl in enumerate(group):
            pts = list(pl.poly.points3d)
            n = len(pts)
            for i in range(n):
                k = _edge_key(_round_key(pts[i], ndigits), _round_key(pts[(i + 1) % n], ndigits))
                if k in edge_owner:
                    uf.union(idx, edge_owner[k])
                else:
                    edge_owner[k] = idx
        for comp in uf.groups().values():
            comp_plates = [group[i] for i in comp]
            if len(comp_plates) < 2:
                out.extend(comp_plates)
            else:
                out.extend(_merge_coplanar_component(comp_plates, parent, ndigits))
    return out


# ── Beams ───────────────────────────────────────────────────────────────


def _unit(v: np.ndarray) -> np.ndarray:
    nrm = float(np.linalg.norm(v))
    return v / nrm if nrm > 0 else v


def merge_colinear_beams(
    beams: list["Beam"], parent: "Part", ndigits: int = 6, ang_tol: float = 1e-4
) -> list["Beam"]:
    """Merge chains of colinear beams sharing section + material into one
    beam spanning the chain. A chain is broken at any node that branches
    (degree != 2) or bends (the two segments are not colinear)."""
    if len(beams) < 2:
        return list(beams)

    by_prop: dict[tuple, list["Beam"]] = {}
    for bm in beams:
        by_prop.setdefault((bm.section.name, bm.material.name), []).append(bm)

    out: list["Beam"] = []
    for group in by_prop.values():
        out.extend(_merge_colinear_group(group, parent, ndigits, ang_tol))
    return out


def _merge_colinear_group(group: list["Beam"], parent: "Part", ndigits: int, ang_tol: float) -> list["Beam"]:
    from ada import Beam

    # node key -> [(beam_index, endpoint_key)]
    node_beams: dict[tuple, list[int]] = {}
    point_at: dict[tuple, object] = {}
    ends: list[tuple[tuple, tuple]] = []
    for i, bm in enumerate(group):
        k1, k2 = _round_key(bm.n1.p, ndigits), _round_key(bm.n2.p, ndigits)
        point_at.setdefault(k1, bm.n1.p)
        point_at.setdefault(k2, bm.n2.p)
        node_beams.setdefault(k1, []).append(i)
        node_beams.setdefault(k2, []).append(i)
        ends.append((k1, k2))

    def away_dir(bi: int, node_key: tuple) -> np.ndarray:
        k1, k2 = ends[bi]
        p1 = np.asarray(group[bi].n1.p, dtype=float)
        p2 = np.asarray(group[bi].n2.p, dtype=float)
        return _unit(p2 - p1) if node_key == k1 else _unit(p1 - p2)

    # A node dissolves (the chain passes straight through it) when exactly
    # two beams meet there and they are colinear (continue in opposite
    # directions).
    dissolvable: set[tuple] = set()
    for k, bis in node_beams.items():
        if len(bis) == 2 and bis[0] != bis[1]:
            v1, v2 = away_dir(bis[0], k), away_dir(bis[1], k)
            if float(np.linalg.norm(np.cross(v1, v2))) < ang_tol and float(np.dot(v1, v2)) < 0:
                dissolvable.add(k)

    # Union beams that share a dissolvable node -> each component is a
    # straight chain.
    uf = _UnionFind(len(group))
    for k in dissolvable:
        bis = node_beams[k]
        uf.union(bis[0], bis[1])

    out: list["Beam"] = []
    for comp in uf.groups().values():
        if len(comp) == 1:
            out.append(group[comp[0]])
            continue
        # Terminal endpoints = endpoints whose node is not dissolvable.
        terms = [nk for bi in comp for nk in ends[bi] if nk not in dissolvable]
        uniq = list(dict.fromkeys(terms))
        if len(uniq) != 2:
            out.extend(group[i] for i in comp)  # unexpected topology — keep
            continue
        ref = group[comp[0]]
        try:
            out.append(
                Beam(
                    f"{ref.name}_m",
                    point_at[uniq[0]],
                    point_at[uniq[1]],
                    sec=ref.section,
                    mat=ref.material,
                    up=ref.up,
                    parent=parent,
                )
            )
        except Exception as exc:
            logger.debug(f"colinear merge skipped for {ref.name!r}: {exc}")
            out.extend(group[i] for i in comp)
    return out
