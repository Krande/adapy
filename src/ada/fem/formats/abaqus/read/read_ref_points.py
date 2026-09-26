"""Reference points: a ``*Node, nset=<name>`` block under a ``** Reference point: <name>`` comment.

adapy keeps reference points apart from the mesh (``FEM.ref_points`` / ``FEM.ref_sets``). The
writer puts each one in a node block of its own, marked by the comment. The node readers leave
those blocks out, and this module reads them back as reference points. Abaqus has no reference
point keyword, so a deck from elsewhere has no such comment. Its reference points (CAE's
``-RefPt_`` sets) read as the ordinary nodes and sets they are in the deck.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.api.nodes import Node
from ada.fem import FemSet

from .keywords import validate
from .lexer import KeywordBlock, comment_property, iter_keywords

if TYPE_CHECKING:
    from ada import FEM

COMMENT = "Reference point"


def is_ref_point_block(block: KeywordBlock) -> bool:
    return bool(comment_property(block, COMMENT))


def add_ref_points_from_bulk(bulk_str: str, fem: FEM) -> None:
    for block in iter_keywords(bulk_str, "NODE"):
        if not is_ref_point_block(block):
            continue
        validate(block)
        name = comment_property(block, COMMENT)[COMMENT]
        nodes = []
        for line in block.data_lines:
            values = [float(t) for t in line.split(",") if t.strip()]
            node = Node(values[1:4], int(values[0]), parent=fem)
            nodes.append(fem.ref_points.add(node))
        fem_set = fem.ref_sets.add(FemSet(block.params.get("NSET") or name, nodes, "nset", parent=fem))
        fem_set.metadata["internal"] = True


def node_by_id(fem: FEM, nid: int):
    """A node of the mesh, or else a reference point: elements and sets name both by id."""
    try:
        return fem.nodes.from_id(nid)
    except ValueError:
        for rp in fem.ref_points:
            if rp.id == nid:
                return rp
        raise
