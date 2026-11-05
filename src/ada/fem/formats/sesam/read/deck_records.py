"""The records of a Sesam input deck (``<name>T<n>.FEM``), as a SIN holds them.

A Results Interface File (SIN) carries the input deck it was solved from beside its result records
(:mod:`..results.export_fem`); this is the reverse of that export: the deck's text cards read back as
the records :meth:`~..results.sin_reader.SinFile.iter_records` and ``iter_text_records`` yield, for
:mod:`..results.sin_writer` to write.

Input Interface File layout: a card name in columns 1-8, then four 16-wide fields a line, a record
continuing on lines that start with blanks. A text card (``TD*``) is ``NFIELD ID CODNAM CODTXT`` and
then ``CODNAM // 100`` name lines and ``CODTXT // 100`` comment lines. What a SIN stores differs in two
ways, both from the solver-written files:

* a numeric record is padded with zeros to whole lines of four fields (``GELMNT1`` of a beam:
  ``ELNOX ELNO ELTYP ELTYAD N1 N2 0 0``), except the records whose first field is ``NFIELD``
  (``GSETMEMB``, ``SCON*``, ``UNITS``, ``HIERARCH``), which are stored as ``NFIELD - 1`` fields, the
  ``NFIELD`` word dropped;
* a text record keeps its name, not its comment lines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Records whose first field is NFIELD (see :mod:`..results.export_fem`).
_NFIELD_FIRST = frozenset({"GSETMEMB", "UNITS", "HIERARCH"})

#: Header and terminator cards a SIN has no type block for, or one of its own making.
_SKIP = frozenset({"IEND", "DATE", "TEXT"})


def _nfield_first(name: str) -> bool:
    return name in _NFIELD_FIRST or name.startswith("SCON")


def _is_text(name: str) -> bool:
    return name.startswith("TD")


@dataclass
class DeckRecords:
    """``numeric[name]``: the data words of each record, in deck order; ``text[name]``: ``(ident, name)``."""

    numeric: dict[str, list[list[float]]] = field(default_factory=dict)
    text: dict[str, list[tuple[float, str]]] = field(default_factory=dict)

    def get(self, name: str) -> list[list[float]]:
        return self.numeric.get(name, [])


def _fields(line: str) -> list[float]:
    body = line[8:]
    out = []
    for i in range(0, len(body), 16):
        chunk = body[i : i + 16].strip()
        if chunk:
            out.append(float(chunk))
    return out


def read_deck_records(path: str | os.PathLike) -> DeckRecords:
    """Every record of the deck at ``path`` but its header and terminator (:data:`_SKIP`)."""
    deck = DeckRecords()
    with open(path, "r", encoding="ascii", errors="replace") as f:
        lines = f.read().splitlines()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line[0] == " ":
            i += 1  # a continuation of a record we skip
            continue
        name = line[:8].strip()
        values = _fields(line)
        i += 1
        if name in ("DATE", "TEXT"):
            # ``TYPE SUBTYPE NRECS NBYTE`` and then NRECS lines of text: none of it a result reader reads.
            i += int(values[2]) if len(values) >= 3 else 0
            continue
        if _is_text(name):
            codnam, codtxt = (int(values[2]), int(values[3])) if len(values) >= 4 else (100, 0)
            n_lines = codnam // 100 + codtxt // 100
            text_lines = [ln.strip() for ln in lines[i : i + n_lines]]
            i += n_lines
            deck.text.setdefault(name, []).append((values[1], text_lines[0] if text_lines else ""))
            continue
        while i < n and lines[i][:1] == " " and lines[i].strip():
            values.extend(_fields(lines[i]))
            i += 1
        if name in _SKIP:
            continue
        if _nfield_first(name):
            values = values[1 : int(values[0])] if values else values
        else:
            values = values + [0.0] * (-len(values) % 4)
        deck.numeric.setdefault(name, []).append(values)
    return deck


__all__ = ["DeckRecords", "read_deck_records"]
