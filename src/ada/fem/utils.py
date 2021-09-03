from typing import List, Union

import numpy as np

from ada import FEM, Assembly, Beam, Node, Part
from ada.fem import Bc, Connector, ConnectorSection, FemSet


def get_eldata(fem_source: Union[Assembly, Part, FEM]):
    """Return a dictionary of basic mesh statistics"""

    el_types = dict()

    def scan_elem(mesh):
        for el in mesh.elements:
            if el.type not in el_types.keys():
                el_types[el.type] = 1
            else:
                el_types[el.type] += 1

    if type(fem_source) is Assembly:
        for p in fem_source.parts.values():
            scan_elem(p.fem)
    elif issubclass(type(fem_source), Part):
        scan_elem(fem_source.fem)
    elif type(fem_source) is FEM:
        scan_elem(fem_source)
    else:
        raise ValueError(f'Unknown fem_source "{fem_source}"')
    return el_types


def convert_springs_to_connectors(assembly: Assembly):
    """Converts all single noded springs to connector elements"""
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


def get_beam_end_nodes(bm: Beam, end=1) -> List[Node]:
    """Get list of nodes from end of beam"""
    p = bm.parent
    nodes = p.fem.nodes
    w = bm.section.w_btn
    h = bm.section.h

    min_np = np.array([-0.1, -(w / 2) * 1.1, -(h / 2) * 1.1])
    max_np = np.array([0.02, (w / 2) * 1.1, (h / 2) * 1.1])
    n = bm.n1.p if end == 1 else bm.n2.p

    members = [e for e in nodes.get_by_volume(n + min_np, n + max_np)]
    return members
