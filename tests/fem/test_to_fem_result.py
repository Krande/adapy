"""``Assembly.to_fem`` always returns an FEAResult; one written but not run says so and runs on ``execute()``."""

from __future__ import annotations

import pytest
from test_code_aster_sin import _stiffened_plate  # a small model with one load case

import ada
from ada.fem.results.common import FEAResult


def test_a_deck_written_not_run_is_an_unexecuted_result(tmp_path):
    a = _stiffened_plate()
    res = a.to_fem("w", "code_aster", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    assert isinstance(res, FEAResult)
    assert res.executed is False and res.results == [] and res.job is not None
    assert (tmp_path / "w" / "w.comm").exists()


def test_a_result_read_from_a_file_has_nothing_to_execute(fem_files):
    res = ada.from_fem_res(fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN")
    assert res.executed is True and res.job is None
    with pytest.raises(ValueError, match="read from a file"):
        res.execute()


def test_from_fem_res_lazy_reads_the_cases_one_at_a_time(fem_files):
    res = ada.from_fem_res(fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN", lazy=True)
    assert res.results == [] and res.cases is not None and res.cases.stored
    assert res.mesh.thickness


def test_execute_runs_the_written_deck_and_overwrite_runs_it_again(tmp_path, require_solver):
    require_solver("code_aster")
    a = _stiffened_plate()
    res = a.to_fem("x", "code_aster", scratch_dir=tmp_path, overwrite=True, exit_on_complete=False)
    assert res.executed is False and res.results == []

    same = res.execute()
    assert same is res and res.executed is True and res.results, "the run's fields read into the same result"
    mess = tmp_path / "x" / "x.mess"
    first_run = mess.stat().st_mtime_ns

    res.execute()  # run already: nothing is done
    assert mess.stat().st_mtime_ns == first_run

    res.execute(overwrite=True)  # written and run again
    assert res.executed is True and res.results
    assert mess.stat().st_mtime_ns != first_run
