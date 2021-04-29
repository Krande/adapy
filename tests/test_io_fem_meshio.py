import unittest

from common import example_files

from ada import Assembly


class TestMeshio(unittest.TestCase):
    def test_read_write_code_aster_to_xdmf(self):
        a = Assembly("meshio_from_ca", "temp")
        a.read_fem(example_files / "fem_files/meshes/med/box.med", fem_converter="meshio")
        a.to_fem("box_analysis_xdmf", fem_format="xdmf", fem_converter="meshio")

    def test_read_write_code_aster_to_abaqus(self):
        a = Assembly("meshio_from_ca", "temp")
        a.read_fem(example_files / "fem_files/meshes/med/box.med", fem_converter="meshio")
        a.to_fem("box_analysis_abaqus", fem_format="abaqus", fem_converter="meshio")

    def test_read_C3D20(self):
        from ada import Assembly

        a = Assembly("my_assembly", "temp")
        a.read_fem(example_files / "fem_files/calculix/contact2e.inp", fem_converter="meshio")

    def test_read_abaqus(self):
        b = Assembly("my_assembly", "temp")
        b.read_fem(example_files / "fem_files/meshes/abaqus/element_elset.inp", fem_converter="meshio")

    def test_read_code_aster(self):
        a = Assembly("meshio_from_ca", "temp")
        a.read_fem(example_files / "fem_files/meshes/med/box.med", fem_converter="meshio")


if __name__ == "__main__":
    unittest.main()
