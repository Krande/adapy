"""The Scene > FEM tab's constraint picker draws whatever ``build_constraint_glyphs``
emits, so what it puts in the payload is what the viewer can show."""

import ada
from ada.extension.fem_concepts_builder import (
    build_constraint_glyphs,
    build_sim_fem_concepts,
)
from ada.fem import Constraint, FemSet, Surface


def _fem_with_constraint(con_type=Constraint.TYPES.COUPLING, **kwargs) -> ada.FEM:
    master = [ada.Node((0, 0, 0), 1)]
    slaves = [ada.Node((1, 0, 0), 2), ada.Node((0, 1, 0), 3)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(master + slaves))
    m = Surface("m_surf", Surface.TYPES.NODE, fem.add_set(FemSet("m", master, "nset")), parent=fem)
    s = Surface("s_surf", Surface.TYPES.NODE, fem.add_set(FemSet("s", slaves, "nset")), parent=fem)
    fem.add_constraint(Constraint("c1", con_type, m, s, parent=fem, **kwargs))
    return fem


def test_master_and_slave_positions_stay_on_their_own_side():
    """The viewer colours the two sides differently, so they must not be merged."""
    glyphs = build_constraint_glyphs(_fem_with_constraint())

    assert len(glyphs) == 1
    g = glyphs[0]
    assert g.name == "c1"
    assert g.constraint_type == "coupling"
    assert [list(p.root) for p in g.master_positions] == [[0.0, 0.0, 0.0]]
    assert [list(p.root) for p in g.slave_positions] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def test_shell2solid_is_carried_with_its_position_tolerance():
    fem = _fem_with_constraint(Constraint.TYPES.SHELL2SOLID, pos_tol=0.06)
    g = build_constraint_glyphs(fem)[0]

    assert g.constraint_type == "shell2solid"
    assert g.position_tolerance == 0.06
    assert g.influence_distance is None


def test_influence_distance_is_carried_when_the_source_set_one():
    g = build_constraint_glyphs(_fem_with_constraint(influence_distance=0.5))[0]

    assert g.influence_distance == 0.5
    assert g.position_tolerance is None


def test_a_fem_with_no_constraints_emits_nothing():
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes([ada.Node((0, 0, 0), 1)]))

    assert build_constraint_glyphs(fem) == []
    assert build_sim_fem_concepts(fem) is None


def test_constraints_reach_the_simulation_bundle():
    """A FEM carrying only constraints (no BCs) must still produce a payload --
    otherwise the FEM tab never appears for a constraint-only model."""
    bundle = build_sim_fem_concepts(_fem_with_constraint())

    assert bundle is not None
    assert bundle.bcs is None
    assert len(bundle.constraints) == 1
    assert bundle.constraints[0].name == "c1"
