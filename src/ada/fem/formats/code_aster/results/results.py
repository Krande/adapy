from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import logger
from ada.fem import StepEigen
from ada.fem.elements import ElemShape
from ada.fem.formats.code_aster.read.reader import med_to_fem
from ada.fem.results.common import CellBlockData, MeshData

if TYPE_CHECKING:
    from ada.fem.results import Results
    from ada.fem.results.eigenvalue import EigenDataSummary


def get_eigen_data(rmed_file) -> EigenDataSummary:
    from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

    with h5py.File(rmed_file) as f:
        modes = f.get("CHA/modes___DEPL")
        eigen_modes = []

        for mname, m in modes.items():
            mode = m.attrs["NDT"]
            freq = m.attrs["PDT"]
            eigen_modes.append(EigenMode(mode, f_hz=freq))

    return EigenDataSummary(eigen_modes)


def get_eigen_frequency_deformed_meshes(rmed_file):
    fem = med_to_fem(rmed_file, "temp")

    with h5py.File(rmed_file) as f:
        modes = f.get("CHA/modes___DEPL")
        nodes = fem.nodes.to_np_array()
        eig_deformed_meshes = []

        for mname, m in modes.items():
            res = m["NOE"]["MED_NO_PROFILE_INTERNAL"]["CO"][()]
            dofs = res.reshape(len(fem.nodes), 6)
            eig_deformed_meshes.append(nodes + np.delete(dofs, np.s_[2:5], 1))
            mode = m.attrs["NDT"]
            freq = m.attrs["PDT"]
            print(mode, freq)

    # TODO: Figure out what kind of information is needed for animating frames in threejs/blender
    return fem, eig_deformed_meshes


def read_code_aster_results(results: "Results", file_ref: pathlib.Path, overwrite) -> MeshData | None:
    if results.assembly is not None and isinstance(results.assembly.fem.steps[0], StepEigen):
        results.eigen_mode_data = get_eigen_data(file_ref)

    fem = med_to_fem(file_ref, "temp")
    if any([x.type == ElemShape.TYPES.shell.TRI7 for x in fem.elements.shell]):
        logger.error("7 node Triangle elements are not yet supported")
        return None

    if any([x.type == ElemShape.TYPES.shell.QUAD9 for x in fem.elements.shell]):
        logger.error("9 node QUAD elements are not yet supported")
        return None

    # Bridge call: meshio handles the MED result-field parsing today;
    # Stage B replaces this with a native h5py reader and removes the
    # meshio import entirely.
    import meshio

    mio_mesh = meshio.read(file_ref, "med")
    cells = [CellBlockData(cell_type=cb.type, data=np.asarray(cb.data)) for cb in mio_mesh.cells]
    return MeshData(
        points=np.asarray(mio_mesh.points),
        cells=cells,
        point_data={k: np.asarray(v) for k, v in mio_mesh.point_data.items()},
        cell_data={k: [np.asarray(b) for b in v] for k, v in mio_mesh.cell_data.items()},
    )
