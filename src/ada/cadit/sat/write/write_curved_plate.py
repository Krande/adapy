from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.cadit.sat.utils import make_ints_if_possible
from ada.config import logger
from ada.cadit.sat.write import sat_entities as se
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

if TYPE_CHECKING:
    from ada.api.plates import PlateCurved
    from ada.cadit.sat.write.writer import SatWriter


class UnsupportedCurvedFace(NotImplementedError):
    """The face uses geometry this writer cannot author yet.

    Raised rather than approximated: a curved plate silently degraded to
    something else is worse than one the caller can log and fall back on.
    """


def _vkey(p, nd: int) -> tuple:
    return tuple(round(float(c), nd) for c in p)


def _curve_key(curve, nd: int) -> tuple:
    """A fingerprint that tells two curves between the same points apart.

    Position alone is not enough to call two edges the same edge: two faces can
    meet at a pair of vertices and still be bounded by different curves between
    them (two arcs of a circle, most obviously).
    """
    if isinstance(curve, geo_cu.Line):
        return ("line",)
    if isinstance(curve, geo_cu.Circle):
        return (
            "circle",
            round(float(curve.radius), nd),
            _vkey(curve.position.location, nd),
            _vkey(curve.position.axis, nd),
        )
    if isinstance(curve, geo_cu.Ellipse):
        return (
            "ellipse",
            round(float(curve.semi_axis1), nd),
            round(float(curve.semi_axis2), nd),
            _vkey(curve.position.location, nd),
            _vkey(curve.position.axis, nd),
        )
    if isinstance(curve, geo_cu.BSplineCurveWithKnots):
        return ("bspline", curve.degree, tuple(_vkey(p, nd) for p in curve.control_points_list))
    return ("?", type(curve).__name__, id(curve))


