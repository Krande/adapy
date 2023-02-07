from __future__ import annotations

import logging
import pathlib

import pytest

import ada
from ada.base.types import GeomRepr
from ada.config import Settings
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.general import FEATypes as FEA
from ada.fem.formats.utils import default_fem_res_path
from ada.fem.meshing.concepts import GmshOptions
from ada.fem.results.common import FEAResult

test_dir = Settings.scratch_dir / "ada_fem_test_eigen"
EL_TYPES = ada.fem.Elem.EL_TYPES


def is_conditions_unsupported(fem_format, geom_repr, elem_order):
    fem_format = FEA.from_str(fem_format)
    if fem_format == FEA.CALCULIX and geom_repr == GeomRepr.LINE:
        return True
    elif fem_format == FEA.CODE_ASTER and geom_repr == GeomRepr.LINE and elem_order == 2:
        return True
    elif fem_format == FEA.SESAM and geom_repr == GeomRepr.SOLID:
        return True
    else:
        return False


@pytest.mark.parametrize("use_hex_quad", [True, False])
@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
def test_fem_eig(
    beam_fixture,
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    short_name_map,
    overwrite=True,
    execute=True,
    eigen_modes=11,
    name=None,
) -> FEAResult | None:
    geom_repr = GeomRepr.from_str(geom_repr)

    if name is None:
        short_name = short_name_map.get(fem_format)
        name = f"cantilever_EIG_{short_name}_{geom_repr.value}_o{elem_order}_hq{use_hex_quad}"

    fem_format = FEA.from_str(fem_format)
    p = ada.Part("MyPart")
    a = ada.Assembly("MyAssembly") / [p / beam_fixture]

    if geom_repr == GeomRepr.LINE and use_hex_quad is True:
        return None

    props = dict(use_hex=use_hex_quad) if geom_repr == GeomRepr.SOLID else dict(use_quads=use_hex_quad)

    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=eigen_modes))

    if overwrite is False:
        if is_conditions_unsupported(fem_format, geom_repr, elem_order):
            return None
        res_path = default_fem_res_path(name, scratch_dir=test_dir, fem_format=fem_format)
        return ada.from_fem_res(res_path, fem_format=fem_format)
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

    if pathlib.Path(res.results_file_path).exists() is False:
        raise FileNotFoundError(f'FEM analysis was not successful. Result file "{res.results_file_path}" not found.')

    return res
