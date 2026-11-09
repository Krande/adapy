"""Ties (``Constraint.TYPES.TIE``) against the closed form, in Code_Aster and Sestra.

A 0.5 x 0.8 x 2 m column of two solid blocks whose meshes do not match at their interface (2 x 2 elements below,
4 x 4 above, at z = 1: secondary nodes mid-edge and mid-face on the main facets, which only the tie holds), the upper
block's bottom nodes tied to the lower block's top face, the column pressed by ``p`` on its top: the field is the
uniaxial one, ``w = -p z / E``, ``u = nu p x / E``, ``v = nu p y / E``, which linear elements reproduce and a tie
interpolating over the main facet carries across exactly -- on a secondary mesh that nests in the main one, where the
secondary nodes' forces carried over the tie's weights integrate the main facets' shape functions exactly (a node-to-
surface tie passes the constant-stress patch test there; on a 3 x 3 over the 2 x 2 it is 0.5 % off, measured). A tie left out leaves the
upper block floating (no solve); one tying to the nearest node, off by up to half an element, puts a kink in it.

Code_Aster: ``LIAISON_MAIL`` (TYPE_RACCORD='MASSIF'); Sestra: BLDEP over the main facet (``write_constraints``).
CalculiX refuses a tie by name: its ``*TIE`` looped without end on this column (calculix.write.write_constraints).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import (
    Bc,
    Constraint,
    Elem,
    FemSection,
    FemSet,
    LoadPressure,
    StepImplicitStatic,
)
from ada.fem.outputs import FieldOutput
from ada.fem.shapes.definitions import SolidShapes
from ada.fem.surfaces import get_surface_from_nodes_on_solid_elements
from ada.materials.metals import CarbonSteel

E, NU = 2.1e11, 0.3
A_, B_, H = 0.5, 0.8, 1.0


def _grid(fem, n, z0, first_nid, first_eid, sec_set_name):
    nodes, nid = {}, first_nid
    for k in range(3):
        for j in range(n + 1):
            for i in range(n + 1):
                nodes[i, j, k] = fem.nodes.add(ada.Node((A_ * i / n, B_ * j / n, z0 + H * k / 2), nid))
                nid += 1
    els, eid = [], first_eid
    for k in range(2):
        for j in range(n):
            for i in range(n):
                c = [nodes[i, j, k], nodes[i + 1, j, k], nodes[i + 1, j + 1, k], nodes[i, j + 1, k]]
                c += [nodes[i, j, k + 1], nodes[i + 1, j, k + 1], nodes[i + 1, j + 1, k + 1], nodes[i, j + 1, k + 1]]
                els.append(fem.add_elem(Elem(eid, c, SolidShapes.HEX8)))
                eid += 1
    return nodes, fem.add_set(FemSet(sec_set_name, els, "elset"))


def _column():
    mat = ada.Material("mat", CarbonSteel("S355", E=E, v=NU))
    p = ada.Part("col")
    fem = p.fem
    low, low_set = _grid(fem, 2, 0.0, 1, 1, "low")
    up, up_set = _grid(fem, 4, H, 1001, 1001, "up")
    for fs in (low_set, up_set):
        fem.add_section(FemSection(f"sec_{fs.name}", "solid", fs, mat))
    p.add_material(mat)
    top_of_low = [n for (i, j, k), n in low.items() if k == 2]
    main = fem.add_surface(
        get_surface_from_nodes_on_solid_elements("low_top", list(low_set.members), top_of_low, fem, True)
    )
    secondary = fem.add_set(FemSet("up_bottom", [n for (i, j, k), n in up.items() if k == 0], "nset"))
    fem.add_constraint(Constraint("glue", Constraint.TYPES.TIE, main, secondary, parent=fem))
    bottom = [n for (i, j, k), n in low.items() if k == 0]
    fem.add_bc(Bc("bz", fem.add_set(FemSet("bottom", bottom, "nset")), [3]))
    fem.add_bc(Bc("bxy", fem.add_set(FemSet("c0", [low[0, 0, 0]], "nset")), [1, 2]))
    fem.add_bc(Bc("by", fem.add_set(FemSet("c1", [low[2, 0, 0]], "nset")), [2]))
    a = ada.Assembly("a") / p
    top = [n for (i, j, k), n in up.items() if k == 2]
    top_surf = fem.add_surface(get_surface_from_nodes_on_solid_elements("top", list(up_set.members), top, fem, True))
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))
    step.add_load(LoadPressure("p", 1.0e7, top_surf))
    return a, fem, up


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_tied_column_of_unmatched_meshes_settles_as_one_block(tmp_path, require_solver, solver):
    require_solver(solver)
    a, fem, _ = _column()
    res = a.to_fem("tie", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    ((_, fields),) = res.case_fields("nodal_displacement").items()
    (f,) = [f for f in fields if getattr(f.role, "value", f.role) == "nodal_displacement"]
    u = {int(r[0]): r[1:4] for r in np.asarray(f.values, float)}
    p = 1.0e7
    # ccx's .frd and Sestra's SIN hold about six digits
    tol = (1e-9 if solver == "code_aster" else 1e-5) * p / E * 2 * H
    for n in fem.nodes:
        x, y, z = n.p
        assert u[int(n.id)] == pytest.approx((NU * p * x / E, NU * p * y / E, -p * z / E), abs=tol), (n.id, n.p)


def test_the_code_aster_deck_ties_with_liaison_mail_and_an_equation_with_liaison_ddl(tmp_path):
    a, fem, up = _column()
    n1, n2 = up[0, 0, 2], up[1, 0, 2]
    pair = fem.add_set(FemSet("eq_nodes", [n1, n2], "nset"))
    terms = [(n1, 3, 1.0), (n2, 3, -1.0)]
    fem.add_constraint(Constraint("eq", Constraint.TYPES.EQUATION, pair, pair, equation_terms=terms, parent=fem))
    a.to_fem("d", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "d" / "d.comm").read_text()
    assert "LIAISON_MAIL=_F(GROUP_MA_MAIT=(" in comm and "TYPE_RACCORD='MASSIF'" in comm
    assert "DDL=('DZ', 'DZ'), COEF_MULT=(1.0, -1.0), COEF_IMPO=0.0)" in comm


def _twin_cantilevers(tied: bool):
    from ada.fem import LoadPoint

    mat = ada.Material("S355", CarbonSteel("S355"))
    beams = [ada.Beam(f"b{k}", (0, k, 0), (4, k, 0), "IPE300", mat, up=(0, 0, 1)) for k in (0, 1)]
    p = ada.Part("P")
    for b in beams:
        p.add_beam(b)
    p.fem = p.to_fem_obj(0.5, "line")
    fem = p.fem
    roots = [n for n in fem.nodes if abs(n.x) < 1e-9]
    tips = sorted((n for n in fem.nodes if abs(n.x - 4) < 1e-9), key=lambda n: n.y)
    fem.add_bc(Bc("fix", fem.add_set(FemSet("roots", roots, "nset")), [1, 2, 3, 4, 5, 6]))
    if tied:
        pair = fem.add_set(FemSet("tips", tips, "nset"))
        terms = [(tips[0], 3, 1.0), (tips[1], 3, -1.0)]
        fem.add_constraint(
            Constraint("same_dz", Constraint.TYPES.EQUATION, pair, pair, equation_terms=terms, parent=fem)
        )
    a = ada.Assembly("a") / p
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))
    step.add_load(LoadPoint("p", -1.0e4, fem.add_set(FemSet("load_at", [tips[0]], "nset")), 3))
    return a, tips


@pytest.mark.parametrize("solver", ["code_aster", "calculix", "sesam"])
def test_an_equation_tying_two_cantilever_tips_halves_the_loaded_ones_deflection(tmp_path, require_solver, solver):
    """``dz_1 - dz_2 = 0`` between the tips of two equal cantilevers, one loaded: they share the load, so each tip
    deflects half the loaded one's alone -- whatever the beam theory (exact by linearity)."""
    require_solver(solver)
    out = []
    for tied in (False, True):
        a, tips = _twin_cantilevers(tied)
        res = a.to_fem(f"eq{int(tied)}", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
        ((_, fields),) = res.case_fields("nodal_displacement").items()
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == "nodal_displacement"]
        u = {int(r[0]): r[3] for r in np.asarray(f.values, float)}
        out.append([u[int(n.id)] for n in tips])
    (alone, other), (t0, t1) = out
    rel = 1e-9 if solver == "code_aster" else 1e-5
    assert abs(other) < 1e-12 + rel * abs(alone)
    assert t0 == pytest.approx(alone / 2, rel=rel) and t1 == pytest.approx(alone / 2, rel=rel)
