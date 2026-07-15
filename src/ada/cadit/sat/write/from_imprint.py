"""Emit ACIS SAT entities from an imprinted planar complex.

The per-plate builder (:mod:`ada.cadit.sat.write.write_plate`) gives each plate
its own vertices and edges, so plates that touch stay topologically strangers.
Genie's own SAT is *imprinted*: a deck crossed by a bulkhead is split into
several faces, neighbours share one edge, and the coedges lying on that edge are
linked in a ring. Genie rebuilds all of that on import when it isn't supplied —
which is what makes importing a polygon-only XML take so long.

This module turns the backend-neutral :class:`~ada.cad.PlanarImprint` (see
``CadBackend.imprint_planar_faces``) into that shared topology directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.cadit.sat.utils import make_ints_if_possible
from ada.cadit.sat.write import sat_entities as se

if TYPE_CHECKING:
    from ada.cad import PlanarImprint
    from ada.cadit.sat.write.writer import SatWriter


def imprint_to_sat_entities(
    imprint: PlanarImprint, sw: SatWriter
) -> tuple[list[se.SATEntity], list[se.Face], list[se.Edge]]:
    """Build every face of ``imprint`` under the writer's shared body/lump/shell.

    Returns ``(entities, faces, edges)``, index-aligned with ``imprint.faces`` and
    ``imprint.edges`` — the caller needs that alignment to resolve
    ``PlanarImprint.sources`` / ``curve_sources`` into FACE and EDGE names.
    ``sw.shell`` must already exist; the caller chains the faces and names them
    (see :func:`part_to_sat_writer`).
    """
    id_gen = sw.id_generator
    entities: list[se.SATEntity] = []

    # --- shared points + vertices -------------------------------------------
    # One SatPoint/Vertex per imprint vertex: this is the sharing. `edge` is
    # backfilled below, from a coedge-carrying edge.
    points = [se.SatPoint(id_gen.next_id(), ada.Point(*v)) for v in imprint.vertices]
    vertices = [se.Vertex(id_gen.next_id(), None, p) for p in points]

    # --- shared edges + curves ----------------------------------------------
    edges: list[se.Edge] = []
    curves: list[se.StraightCurve] = []
    for e in imprint.edges:
        p0, p1 = points[e.start].point, points[e.end].point
        direction = ada.Direction(*(np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)))
        curve = se.StraightCurve(id_gen.next_id(), p0, direction)
        edges.append(
            se.Edge(
                id_gen.next_id(),
                vertices[e.start],
                vertices[e.end],
                None,  # a coedge on this edge, filled in by the face pass
                curve,
                start_pt=p0,
                end_pt=p1,
            )
        )
        curves.append(curve)

    # --- faces, loops, coedges ----------------------------------------------
    # Every coedge lying on a given edge, with the face normal and traversal
    # direction its radial position about that edge is derived from, so the
    # partner rings can be ordered once all the faces are built.
    coedges_on_edge: dict[int, list[tuple[se.CoEdge, np.ndarray, bool]]] = {}
    faces: list[se.Face] = []

    # What each edge is bounded by — the loops and wires owning its coedges,
    # by entity id (not id(), whose ordering would vary between runs). Two edges
    # at a vertex are in the same manifold region when something owns both; see
    # the VertEdgeAttribute pass at the end.
    owners_of_edge: dict[int, set[int]] = {}

    # Which input plate each face came from. All the faces a plate was split
    # into lie on that plate's single plane, and Genie has them share one
    # plane-surface and carry one cached-plane attribute between them — exactly
    # one of each per plate, however many faces it split into.
    face_source: dict[int, int] = {}
    for src_idx, face_idxs in enumerate(imprint.sources):
        for fi in face_idxs:
            face_source.setdefault(fi, src_idx)
    surface_of_source: dict[int, se.PlaneSurface] = {}
    cached_sources: set[int] = set()

    for face_idx, f in enumerate(imprint.faces):
        normal = ada.Direction(*f.normal)
        xdir = ada.Direction(*f.ref_direction)
        src = face_source.get(face_idx)

        surface = surface_of_source.get(src) if src is not None else None
        if surface is None:
            surface = se.PlaneSurface(id_gen.next_id(), ada.Point(*f.origin), normal, xdir)
            entities.append(surface)
            if src is not None:
                surface_of_source[src] = surface

        # One cached-plane attribute per plate, on the first face of it we emit.
        cached_plane = None
        if src is None or src not in cached_sources:
            cached_plane = se.CachedPlaneAttribute(id_gen.next_id(), None, None, ada.Point(*f.origin), normal)
            cached_sources.add(src)

        # face, name attrib and cached-plane attrib reference each other, so
        # build them with the links empty and wire them up once all exist.
        # Named later by part_to_sat_writer, once the plate -> face assignment is known.
        name_attrib = se.StringAttribName(id_gen.next_id(), "", None, cached_plane)
        face = se.Face(id_gen.next_id(), None, sw.shell, name_attrib, surface)
        name_attrib.entity = face
        entities += [face, name_attrib]
        if cached_plane is not None:
            cached_plane.entity = face
            cached_plane.name = name_attrib
            entities.append(cached_plane)
        faces.append(face)

        face_loops: list[se.Loop] = []
        for loop_edges in f.loops:
            if not loop_edges:
                continue
            loop = se.Loop(id_gen.next_id(), None, _loop_bbox(imprint, loop_edges), face=face)
            entities.append(loop)
            face_loops.append(loop)

            ring: list[se.CoEdge] = []
            for edge_idx, forward in loop_edges:
                edge = edges[edge_idx]
                coedge = se.CoEdge(
                    id_gen.next_id(),
                    None,
                    None,
                    edge,
                    loop,
                    "forward" if forward else "reversed",
                )
                if edge.coedge is None:
                    edge.coedge = coedge
                coedges_on_edge.setdefault(edge_idx, []).append((coedge, np.asarray(f.normal, dtype=float), forward))
                owners_of_edge.setdefault(edge_idx, set()).add(loop.id)
                ring.append(coedge)
            entities += ring

            loop.coedge = ring[0]
            n = len(ring)
            for i, coedge in enumerate(ring):
                coedge.next_coedge = ring[(i + 1) % n]
                coedge.prev_coedge = ring[i - 1]

        if not face_loops:
            raise ValueError(f"imprint face {face_idx} has no loops")
        face.loop = face_loops[0]
        # holes hang off the outer loop's next_loop chain
        for cur, nxt in zip(face_loops, face_loops[1:]):
            cur.next_loop = nxt

    # --- wire bodies for the edges that bound no face -----------------------
    # A beam whose axis lies on no plate leaves an edge with no face around it.
    # ACIS carries those as wire bodies hung off the shell's wire pointer; left
    # out, the beam has nothing to reference and Genie rebuilds its geometry.
    #
    # One wire per CONNECTED GROUP of such edges, not one per edge: every edge
    # meeting at a vertex has to sit in the same wire. Split across wires, ACIS
    # sees the vertex as joining several groups and the model fails verification
    # with "vertex has edge in multiple groups" (a frame of beams meeting at a
    # corner puts up to five wires on that corner). Genie groups them the same
    # way — verified against its own export, one wire per connected group.
    wires: list[se.Wire] = []
    for group in _connected_edge_groups(imprint, imprint.free_edges):
        vertex_idx = {v for edge_idx in group for v in (imprint.edges[edge_idx].start, imprint.edges[edge_idx].end)}
        wire = se.Wire(id_gen.next_id(), None, sw.shell, _bbox_of(imprint, vertex_idx))

        coedge_of_edge: dict[int, se.CoEdge] = {}
        for edge_idx in group:
            edge = edges[edge_idx]
            coedge = se.CoEdge(id_gen.next_id(), None, None, edge, wire, "forward")
            coedge_of_edge[edge_idx] = coedge
            owners_of_edge.setdefault(edge_idx, set()).add(wire.id)
            if edge.coedge is None:
                edge.coedge = coedge

        # A wire coedge's `next`/`prev` are not a linear list: `next` is the
        # following coedge around this coedge's END vertex and `prev` the one
        # around its START vertex, so a branched wire stays walkable. Each
        # vertex's coedges therefore form one closed ring — with a single edge
        # at a loose end, that ring is the coedge itself.
        at_vertex: dict[int, list[tuple[se.CoEdge, bool]]] = {}
        for edge_idx in group:
            e = imprint.edges[edge_idx]
            at_vertex.setdefault(e.start, []).append((coedge_of_edge[edge_idx], False))
            at_vertex.setdefault(e.end, []).append((coedge_of_edge[edge_idx], True))

        for fan in at_vertex.values():
            for i, (coedge, is_end) in enumerate(fan):
                around = fan[(i + 1) % len(fan)][0]
                if is_end:
                    coedge.next_coedge = around
                else:
                    coedge.prev_coedge = around

        wire.coedge = coedge_of_edge[group[0]]
        entities += [wire, *coedge_of_edge.values()]
        wires.append(wire)

    if wires:
        sw.shell.wire = wires[0]
        for cur, nxt in zip(wires, wires[1:]):
            cur.next_wire = nxt

    # --- partner rings ------------------------------------------------------
    # ACIS reaches every coedge on an edge by following `next coedge on edge`
    # (SAT v4.0 ch.5) as a circular list. An edge used once keeps $-1, as Genie
    # writes it; an edge shared by N faces links its N coedges into a cycle. A
    # T-junction genuinely produces N > 2 (a deck split by a bulkhead gives the
    # imprint line three coedges).
    #
    # The cycle has to run in radial order about the edge, or the model fails
    # verification with "coedges out of order about edge" — ACIS reads the ring
    # as the faces' angular order, which is what tells it which faces are
    # neighbours around a non-manifold edge. Creation order says nothing about
    # angle; it only ever looks right for N=2, where any cycle is sorted.
    for edge_idx, coedge_list in coedges_on_edge.items():
        if len(coedge_list) < 2:
            continue
        ordered = _ordered_about_edge(imprint, edge_idx, coedge_list)
        for i, coedge in enumerate(ordered):
            coedge.partner = ordered[(i + 1) % len(ordered)]

    # --- prune topology that bounds nothing ---------------------------------
    # General Fuse can leave an edge on a face that no wire traverses (an
    # internal or degenerate edge, which BRepTools_WireExplorer skips). Such an
    # edge has no coedge, so it must not be emitted: ACIS requires an edge to
    # name one. Its vertices/points go too unless a live edge also uses them.
    live_edges = [e for e in edges if e.coedge is not None]
    edge_of_vertex: dict[int, se.Edge] = {}
    for edge in live_edges:
        edge_of_vertex.setdefault(id(edge.vertex_start), edge)
        edge_of_vertex.setdefault(id(edge.vertex_end), edge)

    live_vertices = [v for v in vertices if id(v) in edge_of_vertex]
    for vertex in live_vertices:
        vertex.edge = edge_of_vertex[id(vertex)]
    live_points = [v.point for v in live_vertices]
    live_curves = [e.straight_curve for e in live_edges]

    attribs = _declare_nonmanifold_vertices(imprint, sw, vertices, edges, owners_of_edge, live_edges)

    # points/vertices/edges lead so their ids stay below the faces referencing
    # them; SatWriter.renumber reorders anyway, this only keeps output tidy.
    # `edges` stays index-aligned with imprint.edges (pruned entries included) so
    # curve_sources still resolves; only live ones are emitted.
    return live_points + live_vertices + live_edges + live_curves + entities + attribs, faces, edges


def _declare_nonmanifold_vertices(
    imprint: PlanarImprint,
    sw: SatWriter,
    vertices: list[se.Vertex],
    edges: list[se.Edge],
    owners_of_edge: dict[int, set[int]],
    live_edges: list[se.Edge],
) -> list[se.SATEntity]:
    """Give every vertex whose edges fall into several regions a VertEdgeAttribute.

    A vertex normally just names one of its edges, and everything else there is
    reachable from it. That breaks where a beam's axis leaves the plate it lies
    on: the vertex on the plate boundary carries the plate's face edges AND the
    wire edge for the free run, and no loop or wire owns both, so from either
    one the other is unreachable — "vertex has edge in multiple groups".

    Two edges are in the same region when something (a loop, or a wire) owns
    both. Where that leaves more than one region, the vertex names no edge at
    all and the attribute names one per region instead. Checked against Genie's
    own export: this picks out exactly the vertices it attributes, no more.
    """
    live = {id(e) for e in live_edges}
    edges_at_vertex: dict[int, list[int]] = {}
    for edge_idx, edge in enumerate(edges):
        if id(edge) not in live:
            continue
        e = imprint.edges[edge_idx]
        for v in (e.start, e.end):
            edges_at_vertex.setdefault(v, []).append(edge_idx)

    attribs: list[se.SATEntity] = []
    for vertex_idx, edge_idxs in edges_at_vertex.items():
        regions = _regions_at(edge_idxs, owners_of_edge)
        if len(regions) < 2:
            continue
        vertex = vertices[vertex_idx]
        # one edge per region, lowest index first — the spec asks only for "an
        # edge in each separable manifold region", so any member would do
        reps = [edges[min(region)] for region in regions]
        attrib = se.VertEdgeAttribute(sw.id_generator.next_id(), vertex, reps)
        vertex.attrib = attrib
        vertex.edge = None
        attribs.append(attrib)
    return attribs


def _regions_at(edge_idxs: list[int], owners_of_edge: dict[int, set[int]]) -> list[list[int]]:
    """Group a vertex's edges by what bounds them, ordered by lowest index."""
    parent = {e: e for e in edge_idxs}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first_for_owner: dict[int, int] = {}
    for edge_idx in edge_idxs:
        for owner in sorted(owners_of_edge.get(edge_idx, ())):
            if owner in first_for_owner:
                ra, rb = find(first_for_owner[owner]), find(edge_idx)
                if ra != rb:
                    parent[ra] = rb
            else:
                first_for_owner[owner] = edge_idx

    grouped: dict[int, list[int]] = {}
    for edge_idx in edge_idxs:
        grouped.setdefault(find(edge_idx), []).append(edge_idx)
    return sorted(grouped.values(), key=min)


