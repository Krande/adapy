"""Modal participation factors and effective mass from a Sestra eigenvalue run.

Sestra writes one ``RDMLFACT`` record per mode to the results file (SIN and SIF alike):
``NFIELD, IRES, NDOF, F1 .. F6`` -- the mode's modal load factor for a unit rigid-body motion in
each global degree of freedom. Sestra normalises eigenvectors to unit generalised mass, so the
factor is the participation factor and its square the effective modal mass.

Only the translational terms are used. The rotational factors do not reduce to an effective mass
about a stated point: on the cantilever fixtures a bending mode's Z-rotation factor is ~1e-3 where
Abaqus reports hundreds of kg·m² about the origin. Code_Aster's reader reports translation only
as well, so the two leave the same columns empty.
"""

from __future__ import annotations

import pathlib
import re
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from ada.fem.results.eigenvalue import EigenDataSummary

_RECORD = "RDMLFACT"
_NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+)?")


def _factors_from_records(records: Iterable[Iterable[float]]) -> dict[int, tuple[float, ...]]:
    """``[IRES, NDOF, F1 .. F_NDOF]`` records (NFIELD stripped) → ``{mode: (F1 .. F6)}``."""
    out: dict[int, tuple[float, ...]] = {}
    for rec in records:
        rec = [float(x) for x in rec]
        if len(rec) < 2:
            continue
        ndof = int(round(rec[1]))
        values = rec[2 : 2 + ndof]
        if ndof < 3 or len(values) < ndof:
            continue
        out[int(round(rec[0]))] = tuple(values)
    return out


def _sif_records(sif_file: pathlib.Path) -> list[list[float]]:
    """The ``RDMLFACT`` records of a SIF text file, NFIELD stripped.

    SIF is fixed-format: a record's name in the first 8 columns, its data after, and continuation
    lines with the name columns blank.
    """
    records: list[list[float]] = []
    current: list[float] | None = None
    with open(sif_file, "r", errors="replace") as f:
        for line in f:
            head = line[:8].strip()
            if head:
                if current is not None:
                    records.append(current[1:])
                current = [float(x.replace("D", "E")) for x in _NUMBER.findall(line[8:])] if head == _RECORD else None
            elif current is not None:
                current.extend(float(x.replace("D", "E")) for x in _NUMBER.findall(line))
    if current is not None:
        records.append(current[1:])
    return records


def read_modal_load_factors(results_file: str | pathlib.Path, sin=None) -> dict[int, tuple[float, ...]]:
    """``{mode: (F_x, F_y, F_z, F_rx, F_ry, F_rz)}`` from a Sestra SIN or SIF results file.

    ``sin``: an already open :class:`SinFile` to read from instead of opening ``results_file``.
    Empty when the file has no ``RDMLFACT`` records (a static run, or a deck that did not ask).
    """
    if sin is not None:
        if _RECORD not in sin.type_blocks:
            return {}
        return _factors_from_records(sin.iter_records(_RECORD))

    path = pathlib.Path(results_file)
    if path.suffix.lower() == ".sif":
        return _factors_from_records(_sif_records(path))

    from ada.fem.formats.sesam.results.sin_reader import open_sin

    opened = open_sin(path)
    try:
        return read_modal_load_factors(path, sin=opened)
    finally:
        opened.close()


def add_modal_mass(summary: EigenDataSummary, factors: dict[int, tuple[float, ...]]) -> EigenDataSummary:
    """Set the translational participation factors and effective masses of ``summary``'s modes."""
    for mode in summary.modes:
        f = factors.get(int(mode.no))
        if f is None:
            continue
        mode.px, mode.py, mode.pz = f[0], f[1], f[2]
        mode.efx, mode.efy, mode.efz = f[0] ** 2, f[1] ** 2, f[2] ** 2
    return summary
