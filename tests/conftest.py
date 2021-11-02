import os
import pathlib

import pytest

import ada


@pytest.fixture
def this_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().absolute().parent


@pytest.fixture
def test_dir() -> pathlib.Path:
    testing_dir = ada.config.Settings.test_dir
    os.makedirs(testing_dir, exist_ok=True)
    return testing_dir


@pytest.fixture
def example_files(this_dir) -> pathlib.Path:
    return this_dir / ".." / "files"


@pytest.fixture
def test_shell_beam() -> ada.Assembly:
    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    return ada.Assembly("MyAssembly") / (ada.Part("MyPart", fem=bm.to_fem_obj(0.1, "shell")) / bm)
