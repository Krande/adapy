"""Loads and supports given in a rotated (local) frame, solved by Code_Aster and Sestra against statics.

* **A rotated tip load.** A cantilever along x, clamped at its root, carries a force along the local x of a frame
  tilted 3-4-5 in the x-z plane, ``x' = (0.6, 0, 0.8)``. The root reactions are the force, reversed, in global axes,
  and the root moment about y is ``-F_z L``: statically determinate, so exact whatever the beam's stiffness. A load
  turned the inverse way (``Load.forces_global`` until this) shows as ``x' = (0.6, 0, -0.8)``.

Each case runs on every solver installed, and skips on one that is not (Sestra needs a DNV licence).
"""

from __future__ import annotations

import re

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, StepImplicitStatic
from ada.fem.common import Csys
from ada.materials.metals import CarbonSteel

L, F = 3.0, 1.0e4
SOLVERS = ["code_aster", "sesam"]


def _beam(name="P", mesh=0.5):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part(name) / bm
    p.fem = bm.to_fem_obj(mesh, "line")
    return ada.Assembly("A") / p, p


def _nodes_at(fem, x):
    return [n for n in fem.nodes if abs(n.x - x) < 1e-9]


def _case_fields(res):
    """``(displacement, reaction)`` as ``{node id: six components}``, read the same for every solver."""
    ((_, fields),) = res.case_fields("nodal_displacement", "nodal_reaction").items()
    out = []
    for role in ("nodal_displacement", "nodal_reaction"):
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == role]
        v = np.asarray(f.values, float)
        comps = list(f.components)
        names = [c for c in ("U1", "U2", "U3", "U4", "U5", "U6") if c in comps]  # U4-U6 the rotations / moments
        cols = [1 + comps.index(c) for c in names]
        out.append({int(r[0]): dict(zip(names, r[cols])) for r in v})
    return out


def _tilted_tip_load():
    a, p = _beam()
    fem = p.fem
    fem.add_bc(Bc("fix", fem.add_set(FemSet("root", _nodes_at(fem, 0.0), "nset")), [1, 2, 3, 4, 5, 6]))
    tip = fem.add_set(FemSet("tip", _nodes_at(fem, L), "nset"))
    tilt = Csys("tilt", coords=[(0.6, 0.0, 0.8), (0.0, 1.0, 0.0)], parent=fem)
    step = a.fem.add_step(StepImplicitStatic("rot_load"))
    step.add_load(LoadPoint("f", F, tip, 1, csys=tilt))
    return a, p


@pytest.mark.parametrize("solver", SOLVERS)
def test_a_tip_load_in_a_tilted_frame_is_balanced_by_its_global_components(tmp_path, require_solver, solver):
    require_solver(solver)
    a, p = _tilted_tip_load()
    fem = p.fem

    res = a.to_fem("rot_load", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    u, reac = _case_fields(res)
    (root,) = _nodes_at(fem, 0.0)
    r = reac[int(root.id)]
    rel = 1e-5 if solver == "sesam" else 1e-9
    # the reactions hold the load back: R = -F (0.6, 0, 0.8)
    assert r["U1"] == pytest.approx(-0.6 * F, rel=rel)
    assert r["U3"] == pytest.approx(-0.8 * F, rel=rel)
    assert abs(r["U2"]) < 1e-6 * F
    # the load's moment about the root: (L, 0, 0) x (0.6, 0, 0.8) F = (0, -0.8 L F, 0); the root's balances it
    assert r["U5"] == pytest.approx(0.8 * L * F, rel=rel)
    # and the tip goes up (+z) and out (+x), not down
    (tip_node,) = _nodes_at(fem, L)
    assert u[int(tip_node.id)]["U3"] > 0 and u[int(tip_node.id)]["U1"] > 0


def test_calculix_writes_the_tilted_load_in_global_components(tmp_path):
    """No solver (adapy reads no CalculiX results lazily): ``*CLOAD`` carries 0.6 F on dof 1 and 0.8 F on dof 3."""
    a, _ = _tilted_tip_load()
    a.to_fem("rot_load", "calculix", scratch_dir=tmp_path, overwrite=True)
    inp = (tmp_path / "rot_load" / "rot_load.inp").read_text()
    rows = {int(m[0]): float(m[1]) for m in re.findall(r"^tip,\s*(\d),\s*(\S+)$", inp, re.M)}
    assert rows == {1: pytest.approx(0.6 * F), 3: pytest.approx(0.8 * F)}

