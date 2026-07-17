"""The shared B-rep connectivity store.

Owns the entity graph (:mod:`ada.geom.brep.entities`) for one body and guarantees
its invariants — chiefly that a vertex/edge is a single shared object, however many
faces reach it. Both producers build through it:

* the **import** producer (Genie SAT) preserves record identity: it calls the raw
  ``add_*`` builders and maps each SAT record id to one entity, so sharing comes
  from the source ``$``-references;
* the **derive** producer (geometry alone) calls the deduplicating
  :meth:`vertex_at` / :meth:`edge_between`, so a corner reached from two faces
  resolves to the same :class:`BVertex` and an arc is told apart from its chord.

The store forbids nothing about manifoldness — an edge may carry two coedges (a
normal shared edge) or more (a non-manifold junction, e.g. a stiffener web meeting
a plate). That neutrality is deliberate: it must hold whatever a real hull body
contains.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.geom.brep.entities import (
    BCoEdge,
    BEdge,
    BFace,
    BLoop,
    BLump,
    BShell,
    BVertex,
    BWire,
    LoopKind,
)
from ada.geom.brep.geom_keys import curve_key, point_key

if TYPE_CHECKING:
    from ada.geom.curves import CURVE_GEOM_TYPES
    from ada.geom.points import Point
    from ada.geom.surfaces import SURFACE_GEOM_TYPES


@dataclass
class Unresolved:
    """A source entity a producer could not build — recorded, never silent.

    The store's completeness contract: every source entity is either built or
    listed here with a reason, so a drop can never be mistaken for a faithful
    import. ``kind`` is e.g. "edge"/"face"; ``source_id`` is its source record id.
    """

    kind: str
    source_id: str
    reason: str


class BRepStore:
    """A body's shared vertices, edges, coedges, loops, faces, shells and lumps.

    ``dedup_nd`` is the rounding (decimal places) used by :meth:`vertex_at` and
    :meth:`edge_between` to weld coincident geometry; 1e-6 m is far below any
    modelling tolerance and above read-to-read noise. The import producer does not
    use it (it preserves identity directly).
    """

    def __init__(self, dedup_nd: int = 6):
        self.dedup_nd = dedup_nd
        self._id = 0
        self.vertices: dict[int, BVertex] = {}
        self.edges: dict[int, BEdge] = {}
        self.coedges: dict[int, BCoEdge] = {}
        self.loops: dict[int, BLoop] = {}
        self.faces: dict[int, BFace] = {}
        self.shells: dict[int, BShell] = {}
        self.lumps: dict[int, BLump] = {}
        self.wires: dict[int, BWire] = {}
        # source entities a producer could not build (completeness contract)
        self.unresolved: list[Unresolved] = []
        # dedup indices (derive producer)
        self._vkey: dict[tuple, BVertex] = {}
        self._ekey: dict[tuple, BEdge] = {}
        # partner ring: edge id -> its coedges
        self._coedges_on: dict[int, list[BCoEdge]] = defaultdict(list)

    def next_id(self) -> int:
        self._id += 1
        return self._id

    # ---- raw builders (identity-preserving; used by the import producer) ----
    def add_vertex(self, point: Point, name: str | None = None, source_id: str | None = None) -> BVertex:
        v = BVertex(self.next_id(), point, name=name, source_id=source_id)
        self.vertices[v.id] = v
        return v

    def add_edge(
        self,
        curve: CURVE_GEOM_TYPES,
        start: BVertex,
        end: BVertex,
        t_start: float | None = None,
        t_end: float | None = None,
        name: str | None = None,
        source_id: str | None = None,
    ) -> BEdge:
        e = BEdge(self.next_id(), curve, start, end, t_start, t_end, name=name, source_id=source_id)
        self.edges[e.id] = e
        return e

    def add_coedge(
        self,
        edge: BEdge,
        sense: bool,
        loop: BLoop | None = None,
        pcurve=None,
        source_id: str | None = None,
    ) -> BCoEdge:
        c = BCoEdge(self.next_id(), edge, sense, loop, pcurve=pcurve, source_id=source_id)
        self.coedges[c.id] = c
        self._coedges_on[edge.id].append(c)
        if loop is not None:
            loop.coedges.append(c)
        return c

    def add_loop(
        self, kind: LoopKind = LoopKind.OUTER, bbox: list[float] | None = None, source_id: str | None = None
    ) -> BLoop:
        lp = BLoop(self.next_id(), kind, bbox=bbox, source_id=source_id)
        self.loops[lp.id] = lp
        return lp

    def add_face(
        self,
        surface: SURFACE_GEOM_TYPES,
        sense: bool,
        outer: BLoop | None = None,
        inner: list[BLoop] | None = None,
        name: str | None = None,
        bbox: list[float] | None = None,
        param_box: list[float] | None = None,
        source_id: str | None = None,
    ) -> BFace:
        f = BFace(
            self.next_id(),
            surface,
            sense,
            outer,
            list(inner or []),
            name=name,
            bbox=bbox,
            param_box=param_box,
            source_id=source_id,
        )
        self.faces[f.id] = f
        for lp in f.loops:
            lp.face = f
        return f

    def add_shell(self, faces: list[BFace] | None = None, source_id: str | None = None) -> BShell:
        s = BShell(self.next_id(), list(faces or []), source_id=source_id)
        self.shells[s.id] = s
        for f in s.faces:
            f.shell = s
        return s

    def add_lump(self, shells: list[BShell] | None = None, source_id: str | None = None) -> BLump:
        lp = BLump(self.next_id(), list(shells or []), source_id=source_id)
        self.lumps[lp.id] = lp
        for sh in lp.shells:
            sh.lump = lp
        return lp

    def add_wire(self, coedges: list[BCoEdge] | None = None, source_id: str | None = None) -> BWire:
        w = BWire(self.next_id(), list(coedges or []), source_id=source_id)
        self.wires[w.id] = w
        return w

    def mark_unresolved(self, kind: str, source_id: str, reason: str) -> None:
        self.unresolved.append(Unresolved(kind, source_id, reason))

    # ---- deduplicating builders (used by the derive producer) ----
    def vertex_at(self, point: Point, source_id: str | None = None) -> BVertex:
        """The shared vertex at ``point`` — an existing one within ``dedup_nd`` or a
        new one. This is what makes a corner reached from two faces one vertex."""
        key = point_key(point, self.dedup_nd)
        v = self._vkey.get(key)
        if v is None:
            v = self.add_vertex(point, source_id=source_id)
            self._vkey[key] = v
        return v

    def edge_between(
        self,
        curve: CURVE_GEOM_TYPES,
        start: BVertex,
        end: BVertex,
        t_start: float | None = None,
        t_end: float | None = None,
        source_id: str | None = None,
    ) -> BEdge:
        """The shared edge on ``curve`` between the two vertices — existing or new.

        Keyed on the vertex-id pair plus a curve fingerprint, so two faces meeting
        along one edge share it, while an arc and its chord between the same corners
        stay distinct."""
        key = (min(start.id, end.id), max(start.id, end.id), curve_key(curve, self.dedup_nd))
        e = self._ekey.get(key)
        if e is None:
            e = self.add_edge(curve, start, end, t_start, t_end, source_id=source_id)
            self._ekey[key] = e
        return e

    # ---- queries ----
    def coedges_on(self, edge: BEdge) -> list[BCoEdge]:
        """The partner ring: every coedge that uses ``edge``."""
        return self._coedges_on.get(edge.id, [])

    def summary(self) -> dict[str, int]:
        return {
            "vertices": len(self.vertices),
            "edges": len(self.edges),
            "coedges": len(self.coedges),
            "loops": len(self.loops),
            "faces": len(self.faces),
            "shells": len(self.shells),
            "lumps": len(self.lumps),
            "wires": len(self.wires),
            "unresolved": len(self.unresolved),
        }

    def dangling_coedges(self) -> list[BCoEdge]:
        """Coedges whose edge has no partner (a boundary/free edge) — useful as a
        health check: a closed manifold body should have none."""
        return [c for c in self.coedges.values() if len(self._coedges_on.get(c.edge.id, [])) < 2]
