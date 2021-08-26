import unittest

from ada import Beam
from ada.calc import BeamCalc


class TestCalculations(unittest.TestCase):
    def test_basic_udl(self):
        bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300")
        udl = BeamCalc(bm)
        udl.add_distributed_load(-1e3)
        displ_latex = udl.get_displ_formula()
        shear_latex = udl.get_shear_formula()
        moment_latex = udl.get_moment_formula()

        self.assertEqual(displ_latex, "$$\\frac{w x \\left(L^3-2L x^2+x^3\\right)}{24E I}$$")
        self.assertEqual(moment_latex, "$$\\frac{w x \\left(L-x\\right)}{2}$$")
        self.assertEqual(shear_latex, "$$w \\left(\\frac{L}{2}-x\\right)$$")

        udl._repr_html_()


if __name__ == "__main__":
    unittest.main()
