from operator import attrgetter
from typing import TYPE_CHECKING

from ..grammar import format_number

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
    from .write_sets import aba_set_str

    if len(fem.ref_points.nodes) == 0:
        return "** No Nodes"

    ref_int = fem.nodes.max_nid
    fem.ref_points.renumber(int(ref_int + 1))

    rp_nodes_str = (
        "*NODE\n" + "\n".join([_node_line(no) for no in sorted(fem.ref_points, key=attrgetter("id"))]).rstrip()
    )
    for nset in fem.ref_sets:
        nset.name += "-RefPt_"
    rp_sets_str = "\n" + "\n".join([aba_set_str(no, True, False) for no in fem.ref_sets]).rstrip()

    return rp_nodes_str + rp_sets_str
