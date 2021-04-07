import unittest

from ada import Assembly, Beam, CurvePoly, Section
from ada.config import Settings
from ada.param_models.basic_module import SimpleStru, make_it_complex

test_folder = Settings.test_dir / "step_basics"


class MyStepCases(unittest.TestCase):
    def test_simple_beam(self):
        bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), Section("mysec", from_str="IPE300"))
        bm.to_stp(test_folder / "MySimpleBeam.stp")

    def test_advanced_beam(self):
        poly = CurvePoly([(0, 0), (0.1, 0, 0.01), (0.1, 0.1, 0.01), (0, 0.1)], (0, 0, 0), (1, 0, 0), (0, 1, 0))
        bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), Section("MySec", outer_poly=poly))
        bm.to_stp(test_folder / "MySimpleBeamPoly.stp")

    def test_simple_stru(self):
        a = Assembly("MyTest")
        p = SimpleStru("MyPart")
        a.add_part(p)
        a.to_stp(test_folder / "MySimpleStru.stp")

    def test_complex_stru(self):
        a = make_it_complex()
        p = SimpleStru("MyPart")
        a.add_part(p)
        a.to_stp(test_folder / "MyComplexStru.stp")


if __name__ == "__main__":
    unittest.main()
