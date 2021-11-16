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


def test_ipe_x2_90deg_Z(bm1, bm2, joints_test_dir):
    a = Assembly("IPE") / [bm1, bm2]
    a.connections.find(joint_func=joint_map)
    a.to_ifc(joints_test_dir / "ipe_x2_90deg_Z.ifc")


def test_ipe_x2_45deg_Z(bm1, bm2_1, joints_test_dir):
    a = Assembly("IPE") / [bm1, bm2_1]
    a.connections.find(joint_func=joint_map)
    a.to_ifc(joints_test_dir / "ipe_x2_45deg_Z.ifc")


def test_ipe_x2_90deg_Y(bm1, bm3, joints_test_dir):
    a = Assembly("IPE") / [bm1, bm3]
    a.connections.find(joint_func=joint_map)
    a.to_ifc(joints_test_dir / "ipe_x2_90deg_Y.ifc")


# TODO: Fix cause behind this test case no longer producing a correct result


def test_joint_auto_map_param(joints_test_dir):
    a = Assembly() / SimpleStru("MySimpleStru")
    a.to_ifc(joints_test_dir / "simplestru_no_joints.ifc")

    a.connections.find(joint_func=joint_map)
    a.to_ifc(joints_test_dir / "simplestru_joints_b.ifc")
