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


def test_to_fem_lazy_returns_the_cases_to_read_one_at_a_time(tmp_path, require_solver):
    require_solver("code_aster")
    from ada.fem.results.field_data import FieldRole

    a = _stiffened_plate()
    fea = a.to_fem("lz", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    assert fea.executed and fea.results == [] and fea.cases.names == {1: "pull"}
    assert fea.mesh.thickness and fea.mesh.element_frames
    ((case, step),) = fea.cases.iter_steps([1], roles=[FieldRole.SHELL_STRESS])
    assert case == 1 and all(f.role == FieldRole.SHELL_STRESS for f in step.results) and step.results

    # case_fields: {case: fields} of the roles asked for, by FieldRole or its name.
    stresses = fea.case_fields(FieldRole.SHELL_STRESS)
    assert list(stresses) == [1] and stresses[1] and all(f.role == FieldRole.SHELL_STRESS for f in stresses[1])
    both = fea.case_fields("nodal_displacement", "nodal_reaction", cases=[1])
    assert {f.role for f in both[1]} == {FieldRole.NODAL_DISPLACEMENT, FieldRole.NODAL_REACTION}


def test_a_result_read_case_by_case_is_baked_for_the_viewer_from_its_file(fem_files, tmp_path, monkeypatch):
    """``fea.show()`` of a lazy result: no fields in memory, so the bake reads the file, as the REST viewer's does."""
    import json

    from ada.fem.results import show_streaming

    monkeypatch.setenv(show_streaming.ENV_FEA_BAKE_DIR, str(tmp_path / "bakes"))
    lazy = ada.from_fem_res(fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN", lazy=True)
    bake_dir, _ = show_streaming.bake_for_show(lazy)
    manifest = json.loads((bake_dir / show_streaming.MANIFEST_NAME).read_text())
    assert manifest.get("fields"), "the bake holds the file's fields"


def test_derived_von_mises_is_worked_out_and_baked_beside_the_files_fields(fem_files, tmp_path, monkeypatch):
    """``fea.derived_field("von_mises")`` per case and node; ``fea.show(derived=["von_mises"])`` bakes it."""
    import json

    from ada.fem.results import show_streaming

    monkeypatch.setenv(show_streaming.ENV_FEA_BAKE_DIR, str(tmp_path / "bakes"))
    lazy = ada.from_fem_res(fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN", lazy=True)
    vm = lazy.derived_field("von_mises")
    assert set(vm) == set(lazy.cases.stored)
    assert any(nodes for nodes in vm.values()) and all(v >= 0 for nodes in vm.values() for v in nodes.values())

    bake_dir, bake_id = show_streaming.bake_for_show(lazy, derived=["von_mises"])
    manifest = json.loads((bake_dir / show_streaming.MANIFEST_NAME).read_text())
    assert "von_mises" in json.dumps(manifest["fields"])
    assert bake_id != show_streaming.bake_id_for(lazy), "a bake with derived fields is not the plain one"

    with pytest.raises(KeyError, match="no derived quantity"):
        lazy.derived_field("nonsense")
    whole = ada.from_fem_res(fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN")
    with pytest.raises(ValueError, match="lazy=True"):
        show_streaming.bake_for_show(whole, derived=["von_mises"])


def test_an_unexecuted_result_is_not_shown(tmp_path):
    from ada.fem.results.show_streaming import show_streaming

    a = _stiffened_plate()
    res = a.to_fem("u", "code_aster", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    with pytest.raises(ValueError, match=r"execute\(\)"):
        show_streaming(res)


def test_case_fields_needs_a_result_read_case_by_case(fem_files):
    whole = ada.from_fem_res(fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN")
    with pytest.raises(ValueError, match="lazy=True"):
        whole.case_fields("shell_stress")


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
