"""Regression: perpendicular plates whose shared edge is subdivided differently on
each face used to mesh non-conformally (a hanging T-junction node left the shell
elements disconnected). See find_edge_connected_perpendicular_plates classification.

pl_a is a single plate spanning x in [0, 2]; pl_b1 / pl_b2 are two perpendicular plates
standing on the sub-segments [0, 1] and [1, 2] of pl_a's bottom edge. The sub-plate
endpoint at x=1 lands mid-span on pl_a's edge -> the detector must imprint the contact so
the meshed interface stays conformal.
"""

import ada
from ada.core.clash_check import find_edge_connected_perpendicular_plates
from ada.fem import Elem
from ada.fem.conformality import find_hanging_nodes
from ada.fem.shapes.definitions import ShellShapes


def _tjunction_plates():
    pl_a = ada.Plate("pl_a", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01, origin=(0, 0, 0), normal=(0, 1, 0), xdir=(1, 0, 0))
    pl_b1 = ada.Plate("pl_b1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, origin=(0, 0, 0), normal=(0, 0, 1), xdir=(1, 0, 0))
    pl_b2 = ada.Plate("pl_b2", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, origin=(1, 0, 0), normal=(0, 0, 1), xdir=(1, 0, 0))
    return pl_a, pl_b1, pl_b2


def test_tjunction_detected_for_fragmentation():
    """Unit level: the T-junction pairs must land in a fragment bucket (either one)."""
    pl_a, pl_b1, pl_b2 = _tjunction_plates()
    plates = [pl_a, pl_b1, pl_b2]

    pc = find_edge_connected_perpendicular_plates(plates)

    def bucketed(x, y):
        return (
            y in pc.edge_connected.get(x, [])
            or y in pc.mid_span_connected.get(x, [])
            or x in pc.edge_connected.get(y, [])
            or x in pc.mid_span_connected.get(y, [])
        )

    assert bucketed(pl_a, pl_b1)
    assert bucketed(pl_a, pl_b2)


def test_tjunction_mesh_is_conformal():
    """Mesh level: the meshed interface is welded - one shared node at the mid-span
    junction, referenced by shell elements from both faces, and no hanging nodes."""
    pl_a, pl_b1, pl_b2 = _tjunction_plates()
    p = ada.Part("Tjoint") / [pl_a, pl_b1, pl_b2]

    p.fem = p.to_fem_obj(0.25, interactive=False)

    # Exactly one node at the mid-span junction (no coincident duplicate / hanging node).
    junction = p.fem.nodes.get_by_volume(p=(1, 0, 0))
    assert len(junction) == 1

    # That node is shared by shell elements from both the spanning plate and a stub plate.
    n = junction[0]
    shell_refs = [e for e in n.refs if isinstance(e, Elem) and isinstance(e.type, ShellShapes)]
    assert len(shell_refs) >= 2

    # And the global conformality guard finds nothing hanging.
    assert find_hanging_nodes(p.fem) == []
