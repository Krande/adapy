"""Sesam input-deck (FEM T-file) export from a results file.

SESTRA echoes the whole input deck into the Results Interface File it writes:
geometry (GNODE/GCOORD/GELMNT1/GELREF1/GECCEN/GUNIVEC), sections and
thicknesses, materials, boundary conditions, loads, sets and their names all
sit beside the R* result records. Exporting the FEM file is therefore
EXTRACTION, not reconstruction: walk the SIN's record blocks, keep every
input record verbatim, drop the result records, and emit the fixed-format
text the Input Interface File ([89-7012]) defines. Nothing round-trips
through adapy's object model, so nothing a writer fails to model can be
silently lost.

Two record families, per the manual:

* text records (``TD*``, ``DATE``, ``TEXT``, ``TSLAYER``) carry
  ``NFIELD ID CODNAM CODTXT`` followed by the name on its own line —
  NFIELD is a LOGICAL field there;
* every other input record is plain numeric fields with no NFIELD (the
  NFIELD word in the SIN is a storage prefix, which the record iterator
  already strips).

Input records never start with "R"; the Results Interface File's own
additions all do (RD*/RV*/RB*/RSUM*). ``TDRESREF`` (result-case names) is
the one text record that belongs to the results side.

Comment lines attached to text records are not preserved: the reader
collapses a record's text words into one string, so CODNAM/CODTXT are
re-derived for the single name line actually emitted — a self-consistent
record beats one whose codes promise comment lines that are not there.
"""

from __future__ import annotations

import pathlib
from typing import TextIO

from .sin_reader import SinFile, open_sin
from .sin_to_sif import _format_record_line

#: Record-name prefixes the Results Interface File adds on top of the input
#: deck. Everything else in a SIN is the input deck itself.
_RESULT_PREFIXES = ("RB", "RD", "RS", "RV")

#: Result-side records that do not follow the R-prefix convention.
_RESULT_TYPES = {"TDRESREF"}

#: Emitted first, in this order, when present — the header banner an input
#: file leads with. Everything else keeps the SIN's own on-disk block order.
_HEADER_TYPES = ("DATE", "IDENT", "TEXT", "TDSUPNAM", "UNITS")

#: Records whose LOGICAL first field is NFIELD, per the manual — for most
#: records the SIN's NFIELD word is a storage prefix the iterator strips,
#: but these carry it as part of the record proper and readers (adapy's own
#: design-model reader included) parse the fields after it.
_NFIELD_FIRST = frozenset({"GSETMEMB", "UNITS", "HIERARCH"})


def _has_logical_nfield(name: str) -> bool:
    # The structure-concept family (SCONCEPT / SCONMESH / SCONPLIS / …) is
    # NFIELD-first throughout its section of the manual.
    return name in _NFIELD_FIRST or name.startswith("SCON")


def is_result_record(name: str) -> bool:
    return name in _RESULT_TYPES or any(name.startswith(p) for p in _RESULT_PREFIXES)


def _is_text_type(name: str) -> bool:
    return name == "DATE" or name == "TEXT" or name == "TSLAYER" or name.startswith("TD")


def _write_text_records(sin: SinFile, name: str, out: TextIO) -> None:
    for prefix, text in sin.iter_text_records(name):
        if not text or len(prefix) != 3:
            # No text payload decoded — emit the numeric fields as-is rather
            # than invent an empty name line the reader would mis-consume.
            row = (float(len(prefix) + 1), *prefix)
            for line in _format_record_line(name, row):
                out.write(line + "\n")
            continue
        if name == "DATE":
            # DATE: NFIELD TYPE SUBTYPE NRECS + NRECS text lines. The reader
            # hands back one collapsed string, so NRECS is forced to the one
            # line actually written.
            head = (4.0, float(prefix[0]), float(prefix[1]), 1.0)
        else:
            # TD*: NFIELD ID CODNAM CODTXT + name (+ comment lines). CODNAM
            # re-derived for the emitted name; comments are dropped, so
            # CODTXT says none.
            codnam = 100.0 + float(len(text))
            head = (4.0, float(prefix[0]), codnam, 0.0)
        for line in _format_record_line(name, head):
            out.write(line + "\n")
        out.write(f"        {text}\n")


def write_fem(sin: SinFile, out: TextIO) -> None:
    """Stream the input deck of ``sin`` as Input Interface File text."""

    names = [n for n in sin.type_blocks if not is_result_record(n)]
    ordered = [n for n in _HEADER_TYPES if n in names]
    ordered += [n for n in names if n not in _HEADER_TYPES]

    if "IDENT" not in sin.type_blocks:
        # An input file leads with IDENT; a SIN without one (some solvers'
        # output) still needs the banner for downstream readers. Same
        # top-level values sin_to_sif emits: superelement level 1, type 1,
        # 3D analysis.
        out.write("IDENT       1.00000000E+00  1.00000000E+00  3.00000000E+00  0.00000000E+00\n")

    for name in ordered:
        if _is_text_type(name):
            _write_text_records(sin, name, out)
            continue
        nfield_first = _has_logical_nfield(name)
        for record in sin.iter_records(name):
            if nfield_first:
                record = (float(len(record) + 1), *record)
            for line in _format_record_line(name, record):
                out.write(line + "\n")

    # The input file's terminator. Not a type block in the SIN (NORSAM has its
    # own control records), so it is emitted rather than copied.
    out.write("IEND        0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n")


def export_fem_from_sin(
    sin_path: str | pathlib.Path,
    fem_path: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Materialise the FEM (input-deck) file embedded in ``sin_path``.

    Default output sits next to the SIN as ``<stem>.FEM``.
    """

    sin_path = pathlib.Path(sin_path)
    out = pathlib.Path(fem_path) if fem_path is not None else sin_path.with_suffix(".FEM")
    sin = open_sin(sin_path)
    with open(out, "w", encoding="ascii", newline="\n") as f:
        write_fem(sin, f)
    return out


def export_fem_text(sin_path: str | pathlib.Path) -> str:
    """The FEM text for ``sin_path``, in memory."""

    from io import StringIO

    sin = open_sin(sin_path)
    buf = StringIO()
    write_fem(sin, buf)
    return buf.getvalue()


__all__ = ["export_fem_from_sin", "export_fem_text", "write_fem", "is_result_record"]
