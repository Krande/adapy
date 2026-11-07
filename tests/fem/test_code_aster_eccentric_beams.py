"""Code_Aster solves an eccentric stiffened panel as Sestra does, on the same adapy mesh.

A plate with angle stiffeners placed ``"flush top"`` (hanging below it) and T girders ``"flush bottom"`` along two
edges, meshed by ``to_fem_obj`` on shared nodes with the beams' eccentricities on the elements. The Code_Aster deck
writes each eccentric beam on offset nodes tied to the mesh nodes by ``LIAISON_GROUP`` rigid links
(:mod:`ada.fem.formats.code_aster.write.write_eccentricity`); the Sesam deck writes GECCEN and Sestra does the same
inside. Each solver's leg skips when it is not installed.

Measured (Code_Aster 18.1.7, Sestra V11.3-01) on the 7.8 x 3.9 m panel of the PR (6 mm plate, HP160x8 at 0.65 m
spanning 3.9 m, TG600x300x20x32 girders, 50 kPa, every edge held in dx, dy, dz, 0.325 m quads): -32.88 mm (Code_Aster)
against -33.03 mm (Sestra), -32.58 against -32.61 at 0.1625 m; with the eccentricities stripped from the same mesh
both give -120.6 mm, 3.7 times more -- the panel of the issue, which deflected 4.2 times a GeniE + Sestra model with
its stiffeners on their neutral axes. The composite stiffener-plus-plate closed form (5 q L^4 / 384 E I with the
0.65 m plate strip as a flange) gives 29.7 mm. The test here is the same panel at a third of the size, so a solve is
seconds, and asserts the two solvers against each other and the effect of the eccentricity.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem.concept.constraints import ConstraintConceptCurve
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.loads import LoadConceptCase, LoadConceptSurface

#: Three stiffeners spanning 2.6 m: the stiffeners' bending governs the centre deflection, as in the panel of the PR.
#: (A 1.3 m span at this mesh is governed by the plate's own bending between the stiffeners, where the two shell
#: formulations differ by several per cent on two elements per bay.)
LX, LY, T, SPACING, PRESSURE, MESH = 2.6, 2.6, 0.006, 0.65, 50e3, 0.325


def _dofs(fixed):
    return [Dof(d, "fixed" if d in fixed else "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]


def _panel(eccentric: bool):
    pl = ada.Plate("pl", [(0, 0), (LX, 0), (LX, LY), (0, LY)], T, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
    beams = []
    for i in range(1, int(round(LX / SPACING))):
        x = i * SPACING
        bm = ada.Beam(f"st{i}", (x, 0, 0), (x, LY, 0), "HP160x8", up=(0, 0, 1))
        bm.justification = "flush top"
        beams.append(bm)
    for j, y in enumerate((0.0, LY)):
        bm = ada.Beam(f"g{j}", (0, y, 0), (LX, y, 0), "TG600x300x20x32", up=(0, 0, -1))
        bm.justification = "flush bottom"
        beams.append(bm)
    p = ada.Part("panel") / [pl, *beams]
    c = p.concept_fem.constraints
    for nm, p1, p2 in (
        ("x0", (0, 0, 0), (0, LY, 0)),
        ("x1", (LX, 0, 0), (LX, LY, 0)),
        ("y0", (0, 0, 0), (LX, 0, 0)),
        ("y1", (0, LY, 0), (LX, LY, 0)),
    ):
        c.add_curve_constraint(ConstraintConceptCurve(nm, p1, p2, _dofs(("dx", "dy", "dz"))))
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC_p", [LoadConceptSurface("P", pl, pressure=PRESSURE, side="front")])
    )
    a = ada.Assembly("A") / p
    p.fem = p.to_fem_obj(MESH, bm_repr="line", pl_repr="shell", use_quads=True)
    lines = list(p.fem.elements.lines)
    assert lines and all(el.eccentricity is not None for el in lines)
    if not eccentric:
        for el in lines:
            el.eccentricity = None
    p.fem.options.CODE_ASTER.use_timoshenko_beams = True  # Sestra's beams carry shear deformation
    return a, p


def _centre_node(p):
    (n,) = [n for n in p.fem.nodes if abs(n.x - LX / 2) < 1e-9 and abs(n.y - LY / 2) < 1e-9]
    return int(n.id)


def _code_aster_uz(a, p, name, tmp_path) -> float:
    a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    mess = (tmp_path / name / f"{name}.mess").read_text(encoding="utf-8", errors="replace")
    (verdict,) = [line for line in mess.splitlines() if "DIAGNOSTIC JOB" in line]
    assert "<S>" not in verdict and "<F>" not in verdict and "<E>" not in verdict, verdict
    res = ada.from_fem_res(tmp_path / name / f"{name}.rmed")
    (depl,) = [f for f in res.results if f.name.endswith("DEPL")]
    rows = {int(r[0]): np.asarray(r[1:], dtype=float) for r in np.asarray(depl.values)}
    return float(rows[_centre_node(p)][2])


def _sestra_uz(a, p, name, tmp_path) -> float:
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    a.to_fem(name, "sesam", scratch_dir=tmp_path, overwrite=True, execute=True)
    res = read_sin_file(tmp_path / name / f"{name}R1.SIN")
    ids = list(res.mesh.nodes.identifiers)
    (disp,) = [r for r in res.results if r.name == "sesam.nodes.displacement"]
    # the SIN displacement rows: result case, node, dx, dy, dz, rx, ry, rz (dz is column 4, as the beam solve tests read it)
    return float(np.asarray(disp.values)[ids.index(_centre_node(p))][4])


def _sestra_installed() -> bool:
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        return get_sestra_default_exe_path() is not None
    except Exception:  # noqa: BLE001 - any locator failure is "not installed"
        return False


@pytest.fixture(scope="module")
def code_aster_panel(tmp_path_factory, require_solver):
    require_solver("code_aster")
    tmp = tmp_path_factory.mktemp("ecc_ca")
    a, p = _panel(True)
    ecc = _code_aster_uz(a, p, "ecc", tmp)
    a0, p0 = _panel(False)
    flat = _code_aster_uz(a0, p0, "flat", tmp)
    return ecc, flat


def test_eccentric_stiffeners_stiffen_the_panel(code_aster_panel):
    """The offset beams and their rigid links carry: the centre deflects a fraction of the in-plane model's."""
    ecc, flat = code_aster_panel
    assert ecc < 0 and flat < 0
    assert ecc / flat < 0.5, (ecc, flat)


def test_code_aster_matches_sestra_on_the_eccentric_mesh(code_aster_panel, tmp_path):
    """Rigid links in Code_Aster against GECCEN in Sestra, the same nodes and elements.

    Measured: -5.697 mm (Code_Aster 18.1.7, DKT shells, POU_D_T beams) against -5.781 mm (Sestra V11.3-01), 1.5 %
    apart, where the two solvers' shells on two elements per stiffener bay with every edge held in-plane account for
    the difference (the 7.8 x 3.9 m panel of the PR: 0.4 %; a 1.3 m span governed by the plate's own bending: 7 %).
    """
    if not _sestra_installed():
        pytest.skip("Sestra is not installed")
    ecc, _ = code_aster_panel
    a, p = _panel(True)
    sestra = _sestra_uz(a, p, "ecc_ss", tmp_path)
    assert ecc == pytest.approx(sestra, rel=0.02), (ecc, sestra)
