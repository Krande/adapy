"""Pure-Python SIN (Norsam binary) → SIF (text) converter.

Replaces the legacy ``sin2sif.py`` shell-out to Prepost.exe — the
new path uses :mod:`sin_reader` to walk the binary record-by-record
and emits SIF text matching DNV's published format (record keyword
followed by ``%E``-formatted floats, four per line for multi-line
statements).

End users almost never want a literal SIF file on disk; they want a
:class:`FEAResult`. The convenience function :func:`read_sin_native`
runs the whole pipeline in memory — SIN bytes → SIF lines →
:class:`SifReader` → :class:`FEAResult` — without ever touching the
filesystem for an intermediate.

The text-emit step is deliberate: adapy's :class:`SifReader` is the
authoritative parser for the SIF schema (knows every data-type's
field layout, super-headers, multi-line continuation, etc.). Going
through SIF text means we get all of that for free, and any future
SIF schema additions land automatically in the SIN path too.
"""

from __future__ import annotations

import pathlib
from io import StringIO
from typing import Iterator, TextIO

from .sin_reader import SinFile, open_sin


def _format_record_line(name: str, data: tuple[float, ...]) -> Iterator[str]:
    """Yield SIF text lines for one record.

    Layout: ``NAME  d0  d1  d2  d3`` on the first line, then up to
    four values per continuation line indented to column 11. SIF text
    does not include the NFIELD prefix word (that's a SIN-only
    storage convention); :class:`SifReader` expects raw data values
    after the record keyword, so we emit only what :func:`iter_records`
    returns (data fields stripped of the NFIELD prefix).
    """
    head_chunk = data[:4]
    yield f"{name:<8s}" + "".join(f"  {v:14.8E}" for v in head_chunk)
    for i in range(4, len(data), 4):
        chunk = data[i : i + 4]
        yield " " * 10 + "".join(f"  {v:14.8E}" for v in chunk)


def write_sif(sin: SinFile, out: TextIO) -> None:
    """Stream SIF text for every record in ``sin`` to ``out``.

    Record order: the file-header records (NORSAM / ALLOCATE / RESULTS
    / IEND) are *not* emitted (they're SIN-internal control directives,
    not SIF content). Type blocks are written in their on-disk order;
    within each block, records are written in pointer-table order.
    """
    # Emit the standard SIF file header so SifReader's bootstrap path
    # finds the IDENT / DATE banner it expects. The leading HIERARCH +
    # IEND block tells downstream consumers this is a top-level SIF
    # (no super-element nesting) — matches what Prepost emits.
    out.write(
        "HIERARCH  8.00000000E+00  1.00000000E+00  1.00000000E+00  1.00000000E+00\n"
        "          1.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "IEND                1.00            0.00            0.00            0.00\n"
        "IDENT     1.00000000E+00  1.00000000E+00  3.00000000E+00  0.00000000E+00\n"
    )
    for type_name, block in sin.type_blocks.items():
        # Some types (RVNODDIS / RVSTRESS etc.) carry a "super-header"
        # in the SIF text that maps the type-block layout (dims, etc.)
        # to a leading ``-N`` record. The on-disk SIN encodes the same
        # info in the type-block control fields, but downstream
        # SifReader expects to see it as text. Synthesise a minimal
        # one when the type has > 1 dim — single-dim types don't need
        # it (SifReader streams them directly from the data records).
        if block.ndim >= 2:
            super_vals = (
                -float(block.ndim),
                float(block.ndim),
            ) + tuple(float(d) for d in block.dims)
            out.write(f"{type_name:<8s} " + "".join(f" {v:14.8E}" for v in super_vals) + "\n")
        for record in sin.iter_records(type_name):
            for line in _format_record_line(type_name, record):
                out.write(line + "\n")


def convert_sin_to_sif_text(sin_path: str | pathlib.Path) -> str:
    """Return the full SIF text for ``sin_path``. In-memory; no disk write."""
    sin = open_sin(sin_path)
    buf = StringIO()
    write_sif(sin, buf)
    return buf.getvalue()


def convert_sin_to_sif_file(
    sin_path: str | pathlib.Path,
    sif_path: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Materialise a SIF file on disk from a SIN binary.

    Default output is ``<sin_path>.SIF`` (replacing the .SIN suffix).
    Used by callers that genuinely want a SIF artefact next to the
    SIN — most adapy consumers should prefer :func:`read_sin_native`
    which keeps the conversion in memory.
    """
    sin_path = pathlib.Path(sin_path)
    target = pathlib.Path(sif_path) if sif_path is not None else sin_path.with_suffix(".SIF")
    sin = open_sin(sin_path)
    with open(target, "w") as f:
        write_sif(sin, f)
    return target


__all__ = [
    "convert_sin_to_sif_text",
    "convert_sin_to_sif_file",
    "write_sif",
]
