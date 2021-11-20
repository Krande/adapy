from operator import attrgetter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import FEM


def nodes_str(fem: "FEM"):
    f = "{nid:>7}, {x:>13.6f}, {y:>13.6f}, {z:>13.6f}"
    return (
        "\n".join(
            [f.format(nid=no.id, x=no[0], y=no[1], z=no[2]) for no in sorted(fem.nodes, key=attrgetter("id"))]
        ).rstrip()
        if len(fem.nodes) > 0
        else "** No Nodes"
    )
