import unittest

from ada import Assembly, Beam, Part, Plate, PrimCyl, PrimExtrude
from ada.config import Settings
from ada.core.utils import align_to_plate
from ada.fem.meshing import GmshOptions

test_folder = Settings.test_dir / "mesh"

atts = dict(origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
atts2 = dict(origin=(1, 0, -0.1), xdir=(0, 0, 1), normal=(-1, 0, 0))


class BeamIO(unittest.TestCase):
    def setUp(self) -> None:
        self.profiles = [("IPE220", 680, 1484)]

    def test_beam_mesh(self):
        bm = Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220")

        bm.add_penetration(PrimCyl("Cylinder", (0.5, -0.5, 0), (0.5, 0.5, 0), 0.05))
        a = Assembly("Test") / (Part("MyFem") / bm)

        bm.parent.fem = bm.to_fem_obj(0.1, "solid", options=GmshOptions(Mesh_ElementOrder=2))
        print(a)
        self.assertAlmostEqual(len(bm.parent.fem.elements), 680, delta=10)
        self.assertAlmostEqual(len(bm.parent.fem.nodes), 1484, delta=10)

        # a.to_fem("my_test", "xdmf", scratch_dir=test_folder, fem_converter="meshio", overwrite=True)

    def test_plate_mesh(self):
        pl1 = Plate("MyPl", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts)
        pl2 = Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts2)
        points = [(1, 1, 0.2), (2, 1, 0.2), (2, 2, 0.2), (1, 2, 0.2)]
        pl1.add_penetration(PrimExtrude("poly_extrude", points, **align_to_plate(pl1)))
        pl1.add_penetration(PrimExtrude("poly_extrude2", points, **align_to_plate(pl2)))

        a = Assembly("Test") / (Part("MyFem") / [pl1, pl2])
        print(a)

        parent = pl1.parent
        parent.fem = pl1.to_fem_obj(0.3, "shell")
        parent.fem += pl2.to_fem_obj(0.3, "shell")

        # a.to_ifc(test_folder / "ADA_pl_mesh_ifc")
        # a.to_fem("my_xdmf_plate", "xdmf", overwrite=True, scratch_dir=test_folder, fem_converter="meshio")
        # a.to_fem("ADA_pl_mesh_code_aster", "code_aster", scratch_dir=test_folder, overwrite=True)
        # a.to_fem("ADA_pl_mesh", "abaqus", scratch_dir=test_folder, overwrite=True)

        self.assertEqual(len(parent.fem.elements), 1412)
        self.assertEqual(len(parent.fem.nodes), 786)


if __name__ == "__main__":
    unittest.main()
