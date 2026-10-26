from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import FEAResult


def postprocess(res_path: str | pathlib.Path, fem_format: FEATypes = None, super_element=None) -> FEAResult:
    """Read a result file. ``super_element`` (Sesam SIN only): on a superelement assembly,
    one superelement or a list of them to read instead of the whole assembly."""
    from ada.fem.formats.abaqus.config import AbaqusSetup
    from ada.fem.formats.calculix.config import CalculixSetup
    from ada.fem.formats.code_aster.config import CodeAsterSetup
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.opencourant.config import OpenCourantSetup
    from ada.fem.formats.sesam.config import SesamSetup
    from ada.fem.formats.utils import interpret_fem_format_from_path

    if fem_format is None:
        fem_format = interpret_fem_format_from_path(res_path)

    if isinstance(fem_format, str):
        fem_format = FEATypes.from_str(fem_format)

    if super_element is not None and fem_format != FEATypes.SESAM:
        raise ValueError(f"super_element applies to Sesam results, not {fem_format}")

    if fem_format == FEATypes.SESAM:
        # Only passed when given, so a custom Sesam post-processor without it keeps working.
        if super_element is not None:
            return SesamSetup.default_post_processor(res_path, super_element=super_element)
        return SesamSetup.default_post_processor(res_path)
    elif fem_format == FEATypes.ABAQUS:
        return AbaqusSetup.default_post_processor(res_path)
    elif fem_format == FEATypes.CALCULIX:
        return CalculixSetup.default_post_processor(res_path)
    elif fem_format == FEATypes.CODE_ASTER:
        return CodeAsterSetup.default_post_processor(res_path)
    elif fem_format == FEATypes.OPENCOURANT:
        return OpenCourantSetup.default_post_processor(res_path)
    else:
        raise NotImplementedError(f"Postprocessing for {fem_format} is not implemented.")
