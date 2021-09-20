import unittest

from ada import Assembly, Beam, Part, Pipe, Plate, PrimBox, PrimSphere
from ada.concepts.structural import make_ig_cutplanes
from ada.config import Settings
from ada.fem import Step
from ada.fem.meshing.concepts import GmshOptions, GmshSession, GmshTask
from ada.fem.meshing.multisession import multisession_gmsh_tasker

test_dir = Settings.test_dir / "gmsh_api_v2"


class GmshApiV2(unittest.TestCase):
    def setUp(self) -> None:

        self.bm1 = Beam("MyBeam", (0, 0, 1), (1, 0, 1), "IPE300")
        self.bm2 = Beam("MySecondBeam", (1.1, 0, 1), (2, 0, 1), "IPE300")
        self.bm3 = Beam("MyThirdBeam", (2.1, 0, 1), (3, 0, 1), "IPE300")

        pl_atts = dict(origin=(1, 1, 1), xdir=(1, 0, 0), normal=(0, 0, 1))
        pl_points = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.pl1 = Plate("MyPlate", pl_points, 10e-3, **pl_atts)

        self.pipe = Pipe("MyPipe", [(0, 0.5, 0), (1, 0.5, 0), (1.2, 0.7, 0.2), (1.5, 0.7, 0.2)], "OD120x6")

        p1, p2 = (1, -2, 0), (2, -1, 1)
        self.shp1 = PrimBox("MyBox", p1, p2)
        self.shp1.add_penetration(PrimSphere("MyCutout", p1, 0.5))
        self.cut_planes = make_ig_cutplanes(self.bm2)

        self.step = Step("MyStep", "static")

    def test_pipe(self):
        with GmshSession(silent=True, options=GmshOptions(Mesh_ElementOrder=2)) as gs:
            gs.add_obj(self.pipe, "solid")
            gs.mesh(0.02)
            fem = gs.get_fem()

        a = Assembly() / (Part("MyFemObjects", fem=fem) / [self.bm1])
        print(a)
        # a.to_fem("my_xdmf_pipe", "xdmf", overwrite=True, scratch_dir=test_dir, fem_converter="meshio")

    def test_beam(self):
        with GmshSession(silent=True, options=GmshOptions(Mesh_ElementOrder=2)) as gs:
            gs.add_obj(self.bm1, "solid")
            gs.mesh(0.1)
            fem = gs.get_fem()
        a = Assembly() / (Part("MyFemObjects", fem=fem) / [self.bm1])
        a.to_fem("aba_2nd_order_bm", "abaqus", overwrite=True, scratch_dir=test_dir)
        print(a)

    def test_beam_hex(self):
        # TODO: this test is not yet producing HEX elements.
        with GmshSession(silent=True, options=GmshOptions(Mesh_ElementOrder=2)) as gs:
            solid_bm = gs.add_obj(self.bm1, "shell")

            for cutp in self.cut_planes:
                gs.add_cutting_plane(cutp, [solid_bm])
            gs.make_cuts()
            gs.mesh(0.1)
            fem = gs.get_fem()

        a = Assembly() / (Part("MyFemObjects", fem=fem) / [self.bm1])
        print(a)
        # a.to_fem("aba_2nd_order_bm_hex", "abaqus", overwrite=True, scratch_dir=test_dir)

    def test_mix_geom_repr_in_same_session(self):
        options = GmshOptions(Mesh_ElementOrder=2)
        with GmshSession(silent=True, options=options) as gs:
            gs.add_obj(self.bm1, "shell")
            solid_bm = gs.add_obj(self.bm2, "solid")
            gs.add_obj(self.bm3, "line")
            gs.add_obj(self.pl1, "shell")
            gs.add_obj(self.shp1, "solid")
            gs.add_obj(self.pipe, "shell")

            for cutp in self.cut_planes:
                gs.add_cutting_plane(cutp, [solid_bm])
            gs.make_cuts()

            gs.mesh(0.1)
            fem = gs.get_fem()

        print(fem.elements)

        a = Assembly() / (
            Part("MyFemObjects", fem=fem) / [self.bm1, self.bm2, self.bm3, self.pl1, self.shp1, self.pipe]
        )

        # a.to_fem("my_ca_analysis", "code_aster", overwrite=True, scratch_dir=test_dir)
        # a.to_fem("my_aba_analysis", "abaqus", overwrite=True, scratch_dir=test_dir)
        # a.to_fem("my_xdmf_test", "xdmf", overwrite=True, scratch_dir=test_dir, fem_converter="meshio")
        # a.to_ifc(test_dir / "gmsh_api_v2", include_fem=True)

        map_assert = dict(B32=9, C3D10=5310, STRI65=840)

        for key, val in a.get_part("MyFemObjects").fem.elements.group_by_type():
            num_el = len(list(val))
            if key == "C3D10":
                # TODO: Why is the number of elements for different platforms (win, linux and macos)
                self.assertAlmostEqual(map_assert[key], num_el, delta=50)
            else:
                self.assertEqual(map_assert[key], num_el)

    def test_diff_geom_repr_in_separate_sessions(self):
        t1 = GmshTask([self.bm1], "solid", 0.1, options=GmshOptions(Mesh_ElementOrder=2))
        t2 = GmshTask([self.bm2], "shell", 0.1, options=GmshOptions(Mesh_ElementOrder=1))
        fem = multisession_gmsh_tasker([t1, t2])
        print(fem.elements)
        a = Assembly() / (Part("MyFemObjects", fem=fem) / [self.bm1, self.bm2])
        a.fem.add_step(self.step)
        a.to_fem("aba_mixed_order", "abaqus", overwrite=True, scratch_dir=test_dir)


if __name__ == "__main__":
    unittest.main()
