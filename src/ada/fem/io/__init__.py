import logging

from . import abaqus, calculix, code_aster, sesam, usfos
from .utils import interpret_fem

fem_imports = dict(
    abaqus=abaqus.read_fem,
    sesam=sesam.read_fem,
    code_aster=code_aster.read_fem,
)

fem_exports = dict(
    abaqus=abaqus.to_fem,
    calculix=calculix.to_fem,
    code_aster=code_aster.to_fem,
    sesam=sesam.to_fem,
    usfos=usfos.to_fem,
)

fem_executables = dict(
    abaqus=abaqus.run_abaqus,
    calculix=calculix.run_calculix,
    code_aster=code_aster.run_code_aster,
)


def get_fem_converters(fem_file, fem_format, fem_converter):
    from .io_meshio import meshio_read_fem, meshio_to_fem

    if fem_format is None:
        fem_format = interpret_fem(fem_file)

    if fem_converter == "default":
        fem_importer = fem_imports.get(fem_format, None)
        fem_exporter = fem_exports.get(fem_format, None)
    elif fem_converter.lower() == "meshio":
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
