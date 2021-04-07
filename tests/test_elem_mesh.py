import logging
import unittest

from ada import Assembly, Beam, Part, Plate, PrimCyl, PrimExtrude
from ada.config import Settings
from ada.core.utils import align_to_plate
from ada.fem import Load, Step
from ada.fem.io.mesh.recipes import create_beam_mesh, create_plate_mesh

test_folder = Settings.test_dir / "mesh"

atts = dict(origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
atts2 = dict(origin=(1, 0, -0.1), xdir=(0, 0, 1), normal=(-1, 0, 0))


class BeamIO(unittest.TestCase):
    def test_beam_mesh(self):
        import gmsh

        try:
            gmsh.finalize()
        except BaseException as e:
            logging.error(e)
            pass
        bm = Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220")

        bm.add_penetration(PrimCyl("Cylinder", (0.5, -0.5, 0), (0.5, 0.5, 0), 0.05))

        p = Part("MyFem")
        p.add_beam(bm)

        create_beam_mesh(bm, p.fem, "solid", interactive=False)
        a = Assembly("Test") / p
        a.to_fem("my_test", "xdmf", scratch_dir=test_folder, fem_converter="meshio", overwrite=True)

    def test_plate_mesh(self):
        import gmsh

        try:
            gmsh.finalize()
        except BaseException as e:
            logging.error(e)
            pass
        gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)
        gmsh.option.setNumber("Mesh.Algorithm", 8)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)

        pl1 = Plate("MyPl", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts)
        pl2 = Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **atts2)
        points = [(1, 1, 0.2), (2, 1, 0.2), (2, 2, 0.2), (1, 2, 0.2)]
        pl1.add_penetration(PrimExtrude("poly_extrude", points, **align_to_plate(pl1)))
        pl1.add_penetration(PrimExtrude("poly_extrude2", points, **align_to_plate(pl2)))
        gmsh.model.add("Test")

        p = Part("MyFem") / [pl1, pl2]

        create_plate_mesh(pl1, "shell", fem=p.fem, interactive=False, gmsh_session=gmsh)
        create_plate_mesh(pl2, "shell", fem=p.fem, interactive=False, gmsh_session=gmsh)

        a = Assembly("Test") / p
        a.to_ifc(test_folder / "ADA_pl_mesh_ifc")

        step = a.fem.add_step(Step("gravity", "static", nl_geom=True))
        step.add_load(Load("grav", "gravity", -9.81))

        a.to_fem("ADA_pl_mesh", "abaqus", scratch_dir=test_folder, overwrite=True)


if __name__ == "__main__":
    unittest.main()
