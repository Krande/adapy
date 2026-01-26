import pytest

import ada
from ada.sections.categories import BaseTypes

# todo create mass property on beam and uncomment asserts below to include check


@pytest.mark.parametrize("xml_file_name", ["beams_constant_offset.xml", "beams_flush_offset.xml"])
def test_gxml_offset(xml_file_name, example_files):
    a = ada.from_genie_xml(example_files / "fem_files/sesam/varying_offset" / xml_file_name)
    for bm in a.get_all_physical_objects(by_type=ada.Beam):
        # print(f"beam: {bm.name}, cog: {bm.cog}")
        bm_cog = bm.cog
        if bm.section.type == BaseTypes.BOX:
            # beam: cube_BOX_Room1_f3_i1_j1_gbm1, cog: [0.5 0.  0.4]
            # assert round(bm.mass, 3) == 514.96
            assert round(bm_cog.x, 3) == 0.500
            assert round(bm_cog.y, 3) == 0.000
            assert round(bm_cog.z, 3) == 0.400
        elif bm.section.type == BaseTypes.TUBULAR:
            # beam: cube_TUBULAR_Room1_f3_i1_j1_gbm1, cog: [0.5   1.5   0.375]
            # assert round(bm.mass, 3) == 617.154
            assert round(bm_cog.x, 3) == 0.500
            assert round(bm_cog.y, 3) == 1.500
            assert round(bm_cog.z, 3) == 0.375
        elif bm.section.type == BaseTypes.IPROFILE and bm.name == "cube_IPROFILE_Room1_f3_i1_j1_gbm1":
            # beam: cube_IPROFILE_Room1_f3_i1_j1_gbm1, cog: [0.5   3.    0.145]
            # assert round(bm.mass, 4) == 83.4219
            assert round(bm_cog.x, 3) == 0.500
            assert round(bm_cog.y, 3) == 3.000
            assert round(bm_cog.z, 3) == 0.145
        elif bm.section.type == BaseTypes.IPROFILE and bm.name == "cube_TPROFILE_Room1_f3_i1_j1_gbm1":
            # beam: cube_TPROFILE_Room1_f3_i1_j1_gbm1, cog: [ 0.5         4.4875     -0.44811927]
            # assert round(bm.mass, 3) == 213.912
            assert round(bm_cog.x, 3) == 0.500
            assert round(bm_cog.y, 4) == 4.500
            assert round(bm_cog.z, 3) == -0.448
        elif bm.section.type == BaseTypes.ANGULAR:
            # beam: beam: cube_ANGULAR_Room1_f3_i1_j1_gbm1, cog: [ 5.00000000e-01  6.00000000e+00 -3.81468468e-12]
            # assert round(bm.mass, 4) == 18.0059
            assert round(bm_cog.x, 3) == 0.500
            assert round(bm_cog.y, 3) == 6.000
            assert round(bm_cog.z, 3) == -0.107
        elif bm.section.type == BaseTypes.CHANNEL:
            # beam: cube_CHANNEL_Room1_f3_i1_j1_gbm1, cog: [0.5  7.5  0.09]
            # assert round(bm.mass, 5) == 22.01114
            assert round(bm_cog.x, 3) == 0.500
            assert round(bm_cog.y, 3) == 7.500
            assert round(bm_cog.z, 3) == 0.09
        elif bm.section.type == BaseTypes.FLATBAR:
            # beam: cube_FLATBAR_Room1_f3_i1_j1_gbm1, cog: [0.5  9.   0.05]
            # assert round(bm.mass, 3) == 7.85
            assert round(bm_cog.x, 3) == 0.500
            assert round(bm_cog.y, 3) == 9.00
            assert round(bm_cog.z, 3) == 0.05