class TopologyWeld:
    """The vertices and edges shared by every curved face in the body.

    A face built in isolation mints its own vertex at each corner, so two faces
    meeting along an edge leave two coincident vertices and two coincident
    edges in the same shell — which ACIS rejects as "duplicate vertex", once per
    pair. Genie's own export shares them: 6159 vertices and 11627 edges for the
    5470 faces where one-face-at-a-time produces 23186 of each.

    The sharing is recoverable from what the reader hands over — welding by
    position and curve gives 6111 vertices and 11573 edges, the difference being
    the faces the reader could not convert. Faces are keyed on rounded
    coordinates: two faces carry the same corner through separate reads and need
    not agree in the last bit. 1e-7 m is far below any modelling tolerance and
    far above that noise.
    """

    def __init__(self, id_gen, nd: int = 7):
        self.id_gen = id_gen
        self.nd = nd
        self.entities: list[se.SATEntity] = []
        self._vertices: dict[tuple, se.Vertex] = {}
        self._edges: dict[tuple, se.Edge] = {}
        # edge -> the coedges lying on it, with a vector pointing from the edge
        # into each one's face (for the radial ordering ACIS wants)
        self.coedges_on_edge: dict[int, list[tuple[se.CoEdge, np.ndarray]]] = defaultdict(list)
        self.range_conflicts = 0
        # vertex-key -> axis-candidate edges incident to it, built lazily for the
        # beam-edge naming walk (see find_axis_edges_on). Valid only once no more
        # edges are added, which is the case by the time beams are named.
        self._straight_adj: dict[tuple, list[se.Edge]] | None = None
        # vertex-key -> incident curved (ellipse/intcurve) edges, for arc-beam
        # naming (see find_arc_edge). Same lazy-build lifetime as _straight_adj.
        self._curved_adj: dict[tuple, list[se.Edge]] | None = None
        # Coarser rounding for beam-walk connectivity only (not vertex identity):
        # two sub-faces meeting at a bay boundary come from separate reads and
        # their shared corner can disagree at the 6th-7th decimal, which breaks an
        # exact-key chain walk. 1e-5 m (0.01 mm) merges that noise while staying
        # far below any real vertex spacing. Geometry checks still use full precision.
        self._conn_nd: int = 5

    def vertex_at(self, p) -> se.Vertex:
        key = _vkey(p, self.nd)
        vertex = self._vertices.get(key)
        if vertex is None:
            sat_point = se.SatPoint(self.id_gen.next_id(), ada.Point(*p))
            vertex = se.Vertex(self.id_gen.next_id(), None, sat_point)
            self._vertices[key] = vertex
            self.entities.extend([sat_point, vertex])
        return vertex

    def edge_key(self, p_lo, p_hi, curve_geom) -> tuple:
        ka, kb = _vkey(p_lo, self.nd), _vkey(p_hi, self.nd)
        return (min(ka, kb), max(ka, kb), _curve_key(curve_geom, self.nd))

    def get_edge(self, key: tuple) -> se.Edge | None:
        return self._edges.get(key)

    def add_edge(self, key: tuple, edge: se.Edge, curve: se.SATEntity) -> None:
        self._edges[key] = edge
        self.entities.extend([curve, edge])

    @property
    def n_vertices(self) -> int:
        return len(self._vertices)

    @property
    def n_edges(self) -> int:
        return len(self._edges)

    def _curved_adjacency(self) -> dict[tuple, list[se.Edge]]:
        """Vertex-key -> incident curved edges (ellipse or intcurve), cached.

        A curved beam's arc is authored as an ellipse-curve when the reader read a
        Circle/Ellipse, but as an intcurve-curve when it bounds a spline sub-face —
        the source names both kinds for its curved beams (verified). Straight edges
        are excluded so the arc is not confused with a chord between the same
        corners.
        """
        if self._curved_adj is None:
            adj: dict[tuple, list[se.Edge]] = defaultdict(list)
            for edge in self._edges.values():
                if not isinstance(edge.straight_curve, (se.EllipseCurve, se.IntCurve)):
                    continue
                adj[_vkey(edge.start_pt, self._conn_nd)].append(edge)
                adj[_vkey(edge.end_pt, self._conn_nd)].append(edge)
            self._curved_adj = adj
        return self._curved_adj

    def find_arc_edge(self, p1, p2, tol: float = 1e-4) -> se.Edge | None:
        """An already-built curved edge running between ``p1`` and ``p2``, or None.

        A curved beam's axis is not a standalone wire in a Genie export — it is a
        face edge of the curved plate the beam lies along, which Genie names and
        the beam references (its own SAT is one body / one lump / one shell). So
        instead of authoring a separate wire body (which leaves a vertex shared
        between a face edge and a wire edge — ACIS rejects the non-manifold
        relink with error 21013 on import), find the existing shared edge and let
        the caller name it. Matched on endpoints (a beam endpoint and the plate
        corner it sits on round independently, so within ``tol``, not exact); the
        curve being an ellipse or intcurve — not straight — keeps a chord between
        the same corners from being mistaken for the beam.
        """
        adj = self._curved_adjacency()
        k1 = self._nearest_key(adj, p1, tol)
        k2 = self._nearest_key(adj, p2, tol)
        if k1 is None or k2 is None:
            return None
        want = {k1, k2}
        for edge in adj.get(k1, ()):
            if {_vkey(edge.start_pt, self._conn_nd), _vkey(edge.end_pt, self._conn_nd)} == want:
                return edge
        return None

    def find_arc_edges_on(self, p1, p2, tol: float = 1e-4, max_depth: int = 8) -> list[se.Edge]:
        """The already-built curved edges tiling the arc between ``p1`` and ``p2``.

        A curved stiffener the reader read as a straight chord (it builds a
        ``BeamRevolve`` only from a circular arc; a spline arc comes back as a
        straight :class:`~ada.Beam`) still lies on a chain of curved face edges.
        Genie's export splits such a stiffener into a short arc chain — typically
        two ``intcurve`` edges meeting at a mid-span vertex — and names every one.
        A straight-line walk (:meth:`find_axis_edges_on`) cannot follow the bulge
        and :meth:`find_arc_edge` only matches a *single* edge spanning the ends,
        so neither catches the two-edge case. This walks the curved adjacency
        breadth-first and returns the shortest chain of curved edges whose ends
        are ``p1`` and ``p2``.

        The walk is confined to a corridor around the chord so it cannot wander
        the whole skin; the true stiffener chain is the shortest path between the
        two corners, so BFS returns it even where several arcs pass nearby.
        """
        from collections import deque

        adj = self._curved_adjacency()
        k1 = self._nearest_key(adj, p1, tol)
        k2 = self._nearest_key(adj, p2, tol)
        if k1 is None or k2 is None or k1 == k2:
            return []
        a = np.asarray(p1, dtype=float)
        b = np.asarray(p2, dtype=float)
        d = b - a
        length = float(np.linalg.norm(d))
        if length < 1e-9:
            return []
        u = d / length
        # An arc's mid-span vertex sits a sagitta off the chord; allow a corridor
        # that grows with span so a genuine chain is never clipped, while still
        # bounding the search to the beam's neighbourhood.
        corridor = max(0.5, 0.5 * length)

        # BFS for the fewest-edge chain; prev maps a vertex-key to the (parent,
        # edge) it was reached through, which reconstructs the chain at the end.
        prev: dict[tuple, tuple] = {k1: (None, None)}
        q = deque([(k1, 0)])
        while q:
            cur, depth = q.popleft()
            if cur == k2:
                break
            if depth >= max_depth:
                continue
            for edge in adj.get(cur, ()):
                sk = _vkey(edge.start_pt, self._conn_nd)
                nk = _vkey(edge.end_pt, self._conn_nd) if sk == cur else sk
                if nk in prev:
                    continue
                o = np.asarray(nk, dtype=float)
                t = float(np.dot(o - a, u))
                lat = float(np.linalg.norm((o - a) - t * u))
                if t < -corridor or t > length + corridor or lat > corridor:
                    continue
                prev[nk] = (cur, edge)
                q.append((nk, depth + 1))
        if k2 not in prev:
            return []
        chain: list[se.Edge] = []
        cur = k2
        while prev[cur][1] is not None:
            parent, edge = prev[cur]
            chain.append(edge)
            cur = parent
        chain.reverse()
        return chain

    def _axis_adjacency(self) -> dict[tuple, list[se.Edge]]:
        """Vertex-key -> incident axis-candidate edges, built once and cached.

        Includes straight *and* intcurve edges, because a stiffener's edge is not
        always a straight-curve: where it bounds a spline sub-face Genie authors
        it as an ``intcurve-curve`` (curve-on-surface), and its own export names
        exactly those for the beam (verified against the source — a straight beam
        can reference an intcurve edge). Ellipse arcs are excluded; those are the
        curved beams, named separately via :meth:`find_arc_edge`.

        Lets the beam walk in :meth:`find_axis_edges_on` step vertex to vertex in
        O(chain) instead of rescanning every weld edge per beam (O(beams x edges)
        is minutes on a hull frame).
        """
        if self._straight_adj is None:
            adj: dict[tuple, list[se.Edge]] = defaultdict(list)
            for edge in self._edges.values():
                if not isinstance(edge.straight_curve, (se.StraightCurve, se.IntCurve)):
                    continue
                adj[_vkey(edge.start_pt, self._conn_nd)].append(edge)
                adj[_vkey(edge.end_pt, self._conn_nd)].append(edge)
            self._straight_adj = adj
        return self._straight_adj

    def _nearest_key(self, adj: dict, p, tol: float) -> tuple | None:
        """An adjacency vertex-key within ``tol`` of ``p`` (exact first).

        A beam endpoint and the plate corner it sits on come from separate reads
        and can disagree in the last rounded digit, so an exact ``_vkey`` lookup
        misses a small tail of genuine matches. Only consulted when the exact key
        misses, so the linear scan runs on that tail alone.
        """
        k = _vkey(p, self._conn_nd)
        if k in adj:
            return k
        a = np.asarray(p, dtype=float)
        best, best_d = None, tol
        for key in adj:
            dist = float(np.linalg.norm(np.asarray(key, dtype=float) - a))
            if dist <= best_d:
                best, best_d = key, dist
        return best

    def find_axis_edges_on(self, p1, p2, tol: float = 1e-4) -> list[se.Edge]:
        """The already-built edges tiling the straight segment ``p1``–``p2``.

        A stiffener on a plate is not a separate wire — its axis coincides with
        the face edges the plate was split into along it (Genie's export is
        pre-split, and the reader hands those sub-faces back, so the edges already
        exist in the weld once the neighbouring faces are authored). So rather
        than imprinting the beam (re-cutting an already-cut panel), find the weld
        edges lying on the beam's axis and let the caller name them — no new
        topology, no relink on import.

        Walks vertex to vertex from ``p1`` toward ``p2``: at each vertex it takes
        the incident edge whose far end stays on the line (laterally within
        ``tol``) and advances along it, so a long stiffener crossing several bays
        resolves to the chain of edges tiling its axis. Every intermediate vertex
        must lie on the straight line, which is what keeps a curved boundary
        (whose vertices leave the line) from being mistaken for the beam. The
        beam's endpoints are panel corners, hence weld vertices; a beam whose ends
        are not weld vertices (lies mid-face, off the split) yields nothing and is
        left to fall back.
        """
        a = np.asarray(p1, dtype=float)
        b = np.asarray(p2, dtype=float)
        d = b - a
        length = float(np.linalg.norm(d))
        if length < 1e-9:
            return []
        u = d / length

        adj = self._axis_adjacency()
        # Walk from whichever endpoint is a weld vertex; both are panel corners,
        # but round independently, so one may miss. If p1 misses, walk from p2
        # toward p1 and the chain is the same set of edges.
        start_key = self._nearest_key(adj, p1, tol)
        if start_key is None:
            start_key = self._nearest_key(adj, p2, tol)
            if start_key is None:
                return []
            a, b = b, a
            u = -u

        chain: list[se.Edge] = []
        visited: set[int] = set()
        cur = a
        cur_key = start_key
        while float(np.linalg.norm(cur - b)) > tol:
            t_cur = float(np.dot(cur - a, u))
            best = None
            best_off = tol
            for edge in adj.get(cur_key, ()):
                if id(edge) in visited:
                    continue
                other = edge.end_pt if _vkey(edge.start_pt, self._conn_nd) == cur_key else edge.start_pt
                o = np.asarray(other, dtype=float)
                w = o - a
                t = float(np.dot(w, u))
                # must advance toward b and stay within the segment
                if t <= t_cur + 1e-9 or t > length + tol:
                    continue
                off = float(np.linalg.norm(w - t * u))
                # among the edges leaving this vertex, take the one that hugs the
                # beam line most tightly — with a loose tol two edges can both be
                # "near" the axis and only the straightest is the stiffener's.
                if off < best_off:
                    best = (edge, o, _vkey(other, self._conn_nd))
                    best_off = off
            if best is None:
                break
            edge, o, o_key = best
            visited.add(id(edge))
            chain.append(edge)
            cur = o
            cur_key = o_key
        return chain


