"""With ``options.CODE_ASTER.use_timoshenko_beams`` Code_Aster's beams have shear deformation, as Sestra's do: a short IPE
cantilever against the Timoshenko closed form.

A 1.5 m IPE300 cantilever along x (S355, clamped at x = 0) carries 10 kN along y and 10 kN along z at its tip, each
in a step of its own. Its tip moves ``F L^3 / (3 E I) + F L / (G A_s)``: about the weak axis (load along y) with
``Iz`` and the flanges' shear area ``Shary``, about the strong axis (along z) with ``Iy`` and the web's ``Sharz``.
Shear adds 14 % to the strong-axis deflection here, 1 % to the weak.

Measured before the option (Code_Aster 18.1.11): the writer could only model a beam ``POU_D_E`` (Euler-Bernoulli)
with ``A, IY, IZ, JX``, so Code_Aster's tip deflection was the bending term alone -- 12.8 % short along z (0.670 mm
against 0.768 mm) and 1.0 % along y of the closed form and of Sestra (V11.3-01), which matches it. The default stays
``POU_D_E``; the Euler-Bernoulli expectations of tests/fem/test_calculix_code_aster_solve.py hold without the option.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, StepImplicitStatic
from ada.materials.metals import CarbonSteel

L, F = 1.5, 1.0e4


def _model(dof: int) -> ada.Assembly:
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    root = [n for n in p.fem.nodes if abs(n.x) < 1e-9]
    tip = [n for n in p.fem.nodes if abs(n.x - L) < 1e-9]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("root", root, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    tip_set = p.fem.add_set(FemSet("tip", tip, FemSet.TYPES.NSET))
    p.fem.options.CODE_ASTER.use_timoshenko_beams = True
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("tip_load"))
    step.add_load(ada.fem.LoadPoint("f", F, tip_set, dof))
    return a


def _expected(a: ada.Assembly, dof: int) -> float:
    bm = next(iter(a.get_all_physical_objects(by_type=ada.Beam)))
    p, mat = bm.section.properties, bm.material.model
    G = mat.E / (2 * (1 + mat.v))
    inertia, shear_area = (p.Iz, p.Shary) if dof == 2 else (p.Iy, p.Sharz)
    return F * L**3 / (3 * mat.E * inertia) + F * L / (G * shear_area)


@pytest.mark.parametrize("dof", [2, 3], ids=["along_y", "along_z"])
@pytest.mark.parametrize("fem_format", ["code_aster", "sesam"])
def test_cantilever_tip_has_shear_deformation(tmp_path, require_solver, fem_format, dof):
    require_solver(fem_format)
    a = _model(dof)
    res = a.to_fem("sh", fem_format, scratch_dir=tmp_path, overwrite=True, execute=True)
    if res is None:
        from ada.fem.formats.utils import default_fem_res_path

        res = ada.from_fem_res(default_fem_res_path("sh", scratch_dir=tmp_path, fem_format=fem_format))
    disp_name = {"code_aster": "DEPL", "sesam": "RVNODDIS"}[fem_format]
    disp = next(np.asarray(r.values, float) for r in res.results if disp_name in r.name)
    assert np.nanmax(np.abs(disp[:, dof])) == pytest.approx(_expected(a, dof), rel=5e-3)
