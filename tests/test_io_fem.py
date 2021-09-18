import unittest

from common import build_test_simplestru_fem

from ada.core.utils import roundoff


class TestFemProperties(unittest.TestCase):
    def test_calc_cog(self):

        a = build_test_simplestru_fem()
        p = a.parts["ParametricModel"]
        cog = p.fem.elements.calc_cog()
        tol = 0.01

        assert abs(roundoff(cog.p[0]) - 2.5) < tol
        assert abs(roundoff(cog.p[1]) - 2.5) < tol
        assert abs(roundoff(cog.p[2]) - 1.5) < tol
        assert abs(roundoff(cog.tot_mass) - 7854.90) < tol
        assert abs(roundoff(cog.tot_vol) - 1.001) < tol


if __name__ == "__main__":
    unittest.main()