def _surface_entity(id_gen, surface, same_sense: bool) -> tuple[se.SATEntity, str]:
    """The ACIS surface record for an AdvancedFace's ``face_surface``.

    Both kinds occur under a Genie ``curved_shell``: the B-spline patch of a
    genuinely curved plate, and a plain :class:`~ada.geom.surfaces.Plane` for
    the flat faces whose *edges* curve (924 of 5453 in a hull export — a flat
    plate with a spline boundary is not a polygon, so it reads as an advanced
    face even though its surface is planar).

    Returns the record and the sense the *face* should carry, because ACIS
    splits the normal across the two and Genie puts it on whichever record can
    hold it: a spline-surface has a sense of its own, so the face stays forward
    and the surface flips; a plane-surface has none, so the face flips instead.
    Either way the composition is ``same_sense``, which is what IFC models and
    the rest of adapy already consumes.
    """
    if isinstance(surface, geo_su.BSplineSurfaceWithKnots):
        sense = "forward" if same_sense else "reversed"
        return se.SplineSurface(id_gen.next_id(), surface, sense=sense), "forward"
    if isinstance(surface, geo_su.Plane):
        pos = surface.position
        record = se.PlaneSurface(id_gen.next_id(), pos.location, pos.axis, pos.ref_direction)
        return record, ("forward" if same_sense else "reversed")
    raise UnsupportedCurvedFace(f"no ACIS surface record for {type(surface).__name__}")


