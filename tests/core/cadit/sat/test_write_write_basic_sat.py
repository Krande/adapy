"""SAT writer tests, asserted against the Genie-authored reference bodies in
``files/sat_files``. Those files are ground truth for what Genie itself emits, so
each case compares topology (via :mod:`sat_topology`) rather than raw lines —
record numbering is positional and carries no meaning."""

import pytest
from tests.core.cadit.sat.sat_topology import digest, loop_as_cycle, ref_errors

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

    def test_unfused_leaves_plates_unshared(self):
        """imprint=False keeps the old one-face-per-plate body (no CAD backend)."""
        deck = ada.Plate.from_3d_points("deck", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], 0.01)
        bulk = ada.Plate.from_3d_points("bulk", [(0, 5, 0), (10, 5, 0), (10, 5, 5), (0, 5, 5)], 0.01)
        a = ada.Assembly("t") / (ada.Part("p") / [deck, bulk])

        d = digest(part_to_sat_writer(a, imprint=False).to_str())
        assert d["counts"]["face"] == 2
        assert d["counts"]["vertex"] == 8  # 4 + 4, none welded
        assert d["coedges_with_partner"] == 0
