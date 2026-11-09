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
from ada.fem.outputs import FieldOutput
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


def _at_x(res, x0):
    """The result's node ids at ``x = x0``, by the result mesh's own positions: CalculiX writes a shell's results on
    the bricks it expands it into, under new nodes (one layer each side of the mid-surface), not on the model's."""
    m = res.mesh.nodes
    return [int(i) for i, c in zip(m.identifiers, np.asarray(m.coords, float)) if abs(c[0] - x0) < 1e-9]


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
    rel = 1e-9 if solver == "code_aster" else 1e-5
    assert reac[int(n0.id)][0] == pytest.approx(force, rel=rel)
    assert reac[int(n1.id)][0] == pytest.approx(-force, rel=rel)


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_beam_free_at_one_end_grows_by_alpha_dt_l(tmp_path, require_solver, solver):
    require_solver(solver)
    a, n0, n1, force, alpha = _beam(both_ends=False)
    u, reac = _fields(a.to_fem("free", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True))
    rel = 1e-9 if solver == "code_aster" else 1e-5
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
    step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))  # CalculiX writes only what is asked for
    step.add_load(LoadTemperature("warm", REF + DT, reference=REF))
    return asm, a_side, b_side, mat.model.E * t * b * mat.model.alpha * DT, mat.model.alpha


