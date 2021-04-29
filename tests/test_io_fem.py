import unittest

from common import build_test_model

from ada.core.utils import roundoff


class TestFemProperties(unittest.TestCase):
    def test_calc_cog(self):

        a = build_test_model()
        p = a.parts["ParametricModel"]
        cog = p.fem.elements.calc_cog()

        tol = 0.01

        assert abs(roundoff(cog[0]) - 2.5) < tol
        assert abs(roundoff(cog[1]) - 2.5) < tol
        assert abs(roundoff(cog[2]) - 1.5) < tol
        assert abs(roundoff(cog[3]) - 7854.90) < tol
        assert abs(roundoff(cog[4]) - 1.001) < tol


if __name__ == "__main__":
    unittest.main()
