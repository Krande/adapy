import pytest

import ada
from ada.fem.formats.general import FEATypes


@pytest.mark.parametrize("fem_format", ["gmsh", "xdmf", FEATypes.GMSH])
def test_a_format_with_no_writer_is_refused_by_name(fem_format, tmp_path):
    with pytest.raises(ValueError, match="(gmsh|xdmf).*abaqus") as error:
        ada.Assembly("a").to_fem("m", fem_format, scratch_dir=tmp_path)
    assert "gmsh'" not in str(error.value).split("writer:")[-1]  # not offered as one of the choices


def test_meshio_can_write_a_format_with_no_result_file(tmp_path):
    pytest.importorskip("meshio")
    result = ada.Assembly("a").to_fem("m", "gmsh", fem_converter="meshio", scratch_dir=tmp_path)
    assert result.executed is False and result.results_file_path is None
