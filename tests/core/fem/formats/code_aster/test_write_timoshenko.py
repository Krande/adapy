"""``options.CODE_ASTER.use_timoshenko_beams``: beams with shear areas are written ``POU_D_T`` with their shear
coefficients ``AY = A / Shary`` and ``AZ = A / Sharz``; without it every beam stays ``POU_D_E`` with ``A, IY, IZ, JX``.

Solved against the closed form and Sestra in tests/fem/test_code_aster_beam_shear.py.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import Bc, FemSet, StepImplicitStatic


def _comm(tmp_path, timoshenko: bool) -> tuple[str, ada.Beam]:
    bm = ada.Beam("bm", (0, 0, 0), (1.5, 0, 0), "IPE300")
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    root = [n for n in p.fem.nodes if abs(n.x) < 1e-9]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("root", root, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    p.fem.options.CODE_ASTER.use_timoshenko_beams = timoshenko
    a = ada.Assembly("A") / p
    a.fem.add_step(StepImplicitStatic("grav")).add_load(ada.fem.LoadGravity("g", -9.81))
    a.to_fem("tb", "code_aster", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / "tb" / "tb.comm").read_text(), bm


def test_timoshenko_beams_carry_shear_coefficients(tmp_path):
    comm, bm = _comm(tmp_path, True)
    p = bm.section.properties
    assert "MODELISATION='POU_D_T'" in comm and "POU_D_E" not in comm
    assert "CARA = ('A', 'IY', 'IZ', 'JX', 'AY', 'AZ')" in comm
    values = comm.split("CARA = ('A', 'IY', 'IZ', 'JX', 'AY', 'AZ'),")[1].split("VALE = (")[1].split(")")[0]
    ay, az = (float(v) for v in values.split(",")[4:6])
    assert ay == pytest.approx(p.Ax / p.Shary) and az == pytest.approx(p.Ax / p.Sharz)


def test_default_stays_euler_bernoulli(tmp_path):
    comm, _ = _comm(tmp_path, False)
    assert "MODELISATION='POU_D_E'" in comm and "POU_D_T" not in comm
    assert "CARA = ('A', 'IY', 'IZ', 'JX')," in comm
