import numpy as np
import pytest

from ada.concepts.transforms import Placement
from ada.param_models.basic_module import EquipmentTent, SimpleStru


@pytest.fixture
def param_models_test_dir(test_dir):
    return test_dir / "param_models"


@pytest.fixture
def eq_model_4legged():
    return EquipmentTent("MyEqtent", 15e3, (2, 2, 1), height=1, width=1, length=2)


@pytest.fixture
def simple_stru():
    return SimpleStru(
        "SimpleStructure",
        w=10,
        l=10,
        h=3,
        gsec="BG200x100x10x20",
        csec="BG200x200x20x20",
        placement=Placement(origin=np.array([200, 100, 500])),
    )


def test_eq_model_to_ifc_and_fem(eq_model_4legged, param_models_test_dir):
    eq_model_4legged.fem = eq_model_4legged.to_fem_obj(0.1)

    assert len(eq_model_4legged.sections) == 1
    assert len(eq_model_4legged.materials) == 2

    # import ada
    # from ada.fem import Load, StepImplicit
    # a = ada.Assembly() / eq_model_4legged
    # a.to_ifc(test_dir / "eq_model.ifc")
    # a.to_fem("EqtentFEM", "sesam", scratch_dir=param_models_test_dir, overwrite=True)


def test_simple_stru_with_equipment(simple_stru, eq_model_4legged, param_models_test_dir):
    simple_stru.add_part(eq_model_4legged)
    simple_stru.move_all_mats_and_sec_here_from_subparts()

    # Build FEM model
    simple_stru.fem = simple_stru.to_fem_obj(0.3)
    simple_stru.add_bcs()
    assert len(simple_stru.fem.sections) == 76
    simple_stru.fem.sections.merge_by_properties()
    assert len(simple_stru.fem.sections) == 8

    # Add loads
    # step = simple_stru.fem.add_step(StepImplicit("Static", nl_geom=True))
    # step.add_load(Load("Grav", Load.TYPES.ACC, -9.81, dof=3))

    # Export to STEP,IFC and FEM
    # import ada
    # from ada.fem import Load, StepImplicit
    # a = ada.Assembly() / simple_stru
    # a.to_stp(test_dir / "simple_stru_with_equipments_before_fem")
    # a.to_ifc(test_dir / "simple_stru_with_equipments_before_fem", include_fem=False)
    # a.to_fem("MySimpleStruWEquip_ca", "code_aster", overwrite=True, execute=True)
    # a.to_fem("MySimpleStruWEquip_ufo", "usfos", overwrite=True)
    # a.to_fem("MySimpleStruWEquip_ses", "sesam", overwrite=True)
    # a.to_ifc(param_models_test_dir / "simple_stru_with_equipments_after_fem", include_fem=True)