def _curve_entity(id_gen, curve, t_lo: float, t_hi: float) -> se.SATEntity:
    """The ACIS curve record for an edge's ``edge_geometry``.

    ``t_lo``/``t_hi`` are the edge's parameter range, already ascending; only
    an ellipse needs them (its record carries the range, the other two do not).
    """
    if isinstance(curve, geo_cu.Line):
        return se.StraightCurve(id_gen.next_id(), curve.pnt, curve.dir)
    if isinstance(curve, (geo_cu.Circle, geo_cu.Ellipse)):
        return se.EllipseCurve(id_gen.next_id(), curve, t_lo, t_hi)
    if isinstance(curve, geo_cu.BSplineCurveWithKnots):
        # Covers RationalBSplineCurveWithKnots too — IntCurve picks nurbs off
        # the weights.
        return se.IntCurve(id_gen.next_id(), curve)
    if isinstance(curve, geo_cu.SurfaceCurve):
        # a curve-on-surface writes its exact 3D spline; the per-surface UV
        # images travel on the coedges as pcurve records, which is the pairing
        # ACIS validates ("coedge on spline surface has no PCURVE").
        return se.IntCurve(id_gen.next_id(), curve.curve_3d)
    raise UnsupportedCurvedFace(f"no ACIS curve record for {type(curve).__name__}")


