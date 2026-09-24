from operator import attrgetter
from typing import TYPE_CHECKING

from ..grammar import format_number, render_keyword

if TYPE_CHECKING:
    from ada import FEM


def _node_line(no) -> str:
    """A node's coordinates exactly: ``{:13.6f}`` rounded them to a micrometre in metres, so a
    node at 1/3 read back at 0.333333."""
    return f"{no.id:>7}, {format_number(no[0]):>13}, {format_number(no[1]):>13}, {format_number(no[2]):>13}"


def nodes_str(fem: "FEM"):
    if len(fem.nodes) == 0:
        return "** No Nodes"
    return "*NODE\n" + "\n".join([_node_line(no) for no in sorted(fem.nodes, key=attrgetter("id"))]).rstrip()


def rp_str(fem: "FEM") -> str:
    """Reference points, each as ``*Node, nset=<name>`` under ``** Reference point: <name>``.

    That comment is how the reader tells them from the mesh (``read_ref_points``). Their ids are
    kept unless the mesh already uses one, in which case they are renumbered past it as before.
    This used to renumber every time, rename the set on each write (``rp1-RefPt_-RefPt_`` on
    the second), and crash in the set writer before any of it reached the deck.
    """
    if len(fem.ref_points.nodes) == 0:
        return "** No Nodes"

    mesh_ids = {n.id for n in fem.nodes}
    if any(rp.id in mesh_ids for rp in fem.ref_points):
        fem.ref_points.renumber(int(fem.nodes.max_nid + 1))

    blocks = []
    in_a_set = set()
    for ref_set in fem.ref_sets:
        members = sorted(ref_set.members, key=attrgetter("id"))
        in_a_set.update(id(n) for n in members)
        blocks.append((ref_set.name, members))
    for rp in fem.ref_points:
        if id(rp) not in in_a_set:
            blocks.append((f"RP-{rp.id}", [rp]))
    return "".join(
        render_keyword("Node", [("nset", name)], [_node_line(n) for n in members], [f"Reference point: {name}"])
        for name, members in blocks
    ).rstrip()
