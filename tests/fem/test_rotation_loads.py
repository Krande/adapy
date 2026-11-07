"""A rotating frame's loads (``LoadRotation``) against closed forms, in Code_Aster and Sestra (where installed).

A bar along x, clamped at its root on the axis of rotation z, density ``rho``, area ``A``:

Sestra takes the angular velocity as one BNACCLO per node (the body's centripetal acceleration) and the angular
acceleration as BRIGAC -- both signs measured in Sestra 11.3-01 (the windows agent; BRIGVE, which this wrote first,
is not a Sestra load type).

* **Spinning** at ``w``: the centrifugal force per length ``rho A w^2 x`` pulls it outward. The root holds back
  ``R_x = -rho A w^2 L^2 / 2``, exact, and the tip stretches by ``rho w^2 L^3 / (3 E)`` (``N(x) = rho A w^2 (L^2 -
  x^2) / 2``), exact at the nodes of a two-node bar under the consistent load.
* **Spun up** at ``a``: the field's force per length ``rho A a x`` along +y (``a e_z x x e_x``). The root holds back
  ``R_y = -rho A a L^2 / 2`` and ``M_z = -rho A a L^3 / 3``. Code_Aster's ROTATION has no angular acceleration (it is
  refused by name there); Sestra's BRIGAC does -- and the sign it is written with is the SIF's wording, which this
  test measures.

Results are read by the FieldRole column layout ``[id, x, y, z, rx, ry, rz]``, the same for every solver.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadRotation, StepImplicitStatic
from ada.fem.exceptions.model_definition import UnsupportedLoadType
from ada.materials.metals import CarbonSteel

L = 4.0


def _bar(w=0.0, a=0.0):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.25, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("fix", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    asm = ada.Assembly("A") / p
    step = asm.fem.add_step(StepImplicitStatic("spin"))
    step.add_load(LoadRotation("rot", (0, 0, 0), (0, 0, 1), angular_velocity=w, angular_acceleration=a))
    rho, area, e = mat.model.rho, bm.section.properties.Ax, mat.model.E
    return asm, root, tip, rho * area, rho, e


def _fields(res):
    ((_, fields),) = res.case_fields("nodal_displacement", "nodal_reaction").items()
    out = []
    for role in ("nodal_displacement", "nodal_reaction"):
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == role]
        out.append({int(r[0]): np.asarray(r[1:7], float) for r in np.asarray(f.values, float)})
    return out


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_spinning_bar_is_pulled_out_by_its_centrifugal_force(tmp_path, require_solver, solver):
    require_solver(solver)
    w = 20.0
    asm, root, tip, rho_a, rho, e = _bar(w=w)
    u, reac = _fields(asm.to_fem("spin", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-5 if solver == "sesam" else 1e-6
    assert reac[int(root.id)][0] == pytest.approx(-rho_a * w**2 * L**2 / 2, rel=rel)
    assert u[int(tip.id)][0] == pytest.approx(rho * w**2 * L**3 / (3 * e), rel=1e-3)
    assert abs(reac[int(root.id)][1]) < 1e-6 * rho_a * w**2 * L**2


@pytest.mark.parametrize("solver", ["sesam"])
@pytest.mark.parametrize("on_a_set", [False, True], ids=["whole_model", "on_a_set"])
def test_a_spun_up_bar_is_pushed_along_the_turn(tmp_path, require_solver, solver, on_a_set):
    """The whole model's angular acceleration is one BRIGAC; on a set it is one BNACCLO per node, ``-a e x d``. Here
    the set is every node, so both must give the same closed form -- which measures the per-node convention."""
    require_solver(solver)
    a = 5.0
    asm, root, _, rho_a, _, _ = _bar(a=a)
    if on_a_set:
        fem = asm.get_part("P").fem
        ((load,),) = [st.loads for st in asm.fem.steps]
        load.fem_set = fem.add_set(FemSet("every_node", list(fem.nodes), "nset"))
    _, reac = _fields(asm.to_fem("spinup", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    r = reac[int(root.id)]
    assert r[1] == pytest.approx(-rho_a * a * L**2 / 2, rel=1e-5)
    assert r[5] == pytest.approx(-rho_a * a * L**3 / 3, rel=1e-5)


def test_code_aster_refuses_an_angular_acceleration_by_name(tmp_path):
    asm, *_ = _bar(w=1.0, a=1.0)
    with pytest.raises(UnsupportedLoadType, match="angular acceleration"):
        asm.to_fem("x", "code_aster", scratch_dir=tmp_path, overwrite=True)


def test_the_decks_hold_the_rotation(tmp_path):
    """No solver: Abaqus CENTRIF (w^2) and ROTA (a); Sesam BRIGVE (w e) and BRIGAC (-a e); Code_Aster ROTATION."""
    asm, *_ = _bar(w=3.0, a=2.0)
    asm.to_fem("d", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "d" / "d.inp").read_text()
    inp += "".join(p.read_text() for p in (tmp_path / "d").rglob("*.inp") if p.name != "d.inp")
    assert "\n, CENTRIF, 9.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0\n" in inp  # w^2, a point on the axis, its direction
    assert "\n, ROTA, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0\n" in inp
    asm.to_fem("s", "sesam", scratch_dir=tmp_path, overwrite=True)
    lines = next((tmp_path / "s").glob("*T1.FEM")).read_text().splitlines()
    # the body's centripetal acceleration -w^2 x along x, one BNACCLO per node (Sestra takes no BRIGVE)
    assert not [ln for ln in lines if ln.startswith("BRIGVE")]
    acc = {}
    for k, ln in enumerate(lines):
        if ln.startswith("BNACCLO"):
            nid, ndof, a1, a2 = (float(x) for x in lines[k + 1].split())
            acc[int(nid)] = a1
    xs = {int(n.id): n.x for n in asm.get_part("P").fem.nodes}
    assert acc and all(a == pytest.approx(-9.0 * xs[nid], abs=1e-9) for nid, a in acc.items())
    (j,) = [k for k, ln in enumerate(lines) if ln.startswith("BRIGAC")]
    assert float(lines[j + 3].split()[1]) == -2.0
    asm2, *_ = _bar(w=3.0)
    asm2.to_fem("c", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "c" / "c.comm").read_text()
    assert "ROTATION=_F(VITESSE=3.0, AXE=(0.0, 0.0, 1.0), CENTRE=(0.0, 0.0, 0.0))" in comm


def test_the_body_force_is_the_closed_forms():
    load = LoadRotation("r", (1, 0, 0), (0, 0, 2), angular_velocity=2.0, angular_acceleration=3.0)
    f = load.body_force((1, 2, 5), rho=10.0)  # r = (0, 2, 0) from the axis; e x d = (0, 0, 1) x (0, 2, 5) = (-2, 0, 0)
    assert np.allclose(f, 10.0 * (4.0 * np.array([0, 2, 0]) + 3.0 * np.array([-2, 0, 0])))


def test_calculix_refuses_what_it_has_no_form_for_by_name(tmp_path):
    """Its U1 beams take no body force, and *DLOAD has CENTRIF only."""
    asm, *_ = _bar(w=3.0)
    with pytest.raises(UnsupportedLoadType, match="U1 beams"):
        asm.to_fem("x", "calculix", scratch_dir=tmp_path, overwrite=True)
    asm, *_ = _bar(w=3.0, a=1.0)
    with pytest.raises(UnsupportedLoadType, match="angular acceleration"):
        asm.to_fem("y", "calculix", scratch_dir=tmp_path, overwrite=True)
