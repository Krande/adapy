import pytest

from ada import Assembly, Material, Part, Plate
from ada.api.containers import Materials
from ada.materials.metals import CarbonSteel


@pytest.fixture
def mat1():
    return Material("Mat1", mat_model=CarbonSteel())


@pytest.fixture
def mat2():
    return Material("Mat2", mat_model=CarbonSteel())


def test_merge_materials():
    plates = []

    for i in range(1, 10):
        mat = Material(f"mat{i}", CarbonSteel("S355"))
        plates.append(Plate(f"pl{i}", [(0, 0, 0), (0, 1, 0), (1, 1, 0)], 20e-3, mat=mat))

    a = Assembly() / (Part("MyPart") / plates)
    p = a.get_part("MyPart")
    mats = p.materials
    assert len(mats) == 9
    mats.merge_materials_by_properties()
    assert len(mats) == 1


def test_negative_contained(mat1, mat2):
    collection = Materials([mat1])
    assert mat2 not in collection


def test_positive_contained(mat1, mat2):
    collection = Materials([mat1, mat2])
    assert mat2 in collection
