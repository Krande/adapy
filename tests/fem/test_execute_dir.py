"""A real solver run with ``fea_execute_dir`` set: the solver runs in the deck's folder, the launch
scripts land in ``<fea_execute_dir>/<analysis name>``, and the result is read back.

Before the fix every solver failed: first ``TypeError: unsupported operand type(s) for /: 'str' and
'str'`` (the setting was a ``str``), and past that the solver started in the execute dir and could not
open its deck -- measured with CalculiX 2.23 (``cannot open file ...inp``), Code_Aster 18.1.8
(``run_aster`` could not open the ``.export``), Abaqus 2025 (``Command line option "input" must have
a value``) and Sestra V11.3-00 (no ``R1.SIN``). Skips where the solver is not installed.
"""

from __future__ import annotations

import copy
import sys

import pytest

import ada
from ada.api.fem_tasks import design_cantilever, mesh_cantilever
from ada.config import Config
from ada.fem.formats.general import FEATypes
from ada.fem.formats.utils import default_fem_inp_path, default_fem_res_path


@pytest.fixture
def execute_dir(monkeypatch, tmp_path):
    cfg = Config()
    saved = copy.deepcopy(cfg.config)
    exec_dir = tmp_path / "exec"
    monkeypatch.setenv("ADA_FEA_EXECUTE_DIR", str(exec_dir))
    cfg.reload_config()
    yield exec_dir
    monkeypatch.delenv("ADA_FEA_EXECUTE_DIR", raising=False)
    cfg.config = saved
    cfg._trigger_update_config()


@pytest.mark.parametrize("fem_format", ["calculix", "code_aster", "abaqus", "sesam"])
def test_a_solver_run_with_an_execute_dir_writes_its_result_next_to_the_deck(
    fem_format, tmp_path, execute_dir, require_solver
):
    require_solver(fem_format)
    fmt = FEATypes.from_str(fem_format)

    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    step = a.fem.add_step(ada.fem.StepImplicitStatic("gravity", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))

    name = f"exedir_{fem_format}"
    scratch = tmp_path / "scratch"
    res = a.to_fem(name, fem_format, scratch_dir=scratch, overwrite=True, execute=True, exit_on_complete=False)

    res_path = default_fem_res_path(name, scratch_dir=scratch, fem_format=fmt)
    assert res is not None
    assert res_path.is_file()
    assert len(res.results) > 0

    analysis_name = default_fem_inp_path(name, scratch)[fmt].stem
    if sys.platform == "win32":
        assert (execute_dir / analysis_name / "run.bat").is_file()
