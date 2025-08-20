import pytest

from ada import Assembly, Beam
from ada.param_models.basic_joints import joint_map
from ada.param_models.basic_module import SimpleStru


@pytest.fixture
def bm1():
    return Beam("Xmem", (0, 0, 0), (2, 0, 0), "IPE300")


@pytest.fixture
def bm2():
    return Beam("Zmem", (1, 0, 0), (1, 0, 2), "IPE300", angle=90)


@pytest.fixture
def bm2_1():
    return Beam("Zmem_45", (1, 0, 0), (3, 0, 2), "IPE300", angle=0)


@pytest.fixture
def bm3():
    return Beam("Ymem", (1, 0, 0), (1, 2, 0), "IPE300")


def test_ipe_x2_90deg_Z(bm1, bm2):
    a = Assembly("IPE") / [bm1, bm2]
    a.connections.find(joint_func=joint_map)
    _ = a.to_ifc(file_obj_only=True)


def test_ipe_x2_45deg_Z(bm1, bm2_1):
    a = Assembly("IPE") / [bm1, bm2_1]
    a.connections.find(joint_func=joint_map)
    _ = a.to_ifc(file_obj_only=True)


def test_ipe_x2_90deg_Y(bm1, bm3):
    a = Assembly("IPE") / [bm1, bm3]
    a.connections.find(joint_func=joint_map)
    _ = a.to_ifc(file_obj_only=True)


# TODO: Fix cause behind this test case no longer producing a correct result


def test_joint_auto_map_param(tmp_path):
    a = Assembly() / SimpleStru("MySimpleStru")
    _ = a.to_ifc(tmp_path / "simplestru_no_joints.ifc", file_obj_only=True)

    a.connections.find(joint_func=joint_map)
    a.to_ifc(tmp_path / "simplestru_joints_b.ifc", file_obj_only=True)
