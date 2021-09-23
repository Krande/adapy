import unittest

from ada import Assembly, Material, Part
from ada.config import Settings

test_folder = Settings.test_dir / "materials"


class MyTestCase(unittest.TestCase):
    def test_material_ifc_roundtrip(self):

        ifc_name = "my_material.ifc"

        a = Assembly("MyAssembly")
        p = Part("MyPart")
        p.add_material(Material("my_mat"))
        a.add_part(p)
        a.to_ifc(test_folder / ifc_name)

        b = Assembly("MyImport")
        b.read_ifc(test_folder / ifc_name)
        # assert len(b.materials) == 1

    def test_material_from_ifc(self):
        pass
        # self.assertEqual(True, False)


if __name__ == "__main__":
    unittest.main()
