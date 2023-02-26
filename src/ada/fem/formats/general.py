from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Callable

from ada.base.types import BaseEnum
from ada.config import get_logger

from . import abaqus, calculix, code_aster, sesam, usfos
from .utils import interpret_fem_format_from_path

if TYPE_CHECKING:
    from ada import Assembly

logger = get_logger()


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


fem_imports: dict[FEATypes, Callable[..., Assembly]] = {
    FEATypes.ABAQUS: abaqus.read_fem,
    FEATypes.SESAM: sesam.read_fem,
    FEATypes.CODE_ASTER: code_aster.read_fem,
}

fem_exports = {
    FEATypes.ABAQUS: abaqus.to_fem,
    FEATypes.CALCULIX: calculix.to_fem,
    FEATypes.CODE_ASTER: code_aster.to_fem,
    FEATypes.SESAM: sesam.to_fem,
    FEATypes.USFOS: usfos.to_fem,
}

fem_executables: dict[FEATypes, Callable[..., subprocess.CompletedProcess]] = {
    FEATypes.ABAQUS: abaqus.run_abaqus,
    FEATypes.CALCULIX: calculix.run_calculix,
    FEATypes.CODE_ASTER: code_aster.run_code_aster,
    FEATypes.SESAM: sesam.run_sesam,
}

fem_solver_map = {FEATypes.SESAM: "sestra", FEATypes.CALCULIX: "ccx"}


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
        fem_importer = fem_imports.get(fem_format, None)
        fem_exporter = fem_exports.get(fem_format, None)
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

        fem_exporter(assembly, name, analysis_dir, metadata)

        if make_zip_file is True:
            import shutil

            shutil.make_archive(name, "zip", str(analysis_dir))
    else:
        print(f'Result file "{res_path}" already exists.\nUse "overwrite=True" if you wish to overwrite')

    if out is None and res_path is None:
        logger.info("No Result file is created")
        return None
