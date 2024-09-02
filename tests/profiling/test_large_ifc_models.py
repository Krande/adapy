import pytest

import ada

@pytest.mark.parametrize("num_beams", [100, 1_000, 5_000, 10_000])
@pytest.mark.benchmark
def test_build_big_ifc(num_beams):
    # create beams and write it to ifc
    beams = []
    for i in range(0, num_beams):
        bm = ada.Beam(f"bm{i}", (i, 0, 0), (i + 1, 0, 0), "IPE300")
        beams.append(bm)
    a = ada.Assembly() / beams
    a.to_ifc(file_obj_only=True)
