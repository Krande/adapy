from typing import TYPE_CHECKING

import numpy as np

from ada.api.containers import Nodes
from ada.api.nodes import Node
from ada.fem.formats.utils import str_to_int

from . import cards

if TYPE_CHECKING:
    from ada.fem import FEM


def get_nodes(bulk_str: str, parent: "FEM") -> Nodes:
    nodes = [get_node(m, parent) for m in cards.GCOORD.to_ff_re().finditer(bulk_str)]
    return Nodes(nodes, parent=parent)


def get_nodes_arrays(bulk_str: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse GCOORD straight into packed (coords (n,3), node_ids (n,)) arrays — no
    Node objects. The substrate-direct read path."""
    ids: list[int] = []
    xyz: list[tuple[float, float, float]] = []
    for m in cards.GCOORD.to_ff_re().finditer(bulk_str):
        d = m.groupdict()
        ids.append(str_to_int(d["id"]))
        xyz.append((float(d["x"]), float(d["y"]), float(d["z"])))
    coords = np.array(xyz, dtype=np.float64) if xyz else np.zeros((0, 3))
    node_ids = np.array(ids, dtype=np.int64) if ids else np.zeros((0,), dtype=np.int64)
    return coords, node_ids


def renumber_nodes(bulk_str: str, fem: "FEM") -> dict[int, int]:
    node_map = {nodeno: nodex for nodex, nodeno in map(get_nodeno, cards.GNODE.to_ff_re().finditer(bulk_str))}
    fem.nodes.renumber(renumber_map=node_map)
    return node_map


def get_nodeno(m_gnod):
    d = m_gnod.groupdict()
    return str_to_int(d["nodex"]), str_to_int(d["nodeno"])


def get_node(m, parent):
    d = m.groupdict()
    return Node([float(d["x"]), float(d["y"]), float(d["z"])], str_to_int(d["id"]), parent=parent)
