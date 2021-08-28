import unittest

from ada import Assembly, Beam, Part, Plate, PrimBox
from ada.config import Settings
from ada.fem.mesh.gmshapiv2 import GmshSession

test_dir = Settings.test_dir / "gmsh_api_v2"


class GmshApiV2(unittest.TestCase):
    def setUp(self) -> None:
        self.bm1 = Beam("MyBeam", (0, 0, 1), (1, 0, 1), "IPE300")
        self.bm2 = Beam("MySecondBeam", (1.01, 0, 1), (2, 0, 1), "IPE300")
        self.bm3 = Beam("MyThirdBeam", (2.01, 0, 1), (3, 0, 1), "IPE300")

        pl_atts = dict(origin=(1, 1, 1), xdir=(1, 0, 0), normal=(0, 0, 1))
        pl_points = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.pl1 = Plate("MyPlate", pl_points, 10e-3, **pl_atts)

        self.shp1 = PrimBox("MyBox", (1, -2, -2), (2, -1, -1))

    def test_multiple_geom_repr(self):
        with GmshSession(silent=True) as gs:
            gs.add_obj(self.bm1, "shell")
            gs.add_obj(self.bm2, "solid")
            gs.add_obj(self.bm3, "line")
            gs.add_obj(self.pl1, "shell")
            gs.add_obj(self.shp1, "solid")
            gs.mesh(0.1)
            fem = gs.get_fem()

        a = Assembly() / (Part("MyBeam", fem=fem) / [self.bm1, self.bm2, self.bm3, self.pl1, self.shp1])
        a.to_fem("my_aba_bm", "code_aster", overwrite=True, scratch_dir=test_dir)
        a.to_ifc(test_dir / "gmsh_api_v2", include_fem=True)
        print(fem.elements)
        self.assertEqual(len(list(fem.elements.lines)), 10)
        self.assertEqual(len(list(fem.elements.shell)), 436)
        self.assertEqual(len(list(fem.elements.solids)), 555)


if __name__ == "__main__":
    unittest.main()