def _loop_bbox(imprint: PlanarImprint, loop_edges) -> list[float]:
    """Axis-aligned box of the loop's vertices, as ACIS's `T <lo> <hi>` wants."""
    idx = set()
    for edge_idx, _ in loop_edges:
        e = imprint.edges[edge_idx]
        idx.add(e.start)
        idx.add(e.end)
    return _bbox_of(imprint, idx)


def _ordered_about_edge(imprint: PlanarImprint, edge_idx: int, entries) -> list[se.CoEdge]:
    """The coedges on an edge, counter-clockwise about it — the order ACIS wants.

    A coedge's face lies to the left of the direction the coedge traverses the
    edge, so ``normal x tangent`` points from the edge into that face. It is
    perpendicular to the edge, and its angle about the edge axis is where that
    face sits radially. Sorting on it reproduces the order Genie writes (checked
    against its own export: every ring there is counter-clockwise about the
    edge's own direction).
    """
    e = imprint.edges[edge_idx]
    p0 = np.asarray(imprint.vertices[e.start], dtype=float)
    p1 = np.asarray(imprint.vertices[e.end], dtype=float)
    axis = p1 - p0
    length = float(np.linalg.norm(axis))
    if length < 1e-12:  # nothing to sort about
        return [coedge for coedge, _, _ in entries]
    axis = axis / length

    # any pair of axes perpendicular to the edge; (ref, ortho, axis) is
    # right-handed, so the angle below increases counter-clockwise about it
    seed = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    ref = np.cross(axis, seed)
    ref = ref / np.linalg.norm(ref)
    ortho = np.cross(axis, ref)

    def angle(entry) -> float:
        _, normal, forward = entry
        into_face = np.cross(normal, axis if forward else -axis)
        norm = float(np.linalg.norm(into_face))
        if norm < 1e-12:  # face degenerate along the edge; leave it put
            return 0.0
        into_face = into_face / norm
        return float(np.arctan2(into_face @ ortho, into_face @ ref))

    # stable, so coincident faces keep their creation order rather than swapping
    # between runs
    return [coedge for coedge, _, _ in sorted(entries, key=angle)]


def _connected_edge_groups(imprint: PlanarImprint, edge_idxs) -> list[list[int]]:
    """Split ``edge_idxs`` into groups that touch, sharing a vertex.

    Order is kept stable (groups by first appearance, edges by index) so the
    same model always writes the same file.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    edge_idxs = list(edge_idxs)
    for edge_idx in edge_idxs:
        parent[edge_idx] = edge_idx

    first_at_vertex: dict[int, int] = {}
    for edge_idx in edge_idxs:
        e = imprint.edges[edge_idx]
        for v in (e.start, e.end):
            if v in first_at_vertex:
                union(first_at_vertex[v], edge_idx)
            else:
                first_at_vertex[v] = edge_idx

    groups: dict[int, list[int]] = {}
    for edge_idx in edge_idxs:
        groups.setdefault(find(edge_idx), []).append(edge_idx)
    return list(groups.values())


def _bbox_of(imprint: PlanarImprint, vertex_idx) -> list[float]:
    pts = np.asarray([imprint.vertices[i] for i in vertex_idx], dtype=float)
    return make_ints_if_possible([*np.min(pts, axis=0), *np.max(pts, axis=0)])
