import medcoupling as mc
import pytest

import ada
from ada.base.types import GeomRepr
from ada.fem.meshing import GmshOptions
from ada.materials.metals import CarbonSteel


def _make_cube_part(geom_repr: GeomRepr, element_order: int, use_quads: bool, use_hex: bool) -> ada.Part:
    options = GmshOptions(Mesh_ElementOrder=element_order)
    cube = ada.PrimBox("box", (0, 0, 0), (1, 1, 1), material=ada.Material("S420", CarbonSteel("S420")))
    part = ada.Part("MyPart") / cube
    part.fem = part.to_fem_obj(
        1.5,
        geom_repr,
        options=options,
        use_quads=use_quads,
        use_hex=use_hex,
    )
    nodes = cube.bbox().sides.back(return_fem_nodes=True)
    part.fem.add_bc(ada.fem.Bc("Fixed", ada.fem.FemSet("bc_nodes", nodes), [1, 2, 3]))
    return part


@pytest.fixture
def cube_solid_static_o1():
    cube_part = _make_cube_part(GeomRepr.SOLID, 1, False, True)
    a = ada.Assembly("MyAssembly") / [cube_part]
    step = a.fem.add_step(ada.fem.StepImplicitStatic("Static", nl_geom=True, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("Gravity", -9.81 * 800))
    return a


@pytest.fixture
def cube_solid_static_o2():
    cube_part = _make_cube_part(GeomRepr.SOLID, 2, False, True)
    a = ada.Assembly("MyAssembly") / [cube_part]
    step = a.fem.add_step(ada.fem.StepImplicitStatic("Static", nl_geom=True, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("Gravity", -9.81 * 800))
    return a


def test_basic_cube_mesh(cube_solid_static_o1, cube_solid_static_o2):
    res_o1 = cube_solid_static_o1.to_fem("cube_solid_static_o1", "code_aster", overwrite=False, execute=True)
    res_o2 = cube_solid_static_o2.to_fem("cube_solid_static_o2", "code_aster", overwrite=False, execute=True)

    data_o1 = mc.MEDFileData.New(res_o1.results_file_path.as_posix())
    fields_o1 = data_o1.getFields()
    data_vars = dir(data_o1)

    meshes = {}
    for mesh in data_o1.getMeshes():
        mesh_name = mesh.getName()
        print(mesh_name)
        meshes[mesh_name] = mesh
    for field in fields_o1:
        field_name = field.getName()
        field_info = field.getInfo()
        fields_vars = dir(field)
        field_mesh_name = field.getMeshName()
        mesh = meshes.get(field_mesh_name)
        mesh_vars = dir(mesh)
        coords = mesh.getCoords()
        coords_vars = dir(coords)
        coords_np = coords.toNumPyArray()
        # field_type = field.getTypeOfField()
        print(field_name)

    print("done")
