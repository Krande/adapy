"""Shell-to-solid coupling (``Constraint.TYPES.SHELL2SOLID``, the Abaqus ``*Shell to Solid Coupling`` object) against
the closed form, in Code_Aster and Sestra.

A shell strip ``L x b x t`` (nu = 0) along x in the plane z = 0, its root edge on the face x = 0 of a solid block a
thousand times stiffer, held at its back face: the joint is the strip's clamp, so a tip force across the strip bends
it as a cantilever, ``w = P L^3 / (3 E I)``, ``I = b t^3 / 12``, and one along it stretches it by ``P L / (E b t)``. A
joint that gave the edge its translations only (a pin) leaves the strip a mechanism; a rigid link to the nearest node
(the old Sesam form) holds the root along a line of nodes rather than the face.

Code_Aster: ``LIAISON_MAIL`` ``COQUE_MASSIF``; Sestra: the same kinematics as BLDEP (``ada.fem.shell_to_solid``).
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
    LoadPoint,
    StepImplicitStatic,
)
from ada.fem.outputs import FieldOutput
from ada.fem.shapes.definitions import SolidShapes
from ada.fem.surfaces import get_surface_from_nodes_on_solid_elements
from ada.materials.metals import CarbonSteel

L, B, T = 1.0, 0.2, 0.01
E = 2.1e11


def _model(direction: int, P: float = 100.0):
    mat = ada.Material("S355", CarbonSteel("S355", E=E, v=0.0))
    pl = ada.Plate("strip", [(0, 0), (L, 0), (L, B), (0, B)], T, mat=mat)
    strip = ada.Part("strip") / pl
    strip.fem = strip.to_fem_obj(0.05, use_quads=True)
    sfem = strip.fem

    blk = ada.Part("blk")
    bfem = blk.fem
    stiff = ada.Material("stiff", CarbonSteel("S355", E=1000 * E, v=0.0))
    nx, ny, nz = 2, 4, 4
    nodes, nid = {}, 5001
    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                p = (-0.2 + 0.2 * i / nx, B * j / ny, -0.1 + 0.2 * k / nz)
                nodes[i, j, k] = bfem.nodes.add(ada.Node(p, nid))
                nid += 1
    els, eid = [], 5001
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                c = [nodes[i, j, k], nodes[i + 1, j, k], nodes[i + 1, j + 1, k], nodes[i, j + 1, k]]
                c += [nodes[i, j, k + 1], nodes[i + 1, j, k + 1], nodes[i + 1, j + 1, k + 1], nodes[i, j + 1, k + 1]]
                els.append(bfem.add_elem(Elem(eid, c, SolidShapes.HEX8)))
                eid += 1
    bset = bfem.add_set(FemSet("block", els, "elset"))
    bfem.add_section(FemSection("sec_block", "solid", bset, stiff))
    blk.add_material(stiff)
    back = [n for (i, j, k), n in nodes.items() if i == 0]
    bfem.add_bc(Bc("back", bfem.add_set(FemSet("back", back, "nset")), [1, 2, 3]))
    face_nodes = [n for (i, j, k), n in nodes.items() if i == nx]
    face = bfem.add_surface(get_surface_from_nodes_on_solid_elements("face", els, face_nodes, bfem, True))

    edge = sfem.add_set(FemSet("edge", [n for n in sfem.nodes if abs(n.x) < 1e-9], "nset"))
    a = ada.Assembly("A") / [strip, blk]
    a.fem.add_constraint(Constraint("joint", Constraint.TYPES.SHELL2SOLID, edge, face, parent=a.fem))
    tip = sorted((n for n in sfem.nodes if abs(n.x - L) < 1e-9), key=lambda n: n.y)
    step = a.fem.add_step(StepImplicitStatic("pull"))
    step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))
    for k, n in enumerate(tip):
        share = (0.5 if k in (0, len(tip) - 1) else 1.0) / (len(tip) - 1)
        step.add_load(LoadPoint(f"p{n.id}", P * share, sfem.add_set(FemSet(f"l{n.id}", [n], "nset")), direction))
    want = P * L**3 / (3 * E * B * T**3 / 12) if direction == 3 else P * L / (E * B * T)
    return a, tip, want


@pytest.mark.parametrize("direction", [3, 1], ids=["bending", "stretch"])
@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_strip_joined_to_a_stiff_block_is_a_clamped_cantilever(tmp_path, require_solver, solver, direction):
    require_solver(solver)
    a, tip, want = _model(direction)
    res = a.to_fem("s2s", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    ((_, fields),) = res.case_fields("nodal_displacement").items()
    (f,) = [f for f in fields if getattr(f.role, "value", f.role) == "nodal_displacement"]
    u = {int(r[0]): r[1:4] for r in np.asarray(f.values, float)}
    got = np.mean([u[int(n.id)][direction - 1] for n in tip])
    # measured, Code_Aster 18.1: bending +1.1e-7, stretch +1.8e-5 (the stiff block's own give); Sestra's shells are
    # shear-deformable, which at t / L = 0.01 adds about 1e-4 to the bending
    assert got == pytest.approx(want, rel=1e-4 if solver == "code_aster" else 1e-3), (got, want)


def test_the_edge_relations_carry_a_rigid_motion_of_the_solid_exactly():
    """No solver: the relations Sesam's BLDEP is written from (ada.fem.shell_to_solid.edge_relations), applied to the
    solid nodes moved rigidly (a translation c and a small rotation w), give each edge node that motion: ``u = c + w x
    x`` and ``theta = w - (w . n) n`` -- the fibre turns with the solid, its drilling about the normal n held."""
    from ada.fem.concat import single_part_assembly
    from ada.fem.shell_to_solid import edge_joint, edge_relations

    a, _, _ = _model(3)
    merged = single_part_assembly(a)
    (con,) = [c for p in merged.get_all_parts_in_assembly(include_self=True) for c in p.fem.constraints.values()]
    joint = edge_joint(con)
    relations, why = edge_relations(con, joint)
    assert why is None and len(relations) == len(joint.nodes)
    c, w = np.array([1e-3, -2e-3, 3e-3]), np.array([2e-4, -5e-4, 7e-4])
    for rel in relations:
        u = np.zeros(6)
        for (mid, d, e), beta in rel.terms.items():
            x = np.asarray(rel.masters[mid].p, dtype=float)
            u[d - 1] += beta * (c + np.cross(w, x))[e - 1]
        x, n = np.asarray(rel.node.p, dtype=float), joint.normals[int(rel.node.id)]
        assert u[:3] == pytest.approx(c + np.cross(w, x), abs=1e-12)
        assert u[3:] == pytest.approx(w - np.dot(w, n) * n, abs=1e-12)


def test_the_sesam_deck_joins_every_edge_node_in_its_six_dofs(tmp_path):
    """No solver: one dependent shell edge node per edge node, its six dofs on the solid face's translations -- not the
    rigid links to the nearest node (reported as approximated) the writer used to write."""
    from ada.fem.formats import conversion_report

    a, _, _ = _model(3)
    with conversion_report.collect() as rep:
        a.to_fem("d", "sesam", scratch_dir=tmp_path, overwrite=True)
    lines = next((tmp_path / "d").glob("*T1.FEM")).read_text().splitlines()
    heads = [lines[i] for i, ln in enumerate(lines) if ln.startswith("BLDEP")]
    slaves = {float(h.split()[1]) for h in heads}
    assert len(slaves) == int(L / 0.05 * 0 + B / 0.05 + 1)  # the edge's nodes, every 0.05 m across b
    assert {float(h.split()[3]) for h in heads} == {6.0}  # NDDOF: the six dofs
    assert not [f for f in rep.findings if f.subject == "joint" and f.kind in ("approximated", "omitted")]


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
def test_a_shell_to_solid_coupling_read_from_an_abaqus_deck_converts_to_the_same_clamp(
    tmp_path, require_solver, solver
):
    """Conversion: the model written as an Abaqus deck (``*Shell to Solid Coupling``), read back (``ada.from_fem``) and
    solved -- the coupling the reader makes is the one the writers take, so the strip bends as the same cantilever."""
    require_solver(solver)
    a, _, want = _model(3)
    a.to_fem("aba", "abaqus", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "aba" / "aba.inp").read_text()
    assert "*Shell to Solid Coupling" in inp
    back = ada.from_fem(tmp_path / "aba" / "aba.inp")
    cons = [c for p in back.get_all_parts_in_assembly(include_self=True) for c in p.fem.constraints.values()]
    assert [c.type for c in cons if c.name == "joint"] == [Constraint.TYPES.SHELL2SOLID]
    for p in back.get_all_parts_in_assembly(include_self=True):
        for step in p.fem.steps:
            step.add_field_output(FieldOutput("u_rf", nodal=["U", "RF"]))
    res = back.to_fem("conv", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    ((_, fields),) = res.case_fields("nodal_displacement").items()
    (f,) = [f for f in fields if getattr(f.role, "value", f.role) == "nodal_displacement"]
    pos = res.mesh.nodes
    coords = np.asarray(pos.coords, float)
    u = {int(r[0]): r[1:4] for r in np.asarray(f.values, float)}
    tip = [int(i) for i, c in zip(pos.identifiers, coords) if abs(c[0] - L) < 1e-9 and abs(c[2]) < 1e-9]
    got = np.mean([u[i][2] for i in tip])
    assert got == pytest.approx(want, rel=1e-4 if solver == "code_aster" else 1e-3), (got, want)
