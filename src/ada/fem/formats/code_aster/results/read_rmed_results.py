from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import logger
from ada.fem.formats.code_aster.results.get_version_from_comm import (
    get_code_aster_version_from_mess,
)

if TYPE_CHECKING:
    from ada.fem.results.common import (
        ElementBlock,
        ElementFieldData,
        FEAResult,
        FemNodes,
        FemSet,
        Mesh,
        NodalFieldData,
    )


def read_rmed_file(rmed_file: str | pathlib.Path) -> FEAResult:
    from ada.fem.results.common import FEAResult, FEATypes

    if isinstance(rmed_file, str):
        rmed_file = pathlib.Path(rmed_file)

    mr = MedReader(rmed_file)
    mr.load()

    mess_file = rmed_file.with_suffix(".mess")
    software_version = "N/A"
    if mess_file.exists():
        software_version = get_code_aster_version_from_mess(mess_file)

    return FEAResult(
        rmed_file.name,
        software=FEATypes.CODE_ASTER,
        results=mr.results,
        mesh=mr.mesh,
        results_file_path=rmed_file,
        software_version=software_version,
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
        sets = self.get_sets(nodes, elements)

        return Mesh(elements=elements, nodes=nodes, sets=sets)

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

        field_type = NodalFieldType.UNKNOWN
        if "DX" in components:
            field_type = NodalFieldType.DISP

        return NodalFieldData(
            name=name,
            step=step,
            components=components,
            values=values,
            eigen_freq=eig_freq,
            field_type=field_type,
        )

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

    def get_sets(self, nodes: FemNodes, elements: list[ElementBlock]) -> dict[str, FemSet]:
        """
        Extract finite element sets (node sets and element sets) from MED file.

        Returns:
            list[FemSet]: List of FemSet objects containing both node sets and element sets
        """
        from ada.fem.sets import FemSet

        fem_sets = dict()

        # Get mesh reference
        mesh = self._load_mesh()
        mesh_name = list(self.f["ENS_MAA"].keys())[0]

        # Check if families (FAS) group exists
        if "FAS" not in self.f:
            logger.warning("No families (FAS) found in MED file - no sets available")
            return fem_sets

        families_group = self.f["FAS"]
        if mesh_name not in families_group:
            logger.warning(f"No families found for mesh '{mesh_name}'")
            return fem_sets

        mesh_families = families_group[mesh_name]

        # Extract node sets
        if "NOEUD" in mesh_families:  # NOEUD = nodes in French
            node_families = mesh_families["NOEUD"]
            node_family_map = self._get_node_family_mapping(mesh)

            for family_name in node_families.keys():
                if family_name == "FAMILLE_ZERO":  # Skip default family
                    continue

                family_group = node_families[family_name]
                if "NUM" in family_group.attrs:
                    family_num = family_group.attrs["NUM"]
                    # Find nodes belonging to this family
                    node_ids = self._get_entities_by_family(node_family_map, family_num)
                    if node_ids:
                        # Convert node IDs to Node objects (assuming they exist in self.mesh.nodes)
                        node_members = []
                        for node_id in node_ids:
                            # Find the node by ID - you may need to adjust this based on your Node class
                            node_obj = next((n for n in nodes.identifiers if n == node_id), None)
                            if node_obj is not None:
                                node_members.append(node_obj)

                        if node_members:
                            fem_set = FemSet(name=family_name, members=node_members, set_type="nset")
                            fem_sets[fem_set.name] = fem_set

        # Extract element sets
        if "MAILLE" in mesh_families:  # MAILLE = elements/cells in French
            element_families = mesh_families["MAILLE"]
            element_family_map = self._get_element_family_mapping(mesh)

            for family_name in element_families.keys():
                if family_name == "FAMILLE_ZERO":  # Skip default family
                    continue

                family_group = element_families[family_name]
                if "NUM" in family_group.attrs:
                    family_num = family_group.attrs["NUM"]
                    # Find elements belonging to this family
                    element_ids = self._get_entities_by_family(element_family_map, family_num)
                    if element_ids:
                        # Convert element IDs to Element objects (assuming they exist in self.mesh.elements)
                        element_members = []
                        for elem_id in element_ids:
                            # Find the element by ID - you may need to adjust this based on your Element class
                            elem_obj = next((e for block in elements for e in block.identifiers if e == elem_id), None)
                            if elem_obj is not None:
                                element_members.append(elem_obj)

                        if element_members:
                            fem_set = FemSet(name=family_name, members=element_members, set_type="elset")
                            fem_sets[fem_set.name] = fem_set

        return fem_sets

    def _get_node_family_mapping(self, mesh) -> dict:
        """Get mapping of node indices to family numbers."""
        family_mapping = {}

        if "NOE" in mesh and "FAM" in mesh["NOE"]:
            family_data = mesh["NOE"]["FAM"][()]
            for idx, family_num in enumerate(family_data):
                if family_num != 0:  # Skip default family
                    if family_num not in family_mapping:
                        family_mapping[family_num] = []
                    family_mapping[family_num].append(idx + 1)  # 1-based indexing

        return family_mapping

    def _get_element_family_mapping(self, mesh) -> dict:
        """Get mapping of element indices to family numbers."""
        family_mapping = {}

        if "MAI" in mesh:
            element_offset = 1  # Start element numbering from 1

            for med_cell_type, med_cell_type_group in mesh["MAI"].items():
                if "FAM" in med_cell_type_group:
                    family_data = med_cell_type_group["FAM"][()]
                    n_elements = med_cell_type_group["NOD"].attrs["NBR"]

                    for local_idx, family_num in enumerate(family_data):
                        if family_num != 0:  # Skip default family
                            global_idx = element_offset + local_idx
                            if family_num not in family_mapping:
                                family_mapping[family_num] = []
                            family_mapping[family_num].append(global_idx)

                    element_offset += n_elements

        return family_mapping

    def _get_entities_by_family(self, family_mapping: dict, family_num: int) -> list[int]:
        """Get list of entity IDs for a given family number."""
        return family_mapping.get(family_num, [])
