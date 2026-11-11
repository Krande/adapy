"""A point mass on a node that a support given by name, or a tie on a surface of its set, holds is held.

``node_is_held`` counted only the integer DOFs of a boundary condition, and only a constraint made directly on a node
set. The Abaqus reader keeps ``ENCASTRE`` as that name (and any named restraint a deck states one per line), and a tie
between coincident nodes is made on a node SURFACE of the set. A mass on such a node was taken as free: the Code_Aster
writer left it out, Sesam and CalculiX reported it suspect, and ``ada convert --strict`` exited non-zero for a model
that is sound and used to be written as it is.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import Bc, Constraint, FemSet, Mass, StepEigen
from ada.fem.formats import conversion_report
from ada.fem.utils import bc_fixed_dofs, free_point_masses, node_is_held

MASS = 250.0

DECK = """*Heading
** a mass on a node only a named support holds
*Part, name=P
*Node
      1,          0.,          0.,          0.
*Element, type=MASS, elset=m_el
1, 1
*Mass, elset=m_el
{mass},
*Nset, nset=held
 1,
*End Part
*Assembly, name=Assembly
*Instance, name=P-1, part=P
*End Instance
*Nset, nset=held, instance=P-1
 1,
*End Assembly
*Boundary
{boundary}
"""


def _read(tmp_path, boundary: str) -> ada.Assembly:
    deck = tmp_path / "named.inp"
    deck.write_text(DECK.format(mass=MASS, boundary=boundary))
    a = ada.from_fem(deck, "abaqus")
    a.fem.add_step(StepEigen("eig", num_eigen_modes=1))
    return a


def _mass_fem(a: ada.Assembly):
    (fem,) = [p.fem for p in a.get_all_parts_in_assembly() if len(p.fem.elements.masses)]
    return fem


def _mass_findings(rep, kind: str) -> list:
    return [f for f in rep.findings if f.keyword == "Mass" and f.kind == kind]


def test_the_dofs_a_named_support_fixes():
    assert bc_fixed_dofs("ENCASTRE") == {1, 2, 3, 4, 5, 6}
    assert bc_fixed_dofs(["Pinned"]) == {1, 2, 3}
    assert bc_fixed_dofs(["XSYMM", "YSYMM"]) == {1, 2, 4, 5, 6}
    assert bc_fixed_dofs([1, 2, None, None, None, 6]) == {1, 2, 6}
    assert bc_fixed_dofs(["no such restraint"]) == set()
    assert bc_fixed_dofs(None) == set()


@pytest.mark.parametrize(
    "boundary, held",
    [
        ("held, ENCASTRE", True),
        ("held, PINNED", True),
        # one name per line: the reader keeps the names
        ("held, XSYMM\nheld, YSYMM\nheld, ZSYMM", True),
        # one translation only
        ("held, XSYMM", False),
        ("held, 1, 2", False),
        ("held, 1, 3", True),
    ],
)
def test_a_mass_on_a_supported_node_read_from_an_abaqus_deck(tmp_path, boundary, held):
    a = _read(tmp_path, boundary)
    fem = _mass_fem(a)
    (mass,) = fem.elements.masses
    (node,) = mass.members
    # The support is the assembly's, on the instance's node: the node itself says whether it is held.
    assert node_is_held(node) is held, f"{boundary!r}: dofs read as {[bc.dofs for bc in a.fem.bcs]}"
    assert (free_point_masses(fem) == []) is held


@pytest.mark.parametrize("fem_format", ["calculix", "abaqus"])
def test_a_mass_on_an_encastred_node_is_written_and_not_reported(tmp_path, fem_format):
    a = _read(tmp_path, "held, ENCASTRE")
    with conversion_report.collect() as rep:
        a.to_fem("out", fem_format, scratch_dir=tmp_path, overwrite=True)
    assert _mass_findings(rep, "omitted") == []
    assert _mass_findings(rep, "suspect") == []


def test_code_aster_writes_an_encastre_support_and_the_mass_on_it(tmp_path):
    """Built as objects, with the support in the part. The support was matched against DOF numbers as the name it
    is, matched none, and was not written at all."""
    p = ada.Part("P")
    node = p.fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("held", [node], "nset")), "ENCASTRE"))
    p.fem.add_mass(Mass("m", [node], MASS, mass_id=1))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepEigen("eig", num_eigen_modes=1))
    assert node_is_held(node)

    with conversion_report.collect() as rep:
        a.to_fem("ca", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "ca" / "ca.comm").read_text()
    assert f"CARA='M_T_D_N', VALE={MASS!r}" in comm
    for dof in ("DX", "DY", "DZ"):
        assert f"{dof}=0" in comm.replace(" ", ""), f"the support does not hold {dof}"
    assert _mass_findings(rep, "omitted") == []


def test_the_report_a_writer_makes_of_free_masses_has_nothing_for_an_encastred_one(tmp_path):
    """What the Sesam and CalculiX writers call. (Sesam itself refuses this small deck for another reason: a node
    with a mass alone on it has three DOFs, and ENCASTRE names six.)"""
    from ada.fem.utils import report_free_point_masses

    fem = _mass_fem(_read(tmp_path, "held, ENCASTRE"))
    with conversion_report.collect() as rep:
        assert report_free_point_masses(fem, "write") == []
    assert _mass_findings(rep, "suspect") == []


def _two_coincident_nodes() -> tuple[ada.Assembly, ada.FEM, ada.FEM]:
    """A beam's end node in one part and a mass on a node at the same place in another."""
    p1 = ada.Part("structure")
    n1 = p1.fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    n2 = p1.fem.nodes.add(ada.Node((1.0, 0.0, 0.0), 2))
    p1.fem.add_bc(Bc("fix", p1.fem.add_set(FemSet("fix_n", [n2], "nset")), [1, 2, 3, 4, 5, 6]))
    p1.fem.add_set(FemSet("end", [n1], "nset"))
    p2 = ada.Part("equipment")
    m = p2.fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    p2.fem.add_mass(Mass("m", [m], MASS, mass_id=1))
    return ada.Assembly("A") / [p1, p2], p1.fem, p2.fem


def test_a_mass_node_a_tie_on_a_node_surface_holds_is_held():
    a, structure, equipment = _two_coincident_nodes()
    (mass,) = equipment.elements.masses
    (node,) = mass.members
    assert not node_is_held(node)
    assert free_point_masses(equipment) == [mass]

    tie = Constraint.tie_fem_with_coincident_nodes("tie", structure, equipment)
    # The tie is on surfaces of node sets, not on the sets themselves.
    assert type(tie.m_set).__name__ == type(tie.s_set).__name__ == "Surface"
    a.fem.add_constraint(tie)
    assert node_is_held(node)
    assert free_point_masses(equipment) == []


def test_a_surface_nothing_ties_does_not_hold_its_nodes():
    from ada.fem import Surface

    _, _, equipment = _two_coincident_nodes()
    (mass,) = equipment.elements.masses
    (node,) = mass.members
    fs = equipment.add_set(FemSet("loose", [node], "nset"))
    Surface("loose_surf", Surface.TYPES.NODE, fs)
    assert not node_is_held(node)
