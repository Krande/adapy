from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.cadit.sat.utils import make_ints_if_possible
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
    raise UnsupportedCurvedFace(f"no ACIS curve record for {type(curve).__name__}")


def curved_plate_to_sat_entities(pl: PlateCurved, face_name: str, sw: SatWriter) -> list[se.SATEntity]:
    """Convert one :class:`~ada.api.plates.PlateCurved` into its ACIS face.

    One independent face per plate: no shared topology, no partner ring. The
    imprint path cannot take these — it splits *planar* outlines — so a curved
    plate is emitted unfused whichever mode the writer runs in, and its coedges
    leave the partner slot null the way Genie writes a face that stands alone.

    Senses are reconstructed from the edge's parameter range rather than
    guessed. Every edge in a Genie export is ``forward`` (18924 of 18924 in a
    hull model) and its parameters ascend, so the loop's direction lives on the
    coedge: the reader hands back ``t_start > t_end`` exactly when the loop runs
    the edge against its curve, which is a ``reversed`` coedge and an edge whose
    two vertices swap. Deriving it from the range keeps the edge record's range
    ascending, as ACIS reads it.
    """
    geom = pl.geom.geometry
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

    # One vertex per distinct position on this face. Rounded because the two
    # edges meeting at a corner carry the same point through separate reads and
    # need not agree in the last bit; 1e-9 m is far below any modelling
    # tolerance and far above that noise.
    vertex_map: dict[tuple, se.Vertex] = {}

    def vertex_at(p) -> se.Vertex:
        key = tuple(round(float(c), 9) for c in p)
        v = vertex_map.get(key)
        if v is None:
            sat_point = se.SatPoint(id_gen.next_id(), ada.Point(*p))
            v = se.Vertex(id_gen.next_id(), None, sat_point)
            vertex_map[key] = v
            entities.extend([sat_point, v])
        return v

    coedges: list[se.CoEdge] = []
    for oriented_edge in edge_list:
        edge_curve = oriented_edge.edge_element
        curve_geom = getattr(edge_curve, "edge_geometry", None)
        if curve_geom is None:
            raise UnsupportedCurvedFace("edge carries no curve geometry")

        p_start, p_end = oriented_edge.start, oriented_edge.end

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

        curve = _curve_entity(id_gen, curve_geom, t_lo, t_hi)
        entities.append(curve)

        v_start, v_end = vertex_at(p_lo), vertex_at(p_hi)
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
        entities.append(edge)
        for v in (v_start, v_end):
            if v.edge is None:
                v.edge = edge

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
        edge.coedge = coedge
        entities.append(coedge)
        coedges.append(coedge)

    for i, coedge in enumerate(coedges):
        coedge.next_coedge = coedges[(i + 1) % len(coedges)]
        coedge.prev_coedge = coedges[i - 1]
    loop.coedge = coedges[0]

    pts = np.asarray([v.point.point for v in vertex_map.values()], dtype=float)
    loop.bbox = make_ints_if_possible([*np.min(pts, axis=0), *np.max(pts, axis=0)])

    return sorted(entities, key=lambda x: x.id)