def flat_plate_to_advanced_face(pl) -> geo_su.AdvancedFace:
    """An :class:`~ada.Plate` as the advanced face it already is.

    A flat plate is a plane face bounded by straight edges, which is what a
    curved plate with a planar surface is too — Genie writes them identically,
    down to sharing all four edges with the neighbours (2 coedges each). Putting
    it through the same builder is what lets it share vertices and edges with
    the curved faces around it; built on its own it leaves a coincident copy of
    every corner, which ACIS rejects as "duplicate vertex".

    The outline is wound counter-clockwise about the plate's normal, as ACIS
    reads the material side off the winding, and the surface is stated with that
    normal — so ``same_sense`` is true by construction.
    """
    from ada.cadit.sat.write.write_plate import outline_ccw_about

    points3d, normal = pl.outline_global()
    outline = outline_ccw_about(points3d, normal)

    edges = []
    for i, p_start in enumerate(outline):
        p_end = outline[(i + 1) % len(outline)]
        direction = np.asarray(p_end, dtype=float) - np.asarray(p_start, dtype=float)
        line = geo_cu.Line(ada.Point(*p_start), ada.Direction(*direction))
        edges.append(
            geo_cu.OrientedEdge(
                start=ada.Point(*p_start),
                end=ada.Point(*p_end),
                edge_element=geo_cu.EdgeCurve(
                    start=ada.Point(*p_start), end=ada.Point(*p_end), edge_geometry=line, same_sense=True
                ),
                orientation=True,
            )
        )

    centroid = ada.Point(*np.mean(np.asarray(outline, dtype=float), axis=0))
    plane = geo_su.Plane(
        position=geo_su.Axis2Placement3D(
            location=centroid,
            axis=ada.Direction(*normal),
            ref_direction=ada.Direction(*pl.poly.xdir),
        )
    )
    return geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)],
        face_surface=plane,
        same_sense=True,
    )


def name_curved_beam_edges(sw: SatWriter, weld: TopologyWeld) -> None:
    """Name the shared curved edges the arc beams run along and record the map.

    A :class:`~ada.api.beams.BeamRevolve`'s axis coincides with the face edges of
    the curved plate it lies on. Genie names those edges and points the curved
    beam at them — no new topology. We do the same: find the weld edge(s) between
    the beam's two endpoints and give each an ``EDGE`` name, so the gxml writer
    can emit a ``<curved_beam>`` whose ``<sat_reference>`` resolves on import.

    The arc is usually not a single edge: Genie splits a curved stiffener at each
    frame it crosses, so its axis is a *chain* of curved edges (commonly two, at a
    mid-span vertex). Try the single shared edge first, then walk the curved
    adjacency for the whole chain. A beam whose arc is on no plate edge gets no
    name and falls back to a straight chord in the gxml writer.
    """
    from ada.api.beams import BeamRevolve

    beams = list(sw.part.get_all_physical_objects(by_type=BeamRevolve))
    if not beams:
        return

    named = 0
    for bm in beams:
        p1, p2 = bm.axis_global()
        edge = weld.find_arc_edge(p1, p2, tol=1e-3)
        edges = [edge] if edge is not None else weld.find_arc_edges_on(p1, p2, tol=1e-3)
        if not edges:
            continue
        names: list[str] = []
        for edge in edges:
            if edge.attrib_name is None:
                name = f"EDGE{sw.edge_name_id:08d}"
                sw.edge_name_id += 1
                edge.attrib_name = se.StringAttribName(sw.id_generator.next_id(), name, edge)
                sw.add_entity(edge.attrib_name)
            if edge.attrib_name.name not in names:
                names.append(edge.attrib_name.name)
        sw.edge_map[bm.guid] = names
        named += 1

    logger.info(f"sat-write: named {named}/{len(beams)} curved-beam axes on shared curved-plate edges")


def name_straight_beam_edges(sw: SatWriter, weld: TopologyWeld) -> int:
    """Name the existing welded edges each straight beam's axis lies on.

    A stiffener on a plate coincides with the face edges the plate is split into
    along it. Genie's own export is pre-split and the reader hands those sub-faces
    back, so once the neighbouring faces are welded the stiffener's edge already
    exists — shared between the two sub-faces it separates. Naming it and pointing
    the beam's ``<sat_reference>`` at it lets Genie reuse the edge instead of
    re-imprinting the beam on import (which relinks a face edge and raises ACIS
    21013). No fuse, no new topology — the edge Genie needs is already there.

    A beam already named (e.g. a curved beam handled as an arc) is left alone. A
    long stiffener spanning several bays resolves to a chain of collinear edges,
    all named and referenced in order.
    """
    from ada import Beam, BeamTapered

    beams = list(sw.part.get_all_physical_objects(by_type=(Beam, BeamTapered)))
    if not beams:
        return 0

    named = 0
    for bm in beams:
        if bm.guid in sw.edge_map:
            continue
        p1, p2 = bm.axis_global()
        # 1e-3 (1 mm): a beam endpoint and the plate corner it sits on come from
        # separate reads and disagree by more than the 1e-4 default in a small
        # tail; the min-lateral pick in the walk keeps the looser tol from taking
        # a wrong edge.
        edges = weld.find_axis_edges_on(p1, p2, tol=1e-3)
        if not edges:
            # A curved stiffener read as a straight chord (the reader only builds
            # BeamRevolve from circular arcs) has edges that curve along the skin,
            # so the straight-line walk cannot follow them. Genie splits such a
            # stiffener into a short arc chain (usually two intcurve edges at a
            # mid-span vertex) and references every one for the straight beam, so
            # walk the curved edges and name the whole chain.
            edges = weld.find_arc_edges_on(p1, p2, tol=1e-3)
        if not edges:
            continue
        names: list[str] = []
        for edge in edges:
            if edge.attrib_name is None:
                name = f"EDGE{sw.edge_name_id:08d}"
                sw.edge_name_id += 1
                edge.attrib_name = se.StringAttribName(sw.id_generator.next_id(), name, edge)
                sw.add_entity(edge.attrib_name)
            if edge.attrib_name.name not in names:
                names.append(edge.attrib_name.name)
        if names:
            sw.edge_map[bm.guid] = names
            named += 1

    logger.info(f"sat-write: named {named}/{len(beams)} straight-beam axes on existing welded edges")
    return named


