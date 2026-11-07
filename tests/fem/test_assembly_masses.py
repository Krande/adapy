"""A point mass on the assembly (``assembly.fem.add_mass`` on a part's node) reaches every single-part writer.

Sesam, Code_Aster and CalculiX write one part's FEM: a mass held on the assembly's own FEM was left out -- by Sesam
reported, by Code_Aster and CalculiX without a word (and CalculiX wrote no point mass at all, on a part either). The
model is merged for them (``ada.fem.concat.single_part_assembly``), the mass re-keyed onto the part's node.

The check: a 4 m IPE300 cantilever of near-massless steel with 1000 kg on its tip swings about its weak axis at
``f = sqrt(3 E Iz / (M L^3)) / (2 pi)`` -- exact as the beam's mass goes to nothing (its shear, 4e-4 of the frequency
here, inside the tolerance).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, Mass, StepEigen
from ada.materials.metals import CarbonSteel

L, M = 4.0, 1000.0


def _model():
    mat = ada.Material("S355", CarbonSteel("S355", rho=1e-3))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("fix", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    a.fem.add_mass(Mass("tip_mass", a.fem.add_set(FemSet("tipm", [tip], "nset")), M))
    a.fem.add_step(StepEigen("eig", num_eigen_modes=2))
    want = np.sqrt(3 * mat.model.E * bm.section.properties.Iz / (M * L**3)) / (2 * np.pi)
    return a, want


@pytest.mark.parametrize("solver", ["code_aster", "calculix", "sesam"])
def test_an_assembly_level_tip_mass_sets_the_cantilevers_frequency(tmp_path, require_solver, solver):
    require_solver(solver)
    a, want = _model()
    res = a.to_fem("tm", solver, scratch_dir=tmp_path, overwrite=True, execute=True)
    freqs = sorted(m.f_hz for m in res.get_eig_summary().modes)
    assert freqs[0] == pytest.approx(want, rel=2e-3), (freqs, want)


@pytest.mark.parametrize("fmt", ["code_aster", "calculix", "sesam"])
def test_the_decks_hold_the_assembly_level_mass(tmp_path, fmt):
    """No solver: Code_Aster's DIS_T M_T_D_N, CalculiX's *Mass, Sesam's MGMASS -- each with the 1000 kg."""
    from ada.fem.formats import conversion_report

    a, _ = _model()
    with conversion_report.collect() as rep:
        a.to_fem("d", fmt, scratch_dir=tmp_path, overwrite=True)
    text = "".join(
        p.read_text(errors="replace") for p in (tmp_path / "d").iterdir() if p.suffix in (".comm", ".inp", ".FEM")
    )
    if fmt == "code_aster":
        assert "M_T_D_N', VALE=1000.0" in text
    elif fmt == "calculix":
        assert "*Element, type=MASS" in text and "*Mass, elset=ADA_MASS_tip_mass\n 1.0000000000000E+03" in text
    else:
        assert "MGMASS" in text
    assert not [f for f in rep.findings if f.kind == "omitted" and f.keyword == "Mass"]
