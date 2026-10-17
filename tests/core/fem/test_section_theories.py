"""Membrane, truss and cable section theories on FemSection: defaults, validation, and the writers that refuse them."""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Elem, FemSection, FemSet, LineTheory, ShellTheory
from ada.fem.exceptions import IncompatibleElements
from ada.fem.sections import CABLE_COMPRESSION_RATIO

from .theory_models import (
    cable_and_truss,
    material,
    membrane_square,
    rod,
    two_bar_truss,
)


def _line_set():
    p = ada.Part("p")
    n1 = p.fem.nodes.add(ada.Node((0, 0, 0), 1))
    n2 = p.fem.nodes.add(ada.Node((0, 0, 3), 2))
    el = p.fem.add_elem(Elem(1, [n1, n2], "LINE"))
    return p, p.fem.add_set(FemSet("l", [el], "elset"))


def test_the_defaults_are_a_bending_shell_and_a_beam():
    _, p, _ = membrane_square(theory=None)
    assert p.fem.sections.shells[0].theory is ShellTheory.SHELL
    p2, fs = _line_set()
    sec = FemSection("b", "line", fs, material(), section=rod(), local_y=(1, 0, 0))
    assert sec.theory is LineTheory.BEAM
    assert not sec.is_axial_only and not sec.is_cable


def test_a_theory_is_given_as_the_enum_or_its_value():
    _, p, _ = membrane_square(theory="membrane")
    assert p.fem.sections.shells[0].is_membrane
    _, p, _ = membrane_square(theory=ShellTheory.MEMBRANE)
    assert p.fem.sections.shells[0].theory is ShellTheory.MEMBRANE


def test_a_theory_of_another_section_type_is_refused():
    _, fs = _line_set()
    with pytest.raises(ValueError, match="theory is one of beam, truss, cable"):
        FemSection("x", "line", fs, material(), section=rod(), local_y=(1, 0, 0), theory="membrane")
    with pytest.raises(ValueError, match="takes no theory"):
        FemSection("x", "solid", fs, material(), theory="truss")


def test_a_truss_needs_no_orientation_but_an_area():
    _, fs = _line_set()
    sec = FemSection("t", "line", fs, material(), section=rod(), theory="truss")
    assert sec.area == pytest.approx(np.pi * 0.01**2)
    # A vertical bar: any normal will do, and it is one.
    y = sec.local_y
    assert np.dot(y, [0, 0, 1]) == pytest.approx(0.0) and np.linalg.norm(y) == pytest.approx(1.0)
    assert np.linalg.norm(sec.local_z) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="needs a section"):
        FemSection("t2", "line", fs, material(), theory="cable")


def test_pretension_is_for_cables_and_membranes_only():
    _, fs = _line_set()
    with pytest.raises(ValueError, match="cable and membrane sections only"):
        FemSection("t", "line", fs, material(), section=rod(), theory="truss", pretension=10.0)
    with pytest.raises(ValueError, match="compression_ratio is for cable"):
        FemSection("t", "line", fs, material(), section=rod(), theory="truss", compression_ratio=0.1)
    cable = FemSection("c", "line", fs, material(), section=rod(), theory="cable", pretension=10.0)
    assert (cable.pretension, cable.compression_ratio) == (10.0, CABLE_COMPRESSION_RATIO)
    _, p, _ = membrane_square(pretension=500.0)
    assert p.fem.sections.shells[0].pretension == 500.0


def test_changing_the_theory_drops_what_the_new_one_does_not_have():
    _, fs = _line_set()
    sec = FemSection("c", "line", fs, material(), section=rod(), theory="cable", pretension=10.0)
    sec.theory = "truss"
    assert sec.pretension == 0.0
    with pytest.raises(ValueError, match="pretension is for cable and membrane"):
        sec.pretension = 1.0
    with pytest.raises(ValueError, match="needs local_y or local_z"):
        sec.theory = "beam"


def test_sections_of_different_theories_are_not_equal():
    _, fs = _line_set()
    t = FemSection("t", "line", fs, material(), section=rod(), theory="truss")
    c = FemSection("c", "line", fs, t.material, section=t.section, theory="cable")
    assert not t.has_equal_props(c)
    assert t.has_equal_props(FemSection("t2", "line", fs, t.material, section=t.section, theory="truss"))


def test_a_concept_names_its_theory_in_metadata():
    pytest.importorskip("gmsh")
    from ada.fem.formulations import FEM_THEORY_KEY

    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.002, mat=material())
    pl.metadata[FEM_THEORY_KEY] = ShellTheory.MEMBRANE
    bm = ada.Beam("bm", (0, 0, 1), (1, 0, 1), "CIRC10", material())
    bm.metadata[FEM_THEORY_KEY] = "cable"
    p = ada.Part("p") / [pl, bm]
    ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5)
    assert [s.theory for s in p.fem.sections.shells] == [ShellTheory.MEMBRANE]
    assert [s.theory for s in p.fem.sections.lines] == [LineTheory.CABLE]


@pytest.mark.parametrize("fem_format", ["sesam", "usfos"])
def test_writers_without_the_elements_refuse_them(fem_format, tmp_path):
    a, _, _ = membrane_square()
    with pytest.raises(IncompatibleElements, match="membrane section"):
        a.to_fem("m", fem_format, scratch_dir=tmp_path, overwrite=True)
    a, _ = cable_and_truss(1000.0)
    with pytest.raises(IncompatibleElements, match="(cable|truss) section"):
        a.to_fem("c", fem_format, scratch_dir=tmp_path, overwrite=True)


def test_opencourant_writes_a_truss_and_a_membrane_and_refuses_a_cable(tmp_path):
    a, _, _ = two_bar_truss("truss")
    a.to_fem("t", "opencourant", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "t" / "t_0000.rad").read_text()
    assert "/PROP/TRUSS/1" in deck and "/TRUSS/1" in deck
    rows = deck.split("/PROP/TRUSS/1\n")[1].splitlines()
    assert float(rows[2][:20]) == pytest.approx(np.pi * 0.01**2)
    elems = deck.split("\n/TRUSS/1\n")[1].splitlines()[:2]
    assert [[int(r[i : i + 10]) for i in range(0, 30, 10)] for r in elems] == [[1, 1, 2], [2, 2, 3]]

    a, _, _ = two_bar_truss("cable")
    with pytest.raises(IncompatibleElements, match="cable section"):
        a.to_fem("c", "opencourant", scratch_dir=tmp_path, overwrite=True)
    # a membrane is a shell with one integration point through the thickness: no bending
    a, _, _ = membrane_square()
    a.to_fem("m", "opencourant", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "m" / "m_0000.rad").read_text()
    rows = [r for r in deck.split("/PROP/SHELL/1\n")[1].splitlines() if not r.startswith("#")]
    assert int(rows[3][:10]) == 1 and float(rows[3][20:40]) == pytest.approx(0.002)
    a, _, _ = membrane_square(pretension=100.0)
    with pytest.raises(NotImplementedError):
        a.to_fem("mp", "opencourant", scratch_dir=tmp_path, overwrite=True)
