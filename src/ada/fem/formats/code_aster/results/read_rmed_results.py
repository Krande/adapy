from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import logger

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

    if isinstance(rmed_file, str):
        rmed_file = pathlib.Path(rmed_file)

    mr = MedReader(rmed_file)
    mr.load()

    return FEAResult(
        rmed_file.name, software=FEATypes.CODE_ASTER, results=mr.results, mesh=mr.mesh, results_file_path=rmed_file
    )


@dataclass
class MedReader:
    rmed_file: str | pathlib.Path
    f: h5py.File = None

    mesh: Mesh = None
    results: list[ElementFieldData | NodalFieldData] = None

    _dim: int = None
    is_eigen_analysis: bool = False

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
                logger.warning("Point elements are still not supported")
                continue

            cell_type = med_to_ada_type(med_cell_type)
            cell_types.append(cell_type)

            nod = med_cell_type_group["NOD"]
            n_cells = nod.attrs["NBR"]
            node_refs = nod[()].reshape(n_cells, -1, order="F")

            if "NUM" in med_cell_type_group.keys():
                num = np.array(med_cell_type_group["NUM"])
            else:
                num = np.arange(0, len(node_refs))

            elem_info = ElementInfo(type=cell_type, source_software=FEATypes.CODE_ASTER, source_type=med_cell_type)
            elem_block = ElementBlock(elem_info=elem_info, node_refs=node_refs, identifiers=num)
            blocks.append(elem_block)

        return blocks

    def get_results(self) -> list[ElementFieldData | NodalFieldData]:
        fields = self.f.get("CHA")
        results = []

        for name, data in fields.items():
            nom = data.attrs.get("NOM")
            if nom is None:
                raise ValueError()

            components = nom.decode().split()
            time_step = sorted(data.keys())  # associated time-steps
            time_steps = []
            if len(time_step) == 1:  # single time-step
                names = [name]  # do not change field name
                res = data[time_step[0]].attrs.get("PDT")
                time_steps.append(res)
            else:  # many time-steps
                names = [None] * len(time_step)
                for i, key in enumerate(time_step):
                    t = data[key].attrs["PDT"]  # current time
                    time_steps.append(float(t))
                    names[i] = name + f"[{i:d}] - {t:g}"

            if name == "modes___DEPL":
                self.is_eigen_analysis = True

            # MED field can contain multiple types of data
            for i, key in enumerate(time_step):
                med_data = data[key]  # at a particular time step
                step_name = names[i]
                ts = time_steps[i]
                step_index = i + 1
                for supp in med_data:
                    if supp == "NOE":  # continuous nodal (NOEU) data
                        result = self._load_nodal_field_data(step_index, step_name, med_data, ts, components)
                        results.append(result)
                    else:  # Gauss points (ELGA) or DG (ELNO) data
                        result = self._load_element_field_data(step_index, step_name, med_data[supp], ts, components)
                        results.append(result)

        return results

    def _load_element_field_data(self, i, name, med_data, step, components) -> ElementFieldData:
        from ada.fem.results.common import ElementFieldData

        profile = med_data.attrs["PFL"]
        data_profile = med_data[profile]
        n_cells = data_profile.attrs["NBR"]
        n_gauss_points = data_profile.attrs["NGA"]
        if profile.decode() == "MED_NO_PROFILE_INTERNAL":  # default profile with everything
            values = data_profile["CO"][()].reshape(n_cells, n_gauss_points, -1, order="F")
        else:
            raise NotImplementedError()

        # Only 1 data point per cell, shape -> (n_cells, n_components)
        if n_gauss_points == 1:
            values = values[:, 0, :]
            if values.shape[-1] == 1:  # cut off for scalars
                values = values[:, 0]

        eig_freq = None
        if self.is_eigen_analysis:
            eig_freq = step
            step = i

        return ElementFieldData(name, step, components, values, eigen_freq=eig_freq)

    def _load_nodal_field_data(self, i, name, med_data, step, components) -> NodalFieldData:
        from ada.fem.results.common import NodalFieldData, NodalFieldType

        profile = med_data["NOE"].attrs["PFL"]
        data_profile = med_data["NOE"][profile]
        n_points = data_profile.attrs["NBR"]
        if profile.decode() == "MED_NO_PROFILE_INTERNAL":  # default profile with everything
            values = data_profile["CO"][()].reshape(n_points, -1, order="F")
        else:
            raise NotImplementedError()

        if values.shape[-1] == 1:  # cut off for scalars
            values = values[:, 0]

        node_ids = self.mesh.nodes.identifiers
        values = np.insert(values, 0, node_ids, axis=1)

        eig_freq = None
        if self.is_eigen_analysis:
            eig_freq = step
            step = i

        field_type = None
        if "DX" in components:
            field_type = NodalFieldType.DISP

        return NodalFieldData(name, step, components, values, eigen_freq=eig_freq, field_type=field_type)

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
