import pathlib

import pytest

import ada
from ada.config import Config

_temp_dir = Config().websockets_server_temp_dir


@pytest.fixture
def temp_dir():
    global _temp_dir
    if _temp_dir is None:
        return pathlib.Path("temp")
    else:
        return _temp_dir


@pytest.fixture
def base_stru(temp_dir) -> pathlib.Path:
    base_stru_ifc = temp_dir / "MyBaseStructure.ifc"

    if base_stru_ifc.exists():
        return base_stru_ifc
    else:
        from .components.create_floor import main as create_floor

        return create_floor()


@pytest.fixture
def add_stiffener_ifc(temp_dir):
    add_stiff_stru_ifc = temp_dir / "procedural" / "MyBaseStructure" / "add_stiffeners.ifc"
    if add_stiff_stru_ifc.exists():
        return add_stiff_stru_ifc


def test_add_stiffener(add_stiffener_ifc):
    a = ada.from_ifc(add_stiffener_ifc)
    a.to_gltf()
