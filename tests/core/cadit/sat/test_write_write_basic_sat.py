"""SAT writer tests, asserted against the Genie-authored reference bodies in
``files/sat_files``. Those files are ground truth for what Genie itself emits, so
each case compares topology (via :mod:`sat_topology`) rather than raw lines —
record numbering is positional and carries no meaning."""

import pytest
from tests.core.cadit.sat.sat_topology import digest, loop_as_cycle, parse, ref_errors

import ada
from ada.cadit.sat.write import sat_entities as se
from ada.cadit.sat.write.writer import part_to_sat_writer


def _plate_10x10(name="pl", origin=None, t=0.01):
    return ada.Plate(name, [(0, 0), (10, 0), (10, 10), (0, 10)], t, origin=origin)


def test_write_basic_plate_sat(example_files):
    """A single plate must reproduce the Genie reference body."""
    reference = (example_files / "sat_files/flat_plate_sesam_10x10.sat").read_text()

    a = ada.Assembly() / _plate_10x10(t=0.1)
    sat = part_to_sat_writer(a).to_str()

    assert ref_errors(sat) == []

    ours, theirs = digest(sat), digest(reference)
    assert ours["faces_walked"] == theirs["faces_walked"] == 1
    for key in ("body", "lump", "shell", "face", "loop", "edge", "vertex", "point", "coedge"):
        assert ours["counts"][key] == theirs["counts"][key], key
    assert ours["normals"] == theirs["normals"] == [(0.0, 0.0, 1.0)]
    assert loop_as_cycle(ours["boundaries"][0]) == loop_as_cycle(theirs["boundaries"][0])


def test_plate_loop_winds_with_its_normal():
    """The loop must wind counter-clockwise about the face normal.

    CurvePoly2d hands back an outline wound *against* ``poly.normal``, so writing
    ``segments3d`` straight out inverts which side of the face is material.
    """
    a = ada.Assembly() / _plate_10x10(t=0.1)
    d = digest(part_to_sat_writer(a).to_str())
    assert d["winding_dots"] == [1.0]


def test_every_loop_points_at_its_own_face():
    """Regression: the loop's face pointer was hardcoded to ``$3``, which is only
    ever right for a single-plate body (whose face happens to be entity 3)."""
    a = ada.Assembly() / (_plate_10x10("pl"), _plate_10x10("pl2", origin=(0, 0, 2)))
    sat = part_to_sat_writer(a, imprint=False).to_str()

    assert ref_errors(sat) == []  # a wrong face pointer resolves to a non-face


def test_all_faces_share_one_body_lump_shell():
    """Genie puts every plate face in ONE body/lump/shell, chained via next_face."""
    plates = [_plate_10x10(f"pl{i}", origin=(0, 0, 2 * i)) for i in range(4)]
    a = ada.Assembly() / plates

    sw = part_to_sat_writer(a, imprint=False)
    sat = sw.to_str()
    d = digest(sat)

    assert d["counts"]["body"] == d["counts"]["lump"] == d["counts"]["shell"] == 1
    assert d["counts"]["face"] == 4
    # all four reachable by walking the chain from the shell's single face pointer
    assert d["faces_walked"] == 4
    assert ref_errors(sat) == []


@pytest.mark.parametrize("imprint", [False, True])
def test_plate_in_a_placed_part_lands_in_global_coords(imprint):
    """Regression: poly.points3d is in the plate's own frame, and the SAT body
    used to emit it verbatim — so a plate inside a placed Part was written at
    the wrong position while the polygon writer placed it correctly."""
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    part = ada.Part("offset")
    part.placement = ada.Placement(origin=(100, 0, 0))
    a = ada.Assembly("a") / (part / pl)

    d = digest(part_to_sat_writer(a, imprint=imprint).to_str())
    xs = [p[0] for p in d["boundaries"][0]]
    assert min(xs) == 100.0 and max(xs) == 101.0


@pytest.mark.parametrize("imprint", [False, True])
def test_write_basic_plates_offset_no_shared(imprint):
    a = ada.Assembly() / (_plate_10x10("pl", t=0.1), _plate_10x10("pl2", origin=(0, 0, 2), t=0.1))
    sw = part_to_sat_writer(a, imprint=imprint)
    assert len(sw.get_entities_by_type(se.Face)) == 2
    assert ref_errors(sw.to_str()) == []


