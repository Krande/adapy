import unittest

from common import example_files

from ada.core.utils import traverse_hdf_datasets
from ada.fem.formats.code_aster.results import get_eigen_data
from ada.fem.results import EigenDataSummary
from ada.visualize.femviz import visualize_it

code_aster_files = example_files / "fem_files" / "code_aster"

rmed_bm_eig = code_aster_files / "Cantilever_CA_EIG_bm.rmed"
rmed_sh_eig = code_aster_files / "Cantilever_CA_EIG_sh.rmed"


class FemResults(unittest.TestCase):
    def test_hdf5_file_structure(self):
        traverse_hdf_datasets(rmed_bm_eig)

    def test_ca_bm_eig(self):
        eig_res = get_eigen_data(rmed_bm_eig)
        self.assertEqual(type(eig_res), EigenDataSummary)
        self.assertAlmostEqual(eig_res.modes[0].f_hz, 4.672562038746128)
        self.assertAlmostEqual(eig_res.modes[14].f_hz, 131.94191888574105)

    def test_ca_sh_eig(self):
        eig_res = get_eigen_data(rmed_sh_eig)
        self.assertEqual(type(eig_res), EigenDataSummary)
        self.assertAlmostEqual(eig_res.modes[0].f_hz, 6.18343412480713)
        self.assertAlmostEqual(eig_res.modes[19].f_hz, 258.92237110772226)

    def test_viz_ca_sh_eig(self):

        _ = visualize_it(rmed_sh_eig)
        # display(app)


if __name__ == "__main__":
    unittest.main()
