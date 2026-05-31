"""Domain-generic topology toolkit for adapy.

Build a cell graph from a soup of faces/solids: partition space into cells,
track which faces are shared between adjacent cells vs. on the outer envelope,
and query the resulting adjacency. Inspired by topologic, but implemented on
adapy's own CAD kernel via the ``ada.cad`` backend abstraction (OCC today,
adacpp tomorrow) — this module never imports a CAD kernel directly and holds
no domain-specific (structural-engineering) concepts.

Lazily importable: importing ``ada.topology`` pulls in no CAD kernel, and the
geometry verbs resolve a backend only when first used, so OCC-less environments
(slim / wasm) can import it without error.
"""
from __future__ import annotations

from ada.cad import Containment
from ada.topology.graph import (
    CellGraph,
    FaceConnectionInfo,
    GraphCell,
    GraphEdge,
    GraphFace,
)
from ada.topology.grid import CellGrid, GridIndexError
from ada.topology.metadata import TopologyMetadata

__all__ = [
    "Containment",
    "CellGraph",
    "CellGrid",
    "FaceConnectionInfo",
    "GraphCell",
    "GraphEdge",
    "GraphFace",
    "GridIndexError",
    "TopologyMetadata",
]
