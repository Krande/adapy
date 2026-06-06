from operator import attrgetter

import pytest

import ada
from ada.config import Config
from ada.param_models.fem_models import beam_ex1


@pytest.fixture(autouse=True)
def _object_mode_for_med_write():
    """These are read->write->read MED round-trips. The Code_Aster MED *writer*
    detects multi-set membership via element.refs, which the array-backed (lazy
    id-set) path does not populate — array-backed MED *write* is a follow-up
    (writer migration). The substrate-direct MED *reader* is covered by
    test_med_array_read.py. Force object mode so these writer tests are
    flag-independent."""
    prev = Config().meshing_array_backed
    Config().meshing_array_backed = False
    yield
    Config().meshing_array_backed = prev


def test_read_write_cylinder(example_files, tmp_path):
    name = "cylinder"

    a = ada.from_fem(example_files / "fem_files/code_aster/cylinder.med", "code_aster", name="cylinder_rewritten")
    a.to_fem(name, "code_aster", overwrite=True, scratch_dir=tmp_path)

    b = ada.from_fem((tmp_path / name / name).with_suffix(".med"), fem_format="code_aster")

    p_a = a.parts["cylinder_rewritten"]
    p_b = b.parts["cylinder"]
    # TODO: Fix failing tests. For some reason, FemSets are twice as large when they are reimported.
    compare_fem_objects(p_a.fem, p_b.fem)


def test_read_write_box(example_files, tmp_path):
    name = "box"

    a = ada.from_fem(example_files / "fem_files/code_aster/box.med", "code_aster", name="box")
    a.to_fem(name, "code_aster", overwrite=True, scratch_dir=tmp_path)

    b = ada.from_fem((tmp_path / name / name).with_suffix(".med"), fem_format="code_aster")

    p_a = a.parts["box"]
    p_b = b.parts["box"]

    compare_fem_objects(p_a.fem, p_b.fem)


def test_read_write_portal_frame(example_files, tmp_path):
    name = "portal"

    a = ada.from_fem(example_files / "fem_files/code_aster/portal_01.med", "code_aster", name=name)
    a.to_fem(name, "code_aster", overwrite=True, scratch_dir=tmp_path)

    b = ada.from_fem((tmp_path / name / name).with_suffix(".med"), fem_format="code_aster")

    p_a = a.parts[name]
    p_b = b.parts[name]

    compare_fem_objects(p_a.fem, p_b.fem)


@pytest.mark.parametrize("geom_repr", ["solid", "shell", "line"])
def test_roundtrip_cantilever(tmp_path, geom_repr):
    name = f"cantilever_code_aster_{geom_repr}"

    a = beam_ex1(geom_repr=geom_repr)

    a.to_fem(name, fem_format="code_aster", overwrite=True, scratch_dir=tmp_path)
    b = ada.from_fem((tmp_path / name / name).with_suffix(".med"), fem_format="code_aster")

    p_a = a.parts["MyPart"]
    p_b = b.parts[name]

    compare_fem_objects(p_a.fem, p_b.fem)


def compare_fem_objects(fem_a: ada.FEM, fem_b: ada.FEM):
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
