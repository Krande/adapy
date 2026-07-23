"""One-liner entry points for the topo_model demo.

The flow is the whole engine in three steps: space boxes ->
``TopologyBuilder.from_prim_boxes`` (builds the cell graph) -> ``SteelStru``
blueprint (turns classified faces/edges into structure) -> output assembly.
"""

from __future__ import annotations

import ada
from ada.topology import TopologyBuilder

from .blueprint import SteelStru

__all__ = ["make_space_boxes", "build_topo_model"]


def make_space_boxes() -> list[ada.PrimBox]:
    """Two adjacent 5 m x 5 m x 3 m spaces. Two cells (rather than one)
    exercise the topology machinery: the shared internal wall and the
    deduplication of shared girder/column edges."""
    return [
        ada.PrimBox("Cell1", (0, 0, 0), (5, 5, 3)),
        ada.PrimBox("Cell2", (5, 0, 0), (10, 5, 3)),
    ]


def build_topo_model(name: str = "TopoModelDemo") -> ada.Assembly:
    """Build the demo model with default profiles and return the assembly."""
    builder = TopologyBuilder.from_prim_boxes(make_space_boxes(), blueprint=SteelStru())
    builder.build()
    return builder.get_output_assembly(name)
