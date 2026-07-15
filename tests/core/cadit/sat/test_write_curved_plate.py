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
    UnsupportedCurvedFace,
    curved_plate_to_sat_entities,
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


def _pcurve():
    return geo_cu.Pcurve2dBSpline(
        degree=1,
        control_points_2d=[[0.0, 0.0], [1.0, 0.0]],
        knots=[0.0, 1.0],
        knot_multiplicities=[2, 2],
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


def _square_face(surface=None, pcurve=None):
    corners = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    edges = [_line_edge(corners[i], corners[(i + 1) % 4], pcurve=pcurve) for i in range(4)]
    return geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)],
        face_surface=surface if surface is not None else _plane_surface(),
    )


def _plate(face=None, name="pl1") -> PlateCurved:
    from ada.visit.colors import Color

    geom = Geometry(1, face if face is not None else _square_face(), Color(0.5, 0.5, 0.5))
    return PlateCurved(name, geom, t=0.01)


def _writer(pl) -> SatWriter:
    sw = SatWriter(None)
    sw.init_body([], [pl])
    return sw


def _by_type(entities, cls):
    return [e for e in entities if type(e) is cls]


class TestTopology:
    def test_one_face_one_loop_and_a_closed_coedge_ring(self):
        pl = _plate()
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))

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
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
        assert len(_by_type(ents, se.Vertex)) == 4
        assert len(_by_type(ents, se.SatPoint)) == 4

    def test_the_face_is_named_and_owned_by_the_shell(self):
        pl = _plate()
        sw = _writer(pl)
        ents = curved_plate_to_sat_entities(pl, "FACE00000042", sw)
        face = _by_type(ents, se.Face)[0]
        assert face.name.name == "FACE00000042"
        assert face.shell is sw.shell
        assert face.loop.face is face

    def test_every_vertex_names_an_edge(self):
        pl = _plate()
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
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
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
        senses = [c.orientation for c in _by_type(ents, se.CoEdge)]
        assert senses.count("reversed") == 1
        assert senses.count("forward") == 3

    def test_the_edge_range_still_ascends(self):
        """ACIS reads an edge forward along its curve, so t must ascend."""
        pl = _plate(self._face_with_backwards_edge())
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
        for edge in _by_type(ents, se.Edge):
            assert edge.t_start < edge.t_end
            assert " forward " in edge.to_string()

    def test_a_reversed_edge_swaps_its_vertices(self):
        pl = _plate(self._face_with_backwards_edge())
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
        reversed_coedge = next(c for c in _by_type(ents, se.CoEdge) if c.orientation == "reversed")
        edge = reversed_coedge.edge
        # the loop runs (1,0,0) -> (1,1,0); the edge is written the other way
        assert tuple(edge.vertex_start.point.point) == (1.0, 1.0, 0.0)
        assert tuple(edge.vertex_end.point.point) == (1.0, 0.0, 0.0)


class TestSurfacesAndPCurves:
    def test_a_plane_surfaced_face_carries_no_pcurve(self):
        """A flat face with curved edges: Genie gives its coedges no pcurve."""
        pl = _plate(_square_face(surface=_plane_surface(), pcurve=_pcurve()))
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
        assert len(_by_type(ents, se.PlaneSurface)) == 1
        assert _by_type(ents, se.PCurve) == []
        assert all(c.pcurve is None for c in _by_type(ents, se.CoEdge))

    def test_a_spline_face_carries_one_pcurve_per_coedge(self):
        pl = _plate(_square_face(surface=_spline_surface(), pcurve=_pcurve()))
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
        assert len(_by_type(ents, se.SplineSurface)) == 1
        assert len(_by_type(ents, se.PCurve)) == 4
        assert all(c.pcurve is not None for c in _by_type(ents, se.CoEdge))

    def test_the_pcurve_names_the_face_surface(self):
        pl = _plate(_square_face(surface=_spline_surface(), pcurve=_pcurve()))
        ents = curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))
        surface = _by_type(ents, se.SplineSurface)[0]
        for pc in _by_type(ents, se.PCurve):
            assert pc.surface is surface


class TestRefusals:
    """Refuse rather than approximate — the caller falls back to a polygon."""

    def test_a_face_with_a_hole_is_refused(self):
        face = _square_face()
        face.bounds.append(face.bounds[0])
        pl = _plate(face)
        with pytest.raises(UnsupportedCurvedFace, match="bounds"):
            curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))

    def test_an_unknown_surface_is_refused(self):
        face = _square_face()
        face.face_surface = geo_su.CylindricalSurface(position=_plane_surface().position, radius=1.0)
        pl = _plate(face)
        with pytest.raises(UnsupportedCurvedFace, match="CylindricalSurface"):
            curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))

    def test_a_circle_without_parameters_is_refused(self):
        """A circle passes through two points twice; the range is not derivable."""
        circle = geo_cu.Circle(_plane_surface().position, 1.0)
        face = _square_face()
        oe = face.bounds[0].bound.edge_list[0]
        oe.edge_element.edge_geometry = circle
        oe.t_start = oe.t_end = None
        pl = _plate(face)
        with pytest.raises(UnsupportedCurvedFace, match="without authored parameters"):
            curved_plate_to_sat_entities(pl, "FACE00000001", _writer(pl))


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
