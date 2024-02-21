from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Callable

from ada.base.types import BaseEnum
from ada.config import logger

from .utils import interpret_fem_format_from_path

if TYPE_CHECKING:
    from ada import Assembly


class FEATypes(BaseEnum):
    CODE_ASTER = "code_aster"
    CALCULIX = "calculix"
    ABAQUS = "abaqus"
    SESAM = "sesam"
    USFOS = "usfos"
    GMSH = "gmsh"

    # formats only
    XDMF = "xdmf"

    @staticmethod
    def get_solvers_only():
        non_solvers = [FEATypes.XDMF, FEATypes.GMSH]
        return [x for x in FEATypes if x not in non_solvers]


def get_fem_imports() -> dict[FEATypes, Callable[..., Assembly]]:
    from . import abaqus, code_aster, sesam

    return {
        FEATypes.ABAQUS: abaqus.read_fem,
        FEATypes.SESAM: sesam.read_fem,
        FEATypes.CODE_ASTER: code_aster.read_fem,
    }


def get_fem_exports() -> dict[FEATypes, Callable[..., Assembly]]:
    from ada.fem.formats.abaqus.config import AbaqusSetup
    from ada.fem.formats.calculix.config import CalculixSetup
    from ada.fem.formats.code_aster.config import CodeAsterSetup
    from ada.fem.formats.sesam.config import SesamSetup
    from ada.fem.formats.usfos.config import UsfosSetup

    return {
        FEATypes.ABAQUS: AbaqusSetup.default_pre_processor,
        FEATypes.CALCULIX: CalculixSetup.default_pre_processor,
        FEATypes.CODE_ASTER: CodeAsterSetup.default_pre_processor,
        FEATypes.SESAM: SesamSetup.default_pre_processor,
        FEATypes.USFOS: UsfosSetup.default_pre_processor,
    }


def get_fem_executable() -> dict[FEATypes, Callable[..., subprocess.CompletedProcess]]:
    from .abaqus.config import AbaqusSetup
    from .calculix.config import CalculixSetup
    from .code_aster.config import CodeAsterSetup
    from .sesam.config import SesamSetup

    return {
        FEATypes.ABAQUS: AbaqusSetup.default_executor,
        FEATypes.CALCULIX: CalculixSetup.default_executor,
        FEATypes.CODE_ASTER: CodeAsterSetup.default_executor,
        FEATypes.SESAM: SesamSetup.default_executor,
    }


fem_solver_map = {FEATypes.SESAM: "sestra", FEATypes.CALCULIX: "ccx", FEATypes.CODE_ASTER: "run_aster"}


class FemConverters(BaseEnum):
    DEFAULT = "default"
    MESHIO = "meshio"


def get_fem_converters(fem_file, fem_format: str | FEATypes, fem_converter: str | FemConverters):
    from ada.fem.formats.mesh_io import meshio_read_fem, meshio_to_fem

    if isinstance(fem_format, str):
        fem_format = FEATypes.from_str(fem_format)
    if isinstance(fem_converter, str):
        fem_converter = FemConverters.from_str(fem_converter)

    if fem_format is None:
        fem_format = interpret_fem_format_from_path(fem_file)

    if fem_converter == FemConverters.DEFAULT:
        fem_importer = get_fem_imports().get(fem_format, None)
        fem_exporter = get_fem_exports().get(fem_format, None)
    elif fem_converter == FemConverters.MESHIO:
        fem_importer = meshio_read_fem
        fem_exporter = meshio_to_fem
    else:
        raise ValueError(f'Unrecognized fem_converter "{fem_converter}". Only "meshio" and "default" are supported')

    return fem_importer, fem_exporter


def export_fem(assembly, name, analysis_dir, fem_format, fem_converter, metadata):
    _, fem_exporter = get_fem_converters("", fem_format, fem_converter)
    metadata = dict() if metadata is None else metadata
    metadata["fem_format"] = fem_format
    try:
        fem_exporter(assembly, name, analysis_dir, metadata)
        return True
    except IOError as e:
        logger.error(e)
        return False


def write_to_fem(
    assembly: Assembly,
    name: str,
    fem_format: FEATypes,
    overwrite: bool,
    fem_converter: str,
    scratch_dir,
    metadata: dict,
    make_zip_file,
    model_data_only=False,
):
    from ada.fem.formats.utils import default_fem_res_path, folder_prep, should_convert

    fem_res_files = default_fem_res_path(name, scratch_dir=scratch_dir)

    res_path = fem_res_files.get(fem_format, None)
    metadata = dict() if metadata is None else metadata
    metadata["fem_format"] = fem_format.value

    out = None
    if should_convert(res_path, overwrite):
        analysis_dir = folder_prep(scratch_dir, name, overwrite)
        _, fem_exporter = get_fem_converters("", fem_format, fem_converter)

        if fem_exporter is None:
            raise ValueError(f'FEM export for "{fem_format}" using "{fem_converter}" is currently not supported')

        fem_exporter(assembly, name, analysis_dir, metadata, model_data_only)

        if make_zip_file is True:
            import shutil

            shutil.make_archive(name, "zip", str(analysis_dir))
    else:
        print(f'Result file "{res_path}" already exists.\nUse "overwrite=True" if you wish to overwrite')

    if out is None and res_path is None:
        logger.info("No Result file is created")
        return None
