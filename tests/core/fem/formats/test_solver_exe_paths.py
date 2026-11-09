"""One lookup for solver executables: Config first, then the deprecated variables, then PATH."""

from __future__ import annotations

import os
import pathlib
import warnings

import pytest

from ada.config import Config, ConfigError
from ada.fem.formats import utils
from ada.fem.formats.abaqus.versions import get_abaqus_exe
from ada.fem.formats.general import get_available_software
from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_default_exe_path


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    """No solver variable, nothing on PATH, no install default, and a working directory with no config file."""
    for name in list(os.environ):
        if name.upper().endswith("_EXE") and name.upper().startswith("ADA_") or name == "ADA_FEA_FEM_EXE_PATHS":
            monkeypatch.delenv(name)
    monkeypatch.delenv("ADA_CONFIG_FILE", raising=False)
    monkeypatch.setattr(utils, "_exe_on_path", lambda name: None)
    monkeypatch.setattr("ada.fem.formats.sesam.sesam_exe_locator._get_default_exe_path", lambda name: None)
    monkeypatch.setattr(utils, "_LOGGED_EXE_VARIABLES", set())
    monkeypatch.chdir(tmp_path)
    Config._instances = {}


def exe(tmp_path, name) -> pathlib.Path:
    path = tmp_path / name
    path.write_text("")
    return path


def write_config(tmp_path, text: str) -> None:
    (tmp_path / "ada_config.toml").write_text(text)
    Config._instances = {}


def test_a_path_only_in_the_file_is_available_and_launchable(tmp_path):
    abaqus = exe(tmp_path, "abaqus")
    write_config(tmp_path, f'[fea]\nfem_exe_paths = {{ abaqus = "{abaqus.as_posix()}" }}\n')
    assert "abaqus" in get_available_software() and utils.get_exe_path("abaqus") == abaqus


def test_a_path_only_in_the_environment_table_is_available(tmp_path, monkeypatch):
    sestra = exe(tmp_path, "sestra")
    monkeypatch.setenv("ADA_FEA_FEM_EXE_PATHS", f'{{"sestra": "{sestra.as_posix()}"}}')
    Config._instances = {}
    assert "sesam" in get_available_software() and pathlib.Path(get_sestra_default_exe_path()) == sestra


def test_the_environment_overrides_one_key_and_keeps_the_rest(tmp_path, monkeypatch):
    in_file, in_env = exe(tmp_path, "sestra"), exe(tmp_path, "abaqus")
    write_config(tmp_path, f'[fea]\nfem_exe_paths = {{ sestra = "{in_file.as_posix()}", abaqus = "/not/this" }}\n')
    monkeypatch.setenv("ADA_FEA_FEM_EXE_PATHS", f"{{'abaqus': '{in_env.as_posix()}'}}")
    Config._instances = {}
    table = Config().fea_fem_exe_paths
    assert table["sestra"] == in_file.as_posix() and table["abaqus"] == in_env.as_posix()


def test_an_environment_setting_keeps_the_rest_of_its_file_section(tmp_path, monkeypatch):
    path = exe(tmp_path, "sestra")
    write_config(tmp_path, f'[fea]\nfem_exe_paths = {{ sestra = "{path.as_posix()}" }}\n')
    monkeypatch.setenv("ADA_FEA_EXECUTE_DIR", "somewhere")
    Config._instances = {}
    assert Config().fea_fem_exe_paths["sestra"] == path.as_posix()


def test_a_variable_that_has_gone_stops_applying_on_reload(tmp_path, monkeypatch):
    path = exe(tmp_path, "abaqus")
    monkeypatch.setenv("ADA_FEA_FEM_EXE_PATHS", f'{{"abaqus": "{path.as_posix()}"}}')
    monkeypatch.setenv("ADA_FEA_EXECUTE_DIR", "somewhere")
    Config._instances = {}
    config = Config()
    assert config.fea_fem_exe_paths["abaqus"]
    monkeypatch.delenv("ADA_FEA_FEM_EXE_PATHS")
    config.reload_config()
    assert config.fea_fem_exe_paths["abaqus"] is None


@pytest.mark.parametrize("value", ['{"abaqus": ', "not a table"])
def test_a_malformed_table_names_the_variable(monkeypatch, value):
    monkeypatch.setenv("ADA_FEA_FEM_EXE_PATHS", value)
    Config._instances = {}
    with pytest.raises(ConfigError, match="ADA_FEA_FEM_EXE_PATHS"):
        Config()


def test_config_beats_an_old_variable_without_warning(tmp_path, monkeypatch):
    configured, old = exe(tmp_path, "configured"), exe(tmp_path, "old")
    write_config(tmp_path, f'[fea]\nfem_exe_paths = {{ abaqus = "{configured.as_posix()}" }}\n')
    monkeypatch.setenv("ADA_abaqus_exe", str(old))
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert utils.get_exe_path("abaqus") == configured


@pytest.mark.parametrize("variable", ["ADA_abaqus_exe", "ADA_ABAQUS_EXE"])
def test_an_old_variable_alone_still_works_and_warns(tmp_path, monkeypatch, variable):
    old = exe(tmp_path, "old")
    monkeypatch.setenv(variable, str(old))
    with pytest.warns(DeprecationWarning, match=r"fem_exe_paths\.abaqus"):
        assert get_abaqus_exe() == old


@pytest.mark.parametrize("variable", ["ADA_SESTRA_EXE", "ADA_sesam_exe"])
def test_sestra_aliases_resolve_with_a_warning(tmp_path, monkeypatch, variable):
    old = exe(tmp_path, "old")
    monkeypatch.setenv(variable, str(old))
    with pytest.warns(DeprecationWarning, match=r"fem_exe_paths\.sestra"):
        assert get_sestra_default_exe_path() == str(old)


def test_an_old_variable_pointing_nowhere_is_passed_over(monkeypatch):
    monkeypatch.setenv("ADA_abaqus_exe", "/nothing/here")
    assert get_abaqus_exe() is None


def test_a_configured_path_with_nothing_there_raises(tmp_path):
    write_config(tmp_path, '[fea]\nfem_exe_paths = { abaqus = "/nothing/here" }\n')
    with pytest.raises(FileNotFoundError, match="ABAQUS"):
        get_available_software()


def test_nothing_set_lists_only_the_free_solvers():
    assert get_available_software() == ["calculix", "code_aster"]


def test_path_comes_after_the_old_variables(tmp_path, monkeypatch):
    old, on_path = exe(tmp_path, "old"), exe(tmp_path, "abaqus")
    monkeypatch.setattr(utils, "_exe_on_path", lambda name: on_path)
    monkeypatch.setenv("ADA_abaqus_exe", str(old))
    with pytest.warns(DeprecationWarning):
        assert utils.get_exe_path("abaqus") == old
    monkeypatch.delenv("ADA_abaqus_exe")
    assert utils.get_exe_path("abaqus") == on_path


def test_a_solver_nowhere_raises_from_get_exe_path():
    with pytest.raises(FileNotFoundError, match="No Path to Executable"):
        utils.get_exe_path("abaqus")
