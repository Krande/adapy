import ada
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.read.read_sets import SetReader
from ada.fem.formats.sesam.write.write_sets import sets_str
from ada.fem.formats.sesam.write.writer import write_ff
from ada.fem.shapes.definitions import LineShapes


def test_write_ff():
    flag = "TDMATER"
    data = [
        (1, 1, 0, 0),
        (83025, 4, 0, 3),
        (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
    ]
    test_str = write_ff(flag, data)
    fflag = "BEUSLO"
    ddata = [
        (1, 1, 0, 0),
        (83025, 4, 0, 3),
        (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
    ]
    test_str += write_ff(fflag, ddata)
    # print(test_str)


def test_write_sets():

    elements = [
        ada.fem.Elem(el_id, [ada.Node((el_id, 0, 0), el_id), ada.Node((el_id + 1, 0, 0), el_id + 1)], LineShapes.LINE)
        for el_id in range(1, 2000)
    ]
    fem = ada.FEM("MyFem", elements=FemElements(elements))
    original_set = fem.add_set(ada.fem.FemSet("MySet", elements))
    result_str = sets_str(fem)

    return_fem = ada.FEM("MyFem", elements=fem.elements)
    sr = SetReader(result_str, return_fem)
    roundtripped_sets = sr.run()
    assert len(roundtripped_sets) == 1

    roundtripped_set = roundtripped_sets[0]
    assert len(original_set.members) == len(roundtripped_set.members)
