"""Temperature loads (``LoadTemperature``) against the thermal-expansion closed forms, in Code_Aster and Sestra.

An IPE300 beam of length L along x, heated uniformly by ``dT`` above its reference temperature, ``alpha`` its
material's expansion coefficient:

* **held at both ends** along x: it cannot expand, so it carries ``N = -E A alpha dT`` and each end reacts
  ``E A alpha dT``, exact;
* **held at one end** only: free to expand, the far end moves ``alpha dT L`` and nothing reacts, exact.

Code_Aster: the TEMP command variable of the material field (AFFE_VARC, VALE_REF) and ELAS ALPHA. Sestra: BEISTE per
element, MISOSEL's ALPHA. Results by the FieldRole column layout ``[id, x, y, z, rx, ry, rz]``.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadTemperature, StepImplicitStatic
from ada.materials.metals import CarbonSteel

L, DT, REF = 4.0, 50.0, 20.0


def _beam(both_ends: bool):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    fem = p.fem
    (a_node,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (b_node,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("fix", fem.add_set(FemSet("a", [a_node], "nset")), [1, 2, 3, 4, 5, 6]))
    if both_ends:
        fem.add_bc(Bc("hold", fem.add_set(FemSet("b", [b_node], "nset")), [1]))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("heat"))
    step.add_load(LoadTemperature("warm", REF + DT, reference=REF))
    m = mat.model
    return a, a_node, b_node, m.E * bm.section.properties.Ax * m.alpha * DT, m.alpha


def _fields(res):
    ((_, fields),) = res.case_fields("nodal_displacement", "nodal_reaction").items()
    out = []
    for role in ("nodal_displacement", "nodal_reaction"):
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == role]
        out.append({int(r[0]): np.asarray(r[1:4], float) for r in np.asarray(f.values, float)})
    return out


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_beam_held_at_both_ends_pushes_on_them_with_e_a_alpha_dt(tmp_path, require_solver, solver):
    require_solver(solver)
    a, n0, n1, force, _ = _beam(both_ends=True)
    _, reac = _fields(a.to_fem("held", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-5 if solver == "sesam" else 1e-9
    assert reac[int(n0.id)][0] == pytest.approx(force, rel=rel)
    assert reac[int(n1.id)][0] == pytest.approx(-force, rel=rel)


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_beam_free_at_one_end_grows_by_alpha_dt_l(tmp_path, require_solver, solver):
    require_solver(solver)
    a, n0, n1, force, alpha = _beam(both_ends=False)
    u, reac = _fields(a.to_fem("free", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-5 if solver == "sesam" else 1e-9
    assert u[int(n1.id)][0] == pytest.approx(alpha * DT * L, rel=rel)
    assert abs(reac[int(n0.id)][0]) < 1e-6 * force


def test_the_decks_hold_the_temperature(tmp_path):
    """No solver: Code_Aster's temp_f field and AFFE_VARC with VALE_REF and ELAS ALPHA; Sesam one BEISTE per beam
    element with dT at its two nodes."""
    a, *_ = _beam(both_ends=True)
    a.to_fem("c", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "c" / "c.comm").read_text()
    assert "TYPE_CHAM='NOEU_TEMP_R'" in comm and "NOM_VARC='TEMP', CHAM_GD=temp_f, VALE_REF=20.0" in comm
    assert "ALPHA=" in comm
    a, *_ = _beam(both_ends=True)
    a.to_fem("s", "sesam", scratch_dir=tmp_path, overwrite=True)
    lines = next((tmp_path / "s").glob("*T1.FEM")).read_text().splitlines()
    beiste = [i for i, ln in enumerate(lines) if ln.startswith("BEISTE")]
    assert len(beiste) == 8  # 4 m at 0.5 m
    elno, nnod, intno, t1 = (float(x) for x in lines[beiste[0] + 1].split())
    assert (nnod, intno, t1, float(lines[beiste[0] + 2].split()[0])) == (2.0, 0.0, DT, DT)


def _strip(both_ends: bool):
    mat = ada.Material("S355", CarbonSteel("S355", v=0.0))
    t, b = 0.01, 0.5
    pl = ada.Plate("strip", [(0, 0), (L, 0), (L, b), (0, b)], t, mat=mat)
    p = ada.Part("P") / pl
    p.fem = p.to_fem_obj(0.25, use_quads=True)
    fem = p.fem
    a_side = [n for n in fem.nodes if abs(n.x) < 1e-9]
    b_side = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("a", fem.add_set(FemSet("a", a_side, "nset")), [1]))
    (a0,) = [n for n in a_side if abs(n.y) < 1e-9]
    fem.add_bc(Bc("a0", fem.add_set(FemSet("a0", [a0], "nset")), [2]))
    fem.add_bc(Bc("flat", fem.add_set(FemSet("all", list(fem.nodes), "nset")), [3, 4, 5, 6]))
    if both_ends:
        fem.add_bc(Bc("b", fem.add_set(FemSet("b", b_side, "nset")), [1]))
    asm = ada.Assembly("A") / p
    step = asm.fem.add_step(StepImplicitStatic("heat"))
    step.add_load(LoadTemperature("warm", REF + DT, reference=REF))
    return asm, a_side, b_side, mat.model.E * t * b * mat.model.alpha * DT, mat.model.alpha


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_shell_strip_held_at_both_ends_pushes_with_e_t_b_alpha_dt(tmp_path, require_solver, solver):
    """The strip in its plane (nu = 0): held along x at both ends it carries -E alpha dT, its ends react E t b alpha
    dT; the shell's temperature through its thickness (Code_Aster TEMP_MIL/INF/SUP) uniform."""
    require_solver(solver)
    asm, a_side, b_side, force, _ = _strip(both_ends=True)
    _, reac = _fields(asm.to_fem("sheld", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-5 if solver == "sesam" else 1e-9
    assert sum(reac[int(n.id)][0] for n in a_side) == pytest.approx(force, rel=rel)
    assert sum(reac[int(n.id)][0] for n in b_side) == pytest.approx(-force, rel=rel)


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_free_shell_strip_grows_by_alpha_dt_l(tmp_path, require_solver, solver):
    require_solver(solver)
    asm, _, b_side, _, alpha = _strip(both_ends=False)
    u, _ = _fields(asm.to_fem("sfree", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-5 if solver == "sesam" else 1e-9
    for n in b_side:
        assert u[int(n.id)][0] == pytest.approx(alpha * DT * L, rel=rel)
