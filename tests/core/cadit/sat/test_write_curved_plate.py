"""The curved-plate SAT builder: one face per PlateCurved, senses and all.

The senses are the interesting part. A Genie export writes every edge
``forward`` with an ascending parameter range and lets the coedge carry the
loop's direction, so the writer has to put the direction back where it came
from — the reader hands it back as a ``t_start > t_end`` range instead.
"""

import numpy as np
import pytest

from ada.api.plates import PlateCurved
from ada.cadit.sat.write import sat_entities as se
from ada.cadit.sat.write.write_curved_plate import (
    TopologyWeld,
    UnsupportedCurvedFace,
    advanced_face_to_sat_entities,
    curved_plate_to_sat_entities,
    flat_plate_to_advanced_face,
    link_partner_rings,
)
from ada.cadit.sat.write.writer import SatWriter
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def _plane_surface():
    return geo_su.Plane(
        position=Axis2Placement3D(
            location=Point(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, 1.0),
            ref_direction=(1.0, 0.0, 0.0),
        )
    )


def _spline_surface():
    pts = [[(float(iu), float(iv), 0.0) for iv in range(4)] for iu in range(3)]
    return geo_su.RationalBSplineSurfaceWithKnots(
        u_degree=2,
        v_degree=3,
        control_points_list=pts,
        surface_form=geo_su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=[3, 3],
        v_multiplicities=[4, 4],
        u_knots=[0.0, 1.0],
        v_knots=[0.0, 1.0],
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
        weights_data=[[1.0] * 4 for _ in range(3)],
    )


def _pcurve(same_sense=True):
    return geo_cu.Pcurve2dBSpline(
        degree=1,
        control_points_2d=[[0.0, 0.0], [1.0, 0.0]],
        knots=[0.0, 1.0],
        knot_multiplicities=[2, 2],
        same_sense=same_sense,
    )


def _line_edge(p1, p2, t_start=None, t_end=None, pcurve=None):
    """An oriented edge on a straight curve running p1 -> p2."""
    d = np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)
    length = float(np.linalg.norm(d))
    line = geo_cu.Line(Point(*p1), tuple(d / length))
    ec = geo_cu.EdgeCurve(start=Point(*p1), end=Point(*p2), edge_geometry=line, same_sense=True)
    if t_start is None:
        t_start, t_end = 0.0, length
    return geo_cu.OrientedEdge(
        start=Point(*p1),
        end=Point(*p2),
        edge_element=ec,
        orientation=True,
        pcurve=pcurve,
        t_start=t_start,
        t_end=t_end,
    )


def _square_face(surface=None, pcurve=None, same_sense=True):
    corners = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    edges = [_line_edge(corners[i], corners[(i + 1) % 4], pcurve=pcurve) for i in range(4)]
    return geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)],
        face_surface=surface if surface is not None else _plane_surface(),
        same_sense=same_sense,
    )


def _plate(face=None, name="pl1") -> PlateCurved:
    from ada.visit.colors import Color

    geom = Geometry(1, face if face is not None else _square_face(), Color(0.5, 0.5, 0.5))
    return PlateCurved(name, geom, t=0.01)


def _writer(pl) -> SatWriter:
    sw = SatWriter(None)
    sw.init_body([], [pl])
    return sw


def _build(pl, face_name="FACE00000001"):
    """Build one face, with a weld of its own — the single-plate case."""
    sw = _writer(pl)
    weld = TopologyWeld(sw.id_generator)
    entities = curved_plate_to_sat_entities(pl, face_name, sw, weld)
    return entities + weld.entities


def _by_type(entities, cls):
    return [e for e in entities if type(e) is cls]


