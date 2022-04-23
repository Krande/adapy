import pytest

import ada
from ada import Assembly, Material, Part
from ada.config import Settings

test_folder = Settings.test_dir / "materials"


@pytest.fixture
def materials_test_dir(test_dir):
    return test_dir / "materials"


def test_material_ifc_roundtrip(materials_test_dir):
    ifc_path = materials_test_dir / "my_material.ifc"

    p = Part("MyPart")
    p.add_material(Material("my_mat"))
    a = Assembly("MyAssembly") / p
    fp = a.to_ifc(ifc_path, return_file_obj=True)

    b = ada.from_ifc(fp)
    assert len(b.materials) == 1
