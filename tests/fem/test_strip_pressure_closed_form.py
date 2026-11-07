"""A 4.0 x 0.5 m, 10 mm strip in cylindrical bending under three concept pressures, solved by Code_Aster and Sestra
and compared with the Euler-Bernoulli closed form -- the same strip as ``test_sesam_pressure_load``.

The closed form: the strip, simply supported on its short edges and held in cylindrical bending on its long ones,
is a beam of stiffness ``D b`` (``D = E t^3 / (12 (1 - nu^2))``) under a line load ``p(x) b``; its deflection is
``w(x) = integral p(xi) G(x, xi) dxi / D`` with the simply supported beam's influence function ``G``, integrated
exactly (Gauss-Legendre on the polynomial pieces). It separates what an equilibrium check cannot:

* a **linear** pressure ``1000 + 500 x``: its quarter-point deflections differ (an element-averaged field would be
  closer to symmetric) and the two support lines carry ``q0 L / 2 + g L^2 / 6`` and ``q0 L / 2 + g L^2 / 3`` per
  metre of width;
* a **polygon** band of 1000 Pa over ``1 <= x <= 2`` (GeniE's covered-share rule; the band's edges on mesh lines);
* the **uniform** 2000 Pa reference.

Each case runs on every solver installed, and skips on one that is not (Sestra needs a DNV licence).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet
from ada.materials.metals import CarbonSteel

LENGTH, WIDTH, THICKNESS = 4.0, 0.5, 0.010
E, NU = 210.0e9, 0.3
D = E * THICKNESS**3 / (12.0 * (1.0 - NU**2))
MESH = 0.0625  # 64 elements per span; the strip's discretisation error here is ~2e-4 (test_sesam_pressure_load)


def _influence(x: float, xi: np.ndarray) -> np.ndarray:
    """Deflection at ``x`` of a simply supported beam of unit stiffness under a unit load at ``xi``."""
    L = LENGTH
    a, b = xi, L - xi
    left = b * x * (L**2 - b**2 - x**2) / (6 * L)  # x <= xi
    right = a * (L - x) * (L**2 - a**2 - (L - x) ** 2) / (6 * L)  # x >= xi
    return np.where(x <= xi, left, right)


def closed_form_w(x: float, pressure, lo=0.0, hi=LENGTH) -> float:
    """``integral_lo^hi p(xi) G(x, xi) dxi / D``, exact for the cubic pieces (split at ``x``)."""
    gx, gw = np.polynomial.legendre.leggauss(8)
    total = 0.0
    for a, b in ((lo, min(max(x, lo), hi)), (min(max(x, lo), hi), hi)):
        if b <= a:
            continue
        xi = 0.5 * (b - a) * gx + 0.5 * (a + b)
        total += 0.5 * (b - a) * float(np.sum(gw * pressure(xi) * _influence(x, xi)))
    return total / D


def _strip(load_kind: str):
    from ada.fem.concept.loads import LoadConceptCase, LoadConceptSurface

    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("strip", [(0.0, 0.0), (LENGTH, 0.0), (LENGTH, WIDTH), (0.0, WIDTH)], THICKNESS, mat=mat)
    part = ada.Part("Strip") / pl
    a = ada.Assembly("StripSite") / part
    if load_kind == "uniform":
        load = LoadConceptSurface("q", pl, pressure=2000.0)
    elif load_kind == "linear":
        load = LoadConceptSurface("q", pl, pressure=1000.0, pressure_gradient=(500.0, 0.0, 0.0))
    else:  # a band 1 <= x <= 2, wound so it pushes the way the plate's front pressure does
        band = [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, WIDTH, 0.0), (1.0, WIDTH, 0.0)]
        if pl.poly.normal[2] > 0:  # front pushes -n = -z; a polygon pushes along its own normal
            band = band[::-1]
        load = LoadConceptSurface("q", points=band, pressure=1000.0)
    part.concept_fem.loads.add_load_case(LoadConceptCase("LC", [load]))
    part.fem = part.to_fem_obj(MESH, "line", use_quads=True, interactive=False)
    fem = part.fem
    tol = 1e-9
    for name, nodes, dofs in (
        ("SS_X0", [n for n in fem.nodes if abs(n.x) < tol], (1, 2, 3)),
        ("SS_X1", [n for n in fem.nodes if abs(n.x - LENGTH) < tol], (2, 3)),
        ("CYL", [n for n in fem.nodes if abs(n.y) < tol or abs(n.y - WIDTH) < tol], (2, 4)),
    ):
        fem.add_bc(Bc(name, fem.add_set(FemSet(name, sorted(nodes, key=lambda n: n.id), "nset", parent=fem)), dofs))
    return a, part


CASES = {
    "uniform": (lambda xi: np.full_like(xi, 2000.0), 0.0, LENGTH),
    "linear": (lambda xi: 1000.0 + 500.0 * xi, 0.0, LENGTH),
    "polygon": (lambda xi: np.full_like(xi, 1000.0), 1.0, 2.0),
}


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
@pytest.mark.parametrize("load_kind", sorted(CASES))
def test_the_strip_deflects_as_the_closed_form_says(tmp_path, require_solver, solver, load_kind):
    require_solver(solver)
    a, part = _strip(load_kind)
    fea = a.to_fem(f"strip_{load_kind}", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    ((_, fields),) = fea.case_fields("nodal_displacement", "nodal_reaction").items()
    by_role = {f.role.value if hasattr(f.role, "value") else str(f.role): f for f in fields}
    disp = by_role["nodal_displacement"]
    reac = by_role["nodal_reaction"]
    xyz = {int(n.id): np.asarray(n.p, float) for n in part.fem.nodes}

    pressure, lo, hi = CASES[load_kind]
    u = np.asarray(disp.values, float)
    uz = 1 + list(disp.components).index("U3")
    r = np.asarray(reac.values, float)
    rz = 1 + list(reac.components).index("U3")

    # the support lines' reactions: a statically determinate beam, so exact for any consistent load
    gx, gw = np.polynomial.legendre.leggauss(8)
    xi = 0.5 * (hi - lo) * gx + 0.5 * (hi + lo)
    force = 0.5 * (hi - lo) * float(np.sum(gw * pressure(xi))) * WIDTH
    moment = 0.5 * (hi - lo) * float(np.sum(gw * pressure(xi) * xi)) * WIDTH
    want_right = moment / LENGTH
    want_left = force - want_right
    at = {int(row[0]): row[rz] for row in r if int(row[0]) in xyz}
    left = sum(v for nid, v in at.items() if abs(xyz[nid][0]) < 1e-9)
    right = sum(v for nid, v in at.items() if abs(xyz[nid][0] - LENGTH) < 1e-9)
    sign = float(np.sign(left + right))
    assert sign * left == pytest.approx(want_left, rel=1e-6)
    assert sign * right == pytest.approx(want_right, rel=1e-6)

    # deflection along the centreline: the load pushes against the reactions
    centre = {round(float(xyz[int(row[0])][0]), 9): row[uz] for row in u if abs(xyz[int(row[0])][1] - WIDTH / 2) < 1e-9}
    for x in (1.0, 2.0, 3.0):
        assert -sign * centre[x] == pytest.approx(closed_form_w(x, pressure, lo, hi), rel=1e-3), x
