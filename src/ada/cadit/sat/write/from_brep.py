"""Serialise a :class:`~ada.geom.brep.BRepStore` back to an ACIS SAT body.

The store already holds explicit sharing (one :class:`BEdge` per shared edge, one
:class:`BVertex` per corner), so this is a direct 1:1 mapping to the SAT write
records — no welding, no imprinting. Curve/surface records and the coedge
partner-ring ordering are reused from :mod:`ada.cadit.sat.write.write_curved_plate`.

This is the leg that proves the neutral store is a *complete* description of the
body: a store built by the import producer, serialised here and re-parsed, must
diff clean against itself (see the store-roundtrip test).

Pcurves (the per-coedge UV curves a spline face needs for ACIS to accept it) are
carried when the store has them; a store whose coedges have no pcurve serialises
without, which is enough for the topology round-trip and for planar faces.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np

from ada.cadit.sat.utils import make_ints_if_possible
from ada.cadit.sat.write import sat_entities as se
from ada.cadit.sat.write.write_curved_plate import (
    _curve_entity,
    _into_face,
    _ordered_about_edge,
    _surface_entity,
)

if TYPE_CHECKING:
    from ada.geom.brep import BRepStore


def _bbox(points: list) -> list[float]:
    if not points:
        return [0.0] * 6
    a = np.asarray(points, dtype=float)
    return make_ints_if_possible([*a.min(axis=0), *a.max(axis=0)])


def brep_store_to_sat_writer(store: BRepStore, part=None):
    """Populate and return a :class:`SatWriter` from ``store``."""
    from ada.cadit.sat.write.writer import SatWriter

    sw = SatWriter(part)
    idg = sw.id_generator

    all_pts = [list(v.point)[:3] for v in store.vertices.values()]
    bbox = _bbox(all_pts)

    body = se.Body(idg.next_id(), None, list(bbox))
    lump = se.Lump(idg.next_id(), None, body, list(bbox))
    shell = se.Shell(idg.next_id(), None, lump, list(bbox))
    body.lump = lump
    lump.shell = shell
    for e in (body, lump, shell):
        sw.add_entity(e)

    # vertices + points
    vmap: dict[int, se.Vertex] = {}
    for bv in store.vertices.values():
        sp = se.SatPoint(idg.next_id(), bv.point)
        sv = se.Vertex(idg.next_id(), None, sp)
        vmap[bv.id] = sv
        sw.add_entity(sp)
        sw.add_entity(sv)
        # preserve the source vertex name (Genie's beam Coord evaluation reads it)
        if bv.name:
            vattrib = se.StringAttribName(idg.next_id(), bv.name, sv)
            sv.attrib = vattrib
            sw.add_entity(vattrib)

    # edges + curves (shared — one record per BEdge)
    emap: dict[int, se.Edge] = {}
    for be in store.edges.values():
        curve_rec = _curve_entity(idg, be.curve, be.t_start, be.t_end)
        sw.add_entity(curve_rec)
        se_edge = se.Edge(
            idg.next_id(),
            vmap[be.start.id],
            vmap[be.end.id],
            None,
            curve_rec,
            start_pt=be.start.point,
            end_pt=be.end.point,
            t_start=be.t_start,
            t_end=be.t_end,
        )
        emap[be.id] = se_edge
        sw.add_entity(se_edge)
        # preserve the source edge name (a Genie beam's <sat_reference> resolves to it)
        if be.name:
            attrib = se.StringAttribName(idg.next_id(), be.name, se_edge)
            se_edge.attrib_name = attrib
            sw.add_entity(attrib)
        for sv in (vmap[be.start.id], vmap[be.end.id]):
            if sv.edge is None:
                sv.edge = se_edge

    # per-edge coedge collection for partner rings: se.Edge id -> [(coedge, into)]
    on_edge: dict[int, list] = defaultdict(list)

    def build_coedges(bcoedges, owner, surface_rec=None) -> list[se.CoEdge]:
        loop_pts = [list(bc.edge.start.point)[:3] for bc in bcoedges]
        loop_pts += [list(bc.edge.end.point)[:3] for bc in bcoedges]
        out = []
        for bc in bcoedges:
            se_edge = emap[bc.edge.id]
            # a spline face's coedge needs its UV pcurve or ACIS rejects the face
            pcurve = None
            if bc.pcurve is not None and isinstance(surface_rec, se.SplineSurface):
                pcurve = se.PCurve(
                    idg.next_id(),
                    bc.pcurve,
                    surface_rec,
                    sense="forward" if getattr(bc.pcurve, "same_sense", True) else "reversed",
                )
                sw.add_entity(pcurve)
            ce = se.CoEdge(
                idg.next_id(), None, None, se_edge, owner, "forward" if bc.sense else "reversed", pcurve=pcurve
            )
            if se_edge.coedge is None:
                se_edge.coedge = ce
            sw.add_entity(ce)
            out.append(ce)
            on_edge[id(se_edge)].append((ce, _into_face(se_edge.start_pt, se_edge.end_pt, loop_pts)))
        # loop/wire ring (next/prev)
        for i, ce in enumerate(out):
            ce.next_coedge = out[(i + 1) % len(out)]
            ce.prev_coedge = out[i - 1]
        return out

    # faces + loops + coedges
    face_recs: list[se.Face] = []
    for bf in store.faces.values():
        surf_rec, face_sense = _surface_entity(idg, bf.surface, bf.sense)
        sw.add_entity(surf_rec)
        name = se.StringAttribName(idg.next_id(), bf.name or f"FACE{bf.id:08d}", None)
        se_face = se.Face(idg.next_id(), None, shell, name, surf_rec, sense=face_sense)
        name.entity = se_face
        sw.add_entity(name)
        sw.add_entity(se_face)

        se_loops: list[se.Loop] = []
        for bl in bf.loops:
            if not bl.coedges:
                continue
            se_loop = se.Loop(idg.next_id(), None, [], face=se_face)
            coedges = build_coedges(bl.coedges, se_loop, surface_rec=surf_rec)
            se_loop.coedge = coedges[0]
            se_loop.bbox = _bbox([list(ce.edge.start_pt)[:3] for ce in coedges])
            sw.add_entity(se_loop)
            se_loops.append(se_loop)
        for i in range(len(se_loops) - 1):
            se_loops[i].next_loop = se_loops[i + 1]
        se_face.loop = se_loops[0] if se_loops else None
        face_recs.append(se_face)

    for i in range(len(face_recs) - 1):
        face_recs[i].next_face = face_recs[i + 1]
    shell.face = face_recs[0] if face_recs else None

    # wires (edges bounding no face)
    prev_wire = None
    first_wire = None
    for bw in store.wires.values():
        if not bw.coedges:
            continue
        se_wire = se.Wire(idg.next_id(), None, shell, list(bbox))
        coedges = build_coedges(bw.coedges, se_wire)
        se_wire.coedge = coedges[0]
        sw.add_entity(se_wire)
        if first_wire is None:
            first_wire = se_wire
        if prev_wire is not None:
            prev_wire.next_wire = se_wire
        prev_wire = se_wire
    shell.wire = first_wire

    # partner rings: link the coedges sharing each edge, ordered about it
    for entries in on_edge.values():
        if len(entries) < 2:
            continue
        ordered = [entries[0][0], entries[1][0]] if len(entries) == 2 else _ordered_about_edge(entries)
        for cur, nxt in zip(ordered, ordered[1:] + ordered[:1]):
            cur.partner = nxt

    sw.renumber()
    return sw


def brep_store_to_sat_text(store: BRepStore) -> str:
    return brep_store_to_sat_writer(store).to_str()
