"""Sestra solves a prescribed tip settlement and the node lands where the model said.

The counterpart of the Abaqus route's
``test_cae_licensed_acceptance.py::test_a_prescribed_support_displacement_reaches_the_solver``,
for the Sesam route, which until now wrote a ``Bc`` magnitude as an ordinary clamp: the
settlement case silently became a rigid support.

This is the test that establishes the BNDISPL layout. It is not read off a manual -- none ships
with the installed Sestra -- but off Sestra V11.3-00 itself: the field names and their meaning
come from its own diagnostics ("Invalid DTYPE on BNDISPL card. Supported values are 1
(displacement) and 3 (acceleration)", "BNDISPL card is too short for complex loads and dof count
given in NDOF field", in ``Bin/DataAccess.dll``), and the values from solving this cantilever.
Skips, rather than passes, where Sestra is not installed: an unverified record layout is not
something to report as green.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel

#: The settlement asked of the tip, in metres. Down, and large enough that the two failure modes
#: -- dropped (0.0) and sign-flipped -- are both unmistakable.
SETTLEMENT = -0.01
LENGTH = 1.0

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/sesam_prescribed"


def _sestra_exe():
    from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path

    try:
        return get_sestra_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_sestra_exe() is None, reason="Sestra is not installed")


def _cantilever() -> tuple[ada.Assembly, int]:
    """A 1 m IPE300 cantilever, clamped at x=0, its tip given ``SETTLEMENT`` in z.

    No other loading at all: the settlement is the whole load case, which is the case that
    used to come out of the writer as a model with nothing driving it.
    """
    bm = ada.Beam("bm", (0, 0, 0), (LENGTH, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.2, "line")
    fem = p.fem
    root = [n for n in fem.nodes if abs(n.x) < 1e-9]
    tip = [n for n in fem.nodes if abs(n.x - LENGTH) < 1e-9]
    assert len(root) == 1 and len(tip) == 1
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", root, "nset", parent=fem)), [1, 2, 3, 4, 5, 6]))
    fem.add_bc(
        Bc(
            "settle",
            fem.add_set(FemSet("tip", tip, "nset", parent=fem)),
            [3],
            magnitudes=[SETTLEMENT],
        )
    )
    a.fem.add_step(StepImplicitStatic("static", total_time=1.0, init_incr=1.0, max_incr=1.0))
    return a, int(tip[0].id)


def _tip_displacement(sin_file: pathlib.Path, node_id: int) -> np.ndarray:
    """``[X, Y, Z, RX, RY, RZ]`` of one node, out of the SIN's nodal displacement field."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    res = read_sin_file(sin_file)
    field = next(f for f in res.results if f.name == "sesam.nodes.displacement")
    # Columns are [node id, ALL (magnitude), X, Y, Z, RX, RY, RZ].
    row = np.asarray(field.values)[list(res.mesh.nodes.identifiers).index(node_id)]
    return row[2:8]


def test_a_prescribed_tip_settlement_reaches_sestra(tmp_path):
    a, tip_id = _cantilever()

    a.to_fem("presc", "sesam", scratch_dir=tmp_path, overwrite=True, execute=True)

    run_dir = tmp_path / "presc"
    lis = (run_dir / "SESTRA.LIS").read_text()
    assert "Normal exit from Sestra" in lis, lis[-2000:]

    # The deck holds both halves of the statement, neither of which does anything alone.
    deck = (run_dir / "prescT1.FEM").read_text()
    assert "BNDISPL" in deck, "the value"
    assert "\n          2.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n" in deck, "FIX code 2"

    u = _tip_displacement(run_dir / "prescR1.SIN", tip_id)

    # 1e-07 and not tighter, because a SIN stores nodal results in **single** precision: this
    # -0.01 comes back as -0.009999999776482582, which is float32's nearest neighbour to it (a
    # relative 2.2e-08) and not a solver residual. Measured, and worth knowing before reading a
    # number out of a SIN to nine digits.
    assert u[2] == pytest.approx(SETTLEMENT, rel=1e-07), "the prescribed displacement arrived"
    assert u[2] != 0.0, "a Bc magnitude dropped in translation reads as a fixed support, which is zero here"
    assert u[0] == pytest.approx(0.0, abs=1e-12), "and only in the dof the record named"
    assert u[1] == pytest.approx(0.0, abs=1e-12)
    # The tip rotation is free -- only dof 3 carries FIX code 2 -- so the beam bends rather than
    # translating rigidly. A nonzero RY is what says the settlement is a support movement and not
    # a clamp that happens to sit 10 mm lower.
    assert abs(u[4]) > 1e-04