class TestImprint:
    """The imprinted body must match what Genie produces for the same plates."""

    def test_shared_vertex_matches_reference(self, example_files):
        reference = (example_files / "sat_files/flat_plate_x2_sesam_10x10_shared_vertex.sat").read_text()
        a = ada.Assembly() / (_plate_10x10("pl"), _plate_10x10("pl2", origin=(10, 10, 0)))

        ours, theirs = digest(part_to_sat_writer(a).to_str()), digest(reference)
        # touching at one corner: that vertex is shared, so 7 not 8
        for key in ("face", "edge", "vertex", "point", "coedge"):
            assert ours["counts"][key] == theirs["counts"][key], key
        assert ours["counts"]["vertex"] == 7
        assert ours["coedges_with_partner"] == theirs["coedges_with_partner"] == 0

    def test_shared_edge_matches_reference(self, example_files):
        reference = (example_files / "sat_files/flat_plate_x2_sesam_10x10_offset_shared_edge.sat").read_text()
        a = ada.Assembly() / (_plate_10x10("pl"), _plate_10x10("pl2", origin=(10, 5, 0)))

        sat = part_to_sat_writer(a).to_str()
        assert ref_errors(sat) == []

        ours, theirs = digest(sat), digest(reference)
        # Genie splits both plates' facing edges at the overlap ends: 9 edges and
        # 8 vertices for what would otherwise be 8 and 8.
        for key in ("face", "edge", "vertex", "point", "coedge"):
            assert ours["counts"][key] == theirs["counts"][key], key
        assert ours["counts"]["edge"] == 9
        # the one genuinely shared edge pairs two coedges through the partner ring
        assert ours["coedges_with_partner"] == theirs["coedges_with_partner"] == 2
        assert ours["normals"] == theirs["normals"]
        assert all(d > 0 for d in ours["winding_dots"])

        ours_loops = sorted(loop_as_cycle(b) for b in ours["boundaries"])
        theirs_loops = sorted(loop_as_cycle(b) for b in theirs["boundaries"])
        assert ours_loops == theirs_loops

    def test_t_junction_splits_the_crossed_plate(self):
        """A deck crossed by a bulkhead: the deck becomes two faces, and the
        imprint line is ONE edge carrying three coedges in a partner ring."""
        deck = ada.Plate.from_3d_points("deck", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], 0.01)
        bulk = ada.Plate.from_3d_points("bulk", [(0, 5, 0), (10, 5, 0), (10, 5, 5), (0, 5, 5)], 0.01)
        a = ada.Assembly("t") / (ada.Part("p") / [deck, bulk])

        sw = part_to_sat_writer(a)
        sat = sw.to_str()
        d = digest(sat)

        assert ref_errors(sat) == []
        assert d["counts"]["face"] == 3
        assert d["faces_walked"] == 3
        assert len(sw.face_map[deck.guid]) == 2  # split
        assert len(sw.face_map[bulk.guid]) == 1
        # 3 faces meet along the imprint line -> a 3-cycle, not a pair
        assert d["coedges_with_partner"] == 3
        assert d["counts"]["vertex"] == 8  # welded, not 4+4
        assert all(x > 0 for x in d["winding_dots"])

    def test_partner_ring_runs_in_radial_order(self):
        """The ring on an edge is the faces' angular order, so it must be sorted.

        Two plates crossing put FOUR faces on the intersection line — the case
        that exposes this, since a ring of two is sorted whatever the order and
        one of three is at worst reversed. Unordered, the model fails
        verification with "coedges out of order about edge".
        """
        from tests.core.cadit.sat.sat_topology import partner_rings

        horiz = ada.Plate.from_3d_points("h", [(0, -5, 0), (10, -5, 0), (10, 5, 0), (0, 5, 0)], 0.01)
        vert = ada.Plate.from_3d_points("v", [(0, 0, -5), (10, 0, -5), (10, 0, 5), (0, 0, 5)], 0.01)
        a = ada.Assembly("t") / (ada.Part("p") / [horiz, vert])

        sw = part_to_sat_writer(a)
        sat = sw.to_str()
        assert ref_errors(sat) == []

        # each plate is cut in two by the other
        assert len(sw.face_map[horiz.guid]) == 2
        assert len(sw.face_map[vert.guid]) == 2

        rings = partner_rings(sat)
        assert 4 in rings, f"expected an edge carrying four coedges, got {rings}"
        assert rings[4] == {"sorted_ccw": 1}
        # and nothing anywhere in the body is out of order
        assert all(set(v) == {"sorted_ccw"} for v in rings.values()), rings

    def test_t_junction_partner_ring_is_ordered(self):
        deck = ada.Plate.from_3d_points("deck", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], 0.01)
        bulk = ada.Plate.from_3d_points("bulk", [(0, 5, 0), (10, 5, 0), (10, 5, 5), (0, 5, 5)], 0.01)
        a = ada.Assembly("t") / (ada.Part("p") / [deck, bulk])

        from tests.core.cadit.sat.sat_topology import partner_rings

        rings = partner_rings(part_to_sat_writer(a).to_str())
        assert rings[3] == {"sorted_ccw": 1}
        assert all(set(v) == {"sorted_ccw"} for v in rings.values()), rings

    def test_beam_leaving_its_plate_declares_the_nonmanifold_vertex(self):
        """Where a beam's axis runs off the plate, the boundary vertex is split.

        It carries the plate's face edges AND the wire edge for the free run,
        and nothing owns both, so neither is reachable from the other:
        "vertex has edge in multiple groups". SAT v4.0 ch.7 `vertedge` wants an
        edge pointer per separable region there, and the vertex itself to name
        none — a single pointer could only ever reach one of the two.
        """
        deck = ada.Plate.from_3d_points("deck", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], 0.01)
        # starts on the deck, ends past its edge
        bm = ada.Beam("bm", (5, 5, 0), (15, 5, 0), "IPE200")
        a = ada.Assembly("t") / (ada.Part("p") / [deck, bm])

        sat = part_to_sat_writer(a).to_str()
        assert ref_errors(sat) == []

        ents = parse(sat)
        attribs = [(i, f) for i, (t, f) in ents.items() if t == "vertedge-sys-attrib"]
        assert len(attribs) == 1, "the vertex where the axis crosses the deck edge"

        aid, fields = attribs[0]
        owner = int(fields[4][1:])
        assert ents[owner][0] == "vertex"
        # the vertex hands off to the attribute rather than naming one edge
        assert ents[owner][1][0] == f"${aid}"
        assert ents[owner][1][4] == "$-1"

        # payload: a count, then that many edge pointers, the real ones first
        payload = fields[5 + 18 :]
        assert payload[0] == "4"
        listed = [p for p in payload[1:] if p != "#"]
        assert len(listed) == 4
        named = [int(p[1:]) for p in listed if p != "$-1"]
        assert len(named) == 2, "one edge per region: the deck's, and the free run's"
        assert all(ents[e][0] == "edge" for e in named)

    def test_a_plain_plate_has_no_vertedge_attribs(self):
        """Nothing non-manifold about it — the attribute must not appear."""
        a = ada.Assembly() / _plate_10x10(t=0.1)
        sat = part_to_sat_writer(a).to_str()
        assert "vertedge" not in sat
        assert all(f[4] != "$-1" for t, f in parse(sat).values() if t == "vertex")

    def test_unfused_leaves_plates_unshared(self):
        """imprint=False keeps the old one-face-per-plate body (no CAD backend)."""
        deck = ada.Plate.from_3d_points("deck", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], 0.01)
        bulk = ada.Plate.from_3d_points("bulk", [(0, 5, 0), (10, 5, 0), (10, 5, 5), (0, 5, 5)], 0.01)
        a = ada.Assembly("t") / (ada.Part("p") / [deck, bulk])

        d = digest(part_to_sat_writer(a, imprint=False).to_str())
        assert d["counts"]["face"] == 2
        assert d["counts"]["vertex"] == 8  # 4 + 4, none welded
        assert d["coedges_with_partner"] == 0
