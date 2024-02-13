from __future__ import annotations

import pathlib

from ada.fem.formats.abaqus.results.read_odb import (
    convert_to_pckle,
    read_odb_pckle_file,
)
from ada.fem.formats.sesam.results.read_sif import read_sin_file
from ada.fem.formats.sesam.results.sin2sif import convert_sin_to_sif
from ada.fem.results.common import FEAResult


def from_results_file(fem_res: str | pathlib.Path, fem_format: str = None, force_conversion=False) -> FEAResult:
    file_ref = pathlib.Path(fem_res)
    suffix = file_ref.suffix.lower()

    if suffix == ".sin":
        sif_file = file_ref.with_suffix(".sif")
        if sif_file.exists() and force_conversion is False:
            return read_sin_file(sif_file)

        convert_sin_to_sif(file_ref)
        return read_sin_file(sif_file)
    elif suffix == ".sif":
        return read_sin_file(file_ref.with_suffix(".sif"))
    elif suffix == ".odb":
        pckl_data = file_ref.with_suffix(".pckle")
        if pckl_data.exists() is False:
            convert_to_pckle(file_ref, pckl_data)
        return read_odb_pckle_file(pckl_data)
    else:
        raise NotImplementedError()
