"""Constraints in the CalculiX deck.

The writer used to drop every constraint, so a beam end supported through a coupled reference node was written
as a floating node and an unsupported structure: every eigenfrequency came out at zero. Kinematic couplings are
now written; what CalculiX cannot take is refused rather than left out.
"""

import re

import pytest

import ada
from ada.base.types import GeomRepr
from ada.fem import Constraint, FemSet
from ada.fem.exceptions import IncompatibleElements


def _cantilever(geom_repr: GeomRepr) -> ada.Assembly:
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    p = ada.Part("P1") / bm
    bm.concept_fem.fix_end("n1")  # section_support="coupled": a reference node coupled to the section
    p.fem = bm.to_fem_obj(0.1, geom_repr)
    a = ada.Assembly("a") / p
    a.fem.add_step(ada.fem.StepEigen("eig", num_eigen_modes=3))
    return a


def _deck(a: ada.Assembly, tmp_path) -> str:
    a.to_fem("m", "calculix", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / "m" / "m.inp").read_text()


def test_a_solid_section_coupling_is_written_as_a_kinematic_coupling(tmp_path):
    deck = _deck(_cantilever(GeomRepr.SOLID), tmp_path)

    assert re.search(r"\*SURFACE, NAME=bm1_n1_cpl_surf, TYPE=NODE\nbm1_n1_sec\n", deck)
    m = re.search(
        r"\*COUPLING, CONSTRAINT NAME=bm1_n1_cpl, REF NODE=(\d+), SURFACE=bm1_n1_cpl_surf\n\*KINEMATIC\n", deck
    )
    assert m, deck
    # CalculiX's *KINEMATIC takes the translations only; the rotations come with its rigid-body kinematics
    kinematic = deck[m.end() :].split("\n*", 1)[0].split("\n")
    assert [line for line in kinematic if line.strip()] == ["1, 1", "2, 2", "3, 3"]


def test_a_shell_section_coupling_is_refused(tmp_path):
    with pytest.raises(IncompatibleElements, match="shell nodes"):
        _deck(_cantilever(GeomRepr.SHELL), tmp_path)


def test_a_constraint_it_cannot_write_is_refused_not_dropped(tmp_path):
    a = _cantilever(GeomRepr.SOLID)
    p = a.get_part("P1")
    nodes = list(p.fem.nodes)[:2]
    m_set = p.fem.add_set(FemSet("m", [nodes[0]], FemSet.TYPES.NSET))
    s_set = p.fem.add_set(FemSet("s", [nodes[1]], FemSet.TYPES.NSET))
    p.fem.add_constraint(Constraint("tie1", Constraint.TYPES.TIE, m_set, s_set))
    with pytest.raises(IncompatibleElements, match="tie1"):
        _deck(a, tmp_path)
