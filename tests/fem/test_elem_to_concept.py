import ada

test_dir = ada.config.Settings.test_dir / "fem_to_concepts"


def test_shell_elem_to_plate():
    nodes = [
        ada.Node([-12.0, 4.0, 24.5], 718),
        ada.Node([-12.9978333, 4.0, 24.5], 767),
        ada.Node([-13.0007744, 3.0, 24.5], 768),
        ada.Node([-12.0, 3.0, 24.5], 717),
    ]
    p = ada.Part("MyPart")
    for n in nodes:
        p.fem.nodes.add(n)
    el = p.fem.add_elem(ada.fem.Elem(956, nodes, ada.fem.Elem.EL_TYPES.SHELL_SHAPES.QUAD))
    p.fem.add_section(
        ada.fem.FemSection("PlSec", "shell", ada.fem.FemSet("ShellSet", [el]), ada.Material("S355"), thickness=10e-3)
    )
    a = ada.Assembly() / p
    a.create_objects_from_fem()
    pl: ada.Plate = a.get_by_name("sh956")
    assert tuple(pl.nodes[0].p) == (-12.0, 4.0, 24.5)

    a.to_ifc(test_dir / "Shell2Plate")
    a.to_fem("MyTest", "abaqus", overwrite=True)
