"""Export a Part through its imported connectivity store.

When a Part was read from a Genie concept XML it carries the source body as a
:class:`~ada.geom.brep.BRepStore` (identity-preserving, complete). Exporting through
that store — instead of re-welding the plate outlines — gives back the source's
exact topology (1 lump, every shared edge present), so:

* the SAT body is a faithful reproduction (proven to re-import in Genie), and
* every beam resolves to a real named edge, because the store has them all — the
  edges the geometry weld dropped (the flat-plate stiffener splits) are present.

``face_map`` comes straight from the plate metadata the reader preserved
(``gxml_face_refs``, which are the store's face names). ``edge_map`` is recovered
by matching each beam's axis onto the store's named edges — the same walk the weld
uses, but over the complete store, so it succeeds where the weld left beams bare.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

import numpy as np

from ada.cadit.sat.write.from_brep import brep_store_to_sat_writer
from ada.config import logger
from ada.geom import curves as geo_cu

if TYPE_CHECKING:
    from ada.geom.brep import BRepStore


def _p3(pt) -> tuple:
    return tuple(float(c) for c in list(pt)[:3])


def _vk(p, nd: int) -> tuple:
    return tuple(round(float(c), nd) for c in p)


class _StoreEdgeMatcher:
    """Find the named store edges a beam axis lies on (straight chain or arc chain)."""

    def __init__(self, store: BRepStore, conn_nd: int = 5):
        self.store = store
        self.nd = conn_nd
        self._straight = None  # vertex-key -> [(other-key, edge)]
        self._curved = None

    def _adj(self, straight: bool):
        cache = self._straight if straight else self._curved
        if cache is not None:
            return cache
        adj: dict[tuple, list] = defaultdict(list)
        for e in self.store.edges.values():
            is_line = isinstance(e.curve, geo_cu.Line)
            # a stiffener's edge is a straight-curve OR an intcurve on a spline
            # sub-face; an arc beam's edge is an ellipse/bspline. Split accordingly.
            if straight and not is_line:
                # include bsplines too? straight beams on spline faces get intcurve
                # edges — keep those in the straight set (they run along the axis)
                if not isinstance(e.curve, (geo_cu.BSplineCurveWithKnots,)):
                    continue
            if (not straight) and is_line:
                continue
            a, b = _vk(_p3(e.start.point), self.nd), _vk(_p3(e.end.point), self.nd)
            adj[a].append((b, e))
            adj[b].append((a, e))
        if straight:
            self._straight = adj
        else:
            self._curved = adj
        return adj

    def _nearest(self, adj, p, tol):
        k = _vk(p, self.nd)
        if k in adj:
            return k
        a = np.asarray(p, float)
        best, bd = None, tol
        for key in adj:
            d = float(np.linalg.norm(np.asarray(key, float) - a))
            if d <= bd:
                best, bd = key, d
        return best

    def axis_edges(self, p1, p2, tol=1e-3):
        """Straight-chain walk: the collinear named edges tiling p1->p2."""
        adj = self._adj(straight=True)
        a = np.asarray(_p3(p1), float)
        b = np.asarray(_p3(p2), float)
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            return []
        u = d / L
        sk = self._nearest(adj, a, tol)
        if sk is None:
            sk = self._nearest(adj, b, tol)
            if sk is None:
                return []
            a, b = b, a
            u = -u
        chain, visited = [], set()
        cur = a
        cur_k = sk
        while float(np.linalg.norm(cur - b)) > tol:
            t_cur = float(np.dot(cur - a, u))
            best = None
            best_off = tol
            for ok, e in adj.get(cur_k, ()):
                if id(e) in visited:
                    continue
                o = np.asarray(ok, float)
                w = o - a
                t = float(np.dot(w, u))
                if t <= t_cur + 1e-9 or t > L + tol:
                    continue
                off = float(np.linalg.norm(w - t * u))
                if off < best_off:
                    best = (e, o, ok)
                    best_off = off
            if best is None:
                break
            e, o, ok = best
            visited.add(id(e))
            chain.append(e)
            cur = o
            cur_k = ok
        return chain

    def arc_edges(self, p1, p2, tol=1e-3, max_depth=8):
        """Curved-chain BFS: the arc-edge chain between p1 and p2 within a corridor."""
        adj = self._adj(straight=False)
        k1 = self._nearest(adj, _p3(p1), tol)
        k2 = self._nearest(adj, _p3(p2), tol)
        if k1 is None or k2 is None or k1 == k2:
            return []
        a = np.asarray(_p3(p1), float)
        b = np.asarray(_p3(p2), float)
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            return []
        u = d / L
        corridor = max(0.5, 0.5 * L)
        prev = {k1: (None, None)}
        q = deque([(k1, 0)])
        while q:
            cur, depth = q.popleft()
            if cur == k2:
                break
            if depth >= max_depth:
                continue
            for ok, e in adj.get(cur, ()):
                if ok in prev:
                    continue
                o = np.asarray(ok, float)
                t = float(np.dot(o - a, u))
                lat = float(np.linalg.norm((o - a) - t * u))
                if t < -corridor or t > L + corridor or lat > corridor:
                    continue
                prev[ok] = (cur, e)
                q.append((ok, depth + 1))
        if k2 not in prev:
            return []
        chain, cur = [], k2
        while prev[cur][1] is not None:
            pk, e = prev[cur]
            chain.append(e)
            cur = pk
        return chain

    def names_on_axis(self, p1, p2) -> list[str]:
        edges = self.axis_edges(p1, p2) or self.arc_edges(p1, p2)
        names = []
        for e in edges:
            if e.name and e.name not in names:
                names.append(e.name)
        return names


def part_store_to_sat_writer(part, store: BRepStore):
    """A :class:`SatWriter` serialised from ``store``, with face/edge maps recovered
    from the Part's plates (preserved refs) and beams (axis match)."""
    from ada import Beam, BeamTapered
    from ada.api.beams import BeamRevolve
    from ada.api.plates import PlateCurved

    from ada import Plate

    sw = brep_store_to_sat_writer(store, part)

    for pl in part.get_all_physical_objects(by_type=(Plate, PlateCurved)):
        props = (pl.metadata or {}).get("props", {}) if isinstance(pl.metadata, dict) else {}
        refs = props.get("gxml_face_refs")
        if not refs:
            single = props.get("gxml_face_ref")
            refs = [single] if single else []
        refs = [r for r in refs if r]
        if refs:
            sw.face_map[pl.guid] = list(refs)

    matcher = _StoreEdgeMatcher(store)
    n_beam = n_ref = 0
    for bm in part.get_all_physical_objects(by_type=(Beam, BeamTapered, BeamRevolve)):
        n_beam += 1
        p1, p2 = bm.axis_global()
        names = matcher.names_on_axis(p1, p2)
        if names:
            sw.edge_map[bm.guid] = names
            n_ref += 1
    logger.info(f"brep-part-writer: face_map={len(sw.face_map)} beams referenced {n_ref}/{n_beam}")
    return sw
