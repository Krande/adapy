import pytest

import ada
from ada.occ.step.store import StepStore


def test_read_units(example_files):
    step_mm = StepStore(example_files / "step_files/flat_plate_abaqus_10x10_mm.stp")
    step_m = StepStore(example_files / "step_files/flat_plate_abaqus_10x10_m.stp")
    bbox_m = step_m.get_bbox()
    bbox_mm = step_mm.get_bbox()

    # The plates are imported into units meters and should therefore be 10 for m and 0.01 for mm
    assert bbox_m[1][0] == pytest.approx(10.0, abs=1e-4)
    assert bbox_mm[1][0] == pytest.approx(0.01, abs=1e-4)


def test_read_step_with_colors(colored_flat_plate_step):
    step_color = StepStore(colored_flat_plate_step)
    shapes = list(step_color.iter_all_shapes(True))
    assert len(shapes) == 2
    assert shapes[0].color == (1.0, 0.0, 0.0)
    assert shapes[1].color == (0.0, 0.0, 1.0)

    assert shapes[0].name == "red_plate"
    assert shapes[1].name == "blue_plate"


def test_read_ventilator(example_files):
    a = ada.from_step(example_files / "step_files/Ventilator.stp")
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
