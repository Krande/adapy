import unittest

from common import example_files

from ada.core.utils import traverse_hdf_datasets
from ada.fem.io.code_aster.results import get_eigen_frequency_deformed_meshes
from ada.fem.visualize import visualize_it

code_aster_files = example_files / "fem_files" / "code_aster"

rmed_bm_eig = code_aster_files / "Cantilever_CA_EIG_bm.rmed"
rmed_sh_eig = code_aster_files / "Cantilever_CA_EIG_sh.rmed"


class FemResults(unittest.TestCase):
    def test_hdf5_file_structure(self):
        traverse_hdf_datasets(rmed_bm_eig)

    def test_ca_bm_eig(self):
        get_eigen_frequency_deformed_meshes(rmed_bm_eig)

    def test_ca_sh_eig(self):

        _ = visualize_it(rmed_sh_eig)
        # display(app)


if __name__ == "__main__":
    unittest.main()
