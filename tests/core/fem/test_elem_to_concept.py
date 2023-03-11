from typing import List

import ada
from ada import Node

test_dir = ada.config.Settings.test_dir / "fem_to_concepts"


def create_shell_elem(el_id: int, nodes: List[Node], fem: ada.FEM):
    for n in nodes:
        fem.nodes.add(n)

    el = fem.add_elem(ada.fem.Elem(el_id, nodes, ada.fem.Elem.EL_TYPES.SHELL_SHAPES.QUAD, el_formulation_override="S4"))
    fs = fem.add_section(
        ada.fem.FemSection(
            f"PlSec{el_id}", "shell", ada.fem.FemSet("ShellSet", [el]), ada.Material("S355"), thickness=10e-3
        )
    )
    el.fem_sec = fs


def test_shell_elem_to_plate():
    p = ada.Part("MyPart")
    nodes1 = [
        Node([-15.0001154, -6.0, 24.5], 799),
        Node([-16.0, -6.0, 24.5], 571),
        Node([-16.0, -7.0, 24.5], 570),
        Node([-15.0015383, -7.0, 24.5], 802),
    ]
    nodes2 = [
        Node([-15.0015383, -7.0, 24.5], 802),
        Node([-16.0, -7.0, 24.5], 570),
        Node([-16.0, -8.0, 24.5], 529),
        Node([-15.0, -8.0, 24.5], 651),
    ]

    create_shell_elem(999, nodes1, p.fem)
    create_shell_elem(1003, nodes2, p.fem)

    a = ada.Assembly() / p
    p.fem.sections.merge_by_properties()
    a.create_objects_from_fem()

    pl: ada.Plate = a.get_by_name("sh999")
    nodes_eval = nodes1
    assert tuple(pl.nodes[0].p) == tuple(nodes_eval[0].p)
    assert tuple(pl.poly.seg_global_points[0]) == tuple(nodes_eval[-1].p)
    assert tuple(pl.poly.seg_global_points[1]) == tuple(nodes_eval[0].p)
    assert tuple(pl.poly.seg_global_points[2]) == tuple(nodes_eval[1].p)
    assert tuple(pl.poly.seg_global_points[3]) == tuple(nodes_eval[2].p)

    pl: ada.Plate = a.get_by_name("sh1003")
    nodes_eval = nodes2
    assert tuple(pl.nodes[0].p) == tuple(nodes_eval[0].p)
    assert tuple(pl.poly.seg_global_points[0]) == tuple(nodes_eval[-1].p)
    assert tuple(pl.poly.seg_global_points[1]) == tuple(nodes_eval[0].p)
    assert tuple(pl.poly.seg_global_points[2]) == tuple(nodes_eval[1].p)
    assert tuple(pl.poly.seg_global_points[3]) == tuple(nodes_eval[2].p)

    # a.to_ifc(test_dir / "Shell2Plate")
    # a.to_fem("MyTest", "abaqus", overwrite=True)
