import unittest

from ada import Assembly, Beam, Part
from ada.fem.mesh.gmshapiv2 import GmshSession


class GmshApiV2(unittest.TestCase):
    def setUp(self) -> None:
        self.bm1 = Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300")
        self.bm2 = Beam("MySecondBeam", (1.01, 0, 0), (2, 0, 0), "IPE300")
        self.bm3 = Beam("MyThirdBeam", (2.01, 0, 0), (3, 0, 0), "IPE300")

    def test_multiple_geom_repr(self):
        with GmshSession(silent=False) as gs:
            gs.add_obj(self.bm1, "shell")
            gs.add_obj(self.bm2, "solid")
            gs.add_obj(self.bm3, "beam")
            gs.mesh(0.1)
            fem = gs.get_fem()
        a = Assembly() / (Part("MyBeam", fem=fem) / [self.bm1, self.bm2, self.bm3])
        a.to_fem("my_aba_bm", "code_aster", overwrite=True)
        print(fem)


if __name__ == "__main__":
    unittest.main()
