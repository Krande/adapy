import unittest

from ada import Assembly, Material, Part, Plate
from ada.config import Settings
from ada.materials.metals import CarbonSteel

test_folder = Settings.test_dir / "materials"


class MaterialProtocol(unittest.TestCase):
    def test_merge_materials(self):
        plates = []

        for i in range(1, 10):
            mat = Material(f"mat{i}", CarbonSteel("S355"))
            plates.append(Plate(f"pl{i}", [(0, 0, 0), (0, 1, 0), (1, 1, 0)], 20e-3, mat=mat))

        a = Assembly() / (Part("MyPart") / plates)
        p = a.get_part("MyPart")
        mats = p.materials
        self.assertEqual(len(mats), 9)
        mats.merge_materials_by_properties()
        self.assertEqual(len(mats), 1)


if __name__ == "__main__":
    unittest.main()
