import unittest

from ada import Beam
from ada.calc.udl import UDL


class TestCalculations(unittest.TestCase):
    def test_basic_udl(self):
        bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300")
        udl = UDL(bm)
        udl.add_distributed_load(-1e3)
        udl._repr_html_()


if __name__ == "__main__":
    unittest.main()
