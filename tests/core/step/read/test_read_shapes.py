import pathlib

import pytest

import ada
from ada.occ.step.reader import StepStore
from ada.occ.stp_to_ifc import step_file_to_ifc_file


@pytest.fixture
def colored_step_file(example_files):
    return example_files / "step_files/flat_plate_abaqus_10x10_m_wColors.stp"


def test_read_units(example_files):
    step_mm = StepStore(example_files / "step_files/flat_plate_abaqus_10x10_mm.stp")
    step_m = StepStore(example_files / "step_files/flat_plate_abaqus_10x10_m.stp")
    step_m.get_bbox()
    step_mm.get_bbox()
    print()


def test_read_step_with_colors(colored_step_file):
    step_color = StepStore(colored_step_file)
    for shp in step_color.iter_all_shapes(True):
        print(shp)


def test_convert_step_with_colors_to_ifc(colored_step_file):
    dest_file = pathlib.Path("output/wcolors.ifc").resolve().absolute()
    print(f"Destination file: {dest_file}")
    step_file_to_ifc_file(colored_step_file, dest_file, include_colors=True)


def test_read_ventilator(example_files):
    a = ada.from_step(example_files / "step_files/Ventilator.stp")
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    # geom = objects[0].geom
    # shape = int(geom.this)
    # import gmsh
    # gmsh.initialize()
    # ents = gmsh.model.occ.importShapesNativePointer(shape, highestDimOnly=True)
    # gmsh.model.occ.synchronize()
    # gmsh.fltk.run()
    # print(ents)
