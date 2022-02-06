import logging
import pathlib

import pytest

import ada
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.utils import default_fem_res_path
from ada.fem.meshing.concepts import GmshOptions
from ada.fem.results import Results

test_dir = ada.config.Settings.scratch_dir / "ada_fem_test_static"
EL_TYPES = ada.fem.Elem.EL_TYPES


def is_conditions_unsupported(fem_format, geom_repr, elem_order):
    return False


@pytest.mark.parametrize("use_hex_quad", [True, False])
@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
def test_fem_static(
    beam_fixture,
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    overwrite=True,
    execute=True,
    eigen_modes=11,
    name=None,
):
    geom_repr = geom_repr.upper()
    if name is None:
        name = f"cantilever_static_{fem_format}_{geom_repr}_o{elem_order}_hq{use_hex_quad}"

    p = ada.Part("MyPart")
    a = ada.Assembly("MyAssembly") / [p / beam_fixture]

    if geom_repr == "LINE" and use_hex_quad is True:
        return None

    props = dict(use_hex=use_hex_quad) if geom_repr == "SOLID" else dict(use_quads=use_hex_quad)

    step = a.fem.add_step(ada.fem.StepImplicit("gravity", nl_geom=True, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))

    if overwrite is False:
        if is_conditions_unsupported(fem_format, geom_repr, elem_order):
            return None
        res_path = default_fem_res_path(name, scratch_dir=test_dir, fem_format=fem_format)
        return Results(res_path, name, fem_format, a, import_mesh=False)
    else:
        p.fem = beam_fixture.to_fem_obj(0.05, geom_repr, options=GmshOptions(Mesh_ElementOrder=elem_order), **props)
        fix_set = p.fem.add_set(
            ada.fem.FemSet("bc_nodes", beam_fixture.bbox.sides.back(return_fem_nodes=True, fem=p.fem))
        )
        a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

    try:
        res = a.to_fem(name, fem_format, overwrite=overwrite, execute=execute, scratch_dir=test_dir)
    except IncompatibleElements as e:
        if is_conditions_unsupported(fem_format, geom_repr, elem_order):
            logging.error(e)
            return None
        raise e

    if res.output is not None:
        with open(test_dir / name / "run.log", "w") as f:
            f.write(res.output.stdout)

    if pathlib.Path(res.results_file_path).exists() is False:
        raise FileNotFoundError(f'FEM analysis was not successful. Result file "{res.results_file_path}" not found.')

    return res
