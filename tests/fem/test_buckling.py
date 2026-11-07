"""Linear (Euler) buckling (``StepBuckling``) against Euler's column, in Code_Aster (and Sestra, where installed).

A pinned-pinned IPE300 column of length L along z, pinned at both ends (the top free to move along z, both ends held
against twisting at the bottom), pressed by a unit axial force at the top. Its critical load is Euler's
``P_cr = pi^2 E I / L^2`` about the weak axis (``I = Iz``), so the smallest critical load factor of the unit load is
``P_E`` itself, and the second flexural mode ``4 P_E``. Between them the open section twists: with the column free to
twist at the top and a beam without warping stiffness (Code_Aster's POU_D_E), pure torsional buckling sets in at
``P_T = G J A / (Iy + Iz)`` -- ``G J`` over the polar radius of gyration squared -- twice (measured, Code_Aster
18.1: 346994, 986318, 986318, 1387990 against 346994, 986318, 1387976). 0.25 m elements on a 6 m column keep the
discretisation error under 1e-4.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, StepBuckling
from ada.materials.metals import CarbonSteel

L = 6.0


def _column(n_modes=2):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("col", (0, 0, 0), (0, 0, L), "IPE300", mat, up=(1, 0, 0))
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (bot,) = [n for n in fem.nodes if abs(n.z) < 1e-9]
    (top,) = [n for n in fem.nodes if abs(n.z - L) < 1e-9]
    fem.add_bc(Bc("pin_bot", fem.add_set(FemSet("bot", [bot], "nset")), [1, 2, 3, 6]))
    fem.add_bc(Bc("pin_top", fem.add_set(FemSet("top", [top], "nset")), [1, 2]))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepBuckling("buckle", num_modes=n_modes))
    step.add_load(LoadPoint("press", -1.0, fem.sets.get_nset_from_name("top"), 3))
    props = bm.section.properties
    euler = np.pi**2 * mat.model.E * min(props.Iy, props.Iz) / L**2
    return a, euler, props, mat


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_pinned_column_buckles_at_eulers_load(tmp_path, require_solver, solver):
    require_solver(solver)
    a, euler, props, mat = _column(n_modes=4)
    res = a.to_fem("buck", solver, scratch_dir=tmp_path, overwrite=True, execute=True)
    factors = res.buckling_factors
    assert factors is not None and len(factors) >= 4, factors
    got = sorted(abs(f) for f in factors)[:4]
    # the positive factor is the multiplier of the reference load (a compression here): no sign to undo
    assert all(f > 0 for f in factors[:4]), factors
    g = mat.model.E / (2 * (1 + mat.model.v))
    if solver == "sesam":
        # Sestra's beam (BEAS) is shear-deformable and its geometric stiffness has no torsional term: the factors
        # are the flexural Timoshenko loads P / (1 + P / (G As)) -- weak axis (Iz, shear area SHARY) n = 1, 2, 3,
        # then the strong axis (Iy, SHARZ) n = 1. Measured, Sestra 11.3-01: 1 + 3.4e-6, 5.3e-5, 2.6e-4 (the mesh's
        # error, growing as n^4), 8.4e-5.
        def timoshenko(n, inertia, shear_area):
            p = n**2 * np.pi**2 * mat.model.E * inertia / L**2
            return p / (1 + p / (g * shear_area))

        weak, strong = (props.Iz, props.Shary), (props.Iy, props.Sharz)
        want = sorted([timoshenko(1, *weak), timoshenko(2, *weak), timoshenko(3, *weak), timoshenko(1, *strong)])
        assert got == pytest.approx(want, rel=1e-3)
        return
    torsion = g * props.Ix * props.Ax / (props.Iy + props.Iz)
    assert got == pytest.approx([euler, torsion, torsion, 4 * euler], rel=1e-3)


def test_the_decks_hold_the_buckling_step(tmp_path):
    """No solver: Abaqus a *Buckle perturbation step asking for the modes; Sestra CMAS LBCK = 1 and an EIGA with a
    small positive shift (Sestra UM 2.1.7), the reference load the deck's load case."""
    a, *_ = _column(n_modes=3)
    a.to_fem("b", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = "".join(p.read_text() for p in (tmp_path / "b").rglob("*.inp"))
    assert "perturbation" in inp and "\n*Buckle\n3, , , ,\n" in inp
    a, *_ = _column(n_modes=3)
    a.to_fem("s", "sesam", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "s" / "sestra.inp").read_text().splitlines()
    (cmas,) = [ln for ln in inp if ln.startswith("CMAS")]
    assert cmas.split()[7] == "1."  # LBCK
    (eiga,) = [ln for ln in inp if ln.startswith("EIGA")]
    # Sestra's fixed columns: ENR in 6-10, SHIFT in 71-80. A shift straddling 61-80 was read as EHIGH and SHIFT 1.
    assert eiga[5:10].strip() == "3." and eiga[60:70].strip() == "" and float(eiga[70:80]) > 0
    # NLVEC (21-25) four Lanczos vectors a mode: Sestra's default two gave an unconverged 4th factor (write_steps)
    assert eiga[20:25].strip() == "12."
    deck = next((tmp_path / "s").glob("*T1.FEM")).read_text()
    assert "TDLOAD" in deck and "BNLOAD" in deck
