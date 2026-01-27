import pytest
from pytest import approx

import ada
from ada.sections.categories import BaseTypes


@pytest.mark.parametrize("xml_file_name", ["beams_constant_offset.xml", "beams_flush_offset.xml"])
def test_gxml_offset(xml_file_name, example_files):
    a = ada.from_genie_xml(example_files / "fem_files/sesam/varying_offset" / xml_file_name)

    model_cog = a.calculate_cog()
    if xml_file_name == "beams_constant_offset.xml":
        assert model_cog.p.is_equal(ada.Point([0.5, 1.79937725, 0.23109376]))
    elif xml_file_name == "beams_flush_offset.xml":
        assert model_cog.p.is_equal(ada.Point([0.5, 1.79937725, 0.23109376]))

    for bm in a.get_all_physical_objects(by_type=ada.Beam):
        bm: ada.Beam
        bm_cog = bm.get_cog()
        bm_mass = bm.get_mass()
        if bm.section.type == BaseTypes.BOX:
            # beam: cube_BOX_Room1_f3_i1_j1_gbm1, cog: [0.5 0.  0.4]
            assert bm_mass == approx(514.96, abs=1e-3)
            assert bm_cog.x == approx(0.500, abs=1e-3)
            assert bm_cog.y == approx(0.000, abs=1e-3)
            assert bm_cog.z == approx(0.400, abs=1e-3)
        elif bm.section.type == BaseTypes.TUBULAR:
            # beam: cube_TUBULAR_Room1_f3_i1_j1_gbm1, cog: [0.5   1.5   0.375]
            assert bm_mass == approx(617.154, abs=1e-3)
            assert bm_cog.x == approx(0.500, abs=1e-3)
            assert bm_cog.y == approx(1.500, abs=1e-3)
            assert bm_cog.z == approx(0.375, abs=1e-3)
        elif bm.section.type == BaseTypes.IPROFILE and bm.name == "cube_IPROFILE_Room1_f3_i1_j1_gbm1":
            # beam: cube_IPROFILE_Room1_f3_i1_j1_gbm1, cog: [0.5   3.    0.145]
            assert bm_mass == approx(83.4219, abs=1e-4)
            assert bm_cog.x == approx(0.500, abs=1e-3)
            assert bm_cog.y == approx(3.000, abs=1e-3)
            assert bm_cog.z == approx(0.145, abs=1e-3)
        elif bm.section.type == BaseTypes.IPROFILE and bm.name == "cube_TPROFILE_Room1_f3_i1_j1_gbm1":
            # beam: cube_TPROFILE_Room1_f3_i1_j1_gbm1, cog: [ 0.5         4.4875     -0.44811927]
            assert bm_mass == approx(213.912, abs=1e-3)
            assert bm_cog.x == approx(0.500, abs=1e-3)
            assert bm_cog.y == approx(4.500, abs=1e-4)
            assert bm_cog.z == approx(-0.448, abs=1e-3)
        elif bm.section.type == BaseTypes.ANGULAR:
            # beam: beam: cube_ANGULAR_Room1_f3_i1_j1_gbm1, cog: [ 5.00000000e-01  6.00000000e+00 -3.81468468e-12]
            assert bm_mass == approx(18.0059, abs=1e-4)
            assert bm_cog.x == approx(0.500, abs=1e-3)
            assert bm_cog.y == approx(6.000, abs=1e-3)
            assert bm_cog.z == approx(-0.107, abs=1e-3)
        elif bm.section.type == BaseTypes.CHANNEL:
            # beam: cube_CHANNEL_Room1_f3_i1_j1_gbm1, cog: [0.5  7.5  0.09]
            assert bm_mass == approx(22.01114, abs=1e-3)
            assert bm_cog.x == approx(0.500, abs=1e-3)
            assert bm_cog.y == approx(7.500, abs=1e-3)
            assert bm_cog.z == approx(0.09, abs=1e-3)
        elif bm.section.type == BaseTypes.FLATBAR:
            # beam: cube_FLATBAR_Room1_f3_i1_j1_gbm1, cog: [0.5  9.   0.05]
            assert bm_mass == approx(7.85, abs=1e-3)
            assert bm_cog.x == approx(0.500, abs=1e-3)
            assert bm_cog.y == approx(9.00, abs=1e-3)
            assert bm_cog.z == approx(0.05, abs=1e-3)
