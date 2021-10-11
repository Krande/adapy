import unittest

from ada import Assembly, Material, Part, Plate
from ada.concepts.containers import Materials
from ada.config import Settings
from ada.materials.metals import CarbonSteel

test_folder = Settings.test_dir / "materials"


class MaterialProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.mat1 = Material("Mat1", mat_model=CarbonSteel())
        self.mat2 = Material("Mat2", mat_model=CarbonSteel())

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

    def test_negative_contained(self):
        collection = Materials([self.mat1])
        self.assertFalse(self.mat2 in collection)

    def test_positive_contained(self):
        collection = Materials([self.mat1, self.mat2])
        self.assertTrue(self.mat2 in collection)


if __name__ == "__main__":
    unittest.main()
