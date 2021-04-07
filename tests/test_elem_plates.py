import unittest

from ada import Assembly, Part, Plate
from ada.config import Settings
from ada.core.constants import O, X, Z

test_folder = Settings.test_dir / "plates"


atts = dict(origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))


class TestPlates(unittest.TestCase):
    def test_3dinit(self):
        pl1 = Plate("MyPl", [(0, 0, 0), (5, 0, 0), (5, 5, 0), (0, 5, 0)], 20e-3, use3dnodes=True)
        pl1._repr_html_()

    def test_2dinit(self):
        pl1 = Plate("MyPl", [(0, 0, 0.2), (5, 0), (5, 5), (0, 5)], 20e-3, **atts)
        pl1._repr_html_()

    def test_roundtrip_fillets(self):
        a = Assembly("ExportedPlates")
        p = Part("MyPart")
        a.add_part(p)
        pl1 = Plate("MyPl", [(0, 0, 0.2), (5, 0), (5, 5), (0, 5)], 20e-3, **atts)
        p.add_plate(pl1)

        atts2 = dict(origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, -1, 0))
        pl2 = Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts2)
        p.add_plate(pl2)

        a.to_ifc(test_folder / "my_plate_simple.ifc")

        b = Assembly("MyReimport")
        b.read_ifc(test_folder / "my_plate_simple.ifc")
        b.to_ifc(test_folder / "my_plate_simple_re_exported.ifc")

    def test_2ifc_simple(self):
        a = Assembly("ExportedPlates")
        p = Part("MyPart")
        a.add_part(p)

        atts2 = dict(origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, -1, 0))
        pl2 = Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts2)
        p.add_plate(pl2)
        a.to_ifc(test_folder / "my_plate_poly.ifc")


class BasicShapes(unittest.TestCase):
    def test_triangle(self):
        local_points2d = [(0, 0), (1, 0, 0.1), (0.5, 0.5)]
        pl = Plate("test", local_points2d, 20e-3, origin=O, normal=Z, xdir=X)

        a = Assembly() / [Part("te") / pl]
        a.to_ifc(test_folder / "triangle_plate.ifc")


class Plate2dIn(unittest.TestCase):
    def test_ex1(self):
        origin = [362237.0037951513, 100000.0, 560985.9095763591]
        csys = [
            [0.0, -1.0, 0.0],
            [-0.4626617625735456, 0.0, 0.8865348799975894],
            [-0.8865348799975894, 0.0, -0.4626617625735456],
        ]
        local_points2d = [
            [4.363213751783499e-11, 229.80445306040926],
            [1.4557793281525672e-11, -57.217605078163196],
            [2.912511939794014e-11, -207.22885580839431],
            [-330.0, -207.25, -4.518217213237464e-11],
            [-400.0, -122.2, 50.0],
            [-400.0, 42.79, 50.0],
            [-325.0, 267.83, 50.0],
            [-85.00004587126028, 650.0198678083951, 24.999999999931404],
            [-35.0, 650.02, -7.881391015070461e-12],
            [-35.0, 350.1],
            [-15.14, 261.52],
        ]
        thick = 30
        pl = Plate("test", local_points2d, thick, origin=origin, normal=csys[2], xdir=csys[0], units="mm")

        a = Assembly() / [Part("te") / pl]
        a.to_ifc(test_folder / "error_plate.ifc")

    def test_ex2(self):
        origin = [362857.44778571784, 100000.0, 561902.5557556185]
        csys = [
            [0.0, 1.0, 0.0],
            [-0.9756456931466083, 0.0, 0.2193524138104576],
            [0.2193524138104576, 0.0, 0.9756456931466083],
        ]
        local_points2d = [
            [-35.000000000029075, 246.8832348783828],
            [-15.0, 154.3],
            [6.4472604556706474e-15, 74.98044924694155],
            [0.0, -170.7],
            [-3.376144703353855e-14, -320.6997855533479],
            [-330.0, -320.7, -1.0189517968329821e-10],
            [-400.0, -235.7, 50.0],
            [-400.0, -70.7, 50.0],
            [-325.0, 154.3, 50.0],
            [-85.00004587117287, 500.03303441608927, 24.99999999997542],
            [-34.99999999999994, 500.0330344161669, -7.881391015070461e-12],
        ]
        thick = 30
        pl = Plate("test2", local_points2d, thick, origin=origin, normal=csys[2], xdir=csys[0], units="mm")

        a = Assembly(units="mm") / [Part("te", units="mm") / pl]
        a.to_ifc(test_folder / "error_plate2.ifc")


if __name__ == "__main__":
    unittest.main()
