"""Pressure cases of a stiffened panel through Code_Aster: every case's load and stresses, and the shell pressures the
``.rmed`` reader gives, which are the SIN's (``tests/core/fem/formats/code_aster/test_pressure_charges.py`` for the
deck).

Two 2 x 2 m, 10 mm plates side by side with a FB100x10 stiffener along both, clamped at both ends. Cases: 50 kPa on
plate 1, 20 kPa on plate 2, 30 kPa on plate 1, the first two in one case, and two combinations: ``1.3 LC1 + LC2``
(disjoint plates) and ``LC1 + LC3`` (both on plate 1).

Measured before the fix (Code_Aster 18.1.7): the cases of two pressures (``LC12``, ``ULS``, ``C13``) were each written
as two charges -- the merge read each charge's ``VERI_NORM`` as acting on the whole model -- so their stresses were
left out (<CALCULEL2_92>) and the SIN conversion stopped on the first of them (``KeyError``); ``cases.pressures`` of
the ``.rmed`` was empty. Merging ``C13``'s two items on plate 1 into one charge without adding them (measured with the
overlap check switched off) loaded 120 kN of its 320 kN: the later item replaced the earlier.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet
from ada.fem.concept.loads import (
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptSurface,
)
from ada.fem.results.field_data import FieldRole
from ada.materials.metals import CarbonSteel

AREA = 4.0
#: The vertical load of each case, N.
TOTAL = {
    "LC1": 50e3 * AREA,
    "LC2": 20e3 * AREA,
    "LC3": 30e3 * AREA,
    "LC12": 70e3 * AREA,
    "ULS": (1.3 * 50e3 + 20e3) * AREA,
    "C13": 80e3 * AREA,
}


def _panel() -> ada.Assembly:
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl1 = ada.Plate.from_3d_points("pl1", [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], 0.01, mat=mat)
    pl2 = ada.Plate.from_3d_points("pl2", [(2, 0, 0), (4, 0, 0), (4, 2, 0), (2, 2, 0)], 0.01, mat=mat)
    st = ada.Beam("st", (0, 1, 0), (4, 1, 0), "FB100x10", mat, up=(0, 0, 1))
    p = ada.Part("P") / [pl1, pl2, st]
    a = ada.Assembly("A") / p
    loads = p.concept_fem.loads
    lc1 = loads.add_load_case(LoadConceptCase("LC1", [LoadConceptSurface("S1", pl1, pressure=50e3)]))
    lc2 = loads.add_load_case(LoadConceptCase("LC2", [LoadConceptSurface("S2", pl2, pressure=20e3)]))
    lc3 = loads.add_load_case(LoadConceptCase("LC3", [LoadConceptSurface("S3", pl1, pressure=30e3)]))
    loads.add_load_case(
        LoadConceptCase(
            "LC12", [LoadConceptSurface("S1b", pl1, pressure=50e3), LoadConceptSurface("S2b", pl2, pressure=20e3)]
        )
    )
    loads.add_load_case_combination(
        LoadConceptCaseCombination("ULS", [LoadConceptCaseFactored(lc1, 1.3), LoadConceptCaseFactored(lc2, 1.0)])
    )
    loads.add_load_case_combination(
        LoadConceptCaseCombination("C13", [LoadConceptCaseFactored(lc1, 1.0), LoadConceptCaseFactored(lc3, 1.0)])
    )
    p.fem = p.to_fem_obj(0.25, use_quads=True)
    ends = [n for n in p.fem.nodes if abs(n.x) < 1e-6 or abs(n.x - 4) < 1e-6]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("ends", ends, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    p.fem.options.CODE_ASTER.write_sin = True
    return a


def test_pressure_cases_load_compute_stresses_and_read_back_the_sins_pressures(tmp_path, require_solver):
    require_solver("code_aster")
    a = _panel()
    a.to_fem("pan", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, timeout=600)
    rmed = ada.from_fem_res(tmp_path / "pan" / "pan.rmed", lazy=True)
    sin = ada.from_fem_res(tmp_path / "pan" / "panR1.SIN", lazy=True)
    names = rmed.cases.names
    assert sorted(names.values()) == sorted(TOTAL) and sin.cases.names == names

    roles = [FieldRole.NODAL_REACTION, FieldRole.SHELL_STRESS, FieldRole.BEAM_FORCES]
    for case, res in rmed.cases.iter_steps(rmed.cases.stored, roles=roles):
        by_role = {f.role: f for f in res.results}
        assert set(roles) <= set(by_role), f"{names[case]}: {sorted(r.value for r in by_role)} only"
        fz = float(np.nansum(np.asarray(by_role[FieldRole.NODAL_REACTION].values)[:, 3]))
        assert abs(fz) == pytest.approx(TOTAL[names[case]], rel=1e-9), names[case]

    # The same pressures from the .rmed (the deck adapy wrote) and from the SIN (BEUSLO), element by element.
    assert sorted(rmed.cases.pressures) == sorted(sin.cases.pressures) == sorted(rmed.cases.stored)
    for case, table in sin.cases.pressures.items():
        mine = rmed.cases.pressures[case]
        assert set(mine) == set(table), names[case]
        assert max(abs(mine[e] - table[e]) for e in table) <= 1e-6 * max(abs(v) for v in table.values())
        assert sum(abs(v) for v in table.values()) * AREA / 64 == pytest.approx(TOTAL[names[case]])
