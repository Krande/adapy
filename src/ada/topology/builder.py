"""Topology builder base: orchestrates a cell graph + blueprint into a model.

The generic lifecycle is ``build()`` = ``build_grid()`` (an overridable hook for
populating the graph's grids) followed by ``blueprint.build()``. The base also
provides kernel-agnostic constructors (from boxes / from an assembly part) and
output helpers (assemble, show, export IFC→SQLite).

Domain builders subclass this and add their config-driven ``build_grid`` and
construction entry points; nothing here references a domain concept.
"""

from __future__ import annotations

import pathlib

import ada

__all__ = ["TopologyBuilder"]


class TopologyBuilder:
    def __init__(self, blueprint: object | None = None, cell_graph: object | None = None):
        self.blueprint = blueprint
        self.cell_graph = cell_graph
        self._output_assembly: ada.Assembly | None = None
        if blueprint is not None:
            blueprint.builder = self
        if cell_graph is not None:
            cell_graph.builder = self

    # --- lifecycle --------------------------------------------------------
    def build_grid(self) -> None:
        """Hook: populate the cell graph's grids. No-op in the generic base."""
        return None

    def build(self) -> ada.Part:
        """Build the model: populate grids, then run the blueprint."""
        self.build_grid()
        return self.blueprint.build()

    def _assembly_name(self) -> str:
        return getattr(self.cell_graph, "name", None) or "TopologyModel"

    # --- output -----------------------------------------------------------
    def get_output_assembly(self, assembly_name: str | None = None, auto_sync_ifc_store: bool = False) -> ada.Assembly:
        """Return (and cache) the output assembly wrapping the blueprint's part."""
        if self._output_assembly is None:
            a_name = assembly_name if assembly_name is not None else self._assembly_name()
            a = ada.Assembly(a_name) / self.blueprint.output_part

            if auto_sync_ifc_store:
                a.ifc_store.sync()

            self._output_assembly = a

        return self._output_assembly

    def show_cell_graph_model(self, stream_from_ifc_store: bool = False):
        if self.cell_graph is None:
            raise ValueError("No cell graph found. Please build the model first.")

        a = ada.Assembly(self._assembly_name()) / self.cell_graph.to_part()
        if stream_from_ifc_store:
            a.ifc_store.sync()
        return a.show(stream_from_ifc_store=stream_from_ifc_store)

    def show_output_model(
        self, web3d_output_glb: str | pathlib.Path | None = None, stream_from_ifc_store: bool = False
    ):
        from ada.visit.render_params import RenderParams

        if self.blueprint.output_part is None:
            raise ValueError("No output part found. Please build the model first.")

        a = self.get_output_assembly(auto_sync_ifc_store=stream_from_ifc_store)

        if web3d_output_glb:
            return a.show(
                stream_from_ifc_store=stream_from_ifc_store,
                params_override=RenderParams(
                    gltf_export_to_file=web3d_output_glb,
                    gltf_asset_extras_dict={"web3dversion": "2"},
                    stream_from_ifc_store=stream_from_ifc_store,
                ),
            )
        return a.show(stream_from_ifc_store=stream_from_ifc_store)

    def create_ifc_sqlite(self, sqlite_filepath: str | pathlib.Path) -> None:
        from ada.cadit.ifc.ifc2sql import Ifc2SqlPatcher
        from ada.config import logger

        if isinstance(sqlite_filepath, str):
            sqlite_filepath = pathlib.Path(sqlite_filepath)

        final_model = self.get_output_assembly()
        exported_ifc_file = sqlite_filepath.with_suffix(".ifc")
        sqlite_file = sqlite_filepath
        final_model.to_ifc(exported_ifc_file)

        Ifc2SqlPatcher(exported_ifc_file, logger, dest_sql_file=sqlite_file).patch()

    # --- constructors -----------------------------------------------------
    @classmethod
    def from_prim_boxes(cls, boxes, blueprint: object | None = None) -> "TopologyBuilder":
        from ada.topology.graph import CellGraph

        return cls(blueprint=blueprint, cell_graph=CellGraph.from_prim_boxes(boxes))

    @classmethod
    def from_part(cls, part, blueprint: object | None = None) -> "TopologyBuilder":
        from ada.topology.io import from_part as _from_part

        return cls(blueprint=blueprint, cell_graph=_from_part(part))
