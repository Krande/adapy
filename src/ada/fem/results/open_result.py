"""Open an FEA results file as a format-neutral result whose cases are read one at a time.

:func:`open_result` returns an :class:`~.common.FEAResult` with the mesh, the model data a structural check
reads beside the results (``Mesh.thickness``, ``element_frames``, ``result_points``, ``section_kinds``,
``concept_names``, ``restrained_nodes``, ``eccentricities``) and ``FEAResult.cases``
(:class:`~.cases.ResultCases`), which reads the stored cases one at a time as the role fields of
:class:`~.field_data.FieldRole` -- the same layout and convention from every format.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any, Callable

#: ``{suffix: opener}``; the built-in formats register themselves on first use.
_OPENERS: dict[str, Callable[..., Any]] = {}


def register_result_opener(suffix: str, opener: Callable[..., Any]) -> None:
    """Make :func:`open_result` open files ending in ``suffix`` (e.g. ``".rmed"``) with ``opener(path, **options)``."""
    _OPENERS[suffix.lower()] = opener


def _open_sin(path, **options):
    from ada.fem.formats.sesam.results.structural import open_sin_result

    return open_sin_result(path, **options)


def _open_rmed(path, **options):
    from ada.fem.formats.code_aster.results.structural import open_rmed_result

    return open_rmed_result(path, **options)


def _builtin() -> None:
    _OPENERS.setdefault(".sin", _open_sin)
    _OPENERS.setdefault(".rmed", _open_rmed)


def open_result(path: str | os.PathLike, **options: Any):
    """The results file ``path`` as an FEA result with its cases (see the module).

    ``options`` go to the format's opener: for a Sesam SIN ``super_element`` (which one of an assembly).
    """
    _builtin()
    path = pathlib.Path(path)
    opener = _OPENERS.get(path.suffix.lower())
    if opener is None:
        raise ValueError(f"no result opener for {path.suffix!r} files; known: {sorted(_OPENERS)}")
    return opener(path, **options)


__all__ = ["open_result", "register_result_opener"]