def name_imprinted_beam_edges(sw: SatWriter, weld: TopologyWeld, beams, beam_edges) -> int:
    """Name the weld edges each beam was imprinted into and record beam -> [EDGE].

    ``beam_edges`` is index-aligned with ``beams`` (see
    :func:`~ada.cadit.sat.write.imprint_faces.imprint_advanced_faces`): each entry
    is the endpoint pairs of the face-bounding edges that beam became. Naming them
    and pointing the beam's ``<sat_reference>`` at them lets Genie reuse the edge
    instead of re-imprinting the beam on import (which relinks and raises 21013).
    An edge index built once keeps this off the O(beams x edges) diagonal.
    """
    index: dict[frozenset, se.Edge] = {}
    for edge in weld._edges.values():
        index[frozenset({_vkey(edge.start_pt, weld.nd), _vkey(edge.end_pt, weld.nd)})] = edge

    named = 0
    for bm, pairs in zip(beams, beam_edges):
        names: list[str] = []
        for p1, p2 in pairs:
            edge = index.get(frozenset({_vkey(p1, weld.nd), _vkey(p2, weld.nd)}))
            if edge is None:
                continue
            if edge.attrib_name is None:
                name = f"EDGE{sw.edge_name_id:08d}"
                sw.edge_name_id += 1
                edge.attrib_name = se.StringAttribName(sw.id_generator.next_id(), name, edge)
                sw.add_entity(edge.attrib_name)
            if edge.attrib_name.name not in names:
                names.append(edge.attrib_name.name)
        if names:
            sw.edge_map[bm.guid] = names
            named += 1

    logger.info(f"sat-write: named imprinted edges for {named}/{len(beams)} beams")
    return named


def curved_plate_to_sat_entities(
    pl: PlateCurved, face_name: str, sw: SatWriter, weld: TopologyWeld
) -> list[se.SATEntity]:
    """Convert one :class:`~ada.api.plates.PlateCurved` into its ACIS face."""
    return advanced_face_to_sat_entities(pl.geom.geometry, face_name, sw, weld)


