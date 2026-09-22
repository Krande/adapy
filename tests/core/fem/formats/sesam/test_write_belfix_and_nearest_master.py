"""BELFIX's OPT / TRANO fields, and the determinism of the shell-to-solid pairing.

Two defects that survived the first Presel/Sestra pass because neither one stops the
deck from loading:

* **BELFIX OPT.** The manual (Sesam Input Interface File description, printed 6-8)
  defines BELFIX as ``FIXNO OPT TRANO void A(1)..A(6)`` with OPT taking exactly two
  values -- 1 "A(i) is the degree of fixation", between 0 and 1, "a = 0, fully
  released; a = 1, fully connected"; 2 "A(i) is an interelement elastic spring
  stiffness", -1 meaning rigid. The writer emitted 3, which the manual does not define,
  while writing 0/1 degree-of-fixation data. Only the field was wrong.

* **Tie-breaking in the shell-to-solid pairing.** ``_nearest_master`` chose with
  ``argmin``, so two masters exactly equidistant from a slave were separated by their
  position in the master list -- i.e. by whatever order the region resolver happened to
  produce. On the project model the same master set in two equally valid orders moved
  313 of 8 910 slaves, every one at a distance difference of exactly 0.0. The choice
  among equidistant masters is arbitrary in physical terms but has to be stable, so it
  is now made on the lowest master node id.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.containers import Nodes
from ada.fem import Constraint, Elem, FemSet, Surface
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_constraints import (
    _nearest_master,
    write_shell2solid,
)
from ada.fem.formats.sesam.write.writer import hinges_str
from ada.fem.shapes.definitions import LineShapes


def _beam_fem(**hinges) -> tuple[ada.FEM, Elem]:
    """One line element carrying the ``h1`` / ``h2`` hinge metadata ``hinges_str`` reads."""
    nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    fem = ada.FEM("MyFem", nodes=Nodes(nodes), elements=FemElements([]))
    el = Elem(1, nodes, LineShapes.LINE)
    fem.elements.add(el)
    el.metadata.update(hinges)
    return fem, el


def _belfix(text) -> list[dict[str, float]]:
    """Every BELFIX record as ``{field: value}``, in file order."""
    out = []
    for m in cards.re_belfix.finditer(text):
        out.append({k: float(v) for k, v in m.groupdict().items()})
    return out


# ---------------------------------------------------------------- BELFIX OPT / TRANO


def test_belfix_opt_is_degree_of_fixation_not_three():
    """OPT = 1 (printed 6-8). 3 is not a value the record defines."""
    fem, _ = _beam_fem(h1=[4, 5, 6])
    (rec,) = _belfix(hinges_str(fem))
    assert rec["opt"] == 1.0


def test_belfix_a_values_are_the_zero_to_one_degrees_of_fixation_opt_1_promises():
    """0 for a released dof, 1 for a connected one -- exactly OPT = 1's range.

    This is what makes 1 the right OPT rather than 2: under OPT = 2 the same numbers
    would be spring stiffnesses, and a released dof would read as a zero-stiffness
    spring while a "connected" one read as a stiffness of 1.0 N/m, not as rigid.
    """
    fem, _ = _beam_fem(h1=[4, 5, 6])  # the three rotations released
    (rec,) = _belfix(hinges_str(fem))
    assert [rec[f"a{i}"] for i in range(1, 7)] == [1.0, 1.0, 1.0, 0.0, 0.0, 0.0]
    assert all(0.0 <= rec[f"a{i}"] <= 1.0 for i in range(1, 7))


def test_belfix_trano_is_zero_for_the_local_element_system():
    """TRANO 0 = "A(i) is given in the local element coordinate system" (printed 6-8).

    A beam end release is stated about the beam's own axes, so local is correct here --
    deliberately, not because a field was left unset.
    """
    fem, _ = _beam_fem(h1=[6])
    (rec,) = _belfix(hinges_str(fem))
    assert rec["trano"] == 0.0


def test_belfix_is_written_per_hinged_end_and_referenced_from_gelref1():
    """Both ends hinged gives two records, and their FIXNOs land in ``fixno`` metadata."""
    fem, el = _beam_fem(h1=[6], h2=[5, 6])
    recs = _belfix(hinges_str(fem))
    assert [r["fixno"] for r in recs] == [1.0, 2.0]
    assert {r["opt"] for r in recs} == {1.0}
    assert el.metadata["fixno"] == (1, 2)


def test_unhinged_elements_write_no_belfix():
    fem, el = _beam_fem()
    assert hinges_str(fem) == ""
    assert "fixno" not in el.metadata


# ------------------------------------------------- deterministic nearest-master choice


def test_nearest_master_breaks_an_exact_tie_on_the_lowest_node_id():
    """A slave halfway between two masters goes to the lower id, whichever is listed first."""
    slaves = np.array([[0.5, 0.0, 0.0]])
    master_p = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])

    # confirm the tie is exact, not merely close
    d = ((slaves[:, None, :] - master_p[None, :, :]) ** 2).sum(-1)
    assert d[0, 0] == d[0, 1]

    ids = np.array([20, 10], dtype=np.int64)
    assert _nearest_master(slaves, master_p, ids)[0] == 1  # index of node 10

    # same geometry, masters swapped: still node 10
    ids = np.array([10, 20], dtype=np.int64)
    assert _nearest_master(slaves, master_p[::-1], ids)[0] == 0


def test_nearest_master_is_unaffected_by_master_order_including_ties():
    """Every permutation of the master list produces the same slave -> master *id* map.

    The mesh is symmetric on purpose: the slaves on ``y = 0.5`` are exactly equidistant
    from the two masters, which is the case ``argmin`` alone decided by list position.
    """
    master_p = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 2.0, 0.0]])
    master_ids = np.array([30, 10, 20], dtype=np.int64)
    slaves = np.array([[0.0, y, 0.0] for y in (0.0, 0.5, 0.9, 1.5, 2.0)])

    reference = master_ids[_nearest_master(slaves, master_p, master_ids)]
    # the ties are real: y = 0.5 and y = 1.5 sit between two masters
    assert list(reference) == [30, 10, 10, 10, 20]

    rng = np.random.default_rng(0)
    for _ in range(12):
        perm = rng.permutation(len(master_ids))
        got = master_ids[perm][_nearest_master(slaves, master_p[perm], master_ids[perm])]
        assert list(got) == list(reference)


def test_nearest_master_tie_break_survives_chunking():
    """The chunk loop must not reset the rule -- the mapping back through the sort
    permutation happens once, after every chunk has been filled."""
    master_p = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    master_ids = np.array([9, 4], dtype=np.int64)
    slaves = np.tile(np.array([[0.5, 0.0, 0.0]]), (5000, 1))

    idx = _nearest_master(slaves, master_p, master_ids)
    assert set(master_ids[idx].tolist()) == {4}


def _tie_fem(master_order):
    """A shell edge of two nodes with a solid face exactly between them.

    Each solid node is equidistant from both shell nodes (x = 0.5 on a 0..1 edge), so
    the pairing is decided entirely by the tie-break. ``master_order`` fixes the order
    the master set -- and therefore ``surface_nodes``, which preserves first-seen order
    -- hands the two shell nodes over in.
    """
    shell = {1: ada.Node((0.0, 0, 0), 1), 2: ada.Node((1.0, 0, 0), 2)}
    solid = [ada.Node((0.5, 0, -0.5), 11), ada.Node((0.5, 0, 0.5), 12)]

    fem = ada.FEM("MyFem", nodes=Nodes([shell[1], shell[2]] + solid))
    edge = fem.add_set(FemSet("edge", [shell[i] for i in master_order], "nset"))
    face = fem.add_set(FemSet("face", solid, "nset"))
    m = Surface("edge_surf", Surface.TYPES.NODE, edge, parent=fem)
    s = Surface("face_surf", Surface.TYPES.NODE, face, parent=fem)
    constraint = Constraint("s2s", Constraint.TYPES.SHELL2SOLID, m, s, parent=fem)
    fem.add_constraint(constraint)
    return constraint


def _bldep_pairs(text) -> list[tuple[int, int]]:
    """``(slave, master)`` for every BLDEP header line."""
    out = []
    for ln in text.splitlines():
        if ln.startswith("BLDEP"):
            slave, master = (int(float(x)) for x in ln.split()[1:3])
            out.append((slave, master))
    return out


@pytest.mark.parametrize("master_order", [(1, 2), (2, 1)])
def test_shell2solid_pairing_is_identical_for_either_master_order(master_order):
    """The whole path, not just the helper: the deck's BLDEP pairing is order-free.

    Both solid nodes are an exact tie between shell nodes 1 and 2, so before the
    tie-break the ``(2, 1)`` ordering paired them with node 2 instead.
    """
    text = write_shell2solid(_tie_fem(master_order))
    assert _bldep_pairs(text) == [(11, 1), (12, 1)]


def test_shell2solid_tie_fixture_really_ties():
    """Guard the fixture: if the geometry stopped being symmetric the test above would
    pass for the wrong reason."""
    constraint = _tie_fem((1, 2))
    from ada.fem.surfaces import surface_nodes

    masters = surface_nodes(constraint.m_set)
    slaves = surface_nodes(constraint.s_set)
    assert [n.id for n in masters] == [1, 2], "surface_nodes must keep the set's order"
    for slave in slaves:
        d = [float(np.sum((np.array(slave.p) - np.array(m.p)) ** 2)) for m in masters]
        assert d[0] == d[1], f"slave {slave.id} is not an exact tie"
