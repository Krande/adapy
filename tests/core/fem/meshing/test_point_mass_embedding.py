"""A point mass on a plate or in a beam's span gets a mesh node that the plate's or beam's elements hold.

``Part.to_fem_obj`` used to embed concept point loads and point supports in the geometry before meshing, but not
the positions of mass shapes (a ``MassPoint``): each mass got a node of its own after meshing, which no element held
unless a mesh vertex happened to be there. Code_Aster writes a point mass as a ``DIS_T`` with no stiffness, so such
a node had none at all, and a static solve stopped at ``FACTOR_11`` (a singular matrix) naming only a node id.
Now the mass positions are embedded like the loads and supports; a mass on no meshed element keeps a node of its own
and is reported (suspect).
"""

from __future__ import annotations

import numpy as np

import ada
from ada.api.mass import MassPoint
from ada.fem import FemSet, Mass
from ada.fem.formats import conversion_report
from ada.fem.utils import free_point_masses, node_is_held

MASS = 100.0


def _point_masses(fem) -> list[Mass]:
    return [el for el in fem.elements if isinstance(el, Mass)]


def _held_by(node) -> list:
    return [r for r in node.refs if not isinstance(r, (Mass, FemSet))]


def _plate_part(mass_at) -> ada.Part:
    p = ada.Part("P")
    p.add_plate(ada.Plate("pl", [(0, 0), (2, 0), (2, 2), (0, 2)], 0.01))
    p.add_mass(MassPoint("m", mass_at, MASS))
    return p


def _mass_findings(rep, kind: str, stage: str) -> list:
    return [f for f in rep.findings if f.keyword == "Mass" and f.kind == kind and f.stage == stage]


def test_a_mass_inside_a_plate_is_on_a_node_the_shells_hold():
    """(0.9, 1.1) is no vertex of a 0.5 m mesh of a 2 x 2 m plate: the mass used to be on a node of its own."""
    with conversion_report.collect() as rep:
        fem = _plate_part((0.9, 1.1, 0.0)).to_fem_obj(0.5)

    (mass,) = _point_masses(fem)
    node = mass.nodes[0]
    assert np.allclose(node.p, (0.9, 1.1, 0.0))
    assert len(_held_by(node)) >= 3  # shell elements around an interior node
    assert node_is_held(node)
    assert free_point_masses(fem) == []
    assert not _mass_findings(rep, "suspect", "concept to fem")
    assert mass.mass == MASS


def test_a_mass_in_a_beams_span_is_on_a_node_the_beam_holds():
    bm = ada.Beam("bm", (0, 0, 0), (4, 0, 0), "IPE300")
    p = ada.Part("P") / bm
    p.add_mass(MassPoint("m", (1.3, 0, 0), MASS))

    fem = p.to_fem_obj(0.5, "line")

    (mass,) = _point_masses(fem)
    node = mass.nodes[0]
    assert np.allclose(node.p, (1.3, 0.0, 0.0))
    assert len(_held_by(node)) == 2  # the two line elements meeting there
    assert free_point_masses(fem) == []


def test_without_embedding_the_mass_is_on_a_free_node_and_reported():
    """``embed_concept_points=False`` keeps the old mesh: the mass's node is held by nothing, and said so."""
    with conversion_report.collect() as rep:
        fem = _plate_part((0.9, 1.1, 0.0)).to_fem_obj(0.5, embed_concept_points=False)

    (mass,) = _point_masses(fem)
    assert not node_is_held(mass.nodes[0])
    (finding,) = _mass_findings(rep, "suspect", "concept to fem")
    assert finding.subject == "m_mass"
    assert finding.details["position"] == [0.9, 1.1, 0.0]


def test_a_mass_off_the_structure_keeps_its_own_node_and_is_reported():
    with conversion_report.collect() as rep:
        fem = _plate_part((0.9, 1.1, 0.5)).to_fem_obj(0.5)

    (mass,) = _point_masses(fem)
    assert np.allclose(mass.nodes[0].p, (0.9, 1.1, 0.5))
    assert free_point_masses(fem) == [mass]
    (finding,) = _mass_findings(rep, "suspect", "concept to fem")
    assert finding.details["position"] == [0.9, 1.1, 0.5]
    assert "FACTOR_11" in finding.reason
