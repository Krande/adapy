"""Point masses reach the Code_Aster command file: a DIS_T model on the mass's POI1 group and its mass in
AFFE_CARA_ELEM. They used to be written to the .med file only, so Code_Aster solved without them.

Solved against the closed form and Sestra in tests/fem/test_code_aster_point_mass.py.
"""

from __future__ import annotations

import re

import ada
from ada.fem import Bc, FemSet, Load, Mass, StepImplicitStatic
from ada.materials.metals import CarbonSteel


def _model(mass) -> ada.Assembly:
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("pole", (0, 0, 0), (0, 0, 4), "TUB300x12", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(1.0, "line")
    base = [n for n in p.fem.nodes if abs(n.z) < 1e-9]
    top = [n for n in p.fem.nodes if abs(n.z - 4) < 1e-9]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("base", base, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    ptype = Mass.PTYPES.ANISOTROPIC if isinstance(mass, list) else None
    p.fem.add_mass(Mass("tip_mass", p.fem.add_set(FemSet("tip", top, FemSet.TYPES.NSET)), mass, ptype=ptype))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("acc"))
    step.add_load(Load("acc_x", Load.TYPES.GRAVITY, 2.0, dof=[1, 0, 0]))
    return a


def _comm(tmp_path, mass) -> str:
    _model(mass).to_fem("pm", "code_aster", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / "pm" / "pm.comm").read_text()


def test_isotropic_point_mass(tmp_path):
    comm = _comm(tmp_path, 1000.0)
    group = re.search(r"_F\(GROUP_MA=\('(\w+)',\), PHENOMENE='MECANIQUE', MODELISATION='DIS_T',\)", comm).group(1)
    assert f"_F(GROUP_MA='{group}', CARA='M_T_D_N', VALE=1000.0)" in comm
    assert f"_F(GROUP_MA='{group}', CARA='K_T_D_N', VALE=(0.0, 0.0, 0.0))" in comm
    assert "CARA_ELEM=element" in comm.split("MACRO_ELAS_MULT")[-1] or "CARA_ELEM=element" in comm.split("MECA_STATIQUE")[-1]


def test_anisotropic_point_mass(tmp_path):
    comm = _comm(tmp_path, [1.0, 2.0, 3.0])
    assert "CARA='M_T_N', VALE=(1.0, 0.0, 0.0, 2.0, 0.0, 3.0)" in comm
