def get_eldata(fem_source):
    """

    :return: A dictionary of basic mesh statistics
    """
    from ada import Assembly, Part
    from ada.fem import FEM

    el_types = dict()

    def scan_elem(mesh):
        for el in mesh.elements:
            if el.type not in el_types.keys():
                el_types[el.type] = 1
            else:
                el_types[el.type] += 1

    if type(fem_source) is Assembly:
        assert isinstance(fem_source, Assembly)
        for p in fem_source.parts.values():
            assert issubclass(type(p), Part)
            scan_elem(p.fem)
    elif issubclass(type(fem_source), Part):
        scan_elem(fem_source.fem)
    elif type(fem_source) is FEM:
        scan_elem(fem_source)
    else:
        raise ValueError(f'Unknown fem_source "{fem_source}"')
    return el_types


def convert_springs_to_connectors(assembly):
    """
    Converts all single noded springs to connector elements

    :param assembly:
    :type assembly: ada.Assembly
    """
    import numpy as np

    from ada import Node
    from ada.fem import Bc, Connector, ConnectorSection, FemSet

    for p in assembly.get_all_subparts():
        for spring in p.fem.springs.values():
            n1 = spring.nodes[0]
            n2 = Node(n1.p - np.array([0, 0, 10e-3]))
            assembly.fem.add_rp(spring.name + "_rp", n2)
            fs = FemSet(spring.name + "_bc", [n2], "nset")
            assembly.fem.add_set(fs)
            assembly.fem.add_bc(Bc(spring.name + "_bc", fs, [1, 2, 3, 4, 5, 6]))
            diag = []
            for dof, row in enumerate(spring.stiff):
                for j, stiffness in enumerate(row):
                    if dof == j:
                        diag.append(stiffness)

            con_sec = ConnectorSection(spring.name + "_consec", diag, [])
            assembly.fem.add_connector_section(con_sec)
            con = Connector(spring.name + "_con", spring.id, n1, n2, "bushing", con_sec)
            assembly.fem.add_connector(con)
        p.fem._springs = dict()
        p.fem.elements.filter_elements(delete_elem=["SPRING1"])


def get_beam_end_nodes(bm, end=1, part=None):
    """

    :param bm:
    :param end:
    :param part: Optional if beam parent is not where fem nodes are stored
    :type bm: ada.Beam
    :return: list of nodes
    """
    p = bm.parent
    nodes = p.fem.nodes
    w = bm.section.w_btn
    h = bm.section.h
    members = [
        e for e in nodes.get_by_volume((-0.1, -(w / 2) * 1.1, -(h / 2) * 1.1), (0.02, (w / 2) * 1.1, (h / 2) * 1.1))
    ]
    return members
