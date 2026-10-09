"""A shape with a mass becomes a point mass in `Part.to_fem_obj`, wherever its cog says -- or at its centre."""

import numpy as np

import ada
from ada.fem.elements import Mass


def _point_masses(fem) -> list:
    return [el for el in fem.elements if isinstance(el, Mass)]


def _part_with(box: ada.PrimBox) -> ada.Part:
    return ada.Part("p") / [box, ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)]


def test_a_mass_shape_without_a_cog_is_placed_at_its_centre():
    """Used to crash: `Node(None)` -- "iteration over a 0-d array"."""
    box = ada.PrimBox("m", (0, 0, 0), (2, 1, 1), mass=100.0)
    assert box.cog is None

    fem = _part_with(box).to_fem_obj(0.5, pl_repr="shell", use_quads=True)

    (mass,) = _point_masses(fem)
    assert mass.mass == 100.0
    assert np.allclose(mass.nodes[0].p, (1.0, 0.5, 0.5))


def test_an_explicit_cog_is_used_as_given():
    box = ada.PrimBox("m", (0, 0, 0), (2, 1, 1), mass=100.0, cog=(0.25, 0.25, 0.25))

    fem = _part_with(box).to_fem_obj(0.5, pl_repr="shell", use_quads=True)

    (mass,) = _point_masses(fem)
    assert np.allclose(mass.nodes[0].p, (0.25, 0.25, 0.25))
