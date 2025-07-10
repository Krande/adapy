import pytest
import ada

N_BM_INST = 10_000

@pytest.mark.benchmark
def test_instance_beams():
    # create beams and write it to ifc
    beams = []
    for i in range(0, N_BM_INST):
        bm = ada.Beam(f"bm{i}", (i, 0, 0), (i + 1, 0, 0), "IPE300")
        beams.append(bm)
    a = ada.Assembly() / beams
    beams = list(a.get_all_physical_objects())
    assert len(beams) == N_BM_INST