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
