import unittest

from ada import Assembly, Beam, Part

bm1 = Beam("Bm1", (0, 0, 0), (1, 0, 0), "IPE300")
bm2 = Beam("Bm2", (0, 0, 2), (1, 0, 2), "IPE300")
bm3 = Beam("Bm3", (0, 0, 4), (1, 0, 4), "IPE300")


class SmartAppends(unittest.TestCase):
    def test_ex1(self):
        a = Assembly("MyAssembly") / [Part("MyPart") / bm1, bm2]
        p = a.parts["MyPart"]
        assert p.beams.from_name("Bm1")
        assert a.beams.from_name("Bm2")

    def test_ex2(self):
        a = Assembly("MyAssembly") / (Part("MyPart") / [bm1, bm2, bm3])
        p = a.parts["MyPart"]
        assert p.beams.from_name("Bm1")
        assert p.beams.from_name("Bm2")


if __name__ == "__main__":
    unittest.main()