def advanced_face_to_sat_entities(geom, face_name: str, sw: SatWriter, weld: TopologyWeld) -> list[se.SATEntity]:
    """Convert one :class:`~ada.geom.surfaces.AdvancedFace` into its ACIS face.

    Vertices and edges come from ``weld``, so a face shares them with its
    neighbours instead of minting coincident copies (see :class:`TopologyWeld`).
    The imprint path cannot take these — it splits *planar* outlines and has
    nothing to say about a B-spline patch — so the sharing is recovered by
    position and curve rather than by re-cutting the geometry.

    Senses are reconstructed from the edge's parameter range rather than
    guessed. Every edge in a Genie export is ``forward`` (18924 of 18924 in a
    hull model) and its parameters ascend, so the loop's direction lives on the
    coedge: the reader hands back ``t_start > t_end`` exactly when the loop runs
    the edge against its curve, which is a ``reversed`` coedge and an edge whose
    two vertices swap. Deriving it from the range keeps the edge record's range
    ascending, as ACIS reads it.
    """
    if not isinstance(geom, geo_su.AdvancedFace):
        raise UnsupportedCurvedFace(f"{type(geom).__name__} is not an AdvancedFace")
    # The reader gives one bound per face — it does not surface hole loops (16
    # of a hull export's faces have one and lose it on the way in). Guard
    # rather than emit the outer loop alone and call the plate round-tripped.
    if len(geom.bounds) != 1:
        raise UnsupportedCurvedFace(f"{len(geom.bounds)} bounds; only a single outer loop is supported")
    bound = geom.bounds[0].bound
    if not isinstance(bound, geo_cu.EdgeLoop):
        raise UnsupportedCurvedFace(f"{type(bound).__name__} is not an EdgeLoop")
    edge_list = bound.edge_list
    if len(edge_list) < 2:
        raise UnsupportedCurvedFace(f"{len(edge_list)} edges in the loop")

    id_gen = sw.id_generator
    entities: list[se.SATEntity] = []

    surface, face_sense = _surface_entity(id_gen, geom.face_surface, geom.same_sense)
    entities.append(surface)

    face_id = id_gen.next_id()
    name = se.StringAttribName(id_gen.next_id(), face_name, face_id)
    face = se.Face(face_id, None, sw.shell, name, surface, sense=face_sense)
    name.entity = face
    entities += [face, name]

    loop = se.Loop(id_gen.next_id(), None, [0.0] * 6, face=face)
    face.loop = loop
    entities.append(loop)

    loop_points = [np.asarray(oe.start, dtype=float) for oe in edge_list]

    coedges: list[se.CoEdge] = []
    face_vertices: list[se.Vertex] = []
    for i, oriented_edge in enumerate(edge_list):
        edge_curve = oriented_edge.edge_element
        curve_geom = getattr(edge_curve, "edge_geometry", None)
        if curve_geom is None:
            raise UnsupportedCurvedFace("edge carries no curve geometry")

        p_start, p_end = oriented_edge.start, oriented_edge.end

        # A zero-length edge (coincident endpoints) has no direction to
        # normalise and bounds nothing: a StraightCurve built on it blows up in
        # ``Direction.get_normalized`` and takes the whole SAT write down with
        # it. ACIS marks such singularities with a null curve and the reader
        # steps over them (see ``ACISDegenerateEdge`` in read/curves.py); do the
        # same here so the coedge ring simply closes over the survivors.
        if _vkey(p_start, weld.nd) == _vkey(p_end, weld.nd):
            logger.debug("advanced_face %r: skipping zero-length edge at %s", face_name, tuple(p_start))
            continue

        t_start, t_end = oriented_edge.t_start, oriented_edge.t_end
        if t_start is None or t_end is None:
            # Only a straight curve has a parameterisation we can rebuild
            # unaided (arc length from the edge start); on a circle or a
            # b-spline the range is not recoverable from the endpoints. Re-seat
            # the line on the edge's own start so that 0..length — what Edge
            # falls back to without parameters — is the range it means.
            if not isinstance(curve_geom, geo_cu.Line):
                raise UnsupportedCurvedFace(f"{type(curve_geom).__name__} edge without authored parameters")
            curve_geom = geo_cu.Line(ada.Point(*p_start), ada.Direction(*(np.asarray(p_end) - np.asarray(p_start))))
        runs_backwards = t_start is not None and t_end < t_start
        if runs_backwards:
            t_lo, t_hi = t_end, t_start
            p_lo, p_hi = p_end, p_start
        else:
            t_lo, t_hi = t_start, t_end
            p_lo, p_hi = p_start, p_end

        # The neighbour that already built this edge wrote the vertices, the
        # curve and the range; only the coedge is ours.
        key = weld.edge_key(p_lo, p_hi, curve_geom)
        edge = weld.get_edge(key)
        if edge is None:
            curve = _curve_entity(id_gen, curve_geom, t_lo, t_hi)
            v_start, v_end = weld.vertex_at(p_lo), weld.vertex_at(p_hi)
            edge = se.Edge(
                id_gen.next_id(),
                v_start,
                v_end,
                None,
                curve,
                start_pt=ada.Point(*p_lo),
                end_pt=ada.Point(*p_hi),
                t_start=t_lo,
                t_end=t_hi,
            )
            weld.add_edge(key, edge, curve)
        elif t_lo is not None and edge.t_start is not None:
            # Two faces on one edge must agree on where it starts and stops;
            # they came from the same record, so this is a tolerance question
            # rather than a real disagreement. Count it instead of picking one.
            if abs(edge.t_start - t_lo) > 1e-6 or abs(edge.t_end - t_hi) > 1e-6:
                weld.range_conflicts += 1

        for v in (edge.vertex_start, edge.vertex_end):
            if v.edge is None:
                v.edge = edge
            face_vertices.append(v)

        # Which way this loop runs the edge, asked of the edge rather than of
        # our own parameters: a neighbour may have built it, and the sense has
        # to be read against the record as written. For the face that built it
        # this says exactly what `runs_backwards` did.
        runs_backwards = _vkey(p_start, weld.nd) != _vkey(edge.start_pt, weld.nd)

        # A pcurve belongs to a spline face: it is the edge in that surface's
        # parameter space, and a plane has none to speak of. Genie agrees —
        # every coedge on a spline face carries one and no coedge on a plane
        # face does.
        pcurve = None
        if oriented_edge.pcurve is not None and isinstance(surface, se.SplineSurface):
            # The sense is authored, not derived: it says whether the 2D curve
            # runs along its edge's 3D curve, and nothing in the knots or the
            # coedge implies it. Defaulting it to forward is what ACIS rejects
            # as "pcurve's range doesn't include coedge's range".
            pcurve = se.PCurve(
                id_gen.next_id(),
                oriented_edge.pcurve,
                surface,
                sense="forward" if oriented_edge.pcurve.same_sense else "reversed",
            )
            entities.append(pcurve)

        coedge = se.CoEdge(
            id_gen.next_id(),
            None,
            None,
            edge,
            loop,
            "reversed" if runs_backwards else "forward",
            pcurve=pcurve,
        )
        # An edge names ONE of its coedges; the others are reached round the
        # partner ring, so the first face to claim it wins.
        if edge.coedge is None:
            edge.coedge = coedge
        weld.coedges_on_edge[id(edge)].append((coedge, _into_face(p_lo, p_hi, loop_points)))
        entities.append(coedge)
        coedges.append(coedge)

    for i, coedge in enumerate(coedges):
        coedge.next_coedge = coedges[(i + 1) % len(coedges)]
        coedge.prev_coedge = coedges[i - 1]
    loop.coedge = coedges[0]

    unique = {id(v): v for v in face_vertices}
    pts = np.asarray([v.point.point for v in unique.values()], dtype=float)
    loop.bbox = make_ints_if_possible([*np.min(pts, axis=0), *np.max(pts, axis=0)])

    return sorted(entities, key=lambda x: x.id)


