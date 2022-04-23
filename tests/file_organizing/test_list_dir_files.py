import pathlib

from ada.core.file_system import get_list_of_files


def test_list_of_files(example_files):
    list_of_files = get_list_of_files(example_files / "fem_files/meshes")

    desired_list = [
        "abaqus/element_elset.inp",
        "abaqus/nle1xf3c.inp",
        "abaqus/README.md",
        "abaqus/UUea.inp",
        "flac3d/flac3d_mesh_ex.f3grid",
        "flac3d/flac3d_mesh_ex_bin.f3grid",
        "med/box.med",
        "med/cylinder.med",
        "med/README.md",
        "medit/cube86.mesh",
        "medit/hch_strct.4.be.meshb",
        "medit/hch_strct.4.meshb",
        "medit/sphere_mixed.1.meshb",
        "msh/insulated-2.2.msh",
        "msh/insulated-4.1.msh",
        "msh/Makefile",
        "msh/README.md",
        "nastran/cylinder.fem",
        "nastran/cylinder_cells_first.fem",
        "nastran/README.md",
        "neuroglancer/simple1",
        "obj/elephav.obj",
        "ply/bun_zipper_res4.ply",
        "README.md",
        "tecplot/quad_zone_comma.tec",
        "tecplot/quad_zone_space.tec",
        "ugrid/hch_strct.4.lb8.ugrid",
        "ugrid/pyra_cube.ugrid",
        "ugrid/sphere_mixed.1.lb8.ugrid",
        "vtk/00_image.vtk",
        "vtk/01_image.vtk",
        "vtk/02_structured.vtk",
        "vtk/03_rectilinear.vtk",
        "vtk/04_rectilinear.vtk",
        "vtk/05_rectilinear.vtk",
        "vtk/rbc_001.vtk",
        "vtu/00_raw_binary.vtu",
        "vtu/01_raw_binary_int64.vtu",
        "vtu/02_raw_compressed.vtu",
        "wkt/simple.wkt",
        "wkt/whitespaced.wkt",
    ]
    for p_actual, p_desired in zip(list_of_files, desired_list):
        pa = pathlib.Path(p_actual).resolve().absolute()
        pd = (pathlib.Path(__file__).parent / "../../files/fem_files/meshes" / p_desired).resolve().absolute()
        assert pa == pd
