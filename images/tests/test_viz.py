import unittest

import meshio
import numpy as np

from ada.base.render_fem import Results, render_mesh, viz_fem
from ada.config import Settings
from ada.param_models.fem_models import beam_ex1

vertices = np.asarray(
    [[0, 0, 0], [0, 0, 1], [0, 1, 0], [0, 1, 1], [1, 0, 0], [1, 0, 1], [1, 1, 0], [1, 1, 1]], dtype="float32"
)

faces = np.asarray(
    [
        [0, 1, 3],
        [0, 3, 2],
        [0, 2, 4],
        [2, 6, 4],
        [0, 4, 1],
        [1, 4, 5],
        [2, 3, 6],
        [3, 7, 6],
        [1, 5, 3],
        [3, 5, 7],
        [4, 6, 5],
        [5, 6, 7],
    ],
    dtype="uint16",
).ravel()  # We need to flatten index array


vertexcolors = np.asarray(
    [(0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0), (0, 1, 1), (1, 0, 1), (1, 1, 0), (1, 1, 1)], dtype="float32"
)


class MyTestCase(unittest.TestCase):
    def test_base_example(self):
        render_mesh(vertices, faces, vertexcolors)

    def test_fem_cantilever(self):
        a = beam_ex1()
        p = a.parts["MyPart"]
        ca_name = "MyCantilever_code_aster"
        a.to_fem(ca_name, "code_aster", overwrite=True, execute=True)

        rmed = (Settings.scratch_dir / ca_name / ca_name).with_suffix(".rmed")
        mesh = meshio.read(rmed, "med")

        viz_fem(p.fem, mesh, "DISP[10] - 1")

    def test_fem_cantilever_res_class(self):
        a = beam_ex1()
        p = a.parts["MyPart"]
        ca_name = "MyCantilever_code_aster"
        # a.to_fem(ca_name, "code_aster", overwrite=True, execute=True)

        rmed = (Settings.scratch_dir / ca_name / ca_name).with_suffix(".rmed")
        res = Results(p, rmed, palette=[(1, 0, 0), (0, 149 / 255, 239 / 255)])
        res._repr_html_()
        res._render_sets


if __name__ == "__main__":
    unittest.main()
