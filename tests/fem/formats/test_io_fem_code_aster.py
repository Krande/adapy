from common import compare_fem_objects, example_files

import ada
from ada.config import Settings
from ada.param_models.fem_models import beam_ex1


def test_read_write_cylinder():

    name = "cylinder"

    a = ada.from_fem(example_files / "fem_files/meshes/med/cylinder.med", "code_aster", name="cylinder_rewritten")
    a.to_fem(name, "code_aster", overwrite=True)

    b = ada.from_fem((Settings.scratch_dir / name / name).with_suffix(".med"), fem_format="code_aster")

    p_a = a.parts["cylinder_rewritten"]
    p_b = b.parts["cylinder"]

    compare_fem_objects(p_a.fem, p_b.fem)


def test_read_write_box():

    name = "box"

    a = ada.from_fem(example_files / "fem_files/meshes/med/box.med", "code_aster", name="box")
    a.to_fem(name, "code_aster", overwrite=True)

    b = ada.from_fem((Settings.scratch_dir / name / name).with_suffix(".med"), fem_format="code_aster")

    p_a = a.parts["box"]
    p_b = b.parts["box"]

    compare_fem_objects(p_a.fem, p_b.fem)


def test_read_write_portal_frame():

    name = "portal"

    a = ada.from_fem(example_files / "fem_files/code_aster/portal_01.med", "code_aster", name=name)
    a.to_fem(name, "code_aster", overwrite=True)

    b = ada.from_fem((Settings.scratch_dir / name / name).with_suffix(".med"), fem_format="code_aster")

    p_a = a.parts[name]
    p_b = b.parts[name]

    compare_fem_objects(p_a.fem, p_b.fem)


def test_write_cantilever():

    name = "cantilever_code_aster"

    a = beam_ex1()

    a.to_fem(name, fem_format="code_aster", overwrite=True)
    dest_file = (Settings.scratch_dir / name / name).with_suffix(".med")

    b = ada.from_fem(dest_file, fem_format="code_aster")

    p_a = a.parts["MyPart"]
    p_b = b.parts["cantilever_code_aster"]

    compare_fem_objects(p_a.fem, p_b.fem)