def _into_face(p_lo, p_hi, loop_points: list[np.ndarray]) -> np.ndarray:
    """A vector from the edge into the face it bounds, perpendicular to it.

    Where an edge carries more than two coedges, ACIS wants them ordered by
    where their faces sit radially about it, and this is what that angle is
    measured on. The face's centroid stands in for the surface normal, which
    would otherwise have to be evaluated on the patch; it only has to separate
    the faces around the edge, and only 35 of a hull export's 11573 edges carry
    more than two.
    """
    a = np.asarray(p_lo, dtype=float)
    b = np.asarray(p_hi, dtype=float)
    axis = b - a
    length = float(np.linalg.norm(axis))
    if length < 1e-12:
        return np.zeros(3)
    axis = axis / length
    into = np.mean(np.asarray(loop_points, dtype=float), axis=0) - 0.5 * (a + b)
    into = into - axis * float(into @ axis)
    norm = float(np.linalg.norm(into))
    return into / norm if norm > 1e-12 else np.zeros(3)


def link_partner_rings(weld: TopologyWeld) -> None:
    """Join the coedges lying on each edge into a ring, ordered about it.

    An edge bounding one face leaves the slot null, as Genie writes it; two
    faces link to each other. Beyond that ACIS wants them counter-clockwise
    about the edge's own direction and says so ("coedges out of order about
    edge") — the same rule the imprinted planar path sorts on.
    """
    for entries in weld.coedges_on_edge.values():
        if len(entries) < 2:
            continue
        if len(entries) == 2:
            # a 2-cycle reads the same either way round
            ordered = [entries[0][0], entries[1][0]]
        else:
            ordered = _ordered_about_edge(entries)
        for cur, nxt in zip(ordered, ordered[1:] + ordered[:1]):
            cur.partner = nxt


def _ordered_about_edge(entries) -> list[se.CoEdge]:
    """The coedges on one edge, counter-clockwise about it."""
    coedge = entries[0][0]
    edge = coedge.edge
    a = np.asarray(edge.start_pt, dtype=float)
    b = np.asarray(edge.end_pt, dtype=float)
    axis = b - a
    length = float(np.linalg.norm(axis))
    if length < 1e-12:
        return [c for c, _ in entries]
    axis = axis / length

    # any pair perpendicular to the edge; (ref, ortho, axis) is right-handed, so
    # the angle increases counter-clockwise about it
    seed = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    ref = np.cross(axis, seed)
    ref = ref / np.linalg.norm(ref)
    ortho = np.cross(axis, ref)

    def angle(entry) -> float:
        _, into = entry
        if float(np.linalg.norm(into)) < 1e-12:
            return 0.0
        return float(np.arctan2(into @ ortho, into @ ref))

    return [c for c, _ in sorted(entries, key=angle)]
