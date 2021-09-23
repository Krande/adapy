import pathlib
import unittest
from operator import attrgetter

from ada import FEM, Assembly, Beam, Part, Section
from ada.param_models.basic_module import SimpleStru
from ada.visualize.renderer_pythreejs import MyRenderer, SectionRenderer

this_dir = pathlib.Path(__file__).resolve().absolute().parent
example_files = this_dir / ".." / "files"
is_printed = False


def build_test_beam_fem(geom_repr) -> Assembly:
    bm = Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    return Assembly("MyAssembly") / (Part("MyPart", fem=bm.to_fem_obj(0.1, geom_repr)) / bm)


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


def dummy_display(ada_obj):
    if type(ada_obj) is Section:
        sec_render = SectionRenderer()
        _, _ = sec_render.build_display(ada_obj)
    else:
        renderer = MyRenderer()
        renderer.DisplayObj(ada_obj)
        renderer.build_display()


def build_test_simplestru_fem(mesh_size=0.1, make_fem=True) -> Assembly:
    p = SimpleStru("ParametricModel")

    if make_fem:
        p.fem = p.to_fem_obj(mesh_size)
        p.add_bcs()

    return Assembly("ParametricSite") / p
