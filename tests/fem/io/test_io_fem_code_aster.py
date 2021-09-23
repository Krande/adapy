import unittest

from common import build_test_beam_fem, compare_fem_objects, example_files

from ada import Assembly
from ada.config import Settings
from ada.param_models.fem_models import beam_ex1


class TestCodeAster(unittest.TestCase):
    def test_read_write_cylinder(self):

        name = "cylinder"

        a = Assembly()
        a.read_fem(example_files / "fem_files/meshes/med/cylinder.med", "code_aster", name="cylinder_rewritten")
        a.to_fem(name, "code_aster", overwrite=True)

        b = Assembly()
        b.read_fem((Settings.scratch_dir / name / name).with_suffix(".med"), fem_format="code_aster")

        p_a = a.parts["cylinder_rewritten"]
        p_b = b.parts["cylinder"]

        compare_fem_objects(p_a.fem, p_b.fem, self)

    def test_read_write_box(self):

        name = "box"

        a = Assembly()
        a.read_fem(example_files / "fem_files/meshes/med/box.med", "code_aster", name="box")
        a.to_fem(name, "code_aster", overwrite=True)

        b = Assembly()
        b.read_fem((Settings.scratch_dir / name / name).with_suffix(".med"), fem_format="code_aster")

        p_a = a.parts["box"]
        p_b = b.parts["box"]

        compare_fem_objects(p_a.fem, p_b.fem, self)

    def test_read_write_portal_frame(self):

        name = "portal"

        a = Assembly()
        a.read_fem(example_files / "fem_files/code_aster/portal_01.med", "code_aster", name=name)
        a.to_fem(name, "code_aster", overwrite=True)

        b = Assembly()
        b.read_fem((Settings.scratch_dir / name / name).with_suffix(".med"), fem_format="code_aster")

        p_a = a.parts[name]
        p_b = b.parts[name]

        compare_fem_objects(p_a.fem, p_b.fem, self)

    def test_write_cantilever(self):

        name = "cantilever_code_aster"

        a = beam_ex1()

        a.to_fem(name, fem_format="code_aster", overwrite=True)
        dest_file = (Settings.scratch_dir / name / name).with_suffix(".med")

        b = Assembly()
        b.read_fem(dest_file, fem_format="code_aster")

        p_a = a.parts["MyPart"]
        p_b = b.parts["cantilever_code_aster"]

        compare_fem_objects(p_a.fem, p_b.fem, self)

    def test_write_bm(self):
        a = build_test_beam_fem("line")
        a.to_fem("my_code_aster_bm", fem_format="code_aster", overwrite=True)

    def test_write_bm_shell(self):
        a = build_test_beam_fem("shell")
        a.to_fem("my_code_aster_bm_shell", fem_format="code_aster", overwrite=True)


if __name__ == "__main__":
    unittest.main()
