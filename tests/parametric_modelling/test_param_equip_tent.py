import pytest

import ada
from ada.config import Settings
from ada.param_models.basic_module import EquipmentTent

test_dir = Settings.test_dir / "param_models"


@pytest.fixture
def eq_model_4legged():
    return EquipmentTent("MyEqtent", 15e3, (140, 180, 500), height=3, width=2, length=4)


def test_eq_model_to_ifc_and_fem(eq_model_4legged):
    a = ada.Assembly() / eq_model_4legged
    a.to_ifc(test_dir / "eq_model.ifc")
    eq_model_4legged.fem = eq_model_4legged.to_fem_obj(0.1)

    assert len(eq_model_4legged.sections) == 1
    assert len(eq_model_4legged.materials) == 1

    a.to_fem("EqtentFEM", "sesam", scratch_dir=test_dir, overwrite=True)
