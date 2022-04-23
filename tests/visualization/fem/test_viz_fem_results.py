import pytest

from ada.core.utils import traverse_hdf_datasets
from ada.fem.formats.code_aster.results import get_eigen_data
from ada.fem.results import EigenDataSummary


@pytest.fixture
def code_aster_files(example_files):
    return example_files / "fem_files" / "code_aster"


def test_hdf5_file_structure(code_aster_files):
    rmed_bm_eig = code_aster_files / "Cantilever_CA_EIG_bm.rmed"
    traverse_hdf_datasets(rmed_bm_eig)


def test_ca_bm_eig(code_aster_files):
    rmed_bm_eig = code_aster_files / "Cantilever_CA_EIG_bm.rmed"
    eig_res = get_eigen_data(rmed_bm_eig)
    assert type(eig_res) is EigenDataSummary
    assert eig_res.modes[0].f_hz == pytest.approx(4.672562038746128)
    assert eig_res.modes[14].f_hz == pytest.approx(131.94191888574105)


def test_ca_sh_eig(code_aster_files):
    rmed_sh_eig = code_aster_files / "Cantilever_CA_EIG_sh.rmed"
    eig_res = get_eigen_data(rmed_sh_eig)
    assert type(eig_res) is EigenDataSummary
    assert eig_res.modes[0].f_hz == pytest.approx(6.18343412480713)
    assert eig_res.modes[19].f_hz == pytest.approx(258.92237110772226)
