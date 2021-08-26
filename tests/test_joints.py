import unittest

from ada import Assembly, Beam
from ada.config import Settings
from ada.param_models.basic_joints import joint_map

# from ada.param_models.basic_module import SimpleStru

test_dir = Settings.test_dir / "joints"


class BasicJoints(unittest.TestCase):
    def setUp(self) -> None:
        self.bm1 = Beam("Xmem", (0, 0, 0), (2, 0, 0), "IPE300")
        self.bm2 = Beam("Zmem", (1, 0, 0), (1, 0, 2), "IPE300", angle=90)
        self.bm3 = Beam("Ymem", (1, 0, 0), (1, 2, 0), "IPE300")

    def test_ipe_x2_90deg_Z(self):
        a = Assembly("IPE") / [self.bm1, self.bm2]
        a.connections.find(joint_func=joint_map)
        a.to_ifc(test_dir / "ipe_x2_90deg_Z.ifc")

    def test_ipe_x2_90deg_Y(self):
        a = Assembly("IPE") / [self.bm1, self.bm3]
        a.connections.find(joint_func=joint_map)
        a.to_ifc(test_dir / "ipe_x2_90deg_Y.ifc")


# class MyTestCase(unittest.TestCase):
#     def test_joint_auto_map_param(self):
#         a = Assembly() / SimpleStru("MySimpleStru")
#         a.to_ifc(test_dir / "simplestru_no_joints.ifc")
#
#         a.connections.find()
#         a.to_ifc(test_dir / "simplestru_joints_b.ifc")


if __name__ == "__main__":
    unittest.main()
