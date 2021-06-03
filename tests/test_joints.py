import unittest

from ada import Assembly
from ada.config import Settings
from ada.param_models.basic_joints import joint_map
from ada.param_models.basic_module import SimpleStru

test_folder = Settings.test_dir / "joints"


class MyTestCase(unittest.TestCase):
    def test_joint_auto_map_param(self):
        a = Assembly() / SimpleStru("MySimpleStru")
        a.to_ifc(test_folder / "simplestru_no_joints.ifc")
        a.connections.find(joint_func=joint_map)
        a.to_ifc(test_folder / "simplestru_joints_b.ifc")


if __name__ == "__main__":
    unittest.main()