class TestTopology:
    def test_one_face_one_loop_and_a_closed_coedge_ring(self):
        pl = _plate()
        ents = _build(pl)

        assert len(_by_type(ents, se.Face)) == 1
        assert len(_by_type(ents, se.Loop)) == 1
        coedges = _by_type(ents, se.CoEdge)
        assert len(coedges) == 4

        # walk the ring: it must come back to the start having seen all four
        seen, cur = [], coedges[0]
        for _ in range(len(coedges)):
            seen.append(cur)
            assert cur.next_coedge.prev_coedge is cur
            cur = cur.next_coedge
        assert cur is coedges[0]
        assert len(set(id(c) for c in seen)) == 4

    def test_a_shared_corner_is_one_vertex(self):
        """Four edges, four corners — not eight."""
        pl = _plate()
        ents = _build(pl)
        assert len(_by_type(ents, se.Vertex)) == 4
        assert len(_by_type(ents, se.SatPoint)) == 4

    def test_the_face_is_named_and_owned_by_the_shell(self):
        pl = _plate()
        sw = _writer(pl)
        ents = curved_plate_to_sat_entities(pl, "FACE00000042", sw, TopologyWeld(sw.id_generator))
        face = _by_type(ents, se.Face)[0]
        assert face.name.name == "FACE00000042"
        assert face.shell is sw.shell
        assert face.loop.face is face

    def test_every_vertex_names_an_edge(self):
        pl = _plate()
        ents = _build(pl)
        for v in _by_type(ents, se.Vertex):
            assert v.edge is not None


class TestSenses:
    """The loop's direction lives on the coedge; the edge always runs forward."""

    @staticmethod
    def _face_with_backwards_edge():
        # a loop whose second edge the reader handed back reversed: it runs
        # (1,0,0) -> (1,1,0) but its curve is parameterised the other way, so
        # t_start > t_end
        corners = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
        edges = [_line_edge(corners[i], corners[(i + 1) % 4]) for i in range(4)]
        edges[1] = _line_edge((1.0, 0.0, 0.0), (1.0, 1.0, 0.0), t_start=1.0, t_end=0.0)
        return geo_su.AdvancedFace(
            bounds=[geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)],
            face_surface=_plane_surface(),
        )

    def test_a_descending_range_becomes_a_reversed_coedge(self):
        pl = _plate(self._face_with_backwards_edge())
        ents = _build(pl)
        senses = [c.orientation for c in _by_type(ents, se.CoEdge)]
        assert senses.count("reversed") == 1
        assert senses.count("forward") == 3

    def test_the_edge_range_still_ascends(self):
        """ACIS reads an edge forward along its curve, so t must ascend."""
        pl = _plate(self._face_with_backwards_edge())
        ents = _build(pl)
        for edge in _by_type(ents, se.Edge):
            assert edge.t_start < edge.t_end
            assert " forward " in edge.to_string()

    def test_a_reversed_edge_swaps_its_vertices(self):
        pl = _plate(self._face_with_backwards_edge())
        ents = _build(pl)
        reversed_coedge = next(c for c in _by_type(ents, se.CoEdge) if c.orientation == "reversed")
        edge = reversed_coedge.edge
        # the loop runs (1,0,0) -> (1,1,0); the edge is written the other way
        assert tuple(edge.vertex_start.point.point) == (1.0, 1.0, 0.0)
        assert tuple(edge.vertex_end.point.point) == (1.0, 0.0, 0.0)


class TestSurfacesAndPCurves:
    def test_a_plane_surfaced_face_carries_no_pcurve(self):
        """A flat face with curved edges: Genie gives its coedges no pcurve."""
        pl = _plate(_square_face(surface=_plane_surface(), pcurve=_pcurve()))
        ents = _build(pl)
        assert len(_by_type(ents, se.PlaneSurface)) == 1
        assert _by_type(ents, se.PCurve) == []
        assert all(c.pcurve is None for c in _by_type(ents, se.CoEdge))

    def test_a_spline_face_carries_one_pcurve_per_coedge(self):
        pl = _plate(_square_face(surface=_spline_surface(), pcurve=_pcurve()))
        ents = _build(pl)
        assert len(_by_type(ents, se.SplineSurface)) == 1
        assert len(_by_type(ents, se.PCurve)) == 4
        assert all(c.pcurve is not None for c in _by_type(ents, se.CoEdge))

    def test_the_pcurve_names_the_face_surface(self):
        pl = _plate(_square_face(surface=_spline_surface(), pcurve=_pcurve()))
        ents = _build(pl)
        surface = _by_type(ents, se.SplineSurface)[0]
        for pc in _by_type(ents, se.PCurve):
            assert pc.surface is surface


