"""Code_Aster solves a model whose load or support is named like one of the command file's own concepts,
or not like a Python name at all, exactly as it solves the same model with plain names.

Measured on main with Code_Aster 18.1.8 (this cantilever shell under gravity): a load named ``model``
rebound ``model = AFFE_MODELE`` and stopped the run at ``<SUPERVIS_4>``, a Bc named ``model`` likewise,
and a load named ``my grav`` stopped it at ``<F>_SYNTAX_ERROR``; adapy then reported only
``FileNotFoundError: FEM result file does not exist``.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.fem_tasks import design_cantilever, mesh_cantilever
from ada.fem.formats.general import FEATypes
from ada.fem.formats.utils import default_fem_res_path


def _displacements(tmp_path, name: str, load_name: str, bc_name: str | None) -> np.ndarray:
    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    if bc_name is not None:
        p = a.get_part("MyPart")
        for bc in p.fem.bcs + a.fem.bcs:
            bc.name = bc_name
    step = a.fem.add_step(ada.fem.StepImplicitStatic("gravity", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity(load_name, -9.81 * 80))
    a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, exit_on_complete=False)
    res = ada.from_fem_res(default_fem_res_path(name, scratch_dir=tmp_path, fem_format=FEATypes.CODE_ASTER))
    (disp,) = [f for f in res.results if f.name in ("DISP", "result__DEPL")][-1:]
    return np.asarray(disp.values, dtype=float)


@pytest.fixture(scope="module")
def reference(tmp_path_factory, require_solver):
    require_solver("code_aster")
    return _displacements(tmp_path_factory.mktemp("names_ref"), "plain", "grav", None)


@pytest.mark.parametrize(
    "load_name, bc_name",
    [("model", None), ("my grav", None), ("grav", "model")],
    ids=["load_named_model", "load_named_my_grav", "bc_named_model"],
)
def test_a_name_the_command_file_cannot_bind_solves_as_a_plain_one(tmp_path, reference, load_name, bc_name):
    values = _displacements(tmp_path, "named", load_name, bc_name)
    assert np.abs(reference).max() > 0.0
    # The same deck but for its concept names: the same numbers, to the last digit.
    np.testing.assert_array_equal(values, reference)
