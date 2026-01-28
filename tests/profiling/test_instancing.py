import pytest

import ada

N_BM_INST = 2_000
N_PL_INST = 1_000


@pytest.mark.benchmark
def test_instance_beams():
    beams = []
    for i in range(0, N_BM_INST):
        bm = ada.Beam(f"bm{i}", (i, 0, 0), (i + 1, 0, 0), "IPE300")
        beams.append(bm)
    a = ada.Assembly() / beams
    beams = list(a.get_all_physical_objects(by_type=ada.Beam))
    assert len(beams) == N_BM_INST


@pytest.mark.benchmark
def test_instance_plates():
    plates = []
    for i in range(0, N_PL_INST):
        bm = ada.Plate(f"pl{i}", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.1, origin=(0, 0, i))
        plates.append(bm)
    a = ada.Assembly() / plates
    plates = list(a.get_all_physical_objects(by_type=ada.Plate))
    assert len(plates) == N_PL_INST