class TestNormals:
    """ACIS splits the face normal across two records; Genie picks by surface.

    Every one of these was written `forward` unconditionally at first, which
    flipped 1248 spline surfaces and 112 plane faces of a hull export and had
    Genie draw the model inside-out.
    """

    def test_a_spline_face_puts_the_flip_on_the_surface(self):
        """A spline-surface has a sense of its own, so the face stays forward."""
        pl = _plate(_square_face(surface=_spline_surface(), pcurve=_pcurve(), same_sense=False))
        ents = _build(pl)
        assert _by_type(ents, se.SplineSurface)[0].sense == "reversed"
        assert _by_type(ents, se.Face)[0].sense == "forward"

    def test_a_plane_face_puts_the_flip_on_the_face(self):
        """A plane-surface has no sense to carry it."""
        pl = _plate(_square_face(surface=_plane_surface(), same_sense=False))
        ents = _build(pl)
        assert _by_type(ents, se.Face)[0].sense == "reversed"

    def test_an_agreeing_face_is_forward_either_way(self):
        for surface in (_spline_surface(), _plane_surface()):
            pl = _plate(_square_face(surface=surface, pcurve=_pcurve(), same_sense=True))
            ents = _build(pl)
            assert _by_type(ents, se.Face)[0].sense == "forward"
            splines = _by_type(ents, se.SplineSurface)
            if splines:
                assert splines[0].sense == "forward"

    def test_the_face_sense_reaches_the_record(self):
        pl = _plate(_square_face(surface=_plane_surface(), same_sense=False))
        ents = _build(pl)
        assert " reversed double out " in _by_type(ents, se.Face)[0].to_string()


class TestPCurveSense:
    """The pcurve's sense is authored data, not a default.

    It says whether the 2D curve runs along its edge's 3D curve, and nothing in
    the knots or the coedge implies it — a Genie export splits 13722/5184 with
    no correlation to either. Writing `forward` on all of them is what ACIS
    rejects as "pcurve's range doesn't include coedge's range".
    """

    def test_a_reversed_pcurve_is_written_reversed(self):
        pl = _plate(_square_face(surface=_spline_surface(), pcurve=_pcurve(same_sense=False)))
        ents = _build(pl)
        pcurves = _by_type(ents, se.PCurve)
        assert pcurves and all(pc.sense == "reversed" for pc in pcurves)
        assert all(" 0 reversed { exppc " in pc.to_string() for pc in pcurves)

    def test_a_forward_pcurve_is_written_forward(self):
        pl = _plate(_square_face(surface=_spline_surface(), pcurve=_pcurve(same_sense=True)))
        ents = _build(pl)
        pcurves = _by_type(ents, se.PCurve)
        assert pcurves and all(pc.sense == "forward" for pc in pcurves)

    def test_reversing_a_pcurve_flips_its_sense(self):
        """Reversal inverts the direction, which is what the sense records."""
        from ada.cadit.sat.read.curves import _reverse_pcurve_2d

        assert _reverse_pcurve_2d(_pcurve(same_sense=True)).same_sense is False
        assert _reverse_pcurve_2d(_pcurve(same_sense=False)).same_sense is True


