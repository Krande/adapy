from __future__ import annotations

import logging
from typing import Callable, Dict

from . import abaqus, calculix, code_aster, sesam, usfos
from .utils import interpret_fem


class FEATypes:
    CODE_ASTER = "code_aster"
    CALCULIX = "calculix"
    ABAQUS = "abaqus"
    SESAM = "sesam"
    USFOS = "usfos"

    all = [CODE_ASTER, CALCULIX, ABAQUS, SESAM, USFOS]


fem_imports = {
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

fem_executables: Dict[str, Callable] = {
    FEATypes.ABAQUS: abaqus.run_abaqus,
    FEATypes.CALCULIX: calculix.run_calculix,
    FEATypes.CODE_ASTER: code_aster.run_code_aster,
    FEATypes.SESAM: sesam.run_sesam,
}

fem_solver_map = {FEATypes.SESAM: "sestra", FEATypes.CALCULIX: "ccx"}


class FemConverters:
    DEFAULT = "default"
    MESHIO = "meshio"


def get_fem_converters(fem_file, fem_format, fem_converter):
    from ada.fem.formats.mesh_io import meshio_read_fem, meshio_to_fem

    if fem_format is None:
        fem_format = interpret_fem(fem_file)

    if fem_converter == FemConverters.DEFAULT:
        fem_importer = fem_imports.get(fem_format, None)
        fem_exporter = fem_exports.get(fem_format, None)
    elif fem_converter.lower() == FemConverters.MESHIO:
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
        logging.error(e)
        return False
