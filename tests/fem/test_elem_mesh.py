import unittest

from ada import Assembly, Beam, Part, Placement, Plate, PrimCyl, PrimExtrude
from ada.config import Settings
from ada.core.alignment_utils import align_to_plate

test_dir = Settings.test_dir / "mesh"


class BeamIO(unittest.TestCase):
    def test_beam_mesh(self):
        bm = Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220")

        bm.add_penetration(PrimCyl("Cylinder", (0.5, -0.5, 0), (0.5, 0.5, 0), 0.05))
        a = Assembly("Test") / (Part("MyFem") / bm)

        bm.parent.fem = bm.to_fem_obj(0.5, "line")
        a.to_ifc(test_dir / "ADA_bm_mesh_ifc", include_fem=True)
        print(a)
        self.assertEqual(len(bm.parent.fem.elements), 2)
        self.assertEqual(len(bm.parent.fem.nodes), 3)


class PlateIO(unittest.TestCase):
    def setUp(self) -> None:
        atts = dict(placement=Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, 0, 1)))
        atts2 = dict(placement=Placement(origin=(1, 0, -0.1), xdir=(0, 0, 1), zdir=(-1, 0, 0)))
        self.pl1 = Plate("MyPl", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts)
        self.pl2 = Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts2)

    def test_basic_plate(self):
        pl1 = self.pl1
        a = Assembly("Test") / (Part("MyFem") / [pl1])
        p = pl1.parent
        p.fem = pl1.to_fem_obj(5, "shell")
        a.to_ifc(test_dir / "ADA_pl_mesh_ifc", include_fem=False)

    def test_plate_mesh(self):

        points = [(1, 1, 0.2), (2, 1, 0.2), (2, 2, 0.2), (1, 2, 0.2)]
        pl1, pl2 = self.pl1, self.pl2
        pl1.add_penetration(PrimExtrude("poly_extrude", points, **align_to_plate(pl1)))
        pl1.add_penetration(PrimExtrude("poly_extrude2", points, **align_to_plate(pl2)))

        a = Assembly("Test") / (Part("MyFem") / [pl1, pl2])
        parent = pl1.parent
        parent.fem = pl1.to_fem_obj(1, "shell")
        parent.fem += pl2.to_fem_obj(1, "shell")

        a.to_ifc(test_dir / "ADA_pl_w_holes_mesh_ifc", include_fem=True)
        # a.to_fem("my_xdmf_plate", "xdmf", overwrite=True, scratch_dir=test_folder, fem_converter="meshio")
        # a.to_fem("ADA_pl_mesh_code_aster", "code_aster", scratch_dir=test_folder, overwrite=True)
        # a.to_fem("ADA_pl_mesh", "abaqus", scratch_dir=test_folder, overwrite=True)

        self.assertEqual(len(parent.fem.elements), 236)
        self.assertEqual(len(parent.fem.nodes), 153)


if __name__ == "__main__":
    unittest.main()