class TestWeld:
    """Neighbouring faces share their vertices and edges.

    A face built alone mints its own vertex at each corner, so two faces meeting
    along an edge leave coincident copies in the same shell — ACIS calls that
    "duplicate vertex". Genie's export shares them: 6159 vertices for the 5470
    faces where one-face-at-a-time gives 23186.
    """

    @staticmethod
    def _two_squares():
        """Two unit squares meeting along the x=1 edge."""

        def square(x0, x1):
            c = [(x0, 0.0, 0.0), (x1, 0.0, 0.0), (x1, 1.0, 0.0), (x0, 1.0, 0.0)]
            edges = [_line_edge(c[i], c[(i + 1) % 4]) for i in range(4)]
            return geo_su.AdvancedFace(
                bounds=[geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)],
                face_surface=_plane_surface(),
            )

        return _plate(square(0.0, 1.0), name="a"), _plate(square(1.0, 2.0), name="b")

    def _build_both(self):
        pl_a, pl_b = self._two_squares()
        sw = _writer(pl_a)
        weld = TopologyWeld(sw.id_generator)
        ents = curved_plate_to_sat_entities(pl_a, "FACE00000001", sw, weld)
        ents += curved_plate_to_sat_entities(pl_b, "FACE00000002", sw, weld)
        link_partner_rings(weld)
        return ents + weld.entities, weld

    def test_the_shared_corners_are_one_vertex_each(self):
        ents, weld = self._build_both()
        # 6 distinct corners across the two squares, not 8
        assert weld.n_vertices == 6
        assert len(_by_type(ents, se.Vertex)) == 6
        assert len(_by_type(ents, se.SatPoint)) == 6

    def test_no_two_points_are_coincident(self):
        ents, _ = self._build_both()
        pts = [tuple(round(float(c), 7) for c in p.point) for p in _by_type(ents, se.SatPoint)]
        assert len(pts) == len(set(pts))

    def test_the_shared_edge_is_one_record(self):
        ents, weld = self._build_both()
        # 7 edges: 3 on each square plus the one they share, not 8
        assert weld.n_edges == 7
        assert len(_by_type(ents, se.Edge)) == 7

    def test_the_shared_edge_carries_two_coedges_that_partner_each_other(self):
        ents, weld = self._build_both()
        shared = [e for e in weld.coedges_on_edge.values() if len(e) == 2]
        assert len(shared) == 1
        (ca, _), (cb, _) = shared[0]
        assert ca.partner is cb
        assert cb.partner is ca
        assert ca.edge is cb.edge

    def test_an_edge_bounding_one_face_has_no_partner(self):
        ents, weld = self._build_both()
        for entries in weld.coedges_on_edge.values():
            if len(entries) == 1:
                assert entries[0][0].partner is None

    def test_every_coedge_still_belongs_to_its_own_loop(self):
        """Sharing an edge must not merge the two faces' loops."""
        ents, _ = self._build_both()
        loops = _by_type(ents, se.Loop)
        assert len(loops) == 2
        for loop in loops:
            n, cur = 0, loop.coedge
            while True:
                assert cur.loop is loop
                cur = cur.next_coedge
                n += 1
                if cur is loop.coedge or n > 8:
                    break
            assert n == 4

    def test_a_differing_curve_between_the_same_points_is_not_the_same_edge(self):
        """Two arcs can join one pair of vertices; position alone cannot tell."""
        weld = TopologyWeld(_writer(_plate()).id_generator)
        p1, p2 = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)
        line = geo_cu.Line(Point(*p1), (1.0, 0.0, 0.0))
        circle = geo_cu.Circle(_plane_surface().position, 1.0)
        assert weld.edge_key(p1, p2, line) != weld.edge_key(p1, p2, circle)

    def test_the_key_ignores_which_way_the_edge_is_given(self):
        weld = TopologyWeld(_writer(_plate()).id_generator)
        p1, p2 = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)
        line = geo_cu.Line(Point(*p1), (1.0, 0.0, 0.0))
        assert weld.edge_key(p1, p2, line) == weld.edge_key(p2, p1, line)


