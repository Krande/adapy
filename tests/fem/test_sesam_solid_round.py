"""Sestra runs a solid round bar written as GPIPE without a bore, and takes its stiffness from GBEAMG.

adapy writes a CIRCULAR section as GeniE writes ``PipeSection(D, D/2)``: GPIPE with inner diameter 0
and wall thickness r, beside a GBEAMG with the disc's properties (shear area 3/4 pi r^2). This is
the evidence that Sestra V11.3 accepts that record. A 1 m cantilever of D100, clamped at the root,
its tip settled 10 mm with the rotation free: the tip rotation is
RY = delta L^2 / (2 E I) / (L^3 / (3 E I) + L / (G As)). With the GBEAMG's As = 5.89049e-3 that is
1.490313e-2; Sestra gives 1.490313e-2 (2.4e-8, the SIN's single precision). The tube of the old
1 % bore (As 5.83159e-3) would give 6.5e-5 less, and no shear deformation 6.5e-3 more.

Skips, rather than passes, where Sestra is not installed.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel

SETTLEMENT = -0.01
LENGTH = 1.0


def _sestra_exe():
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        return get_sestra_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_sestra_exe() is None, reason="Sestra is not installed")


def test_a_solid_round_cantilever_runs_in_sestra_with_the_disc_shear_area(tmp_path):
    bm = ada.Beam("bm", (0, 0, 0), (LENGTH, 0, 0), "CIRC50", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.2, "line")
    fem = p.fem
    root = [n for n in fem.nodes if abs(n.x) < 1e-9]
    tip = [n for n in fem.nodes if abs(n.x - LENGTH) < 1e-9]
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", root, "nset", parent=fem)), [1, 2, 3, 4, 5, 6]))
    fem.add_bc(Bc("settle", fem.add_set(FemSet("tip", tip, "nset", parent=fem)), [3], magnitudes=[SETTLEMENT]))
    a.fem.add_step(StepImplicitStatic("static", total_time=1.0, init_incr=1.0, max_incr=1.0))

    a.to_fem("rod", "sesam", scratch_dir=tmp_path, overwrite=True, execute=True)

    run_dir = tmp_path / "rod"
    lis = (run_dir / "SESTRA.LIS").read_text(errors="replace")
    assert "Normal exit from Sestra" in lis, lis[-2000:]
    deck = (run_dir / "rodT1.FEM").read_text()
    assert "GPIPE     1.00000000E+00  0.00000000E+00  1.00000000E-01  5.00000000E-02" in deck

    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    res = read_sin_file(run_dir / "rodR1.SIN")
    field = next(f for f in res.results if f.name == "sesam.nodes.displacement")
    row = np.asarray(field.values)[list(res.mesh.nodes.identifiers).index(int(tip[0].id))]

    props, mat = bm.section.properties, bm.material.model
    e, g = mat.E, mat.E / (2 * (1 + mat.v))
    flex = LENGTH**3 / (3 * e * props.Iy) + LENGTH / (g * props.Sharz)
    ry = SETTLEMENT * LENGTH**2 / (2 * e * props.Iy) / flex
    # Sestra's RY is positive for this downward settlement (its sign convention); the size is the
    # check. 1e-6: the SIN stores single precision (measured 2.4e-8); the old bore is 6.5e-5 away.
    # Columns are [node id, ALL, X, Y, Z, RX, RY, RZ].
    assert row[4] == pytest.approx(SETTLEMENT, rel=1e-7)
    assert abs(row[6]) == pytest.approx(abs(ry), rel=1e-6)
