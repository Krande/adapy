import unittest

from ada import Assembly, Beam, Part, Plate, Section
from ada.param_models.basic_module import SimpleStru


class VisualizeTests(unittest.TestCase):
    def test_beams_viz(self):
        def viz(a):
            a._repr_html_()

        bm1 = Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red")
        bm2 = Beam("bm2", n1=[0, 0, 1], n2=[2, 0, 1], sec="HP220x10", colour="blue")
        bm3 = Beam("bm3", n1=[0, 0, 2], n2=[2, 0, 2], sec="BG800x400x20x40", colour="green")
        bm4 = Beam("bm4", n1=[0, 0, 3], n2=[2, 0, 3], sec="CIRC200", colour="green")
        bm5 = Beam("bm5", n1=[0, 0, 4], n2=[2, 0, 4], sec="TUB200x10", colour="green")

        viz(bm1)
        viz(bm2)
        viz(bm3)
        viz(bm4)
        viz(bm5)

    def test_viz(self):
        a = Assembly("my_test_assembly")
        a.add_beam(Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red"))
        a.add_beam(Beam("bm2", n1=[0, 0, 1], n2=[2, 0, 1], sec="HP220x10", colour="blue"))
        a.add_beam(Beam("bm3", n1=[0, 0, 2], n2=[2, 0, 2], sec="BG800x400x20x40", colour="green"))
        a.add_beam(Beam("bm4", n1=[0, 0, 3], n2=[2, 0, 3], sec="CIRC200", colour="green"))
        a.add_beam(Beam("bm5", n1=[0, 0, 4], n2=[2, 0, 4], sec="TUB200x10", colour="green"))
        a.add_plate(
            Plate(
                "pl1",
                [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
                0.01,
                use3dnodes=True,
            )
        )
        a._repr_html_()

    def test_fem(self):
        a = Assembly("MyAssembly")
        p = Part("MyPart")
        p.add_beam(Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300"))
        a.add_part(p)
        a.gmsh.mesh()

        a._repr_html_()
        a._renderer.toggle_mesh_visibility()

    def test_module(self):
        a = Assembly("ParametricSite")
        pm = SimpleStru("ParametricModel")
        a.add_part(pm)
        a.gmsh.mesh()

        a._repr_html_()
        a._renderer.toggle_mesh_visibility()

    def test_module2(self):
        param_model = SimpleStru("ParametricModel")
        param_model.gmsh.mesh(size=0.1, max_dim=2)
        param_model.add_bcs()
        a = Assembly("ParametricSite")
        a.add_part(param_model)
        a._repr_html_()
        # a._renderer.toggle_geom_visibility()
        a._renderer.toggle_mesh_visibility()


class TestEqualityProtocol(unittest.TestCase):
    def test_section_equal(self):
        import copy

        secvar = dict(
            sec_type="IG",
            h=0.8,
            w_top=0.2,
            w_btn=0.2,
            t_fbtn=0.01,
            t_ftop=0.01,
            t_w=0.01,
        )
        sec1 = Section(name="sec1", **secvar)
        sec2 = copy.deepcopy(sec1)
        sec2.name = "sec2"
        sec3 = Section(name="sec3", **secvar)
        list_of_secs = [sec1, sec2, sec3]
        self.assertTrue(sec1 == sec1)
        self.assertTrue(sec1 in list_of_secs)
        self.assertFalse(Section(name="sec4", **secvar) in list_of_secs)

    def test_parts_list(self):
        a = Assembly("MyAssembly")
        # Level 1
        part = Part("my_part1")
        a.add_part(part)
        a.add_part(Part("my_part2"))
        # Level 2
        part.add_part(Part("my_part1_subpart1"))
        part.add_part(Part("my_part1_subpart2"))
        # Level 3
        subpart3 = Part("my_part1_subpart3")
        subpart3.add_part(Part("my_part1_subpart3_sub1"))
        part.add_part(subpart3)
        list_of_ps = a.get_all_parts_in_assembly()

        self.assertEqual(len(list_of_ps), 6)


if __name__ == "__main__":
    unittest.main()
