import logging
import os
import pathlib

import pytest

import ada
from ada.base.types import GeomRepr
from ada.config import Settings
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.general import FEATypes as FEA
from ada.fem.formats.utils import default_fem_res_path
from ada.fem.meshing.concepts import GmshOptions
from ada.fem.results import Results

test_dir = Settings.scratch_dir / "ada_fem_test_static"
EL_TYPES = ada.fem.Elem.EL_TYPES


def is_conditions_unsupported(fem_format, geom_repr, elem_order, nl_geom):
    if fem_format == FEA.CALCULIX:
        if geom_repr == GeomRepr.LINE:
            return True
    elif fem_format == FEA.CODE_ASTER:
        if geom_repr == GeomRepr.LINE:
            if nl_geom is True or elem_order == 2:
                return True
        if geom_repr == GeomRepr.SHELL and elem_order == 2 and nl_geom is True:
            return True
    return False


@pytest.mark.parametrize("use_hex_quad", [True, False])
@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])  # , "sesam", "abaqus"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
@pytest.mark.parametrize("nl_geom", [True, False])
def test_fem_static(
    beam_fixture,
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    nl_geom,
    short_name_map,
    overwrite=True,
    execute=True,
    name=None,
):
    geom_repr = GeomRepr.from_str(geom_repr)

    if name is None:
        short_fem_name = short_name_map.get(fem_format)
        name = f"cantilever_static_{short_fem_name}_{geom_repr.value}_o{elem_order}_hq{use_hex_quad}_nl{nl_geom}"

    fem_format = FEA.from_str(fem_format)
    p = ada.Part("MyPart")
    a = ada.Assembly("MyAssembly") / [p / beam_fixture]

    if geom_repr == GeomRepr.LINE and use_hex_quad is True:
        print("Skipping test as Line elements have no HEX/QUAD alternative")
        return None

    props = dict(use_hex=use_hex_quad) if geom_repr == GeomRepr.SOLID else dict(use_quads=use_hex_quad)

    step = a.fem.add_step(ada.fem.StepImplicit("gravity", nl_geom=nl_geom, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))

    if overwrite is False:
        if is_conditions_unsupported(fem_format, geom_repr, elem_order, nl_geom):
            return None

        if "PYTEST_CURRENT_TEST" in os.environ:
            return None

        res_path = default_fem_res_path(name, scratch_dir=test_dir, fem_format=fem_format)
        return Results(res_path, name, fem_format, a, import_mesh=False)
    else:
        p.fem = beam_fixture.to_fem_obj(0.05, geom_repr, options=GmshOptions(Mesh_ElementOrder=elem_order), **props)
        fix_set = p.fem.add_set(
            ada.fem.FemSet("bc_nodes", beam_fixture.bbox().sides.back(return_fem_nodes=True, fem=p.fem))
        )
        a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

    try:
        res = a.to_fem(
            name, fem_format, overwrite=overwrite, execute=execute, scratch_dir=test_dir, exit_on_complete=False
        )
    except IncompatibleElements as e:
        if is_conditions_unsupported(fem_format, geom_repr, elem_order, nl_geom):
            logging.error(e)
            return None
        raise e

    if pathlib.Path(res.results_file_path).exists() is False:
        raise FileNotFoundError(f'FEM analysis was not successful. Result file "{res.results_file_path}" not found.')

    if "PYTEST_CURRENT_TEST" in os.environ:
        return None

    return res
