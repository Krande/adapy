import os
import pathlib

import pytest

import ada
from ada.visualize.renderer_pythreejs import MyRenderer, SectionRenderer

is_printed = False


def dummy_display_func(ada_obj):
    if type(ada_obj) is ada.Section:
        sec_render = SectionRenderer()
        _, _ = sec_render.build_display(ada_obj)
    else:
        renderer = MyRenderer()
        renderer.DisplayObj(ada_obj)
        renderer.build_display()


@pytest.fixture
def dummy_display():
    return dummy_display_func


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


@pytest.fixture
def plate1():
    return ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3)


@pytest.fixture
def bm_ipe300():
    return ada.Beam("MyIPE300", (0, 0, 0), (5, 0, 0), "IPE300")
