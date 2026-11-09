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


def _shell_strip(d: float, order: int = 1, nl_geom: bool = False):
    """A DKT strip 1 x 0.2 m, 10 mm, held along x at x = 0 (and in y at one node), flat (dz, rotations held), its
    far edge pulled along x by ``d``: uniaxial plane stress, free to contract sideways."""
    mat = ada.Material("pl_sh", CarbonSteel("S355", E=E, v=0.3))
    mat.model.plasticity_model = PlasticityModel(sig_p=[SY, SY + H * 0.05], eps_p=[0.0, 0.05])
    t, b = 0.01, 0.2
    pl = ada.Plate("strip", [(0, 0), (L, 0), (L, b), (0, b)], t, mat=mat)
    p = ada.Part("S") / pl
    from ada.fem.meshing import GmshOptions

    p.fem = p.to_fem_obj(0.1, use_quads=True, options=GmshOptions(Mesh_ElementOrder=order))
    fem = p.fem
    a_side = [n for n in fem.nodes if abs(n.x) < 1e-9]
    b_side = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("ax", fem.add_set(FemSet("a_side", a_side, "nset")), [1]))
    (a0,) = [n for n in a_side if abs(n.y) < 1e-9]
    fem.add_bc(Bc("ay", fem.add_set(FemSet("a0", [a0], "nset")), [2]))
    fem.add_bc(Bc("flat", fem.add_set(FemSet("all", list(fem.nodes), "nset")), [3, 4, 5, 6]))
    a = ada.Assembly("a") / p
    step = a.fem.add_step(StepImplicitStatic("pull", init_incr=0.1, total_time=1.0, max_incr=0.1, nl_geom=nl_geom))
    step.add_bc(Bc("pull", fem.add_set(FemSet("b_side", b_side, "nset")), [1], magnitudes=[d]))
    return a, a_side, t * b


@pytest.mark.parametrize("order", [1, 2], ids=["dkt", "coque_3d"])
@pytest.mark.parametrize("strain", [0.001, 0.004, 0.02], ids=["elastic", "just_past_yield", "far_past_yield"])
def test_a_shell_strip_pulled_past_yield_carries_the_hardening_curves_stress(tmp_path, require_solver, strain, order):
    """The same uniaxial closed form on shells, plastic through 5 layers: first-order (DKT) and second-order
    (COQUE_3D). A shell of a plastic material used to be solved elastic (reported), and a second-order one still was."""
    import numpy as np

    require_solver("code_aster")
    a, a_side, area = _shell_strip(strain * L, order)
    res = a.to_fem("plsh", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    last = max(r.step for r in res.results if "REAC_NODA" in r.name)
    reac = next(r for r in res.results if "REAC_NODA" in r.name and r.step == last)
    v = np.asarray(reac.values, float)
    ids = {int(n.id) for n in a_side}
    rx = sum(row[1] for row in v if int(row[0]) in ids)
    assert -rx == pytest.approx(_sigma(strain) * area, rel=1e-6)


def test_a_plastic_second_order_shell_keeps_its_large_rotations_in_an_nlgeom_step(tmp_path, require_solver):
    """A plastic COQUE_3D in an nlgeom step: its plastic entry is GROT_GDEP too -- an entry later in COMPORTEMENT
    overrides an earlier one for the same group, and a PETIT one after the elastic GROT_GDEP made it small-rotation
    without a word. Solved past yield it converges, the root carrying the curve's stress to within the strain
    measure's difference: GROT_GDEP's reaction is nominal, its stress the second Piola-Kirchhoff one, about (1 + eps)
    apart (measured, 18.1: +4.2e-3 at eps = 4e-3)."""
    import numpy as np

    strain = 0.004
    a, a_side, area = _shell_strip(strain * L, order=2, nl_geom=True)
    a.to_fem("deck", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "deck" / "deck.comm").read_text()
    assert 'RELATION="VMIS_ISOT_TRAC", DEFORMATION="GROT_GDEP"' in comm
    assert 'RELATION="VMIS_ISOT_TRAC", DEFORMATION="PETIT"' not in comm

    require_solver("code_aster")
    res = a.to_fem("plnl", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    last = max(r.step for r in res.results if "REAC_NODA" in r.name)
    reac = next(r for r in res.results if "REAC_NODA" in r.name and r.step == last)
    v = np.asarray(reac.values, float)
    rx = sum(row[1] for row in v if int(row[0]) in {int(n.id) for n in a_side})
    assert -rx == pytest.approx(_sigma(strain) * area, rel=2 * strain)
