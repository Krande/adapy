import logging
import pathlib
from typing import TYPE_CHECKING

import h5py
import meshio
import numpy as np

from ada.fem import StepEigen
from ada.fem.concepts.eigenvalue import EigenDataSummary, EigenMode
from ada.fem.elements import ElemShape

from .read.reader import med_to_fem

if TYPE_CHECKING:
    from ada.fem.results import Results


def get_eigen_data(rmed_file) -> EigenDataSummary:
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


def read_code_aster_results(results: "Results", file_ref: pathlib.Path, overwrite):
    if type(results.assembly.fem.steps[0]) is StepEigen:
        results.eigen_mode_data = get_eigen_data(file_ref)

    fem = med_to_fem(file_ref, "temp")
    if any([x.type == ElemShape.TYPES.shell.TRI7 for x in fem.elements.shell]):
        logging.error("Meshio does not support 7 node Triangle elements yet")
        return None

    if any([x.type == ElemShape.TYPES.shell.QUAD9 for x in fem.elements.shell]):
        logging.error("Meshio does not support 9 node QUAD elements yet")
        return None

    return meshio.read(file_ref, "med")
