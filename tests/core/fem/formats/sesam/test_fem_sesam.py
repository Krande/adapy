import pytest

import ada
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.read.read_sets import SetReader
from ada.fem.formats.sesam.write.write_sets import sets_str
from ada.fem.formats.sesam.write.writer import nodes_str, write_ff
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


def test_write_ff_keeps_repeated_rows():
    """A record whose intermediate row equals its last row must still be continued."""
    rows = [(1, 2, 3, 4), (0, 0, 0, 0), (0, 0, 0, 0)]
    out = write_ff("GELREF1", rows)
    lines = out.splitlines()
    assert len(lines) == len(rows)
    assert out.endswith("\n") and out.count("\n") == len(rows)
    # every row but the last is continued, i.e. followed by the 8-space continuation
    assert lines[0].startswith("GELREF1")
    for line in lines[1:]:
        assert line.startswith(" " * 8)


def test_nodes_str_rejects_duplicate_ids():
    nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes + [ada.Node((2, 0, 0), 1)]))
    with pytest.raises(Exception, match="Doubly defined node id"):
        nodes_str(fem)


def test_nodes_str_emits_every_node_sorted_by_id():
    ids = [7, 3, 11, 1]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes([ada.Node((i, 0, 0), i) for i in ids]))
    gnodes = [ln for ln in nodes_str(fem).splitlines() if ln.startswith("GNODE")]
    assert len(gnodes) == len(ids)
    emitted = [int(float(ln.split()[1])) for ln in gnodes]
    assert emitted == sorted(ids)
