import h5py
import numpy as np

from .reader import med_to_fem


def get_eigen_frequency_deformed_meshes(rmed_file):
    fem = med_to_fem(rmed_file, "temp")
    f = h5py.File(rmed_file)
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
