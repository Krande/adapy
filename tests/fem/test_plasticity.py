"""Von Mises plasticity in a general static step, against the uniaxial closed form, in Code_Aster.

A solid bar ``a x b x L`` (HEX8), held only against rigid motion at its bottom (z along, one corner in x and y, the
next in y) so it may contract sideways, is stretched by a prescribed top displacement ``d``: the strain is uniform
and uniaxial, ``eps = d / L``. Bilinear hardening from the yield stress ``s_y`` with plastic modulus ``H``
(adapy's curve is Abaqus' ``*Plastic``: stress against plastic strain), so

``sigma = (eps + s_y / H) / (1 / E + 1 / H)`` past yield, ``E eps`` before it,

and the bottom reaction is ``sigma a b``, exact for linear hardening at a converged increment. The writer used to
solve a general step of a plastic material linearly (MECA_STATIQUE) -- ``E eps a b`` -- and wrote its curve with
the plastic strains as total ones and a yield of ``E 1e-5`` (2.1 MPa).
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import Bc, Elem, FemSection, FemSet, StepImplicitStatic
from ada.fem.shapes.definitions import SolidShapes
from ada.materials.metals import CarbonSteel
from ada.materials.metals.plasticity_models import PlasticityModel

E, SY, H = 2.1e11, 3.55e8, 1.0e10
A_, B_, L = 0.1, 0.2, 1.0


def _bar(d: float):
    mat = ada.Material("pl", CarbonSteel("S355", E=E, v=0.3))
    mat.model.plasticity_model = PlasticityModel(sig_p=[SY, SY + H * 0.05], eps_p=[0.0, 0.05])
    p = ada.Part("bar")
    fem = p.fem
    nodes, nid = {}, 1
    nz = 2
    for k in range(nz + 1):
        for j in range(2):
            for i in range(2):
                nodes[i, j, k] = fem.nodes.add(ada.Node((A_ * i, B_ * j, L * k / nz), nid))
                nid += 1
    els = []
    for k in range(nz):
        hexa = [nodes[di, dj, k + dk] for dk in (0, 1) for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1))]
        els.append(fem.add_elem(Elem(k + 1, hexa, SolidShapes.HEX8)))
    fs = fem.add_set(FemSet("solid", els, "elset"))
    fem.add_section(FemSection("sec", "solid", fs, mat))
    p.add_material(mat)
    a = ada.Assembly("a") / p
    bottom = [n for (i, j, k), n in nodes.items() if k == 0]
    top = [n for (i, j, k), n in nodes.items() if k == nz]
    fem.add_bc(Bc("bz", fem.add_set(FemSet("bottom", bottom, "nset")), [3]))
    fem.add_bc(Bc("bxy", fem.add_set(FemSet("c0", [nodes[0, 0, 0]], "nset")), [1, 2]))
    fem.add_bc(Bc("by", fem.add_set(FemSet("c1", [nodes[1, 0, 0]], "nset")), [2]))
    step = a.fem.add_step(StepImplicitStatic("pull", init_incr=0.1, total_time=1.0, max_incr=0.1))
    step.add_bc(Bc("pull", fem.add_set(FemSet("top", top, "nset")), [3], magnitudes=[d]))
    return a, bottom


def _sigma(eps: float) -> float:
    if E * eps <= SY:
        return E * eps
    return (eps + SY / H) / (1 / E + 1 / H)


@pytest.mark.parametrize("strain", [0.001, 0.004, 0.02], ids=["elastic", "just_past_yield", "far_past_yield"])
def test_a_bar_pulled_past_yield_carries_the_hardening_curves_stress(tmp_path, require_solver, strain):
    require_solver("code_aster")
    a, bottom = _bar(strain * L)
    res = a.to_fem("pl", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    reac = next(r for r in res.results if "REAC_NODA" in r.name and r.step == max(x.step for x in res.results))
    import numpy as np

    v = np.asarray(reac.values, float)
    ids = {int(n.id) for n in bottom}
    rz = sum(row[3] for row in v if int(row[0]) in ids)
    assert -rz == pytest.approx(_sigma(strain) * A_ * B_, rel=1e-6)
