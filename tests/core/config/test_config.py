import os
from unittest import mock

import pytest


@pytest.fixture
def workspace_file(tmp_path):
    with open(tmp_path / "ada_config.toml", "w") as f:
        f.write("point_tol=1e-4")
    return tmp_path


@pytest.fixture
def workspace_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with mock.patch.dict(os.environ, clear=True):
        envvars = {"ADA_GENERAL_POINT_TOL": "0.1"}
        for k, v in envvars.items():
            monkeypatch.setenv(k, v)
        yield  # This is the magical bit which restore the environment after


def test_basic_config(monkeypatch, workspace_file):
    monkeypatch.chdir(workspace_file)
    from ada.config import Config

    config = Config()

    assert config.general_point_tol == 1e-4


def test_env_config(workspace_env):
    from ada.config import Config

    config = Config()

    assert config.general_point_tol == 1e-1


@pytest.fixture
def config_files(tmp_path, monkeypatch):
    """A working directory holding ada_config.toml, and two files elsewhere."""
    monkeypatch.delenv("ADA_CONFIG_FILE", raising=False)
    local = tmp_path / "project"
    local.mkdir()
    (local / "ada_config.toml").write_text("")
    monkeypatch.chdir(local)
    for name in ("explicit.toml", "from_env.toml"):
        (tmp_path / name).write_text("")
    return tmp_path


def test_config_file_from_the_environment_wins(config_files, monkeypatch):
    from ada.config import Config

    monkeypatch.setenv("ADA_CONFIG_FILE", str(config_files / "from_env.toml"))
    assert Config.find_file(str(config_files / "explicit.toml")) == str(config_files / "from_env.toml")


def test_explicit_config_file_beats_the_working_directory(config_files):
    from ada.config import Config

    assert Config.find_file(str(config_files / "explicit.toml")) == str(config_files / "explicit.toml")


def test_missing_config_files_fall_through_to_the_working_directory(config_files, monkeypatch):
    from ada.config import Config

    monkeypatch.setenv("ADA_CONFIG_FILE", str(config_files / "absent.toml"))
    assert Config.find_file(str(config_files / "also_absent.toml")) == str(config_files / "project" / "ada_config.toml")


def test_the_working_directory_is_read_when_looking(config_files, monkeypatch):
    from ada.config import Config

    monkeypatch.chdir(config_files)  # after ada.config was imported
    assert Config.find_file() is None


def test_a_deleted_working_directory_is_not_fatal(monkeypatch):
    import pathlib
    import tempfile

    from ada import config

    def gone():
        raise FileNotFoundError

    monkeypatch.setattr(pathlib.Path, "cwd", gone)
    assert config._working_dir() == pathlib.Path(tempfile.gettempdir())


def _bare_env(monkeypatch, tmp_path):
    """An environment with nothing but the ADA_* variables the test sets, in an empty working
    directory, so no file or inherited variable interferes."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADA_CONFIG_FILE", raising=False)
    return mock.patch.dict(os.environ, clear=True)


def test_list_entry_keeps_a_scalar_env_value_whole(monkeypatch, tmp_path):
    """A single env value without ',' or '[' is one list item, not a string to iterate:
    list("/data") would give ['/', 'd', 'a', 't', 'a'], and list[pathlib.Path] never
    converted its items to Path."""
    import pathlib

    from ada.config import Config

    with _bare_env(monkeypatch, tmp_path):
        monkeypatch.setenv("ADA_GENERAL_VALID_UNITS", "mm")
        monkeypatch.setenv("ADA_WEBSOCKETS_EXTERNAL_FILES_DIRS", "/data")
        config = Config()
        assert config.general_valid_units == ["mm"]
        assert config.websockets_external_files_dirs == [pathlib.Path("/data")]


def test_list_entry_splits_a_comma_separated_env_value(monkeypatch, tmp_path):
    from ada.config import Config

    with _bare_env(monkeypatch, tmp_path):
        monkeypatch.setenv("ADA_GENERAL_VALID_UNITS", "mm,cm")
        assert Config().general_valid_units == ["mm", "cm"]


def test_unknown_ada_variables_are_ignored_even_with_json_like_values(monkeypatch, tmp_path):
    """A variable naming no section or entry is ignored whole. It used to be JSON-corrected
    before the lookup, so ADA_VIEWER_HOST=[::1] raised a raw JSONDecodeError and a '{' value
    raised ConfigError even though the variable maps to nothing."""
    from ada.config import Config

    with _bare_env(monkeypatch, tmp_path):
        monkeypatch.setenv("ADA_VIEWER_HOST", "[::1]")
        monkeypatch.setenv("ADA_SOME_UNKNOWN_TABLE", "{not json")
        monkeypatch.setenv("ADA_GENERAL_UNKNOWN_ENTRY", "[1,2")
        config = Config()
        assert config.general_point_tol == 1e-4


def test_known_list_entry_with_malformed_json_raises_config_error(monkeypatch, tmp_path):
    from ada.config import Config, ConfigError

    with _bare_env(monkeypatch, tmp_path):
        monkeypatch.setenv("ADA_GENERAL_VALID_UNITS", "[1,2")
        with pytest.raises(ConfigError, match="not a valid JSON list"):
            Config()
