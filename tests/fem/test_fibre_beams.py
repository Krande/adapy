"""Plastic beams in Code_Aster as multi-fibre beams (``POU_D_EM``, ``ada.fem.formats.code_aster.write.write_fibres``),
against the moment-curvature closed form.

A 2 m IPE300 cantilever along x, its root clamped, its tip turned about y by a prescribed ``theta``: pure bending at a
uniform curvature ``kappa = theta / L`` about the strong axis, so the root reacts the moment the section carries at
that curvature, ``M = integral(sigma(kappa z) z b(z) dz)`` -- bilinear hardening from ``s_y`` with plastic modulus
``H`` (the material's ``*Plastic`` curve), integrated over the exact flanges and web. Past yield the beam used to be
solved elastic (reported); the moment would be ``E I kappa``.

Measured, Code_Aster 18.1, 0.25 m elements: -1.7e-4 at theta = 0.01 (elastic; the fibres' lumped inertia), -5.1e-5
at 0.05 and -1.1e-5 at 0.1, where solved elastic it would have been 1.9 and 3.5 times the moment.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, StepImplicitStatic
from ada.materials.metals import CarbonSteel
from ada.materials.metals.plasticity_models import PlasticityModel

E, SY, H = 2.1e11, 3.55e8, 1.0e10
L = 2.0


def _sigma(eps):
    """The uniaxial stress: ``E eps`` to yield, then rising from ``s_y`` with the tangent ``E H / (E + H)``."""
    eps = np.asarray(eps, dtype=float)
    ey, et = SY / E, E * H / (E + H)
    return np.where(np.abs(eps) <= ey, E * eps, np.sign(eps) * (SY + et * (np.abs(eps) - ey)))


def _moment(section, kappa, n=200000):
    """The moment of the I section at curvature ``kappa``, the stress integrated over its flanges and web."""
    h, b, tf, tw = section.h, section.w_top, section.t_ftop, section.t_w
    z = (np.arange(n) + 0.5) / n * h - h / 2
    width = np.where(np.abs(z) > h / 2 - tf, b, tw)
    return float(np.sum(_sigma(kappa * z) * z * width) * h / n)


def _cantilever(theta):
    mat = ada.Material("pl", CarbonSteel("S355", E=E, v=0.3))
    mat.model.plasticity_model = PlasticityModel(sig_p=[SY, SY + H * 0.05], eps_p=[0.0, 0.05])
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat, up=(0, 0, 1))
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("root", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("bend", init_incr=0.05, total_time=1.0, max_incr=0.05))
    step.add_bc(Bc("turn", fem.add_set(FemSet("tip", [tip], "nset")), [5], magnitudes=[theta]))
    return a, root, bm.section


@pytest.mark.parametrize("theta", [0.01, 0.05, 0.1], ids=["elastic", "past_yield", "far_past_yield"])
def test_a_plastic_beam_bent_past_yield_carries_the_sections_moment(tmp_path, require_solver, theta):
    require_solver("code_aster")
    a, root, section = _cantilever(theta)
    res = a.to_fem("mf", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    last = max(r.step for r in res.results if "REAC_NODA" in r.name)
    reac = next(r for r in res.results if "REAC_NODA" in r.name and r.step == last)
    (row,) = [r for r in np.asarray(reac.values, float) if int(r[0]) == int(root.id)]
    want = _moment(section, theta / L)
    assert abs(row[5]) == pytest.approx(want, rel=5e-4), (row[5], want, E * section.properties.Iy * theta / L)


def test_the_deck_holds_the_fibres_and_their_behaviour(tmp_path):
    """No solver: POU_D_EM, the fibres summing to the section's area, a DEFI_COMPOR of VMIS_ISOT_TRAC on them, given
    in the material field, and RELATION='MULTIFIBRE' in the step."""
    from ada.fem.formats.code_aster.write.write_fibres import section_fibres

    a, _, section = _cantilever(0.05)
    a.to_fem("d", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "d" / "d.comm").read_text()
    assert "MODELISATION='POU_D_EM'" in comm and "DEFI_GEOM_FIBRE(" in comm
    assert "RELATION='VMIS_ISOT_TRAC'" in comm and "AFFE_COMPOR=(" in comm and 'RELATION="MULTIFIBRE"' in comm
    f = section_fibres(section)
    assert f[:, 2].sum() == pytest.approx(section.properties.Ax, rel=1e-2)
    iy = float((f[:, 2] * f[:, 1] ** 2).sum())
    assert iy == pytest.approx(section.properties.Iy, rel=1e-2)
