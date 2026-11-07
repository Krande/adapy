"""Large displacements in a geometrically nonlinear step (``nl_geom=True``), against the elastica, in Code_Aster.

A strip ``L x b x t`` of 8-node shells (COQUE_3D, on 9-node cells), clamped at ``x = 0`` and held in plane bending (``dy``, ``rx``, ``rz``
at every node: cylindrical bending, stiffness ``D b`` with ``D = E t^3 / (12 (1 - nu^2))``), is bent by an end moment
``M`` about y. A constant moment bends it into a circular arc of curvature ``k = M / (D b)``: through ``theta = k L``
the tip moves to ``x = sin(theta) / k``, ``|z| = (1 - cos(theta)) / k`` -- exact for any rotation, the moment's axis
staying y. At ``theta = 1`` rad the linear answer (tip at ``x = L``, ``|z| = k L^2 / 2``) is 17 % short along z and
leaves x at L instead of 0.84 L.

The writer used to solve an nlgeom step with small displacements (``DEFORMATION='PETIT'``, reported): the linear
answer. Second-order shells and solids now get ``GROT_GDEP`` (Code_Aster gives large rotations to COQUE_3D only; a
first-order shell, DKT, stays small-displacement and is reported). Measured, Code_Aster 18.1 at 0.125 m: the tip's x
and z within 1.1e-6 and 4.5e-8 of the arc. Held in dy and rx only (rz free), the strip lands 0.9 % and 1.9 % off:
in-plane rotation is the shells' fictitious drilling stiffness then (COEF_RIGI_DRZ = 1e-3, U2.02.01's SSNV138).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, StepImplicitStatic
from ada.materials.metals import CarbonSteel

L, WID, T = 4.0, 0.5, 0.01
THETA = 1.0


def _strip():
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("strip", [(0, 0), (L, 0), (L, WID), (0, WID)], T, mat=mat)
    p = ada.Part("P") / pl
    from ada.fem.meshing import GmshOptions

    p.fem = p.to_fem_obj(0.125, use_quads=True, options=GmshOptions(Mesh_ElementOrder=2))
    fem = p.fem
    tol = 1e-9
    root = [n for n in fem.nodes if abs(n.x) < tol]
    tip = sorted([n for n in fem.nodes if abs(n.x - L) < tol], key=lambda n: n.y)
    fem.add_bc(Bc("root", fem.add_set(FemSet("root", root, "nset")), [1, 2, 3, 4, 5, 6]))
    fem.add_bc(Bc("plane", fem.add_set(FemSet("plane", list(fem.nodes), "nset")), [2, 4, 6]))
    e, nu = mat.model.E, mat.model.v
    d = e * T**3 / (12 * (1 - nu**2))
    m = THETA * d * WID / L
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("bend", nl_geom=True, init_incr=0.05, total_time=1.0, max_incr=0.05))
    # the end moment shared over the tip edge's nodes as a quadratic edge's consistent load: 1/6 at the corners,
    # 4/6 at the mid-side nodes, of each element's width
    elems = len(tip) // 2
    w = WID / elems
    for i, n in enumerate(tip):
        share = (1 / 6 if i in (0, len(tip) - 1) else 2 / 6) if i % 2 == 0 else 4 / 6
        tip_set = fem.add_set(FemSet(f"t{n.id}", [n], "nset"))
        step.add_load(LoadPoint(f"m{n.id}", -m * w * share / WID, tip_set, 5))
    return a, tip, m / (d * WID)


def test_an_end_moment_rolls_the_strip_into_a_circular_arc(tmp_path, require_solver):
    require_solver("code_aster")
    a, tip, k = _strip()
    res = a.to_fem("arc", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    last = max(f.step for f in res.results if f.name.split("[")[0] == "DISP")
    (disp,) = [f for f in res.results if f.name.split("[")[0] == "DISP" and f.step == last]
    u = {int(r[0]): np.asarray(r[1:4]) for r in np.asarray(disp.values)}
    x_tip, z_tip = np.sin(THETA) / k, (1 - np.cos(THETA)) / k
    for n in tip:
        assert n.x + u[int(n.id)][0] == pytest.approx(x_tip, rel=1e-4)
        assert abs(u[int(n.id)][2]) == pytest.approx(z_tip, rel=1e-4)
