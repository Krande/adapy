"""Neutral shared B-rep connectivity store for ngeom.

A body's shared vertices/edges/faces as an identity graph, referencing ngeom
curves/surfaces for shape. Two producers build it — an import producer that
preserves source record identity (Genie SAT) and a derive producer that welds
geometry — and a differ (:mod:`ada.geom.brep.diff`) pins the derive producer to
the imported ground truth. Depends only on the stdlib, numpy and ngeom.
"""

from ada.geom.brep.entities import (
    BCoEdge,
    BEdge,
    BFace,
    BLoop,
    BLump,
    BShell,
    BVertex,
    BWire,
    LoopKind,
)
from ada.geom.brep.store import BRepStore, Unresolved

__all__ = [
    "BVertex",
    "BEdge",
    "BCoEdge",
    "BLoop",
    "BFace",
    "BShell",
    "BLump",
    "BWire",
    "LoopKind",
    "BRepStore",
    "Unresolved",
]
