import numpy as np
import pytest

import ada
from ada.concepts.transforms import Placement
from ada.config import Settings
from ada.param_models.basic_module import EquipmentTent, SimpleStru

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


def test_simple_stru_with_equipment():
    p = SimpleStru(
        "SimpleStructure",
        10,
        10,
        3,
        gsec="BG200x100x10x20",
        csec="BG200x200x20x20",
        placement=Placement(origin=np.array([200, 100, 500])),
    )
    a = ada.Assembly() / p
    p.add_part(EquipmentTent("MyEqtent", 15e3, (2, 2, 1), height=1, width=1, length=2))

    p.fem = p.to_fem_obj(0.1)
    p.fem.sections.merge_by_properties()

    a.to_ifc(test_dir / "simple_stru_with_equipments", include_fem=True)
    a.to_fem("MySimpleStruWEquip", "usfos", overwrite=True)
