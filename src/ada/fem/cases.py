from __future__ import annotations

import logging
import os
import pathlib

import ada
from ada.base.types import GeomRepr
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.general import FEATypes as FEA
from ada.fem.formats.utils import default_fem_res_path
from ada.fem.meshing.concepts import GmshOptions
from ada.fem.results.common import FEAResult

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/eigen"

EL_TYPES = ada.fem.Elem.EL_TYPES


def is_conditions_unsupported(fem_format, geom_repr, elem_order, reduced_integration, use_hex_quad):
    fem_format = FEA.from_str(fem_format)
    if reduced_integration is True:
        if use_hex_quad is False:
            if geom_repr == GeomRepr.SHELL or geom_repr == GeomRepr.SOLID:
                return True
        if fem_format == FEA.CODE_ASTER:
            return True
    if fem_format == FEA.CALCULIX and geom_repr == GeomRepr.LINE:
        return True
    elif fem_format == FEA.CODE_ASTER and geom_repr == GeomRepr.LINE and elem_order == 2:
        return True
    elif fem_format == FEA.SESAM and geom_repr == GeomRepr.SOLID:
        return True
    else:
        return False


def eigen_test(
    beam_fixture,
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    short_name_map,
    reduced_integration,
    overwrite=True,
    execute=True,
    eigen_modes=11,
    name=None,
    debug=False,
    **kwargs,
) -> FEAResult | None:
    geom_repr = GeomRepr.from_str(geom_repr)

    if name is None:
        short_name = short_name_map.get(fem_format, fem_format)
        name = f"cantilever_EIG_{short_name}_{geom_repr.value}_o{elem_order}_hq{use_hex_quad}_ri{reduced_integration}"

    fem_format = FEA.from_str(fem_format)
    p = ada.Part("MyPart")
    a = ada.Assembly("MyAssembly") / [p / beam_fixture]

    if geom_repr == GeomRepr.LINE and use_hex_quad is True:
        return None
    if reduced_integration is True and fem_format in (FEA.CODE_ASTER, FEA.SESAM):
        return None
    if (
        fem_format == FEA.ABAQUS
        and geom_repr == GeomRepr.SHELL
        and elem_order == 1
        and reduced_integration is False
        and use_hex_quad is False
    ):
        print("Abaqus S3 and S3R are identical. Skipping S3 for now.")
        return None

    props = dict(use_hex=use_hex_quad) if geom_repr == GeomRepr.SOLID else dict(use_quads=use_hex_quad)
    if debug:
        props.update(**kwargs)
    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=eigen_modes))

    if kwargs.get("options") is None:
        props["options"] = GmshOptions(Mesh_ElementOrder=elem_order)

    if overwrite is False:
        if is_conditions_unsupported(fem_format, geom_repr, elem_order, reduced_integration, use_hex_quad):
            return None

        if "PYTEST_CURRENT_TEST" in os.environ:
            return None

        res_path = default_fem_res_path(name, scratch_dir=SCRATCH_DIR, fem_format=fem_format)
        if isinstance(res_path, pathlib.Path) and not res_path.exists():
            print(f"Result file {res_path} not found.")
            return None
        return ada.from_fem_res(res_path, fem_format=fem_format)
    else:
        p.fem = beam_fixture.to_fem_obj(0.07, geom_repr, **props)
        fix_set = p.fem.add_set(
            ada.fem.FemSet("bc_nodes", beam_fixture.bbox().sides.back(return_fem_nodes=True, fem=p.fem))
        )
        a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    for p in a.get_all_parts_in_assembly():
        if p.fem.is_empty():
            continue
        p.fem.options.ABAQUS.default_elements.use_reduced_integration = reduced_integration
        p.fem.options.CALCULIX.default_elements.use_reduced_integration = reduced_integration
        p.fem.options.CODE_ASTER.use_reduced_integration = reduced_integration

    try:
        res = a.to_fem(name, fem_format, overwrite=overwrite, execute=execute, scratch_dir=SCRATCH_DIR)
    except IncompatibleElements as e:
        logging.error(e)
        # if is_conditions_unsupported(fem_format, geom_repr, elem_order, reduced_integration, use_hex_quad):
        return None
        # raise e

    if res is None or pathlib.Path(res.results_file_path).exists() is False:
        raise FileNotFoundError(f'FEM analysis was not successful. Result file "{res}" not found.')

    if "PYTEST_CURRENT_TEST" in os.environ:
        return None

    return res
