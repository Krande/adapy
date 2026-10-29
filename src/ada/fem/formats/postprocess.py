from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import FEAResult


#: Set to 1 to keep every read's field values in a file cache (``field_cache=None``).
FIELD_CACHE_ENV = "ADA_FEM_RES_CACHE"


def postprocess(
    res_path: str | pathlib.Path,
    fem_format: FEATypes = None,
    super_element=None,
    field_cache: bool | str | pathlib.Path | None = None,
) -> FEAResult:
    """Read a result file. ``super_element`` (Sesam SIN only): on a superelement assembly,
    one superelement or a list of them to read instead of the whole assembly.

    ``field_cache``: keep the field values in a memory-mapped temporary file instead of in
    memory (:class:`~ada.fem.results.field_cache.FieldCache`); a directory puts the file
    there. None reads ``ADA_FEM_RES_CACHE``. A Sesam ``.sin`` is then read one result case at
    a time, so it never holds all its fields in memory; other formats are read as usual and
    their fields moved to the file afterwards.
    """
    import os

    if field_cache is None:
        field_cache = os.environ.get(FIELD_CACHE_ENV, "").strip().lower() in ("1", "true", "yes")
    if field_cache:
        return _read_through_cache(res_path, fem_format, super_element, field_cache)
    return _read(res_path, fem_format, super_element)


def _read_through_cache(res_path, fem_format, super_element, field_cache) -> FEAResult:
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.formats.utils import interpret_fem_format_from_path
    from ada.fem.results.field_cache import FieldCache

    fmt = fem_format or interpret_fem_format_from_path(res_path)
    if isinstance(fmt, str):
        fmt = FEATypes.from_str(fmt)
    if fmt == FEATypes.SESAM and str(res_path).lower().endswith(".sin"):
        return read_sin_file(res_path, super_element=super_element, field_cache=field_cache)
    result = _read(res_path, fem_format, super_element)
    with FieldCache(None if field_cache is True else field_cache) as cache:
        cache.spill(result.results)
    return result


def _read(res_path, fem_format, super_element) -> FEAResult:
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