class TestFlatPlatesJoinTheWeld:
    """A flat plate meeting a curved one must share its corners with it.

    Built apart they leave a coincident copy of every shared corner, which ACIS
    rejects as "duplicate vertex" — 54 positions on a hull export, every one of
    them a flat plate meeting a curved face. Genie shares all four edges of such
    a face with its neighbours.
    """

    @staticmethod
    def _flat_plate(x0, x1, normal=(0.0, 0.0, 1.0)):
        """A unit square facing ``normal``.

        The normal is pinned because CurvePoly2d picks its own: these points
        come back facing -z, and two faces facing opposite ways run a shared
        edge the SAME way, which is correct and not what these tests are about.
        """
        import ada

        pts = [(x0, 0.0, 0.0), (x1, 0.0, 0.0), (x1, 1.0, 0.0), (x0, 1.0, 0.0)]
        pl = ada.Plate.from_3d_points(f"flat{x0}", pts, 0.01)
        if np.dot(np.asarray(pl.poly.normal, dtype=float), np.asarray(normal, dtype=float)) < 0:
            pl = ada.Plate.from_3d_points(f"flat{x0}", pts, 0.01, flip_normal=True)
        return pl

    def test_a_flat_plate_becomes_a_plane_face_with_a_loop(self):
        face = flat_plate_to_advanced_face(self._flat_plate(0.0, 1.0))
        assert isinstance(face, geo_su.AdvancedFace)
        assert isinstance(face.face_surface, geo_su.Plane)
        assert len(face.bounds) == 1
        assert len(face.bounds[0].bound.edge_list) == 4

    def test_the_surface_states_the_plates_own_normal(self):
        pl = self._flat_plate(0.0, 1.0)
        face = flat_plate_to_advanced_face(pl)
        axis = np.asarray(face.face_surface.position.axis, dtype=float)
        assert np.dot(axis, np.asarray(pl.poly.normal, dtype=float)) > 0
        # stated with the plate's normal, so the face agrees with it
        assert face.same_sense is True

    def test_a_flat_plate_shares_the_curved_faces_corners(self):
        """The whole point: one vertex where they meet, not two."""
        pl_curved = _plate(name="curved")  # the unit square at x in [0, 1]
        pl_flat = self._flat_plate(1.0, 2.0)  # meets it along x = 1

        sw = _writer(pl_curved)
        weld = TopologyWeld(sw.id_generator)
        ents = curved_plate_to_sat_entities(pl_curved, "FACE00000001", sw, weld)
        ents += advanced_face_to_sat_entities(flat_plate_to_advanced_face(pl_flat), "FACE00000002", sw, weld)
        link_partner_rings(weld)
        ents += weld.entities

        assert weld.n_vertices == 6, "the two shared corners were duplicated"
        pts = [tuple(round(float(c), 7) for c in p.point) for p in _by_type(ents, se.SatPoint)]
        assert len(pts) == len(set(pts))

    def test_a_flat_plate_shares_the_curved_faces_edge(self):
        pl_curved = _plate(name="curved")
        pl_flat = self._flat_plate(1.0, 2.0)

        sw = _writer(pl_curved)
        weld = TopologyWeld(sw.id_generator)
        curved_plate_to_sat_entities(pl_curved, "FACE00000001", sw, weld)
        advanced_face_to_sat_entities(flat_plate_to_advanced_face(pl_flat), "FACE00000002", sw, weld)
        link_partner_rings(weld)

        assert weld.n_edges == 7, "the shared edge was written twice"
        shared = [e for e in weld.coedges_on_edge.values() if len(e) == 2]
        assert len(shared) == 1
        (ca, _), (cb, _) = shared[0]
        assert ca.partner is cb and cb.partner is ca
        # both faces face +z, so they run the edge between them opposite ways
        assert {ca.orientation, cb.orientation} == {"forward", "reversed"}

    def test_two_faces_facing_opposite_ways_run_the_edge_the_same_way(self):
        """The sense follows the winding, and the winding follows the normal.

        Not a curiosity: the flat plate above comes out of CurvePoly2d facing
        -z unless asked otherwise, and then both coedges reading `forward` is
        the correct answer rather than a bug.
        """
        pl_curved = _plate(name="curved")  # faces +z
        pl_flat = self._flat_plate(1.0, 2.0, normal=(0.0, 0.0, -1.0))

        sw = _writer(pl_curved)
        weld = TopologyWeld(sw.id_generator)
        curved_plate_to_sat_entities(pl_curved, "FACE00000001", sw, weld)
        advanced_face_to_sat_entities(flat_plate_to_advanced_face(pl_flat), "FACE00000002", sw, weld)

        shared = [e for e in weld.coedges_on_edge.values() if len(e) == 2]
        assert len(shared) == 1
        (ca, _), (cb, _) = shared[0]
        assert ca.orientation == cb.orientation

    def test_the_curved_faces_parameter_range_survives_the_sharing(self):
        """A straight edge has no range to contribute; the curved face's stands."""
        pl_curved = _plate(name="curved")
        pl_flat = self._flat_plate(1.0, 2.0)

        sw = _writer(pl_curved)
        weld = TopologyWeld(sw.id_generator)
        curved_plate_to_sat_entities(pl_curved, "FACE00000001", sw, weld)
        advanced_face_to_sat_entities(flat_plate_to_advanced_face(pl_flat), "FACE00000002", sw, weld)

        assert weld.range_conflicts == 0
        for edge in _by_type(weld.entities, se.Edge):
            assert edge.t_start is None or edge.t_start < edge.t_end


