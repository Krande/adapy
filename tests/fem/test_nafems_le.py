"""NAFEMS LE1 and LE10 through each solver, against the published targets.

At the finest mesh the report uses, where every solver measured within 0.8 % of the targets. A solver that is not
installed is skipped, which in CI is Abaqus and Sestra.
"""

from __future__ import annotations

import pytest

from ada.api.fem_benchmarks import (
    NAFEMS_CASES,
    add_nafems_load,
    mesh_nafems_le,
    stress_yy_at,
)
from ada.fem.exceptions.fea_software import FEASolverNotInstalled


@pytest.mark.parametrize("solver", ["abaqus", "calculix", "code_aster", "sesam"])
@pytest.mark.parametrize("case", ["LE1", "LE10"])
def test_stress_at_d_reaches_the_published_target(case, solver, tmp_path):
    assembly = mesh_nafems_le(case, 8)
    add_nafems_load(assembly)
    try:
        result = assembly.to_fem(
            f"{case}_{solver}", solver, overwrite=True, execute=True, scratch_dir=tmp_path, exit_on_complete=False
        )
    except FEASolverNotInstalled:
        pytest.skip(f"{solver} is not installed")
    stress = stress_yy_at(result, assembly.metadata["nafems"]["point_d"])
    assert stress == pytest.approx(NAFEMS_CASES[case].target_pa, rel=0.01)
