import pathlib

from ada.core.utils import get_list_of_files


def test_list_of_files(example_files):
    list_of_files = get_list_of_files(example_files)

    desired_list = [
        "fem_files\\abaqus\\box.inp",
        "fem_files\\abaqus\\box_rigid.inp",
        "fem_files\\calculix\\contact2e.inp",
        "fem_files\\calculix\\u1general.inp",
        "fem_files\\code_aster\\Cantilever_CA_EIG_bm.rmed",
        "fem_files\\code_aster\\Cantilever_CA_EIG_sh.rmed",
        "fem_files\\code_aster\\portal_01.med",
        "fem_files\\code_aster\\portal_01.rmed",
        "fem_files\\meshes\\abaqus\\element_elset.inp",
        "fem_files\\meshes\\abaqus\\nle1xf3c.inp",
        "fem_files\\meshes\\abaqus\\README.md",
        "fem_files\\meshes\\abaqus\\UUea.inp",
        "fem_files\\meshes\\flac3d\\flac3d_mesh_ex.f3grid",
        "fem_files\\meshes\\flac3d\\flac3d_mesh_ex_bin.f3grid",
        "fem_files\\meshes\\med\\box.med",
        "fem_files\\meshes\\med\\cylinder.med",
        "fem_files\\meshes\\med\\README.md",
        "fem_files\\meshes\\medit\\cube86.mesh",
        "fem_files\\meshes\\medit\\hch_strct.4.be.meshb",
        "fem_files\\meshes\\medit\\hch_strct.4.meshb",
        "fem_files\\meshes\\medit\\sphere_mixed.1.meshb",
        "fem_files\\meshes\\msh\\insulated-2.2.msh",
        "fem_files\\meshes\\msh\\insulated-4.1.msh",
        "fem_files\\meshes\\msh\\Makefile",
        "fem_files\\meshes\\msh\\README.md",
        "fem_files\\meshes\\nastran\\cylinder.fem",
        "fem_files\\meshes\\nastran\\cylinder_cells_first.fem",
        "fem_files\\meshes\\nastran\\README.md",
        "fem_files\\meshes\\neuroglancer\\simple1",
        "fem_files\\meshes\\obj\\elephav.obj",
        "fem_files\\meshes\\ply\\bun_zipper_res4.ply",
        "fem_files\\meshes\\README.md",
        "fem_files\\meshes\\tecplot\\quad_zone_comma.tec",
        "fem_files\\meshes\\tecplot\\quad_zone_space.tec",
        "fem_files\\meshes\\ugrid\\hch_strct.4.lb8.ugrid",
        "fem_files\\meshes\\ugrid\\pyra_cube.ugrid",
        "fem_files\\meshes\\ugrid\\sphere_mixed.1.lb8.ugrid",
        "fem_files\\meshes\\vtk\\00_image.vtk",
        "fem_files\\meshes\\vtk\\01_image.vtk",
        "fem_files\\meshes\\vtk\\02_structured.vtk",
        "fem_files\\meshes\\vtk\\03_rectilinear.vtk",
        "fem_files\\meshes\\vtk\\04_rectilinear.vtk",
        "fem_files\\meshes\\vtk\\05_rectilinear.vtk",
        "fem_files\\meshes\\vtk\\rbc_001.vtk",
        "fem_files\\meshes\\vtu\\00_raw_binary.vtu",
        "fem_files\\meshes\\vtu\\01_raw_binary_int64.vtu",
        "fem_files\\meshes\\vtu\\02_raw_compressed.vtu",
        "fem_files\\meshes\\wkt\\simple.wkt",
        "fem_files\\meshes\\wkt\\whitespaced.wkt",
        "ifc_files\\rotated_plate.Ifc",
        "ifc_files\\rotated_plate_and_beams.Ifc",
    ]
    for p_actual, p_desired in zip(list_of_files, desired_list):
        pa = pathlib.Path(p_actual).resolve().absolute()
        pd = (pathlib.Path(__file__).parent / "../../files" / p_desired).resolve().absolute()
        assert pa == pd