class TestRefusals:
    """Refuse rather than approximate — the caller falls back to a polygon."""

    def test_a_face_with_a_hole_is_refused(self):
        face = _square_face()
        face.bounds.append(face.bounds[0])
        pl = _plate(face)
        with pytest.raises(UnsupportedCurvedFace, match="bounds"):
            _build(pl)

    def test_an_unknown_surface_is_refused(self):
        face = _square_face()
        face.face_surface = geo_su.CylindricalSurface(position=_plane_surface().position, radius=1.0)
        pl = _plate(face)
        with pytest.raises(UnsupportedCurvedFace, match="CylindricalSurface"):
            _build(pl)

    def test_a_circle_without_parameters_is_refused(self):
        """A circle passes through two points twice; the range is not derivable."""
        circle = geo_cu.Circle(_plane_surface().position, 1.0)
        face = _square_face()
        oe = face.bounds[0].bound.edge_list[0]
        oe.edge_element.edge_geometry = circle
        oe.t_start = oe.t_end = None
        pl = _plate(face)
        with pytest.raises(UnsupportedCurvedFace, match="without authored parameters"):
            _build(pl)


class TestWiring:
    def test_part_to_sat_writer_gives_every_curved_plate_a_face(self):
        import ada
        from ada.cadit.sat.write.writer import part_to_sat_writer

        p = ada.Part("p")
        for i in range(3):
            p.add_plate(_plate(name=f"cp{i}"))
        a = ada.Assembly("a") / p

        sw = part_to_sat_writer(a, imprint=False)
        assert len(sw.get_entities_by_type(se.Face)) == 3
        # each plate resolves to a face, and the names do not collide
        names = [sw.face_map[pl.guid][0] for pl in p.get_all_physical_objects(by_type=PlateCurved)]
        assert sorted(names) == ["FACE00000001", "FACE00000002", "FACE00000003"]

    def test_a_curved_only_model_still_gets_a_body(self):
        """The early-out used to key on flat plates alone."""
        import ada
        from ada.cadit.sat.write.writer import part_to_sat_writer

        p = ada.Part("p")
        p.add_plate(_plate())
        a = ada.Assembly("a") / p

        sw = part_to_sat_writer(a, imprint=False)
        assert not sw.is_empty
        assert len(sw.get_entities_by_type(se.Body)) == 1
        assert sw.shell.face is not None

    def test_the_shell_chains_every_face(self):
        import ada
        from ada.cadit.sat.write.writer import part_to_sat_writer

        p = ada.Part("p")
        for i in range(4):
            p.add_plate(_plate(name=f"cp{i}"))
        a = ada.Assembly("a") / p

        sw = part_to_sat_writer(a, imprint=False)
        seen, face = 0, sw.shell.face
        while face is not None:
            seen += 1
            face = face.next_face
        assert seen == 4
