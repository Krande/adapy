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
