import pytest

import ada


@pytest.mark.benchmark
def test_build_big_ifc():
    # create beams and write it to ifc
    beams = []
    for i in range(0, 1_000):
        bm = ada.Beam(f"bm{i}", (i, 0, 0), (i + 1, 0, 0), "IPE300")
        beams.append(bm)
    a = ada.Assembly() / beams
    a.to_ifc(file_obj_only=True)


@pytest.mark.benchmark
def test_build_big_ifc_plates():
    objects = []
    points = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for i in range(0, 1_000):
        bm = ada.Plate(f"pl{i}", points, 0.01, origin=(0, 0, i), )
        objects.append(bm)
    a = ada.Assembly() / objects
    a.to_ifc(file_obj_only=True)


@pytest.mark.benchmark
def test_build_big_ifc_box():
    objects = []
    for i in range(0, 1_000):
        bm = ada.PrimBox(f"obj{i}", (0, 0, i), (0.5, 0.5, i + 0.5))
        objects.append(bm)
    a = ada.Assembly() / objects
    a.to_ifc(file_obj_only=True)

@pytest.mark.benchmark
def test_build_big_ifc_sphere():
    objects = []
    for i in range(0, 1_000):
        bm = ada.PrimSphere(f"obj{i}", (0, 0, i), 0.2)
        objects.append(bm)
    a = ada.Assembly() / objects
    a.to_ifc(file_obj_only=True)