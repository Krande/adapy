import pathlib
import unittest
from operator import attrgetter

from ada import FEM, Assembly, Beam, Part, Section
from ada.param_models.basic_module import SimpleStru
from ada.visualize.renderer import MyRenderer, SectionRenderer

this_dir = pathlib.Path(__file__).resolve().absolute().parent
example_files = this_dir / ".." / "files"
is_printed = False


def dummy_display(ada_obj):

    if type(ada_obj) is Section:
        sec_render = SectionRenderer()
        _, _ = sec_render.build_display(ada_obj)
    else:
        renderer = MyRenderer()
        renderer.DisplayObj(ada_obj)
        renderer.build_display()


def build_test_model():
    param_model = SimpleStru("ParametricModel")
    a = Assembly("ParametricSite")
    a.add_part(param_model)
    param_model.gmsh.mesh(max_dim=2, interactive=False, gmsh_silent=True)
    param_model.add_bcs()

    return a


def build_test_beam():
    a = Assembly("MyAssembly")
    p = Part("MyPart")
    p.add_beam(Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300"))
    p.gmsh.mesh(0.5)
    a.add_part(p)
    return a


def compare_fem_objects(fem_a: FEM, fem_b: FEM, test_class: unittest.TestCase):
    for na, nb in zip(fem_a.elements, fem_b.elements):
        test_class.assertEqual(na.id, nb.id)
        for nma, nmb in zip(na.nodes, nb.nodes):
            test_class.assertEqual(nma, nmb)

    for na, nb in zip(fem_a.nodes, fem_b.nodes):
        test_class.assertEqual(na.id, nb.id)
        test_class.assertEqual(na.x, nb.x)
        test_class.assertEqual(na.y, nb.y)
        test_class.assertEqual(na.z, nb.z)

    def assert_sets(s1, s2):
        for m1, m2 in zip(
            sorted(s1, key=attrgetter("name")),
            sorted(s2, key=attrgetter("name")),
        ):
            for ma, mb in zip(sorted(m1.members, key=attrgetter("id")), sorted(m2.members, key=attrgetter("id"))):
                test_class.assertEqual(ma.id, mb.id)

        test_class.assertEqual(len(s1), len(s2))

    assert_sets(fem_a.sets.elements.values(), fem_b.sets.elements.values())
    assert_sets(fem_a.sets.nodes.values(), fem_b.sets.nodes.values())

    print(f"No differences found for FEM objects\nA:{fem_a}\nB:{fem_b}")
