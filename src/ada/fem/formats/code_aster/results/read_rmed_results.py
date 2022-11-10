from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import h5py
import numpy as np

if TYPE_CHECKING:
    from ada.fem.results.common import (
        ElementBlock,
        ElementFieldData,
        FEAResult,
        FemNodes,
        Mesh,
        NodalFieldData,
    )


def read_rmed_file(rmed_file: str | pathlib.Path) -> FEAResult:
    from ada.fem.results.common import FEAResult, FEATypes

    mr = MedReader(rmed_file)
    mr.load()
    return FEAResult(rmed_file.name, software=FEATypes.CODE_ASTER, results=mr.results, mesh=mr.mesh)


@dataclass
class MedReader:
    rmed_file: str | pathlib.Path
    f: h5py.File = None

    mesh: Mesh = None
    results: list[ElementFieldData | NodalFieldData] = None

    _dim: int = None

    def __post_init__(self):
        if isinstance(self.rmed_file, str):
            self.rmed_file = pathlib.Path(self.rmed_file)

    def load(self):
        self.f = h5py.File(self.rmed_file)
        self.mesh = self.get_mesh()
        self.results = self.get_results()
        self.f.close()

    def get_mesh(self) -> Mesh:
        from ada.fem.results.common import Mesh

        mesh = self._load_mesh()

        nodes = self.get_nodes(mesh)
        elements = self.get_elements(mesh)

        return Mesh(elements=elements, nodes=nodes)

    def get_nodes(self, mesh) -> FemNodes:
        from ada.fem.results.common import FemNodes

        pts_dataset = mesh["NOE"]["COO"]
        n_points = pts_dataset.attrs["NBR"]
        coords = pts_dataset[()].reshape((n_points, self._dim), order="F")
        node_identifiers = np.array(range(1, len(coords) + 1))

        # TODO: Figure out where the actual point identifiers are stored in med files
        return FemNodes(coords=coords, identifiers=node_identifiers)

    def get_elements(self, mesh) -> list[ElementBlock]:
        from ada.fem.formats.code_aster.common import med_to_ada_type
        from ada.fem.results.common import ElementBlock, ElementInfo, FEATypes

        cell_types = []
        med_cells = mesh["MAI"]

        blocks = []
        for med_cell_type, med_cell_type_group in med_cells.items():
            if med_cell_type == "PO1":
                logging.warning("Point elements are still not supported")
                continue

            cell_type = med_to_ada_type(med_cell_type)
            cell_types.append(cell_type)

            nod = med_cell_type_group["NOD"]
            n_cells = nod.attrs["NBR"]
            node_refs = nod[()].reshape(n_cells, -1, order="F")

            if "NUM" in med_cell_type_group.keys():
                num = list(med_cell_type_group["NUM"])
            else:
                num = np.arange(0, len(node_refs))

            elem_info = ElementInfo(type=cell_type, source_software=FEATypes.CODE_ASTER, source_type=med_cell_type)
            elem_block = ElementBlock(elem_info=elem_info, node_refs=node_refs, identifiers=num)
            blocks.append(elem_block)

        return blocks

    def get_results(self) -> list[ElementFieldData | NodalFieldData]:
        ...

    def _load_mesh(self):
        mesh_ensemble = self.f["ENS_MAA"]
        meshes = mesh_ensemble.keys()
        if len(meshes) != 1:
            raise ValueError("Must only contain exactly 1 mesh, found {}.".format(len(meshes)))

        mesh_name = list(meshes)[0]
        mesh = mesh_ensemble[mesh_name]
        self._dim = mesh.attrs["ESP"]

        # Possible time-stepping
        if "NOE" not in mesh:
            # One needs NOE (node) and MAI (French maillage, meshing) data. If they
            # are not available in the mesh, check for time-steppings.
            time_step = mesh.keys()
            if len(time_step) != 1:
                raise ValueError(f"Must only contain exactly 1 time-step, found {len(time_step)}.")
            mesh = mesh[list(time_step)[0]]
        return mesh
