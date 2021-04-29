import pathlib
from operator import attrgetter

from ada import Assembly, Beam, Part
from ada.param_models.basic_module import SimpleStru

this_dir = pathlib.Path(__file__).resolve().absolute().parent
example_files = this_dir / ".." / "files"


def build_test_model():
    param_model = SimpleStru("ParametricModel")
    a = Assembly("ParametricSite")
    a.add_part(param_model)
    param_model.gmsh.mesh(max_dim=2, interactive=False)
    param_model.add_bcs()

    return a


def build_test_beam():
    a = Assembly("MyAssembly")
    p = Part("MyPart")
    p.add_beam(Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300"))
    p.gmsh.mesh(0.5)
    a.add_part(p)
    return a


def compare_fem_objects(fem_a, fem_b):
    """
    Compare FEM objects

    :param fem_a:
    :param fem_b:
    :return:
    """
    for na, nb in zip(fem_a.elements, fem_b.elements):
        assert na.id == nb.id
        for nma, nmb in zip(na.nodes, nb.nodes):
            assert nma == nmb

    for na, nb in zip(fem_a.nodes, fem_b.nodes):
        assert na.id == nb.id
        assert na.x == nb.x
        assert na.y == nb.y
        assert na.z == nb.z

    def assert_sets(s1, s2):
        for m1, m2 in zip(
            sorted(s1, key=attrgetter("name")),
            sorted(s2, key=attrgetter("name")),
        ):
            for ma, mb in zip(sorted(m1.members, key=attrgetter("id")), sorted(m2.members, key=attrgetter("id"))):
                assert ma.id == mb.id

        assert len(s1) == len(s2)

    assert_sets(fem_a.sets.elements.values(), fem_b.sets.elements.values())
    assert_sets(fem_a.sets.nodes.values(), fem_b.sets.nodes.values())

    print(f"No differences found for FEM objects\nA:{fem_a}\nB:{fem_b}")
