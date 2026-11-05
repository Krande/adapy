"""A point mass loads the structure in an acceleration field: Code_Aster against the closed form and Sestra.

A 4 m vertical cantilever (a 300 x 12 mm tube, S355, clamped at its base) carries a 1000 kg point mass at its top
and is accelerated 2 m/s^2 along x. The base reaction is ``(rho A L + m) a`` and the tip moves
``q L^4 / (8 EI) + F L^3 / (3 EI)`` (Euler-Bernoulli; shear adds 0.1 % here), ``q = rho A a`` and ``F = m a``.

Measured before the fix (Code_Aster 18.1.11): the .med file held the mass's POI1 cell but the command file never
modelled it, so the mass did not exist for Code_Aster: the base reaction was the tube's own, 1.392 kN of 3.392 kN.
Sestra (V11.3-01) gives the closed form, the Timoshenko one (its beams have shear deformation).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, Load, Mass, StepImplicitStatic
from ada.materials.metals import CarbonSteel

H, M, ACC = 4.0, 1000.0, 2.0


def _model() -> ada.Assembly:
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("pole", (0, 0, 0), (0, 0, H), "TUB300x12", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    base = [n for n in p.fem.nodes if abs(n.z) < 1e-9]
    top = [n for n in p.fem.nodes if abs(n.z - H) < 1e-9]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("base", base, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    p.fem.add_mass(Mass("tip_mass", p.fem.add_set(FemSet("tip", top, FemSet.TYPES.NSET)), M))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("acc"))
    step.add_load(Load("acc_x", Load.TYPES.GRAVITY, ACC, dof=[1, 0, 0]))
    return a


def _expected(a: ada.Assembly) -> tuple[float, float, float]:
    """The base reaction along x, and the tip displacement along x without and with shear deformation (Euler-Bernoulli
    and Timoshenko, shear area ``Shary``), closed form."""
    bm = next(iter(a.get_all_physical_objects(by_type=ada.Beam)))
    props = bm.section.properties
    rho, E, nu = bm.material.model.rho, bm.material.model.E, bm.material.model.v
    q, F = rho * props.Ax * ACC, M * ACC
    I, GAs = props.Iy, E / (2 * (1 + nu)) * props.Shary
    bending = q * H**4 / (8 * E * I) + F * H**3 / (3 * E * I)
    shear = q * H**2 / (2 * GAs) + F * H / GAs
    return q * H + F, bending, bending + shear


@pytest.mark.parametrize("fem_format", ["code_aster", "sesam"])
def test_point_mass_in_acceleration_field(tmp_path, require_solver, fem_format):
    require_solver(fem_format)
    a = _model()
    reaction, tip_euler, tip_timoshenko = _expected(a)
    # Sestra's beam has shear deformation (4.4 % of the tip displacement here); Code_Aster's POU_D_E has none.
    tip = tip_timoshenko if fem_format == "sesam" else tip_euler
    res = a.to_fem("pm", fem_format, scratch_dir=tmp_path, overwrite=True, execute=True)
    if res is None:
        from ada.fem.formats.utils import default_fem_res_path

        res = ada.from_fem_res(default_fem_res_path("pm", scratch_dir=tmp_path, fem_format=fem_format))
    disp_name = {"code_aster": "DEPL", "sesam": "RVNODDIS"}[fem_format]
    reac_name = {"code_aster": "REAC_NODA", "sesam": "REACTION-FORCE"}[fem_format]
    disp = next(np.asarray(r.values, float) for r in res.results if disp_name in r.name)
    reac = next(np.asarray(r.values, float) for r in res.results if reac_name in r.name)
    assert -np.nansum(reac[:, 1]) == pytest.approx(reaction, rel=1e-6)
    assert np.nanmax(np.abs(disp[:, 1])) == pytest.approx(tip, rel=1e-3)
