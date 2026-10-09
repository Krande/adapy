"""Code_Aster applies every load of a static step: solved, two gravity loads in one step deflect the
cantilever as one gravity load of their sum does.

With ``step.loads[0]`` alone in ``EXCIT`` the second load was written and never applied: the two-load
step deflected 80 % as much as the one-load step (the first load is 80 of the 100).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.fem_tasks import design_cantilever, mesh_cantilever


def _max_uz(tmp_path, name: str, gravities: list[float], nl_geom: bool) -> float:
    from ada.fem.formats.utils import default_fem_res_path
    from ada.fem.results.field_data import NodalFieldType

    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    step = a.fem.add_step(ada.fem.StepImplicitStatic("st", nl_geom=nl_geom, init_incr=100.0, total_time=100.0))
    for i, g in enumerate(gravities):
        step.add_load(ada.fem.LoadGravity(f"grav{i}", g))
    res = a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    if res is None:
        res = ada.from_fem_res(default_fem_res_path(name, scratch_dir=tmp_path, fem_format="code_aster"))
    field = [
        f
        for f in res.results
        if getattr(f, "field_type", None) == NodalFieldType.DISP or f.name in ("DISP", "result__DEPL")
    ][-1]
    col = next(i for i, c in enumerate(field.components) if c.upper() in ("U3", "D3", "DZ", "Z"))
    return float(np.max(np.abs(np.asarray(field.values, dtype=float)[:, col + 1])))


@pytest.mark.parametrize("nl_geom", [False, True], ids=["linear", "nonlinear"])
def test_two_loads_in_a_step_act_as_their_sum(tmp_path, require_solver, nl_geom):
    require_solver("code_aster")
    two = _max_uz(tmp_path / "two", "two", [-9.81 * 80, -9.81 * 20], nl_geom)
    one = _max_uz(tmp_path / "one", "one", [-9.81 * 100], nl_geom)
    assert one > 0
    assert two == pytest.approx(one, rel=1e-6)
