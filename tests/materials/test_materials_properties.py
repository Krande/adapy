import unittest

from ada import Material
from ada.materials.metals import CarbonSteel


class MyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.matS355 = Material("MatS355", mat_model=CarbonSteel("S355"))
        self.matS420 = Material("MatS420", mat_model=CarbonSteel("S420"))

    def test_main_properties(self):
        for model in [self.matS355.model, self.matS420.model]:
            self.assertEqual(model.E, 2.1e11)
            self.assertEqual(model.rho, 7850)
            self.assertEqual(model.v, 0.3)

        self.assertEqual(self.matS355.model.sig_y, 355e6)
        self.assertEqual(self.matS420.model.sig_y, 420e6)


if __name__ == "__main__":
    unittest.main()