@pytest.mark.parametrize("solver", ["code_aster", "sesam", "calculix"])
def test_a_shell_strip_held_at_both_ends_pushes_with_e_t_b_alpha_dt(tmp_path, require_solver, solver):
    """The strip in its plane (nu = 0): held along x at both ends it carries -E alpha dT, its ends react E t b alpha
    dT; the shell's temperature through its thickness (Code_Aster TEMP_MIL/INF/SUP) uniform."""
    require_solver(solver)
    asm, _, _, force, _ = _strip(both_ends=True)
    res = asm.to_fem("sheld", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    _, reac = _fields(res)
    rel = 1e-9 if solver == "code_aster" else 1e-5
    assert sum(reac[i][0] for i in _at_x(res, 0.0)) == pytest.approx(force, rel=rel)
    assert sum(reac[i][0] for i in _at_x(res, L)) == pytest.approx(-force, rel=rel)


@pytest.mark.parametrize("solver", ["code_aster", "sesam", "calculix"])
def test_a_free_shell_strip_grows_by_alpha_dt_l(tmp_path, require_solver, solver):
    require_solver(solver)
    asm, _, _, _, alpha = _strip(both_ends=False)
    res = asm.to_fem("sfree", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    u, _ = _fields(res)
    rel = 1e-9 if solver == "code_aster" else 1e-5
    far = _at_x(res, L)
    assert far
    for i in far:
        assert u[i][0] == pytest.approx(alpha * DT * L, rel=rel)


def test_the_abaqus_and_calculix_decks_hold_the_temperature(tmp_path):
    """No solver for Abaqus here: the reference as every node's initial temperature, the load as *Temperature in the
    step, the material's *Expansion its alpha (not its damping). CalculiX resets the temperatures at each step
    (OP=NEW) as it does its other loads."""
    asm, *_ = _strip(both_ends=True)
    asm.to_fem("aba", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "aba" / "aba.inp").read_text()
    n_nodes = len(asm.get_part("P").fem.nodes)
    initial = inp[inp.index("*Initial Conditions, type=TEMPERATURE") :].split("\n*", 1)[0]
    assert initial.count(", 20") == n_nodes and initial.index("*Initial") < inp.index("*Step")
    heat = inp[inp.index("*Temperature") :].split("\n*", 1)[0]
    assert heat.count(", 70") == n_nodes and inp.index("*Temperature") > inp.index("*Step")
    assert "*Expansion\n 1.2e-05" in inp

    asm, *_ = _strip(both_ends=True)
    asm.to_fem("ccx", "calculix", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "ccx" / "ccx.inp").read_text()
    assert "*Initial conditions, type=temperature\n ADA_TALL, 2.0000000000000E+01" in inp
    assert "*Temperature, OP=NEW\n*Temperature\n ADA_TALL, 7.0000000000000E+01" in inp
    assert "*Expansion\n 1.2e-05" in inp


def test_calculix_refuses_a_temperature_on_u1_beams(tmp_path):
    """U1 beams (every 2-node beam of a general section) take no thermal strain in ccx: refused by name, never
    written as a temperature with no effect."""
    from ada.fem.exceptions.model_definition import UnsupportedLoadType

    a, *_ = _beam(both_ends=True)
    with pytest.raises(UnsupportedLoadType, match="U1"):
        a.to_fem("u1", "calculix", scratch_dir=tmp_path, overwrite=True)


@pytest.mark.parametrize("solver", ["code_aster", "calculix"])
@pytest.mark.parametrize("shape", ["HEX8", "TETRA10"])
def test_a_heated_solid_block_grows_by_alpha_dt_and_held_pushes_with_e_alpha_dt(
    tmp_path, require_solver, solver, shape
):
    """A 0.5 x 0.8 x 2 m block, solids (Sestra takes no temperature on solids). Held statically determinate at three
    corners, it grows to ``u = alpha dT r``, exact for every element; held along z at the bottom and the top it
    carries ``-E alpha dT`` along z and each face reacts ``E alpha dT a b`` (lateral faces free)."""
    from test_code_aster_solid_pressure import _block

    from ada.fem.shapes.definitions import SolidShapes

    require_solver(solver)
    a_, b_, h = 0.5, 0.8, 2.0
    rel = 1e-9 if solver == "code_aster" else 1e-5
    for held in (False, True):
        a, p, nodes = _block(2, 2, 3, a_, b_, h, name="blk", shape=SolidShapes[shape])
        fem = p.fem
        bottom = [n for n in fem.nodes if abs(n.p[2]) < 1e-9]
        if held:
            fem.add_bc(Bc("bz", fem.add_set(FemSet("bot", bottom, "nset")), [3]))
            top = [n for n in fem.nodes if abs(n.p[2] - h) < 1e-9]
            fem.add_bc(Bc("tz", fem.add_set(FemSet("top", top, "nset")), [3]))
        else:
            fem.add_bc(Bc("c0", fem.add_set(FemSet("c0", [nodes[0, 0, 0]], "nset")), [3]))
        fem.add_bc(Bc("cxy", fem.add_set(FemSet("cxy", [nodes[0, 0, 0]], "nset")), [1, 2]))
        fem.add_bc(Bc("cy", fem.add_set(FemSet("cy", [nodes[2, 0, 0]], "nset")), [2] if held else [2, 3]))
        if not held:
            fem.add_bc(Bc("cz", fem.add_set(FemSet("cz", [nodes[0, 2, 0]], "nset")), [3]))
        step = a.fem.add_step(StepImplicitStatic("heat"))
        step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))
        step.add_load(LoadTemperature("warm", REF + DT, reference=REF))
        res = a.to_fem(f"blk{int(held)}", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
        u, reac = _fields(res)
        m = next(iter(p.materials)).model
        if held:
            want = m.E * m.alpha * DT * a_ * b_
            # the block pushes down on the bottom support, which pushes back up
            assert sum(reac[int(n.id)][2] for n in bottom) == pytest.approx(want, rel=rel)
        else:
            for n in fem.nodes:
                assert u[int(n.id)] == pytest.approx(m.alpha * DT * np.asarray(n.p), abs=rel * m.alpha * DT * h)


@pytest.mark.parametrize("deck", ["abaqus", "calculix"])
def test_a_temperature_read_back_from_a_deck_solves_to_the_same_field(tmp_path, require_solver, deck):
    """The strip written to an Abaqus or CalculiX deck and read back (``ada.from_fem``): its *Temperature and the
    nodes' initial temperature come back as the load and its reference, so the read model, solved in CalculiX,
    still grows by alpha dT L -- a reader that dropped them would leave it unloaded."""
    from ada.fem.loads import LoadTemperature as LT

    require_solver("calculix")
    asm, _, _, _, alpha = _strip(both_ends=False)
    asm.to_fem("deck", deck, scratch_dir=tmp_path, overwrite=True)
    back = ada.from_fem(next((tmp_path / "deck").glob("*.inp")))
    loads = [ld for p in back.get_all_parts_in_assembly(include_self=True) for st in p.fem.steps for ld in st.loads]
    (temp,) = [ld for ld in loads if isinstance(ld, LT)]
    assert (temp.temperature, temp.reference, temp.fem_set) == (REF + DT, REF, None)
    for part in back.get_all_parts_in_assembly(include_self=True):
        for step in part.fem.steps:
            step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))
    res = back.to_fem("again", "calculix", scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    u, _ = _fields(res)
    far = _at_x(res, L)
    assert far and all(u[i][0] == pytest.approx(alpha * DT * L, rel=1e-5) for i in far)
