from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import FEAResult


def postprocess(res_path: str | pathlib.Path, fem_format: FEATypes = None) -> FEAResult:
    from ada.fem.formats.abaqus.results.read_odb import read_odb_pckle_file
    from ada.fem.formats.calculix.results.read_frd_file import read_from_frd_file_proto
    from ada.fem.formats.code_aster.results.read_rmed_results import read_rmed_file
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.sesam.results.read_sif import read_sif_file
    from ada.fem.formats.utils import interpret_fem_format_from_path

    if fem_format is None:
        fem_format = interpret_fem_format_from_path(res_path)

    if isinstance(fem_format, str):
        fem_format = FEATypes.from_str(fem_format)

    if fem_format == FEATypes.SESAM:
        return read_sif_file(res_path)
    elif fem_format == FEATypes.ABAQUS:
        return read_odb_pckle_file(res_path)
    elif fem_format == FEATypes.CALCULIX:
        return read_from_frd_file_proto(res_path)
    elif fem_format == FEATypes.CODE_ASTER:
        return read_rmed_file(res_path)
    else:
        raise NotImplementedError()
