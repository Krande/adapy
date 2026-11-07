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
