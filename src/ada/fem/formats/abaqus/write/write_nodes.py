from operator import attrgetter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import FEM


def nodes_str(fem: "FEM"):
    f = "{nid:>7}, {x:>13.6f}, {y:>13.6f}, {z:>13.6f}"
    if len(fem.nodes) == 0:
        return "** No Nodes"
    return (
        "*NODE\n"
        + "\n".join(
            [f.format(nid=no.id, x=no[0], y=no[1], z=no[2]) for no in sorted(fem.nodes, key=attrgetter("id"))]
        ).rstrip()
    )


def rp_str(fem: "FEM") -> str:
    from .write_sets import aba_set_str

    f = "{nid:>7}, {x:>13.6f}, {y:>13.6f}, {z:>13.6f}"

    if len(fem.ref_points.nodes) == 0:
        return "** No Nodes"

    ref_int = fem.nodes.max_nid
    fem.ref_points.renumber(int(ref_int + 1))

    rp_nodes_str = (
        "*NODE\n"
        + "\n".join(
            [f.format(nid=no.id, x=no[0], y=no[1], z=no[2]) for no in sorted(fem.ref_points, key=attrgetter("id"))]
        ).rstrip()
    )
    for nset in fem.ref_sets:
        nset.name += "-RefPt_"
    rp_sets_str = "\n" + "\n".join([aba_set_str(no, True, False) for no in fem.ref_sets]).rstrip()

    return rp_nodes_str + rp_sets_str
