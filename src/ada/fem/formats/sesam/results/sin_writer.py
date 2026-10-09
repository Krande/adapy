"""A minimal writer of SIN (Norsam binary) result files, for test decks.

It writes exactly the layout :mod:`.sin_reader` decodes (see ``SIN_FORMAT.md``):
a header area (``NORSAM``, ``RESULTS`` pointing at one super-element, ``IEND``),
that super-element's ``PTAB``, and one type block per record type -- a slotted
header, a 1-based pointer table and the float32 records (``NFIELD`` first, padded
to an even word count). Text records (``TD*``) carry three numeric words, a
length word and the packed ASCII, as the reader expects.

It is NOT a general Norsam writer: the header records other than those the
reader walks are omitted, every block is written with the dimensions the reader
needs to size its pointer table, and nothing is known about the type-flag enum
beyond the values seen on real files. Its purpose is to build synthetic decks --
a mesh with result cases, complex cases and load-case combinations -- that the
pure-Python reader, and so the whole bake, read like a solver-written file.

Records are given as they appear without ``NFIELD``, i.e. what
:meth:`~.sin_reader.SinFile.iter_records` yields back.
"""

from __future__ import annotations

import os
import pathlib
import struct
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from .sin_reader import PREAMBLE

_SLOT = 8
_HEADER_AREA = 4096

#: Type-flag values observed on solver-written files (see ``SIN_FORMAT.md``);
#: the reader stores but never consumes them.
_TYPE_FLAGS = {
    "GNODE": 31,
    "GELMNT1": 31,
    "GCOORD": 21,
    "GELREF1": 21,
    "GELTH": 21,
    "BNBCD": 21,
    "MISOSEL": 20,
    "RDSTRESS": 1,
    "RDIELCOR": 1,
    "RDRESREF": 1,
    "RDFORCES": 1,
    "RDNODREA": 1,
    "RDRESCMB": 1,
    "RDPOINTS": 2,
    "RVNODDIS": 2,
    "RVNODREA": 2,
    "RVSTRESS": 2,
    "RVFORCES": 2,
}


@dataclass
class SinDeck:
    """Records per type, in the order they are added.

    ``numeric[name]`` holds the data words of each record (no ``NFIELD``);
    ``text[name]`` holds ``(numeric prefix of 3 words, text)`` per record.
    """

    numeric: dict[str, list[Sequence[float]]] = field(default_factory=dict)
    text: dict[str, list[tuple[Sequence[float], str]]] = field(default_factory=dict)

    def add(self, name: str, words: Iterable[float]) -> None:
        self.numeric.setdefault(name, []).append([float(w) for w in words])

    def add_many(self, name: str, rows: Iterable[Iterable[float]]) -> None:
        bucket = self.numeric.setdefault(name, [])
        bucket.extend([float(w) for w in row] for row in rows)

    def add_text(self, name: str, ident: float, text: str) -> None:
        """A ``TD*`` record: id, ``CODNAM`` (100 + length, as solvers write it), ``CODTXT``."""
        self.text.setdefault(name, []).append(((float(ident), float(100 + len(text)), 0.0), text))


def _u32_slot(value: int) -> bytes:
    """One 8-byte header slot: zero pad, then the u32 value (the reader reads +4)."""
    return struct.pack("<II", 0, int(value) & 0xFFFFFFFF)


def _record_words(words: Sequence[float]) -> np.ndarray:
    nfield = len(words) + 1
    out = np.zeros(nfield + (nfield & 1), dtype=np.float32)
    out[0] = nfield
    out[1:nfield] = np.asarray(words, dtype=np.float64)
    return out


def _text_record_bytes(prefix: Sequence[float], text: str) -> bytes:
    encoded = text.encode("ascii")
    n_text_words = max(1, (len(encoded) + 3) // 4)
    nfield = 1 + 3 + 1 + n_text_words
    body = struct.pack("<f3f", float(nfield), *[float(x) for x in prefix])
    body += struct.pack("<I", len(encoded) << 8)
    body += encoded.ljust(n_text_words * 4, b" ")
    if nfield & 1:
        body += b"\x00" * 4
    return body


def _block_bytes(name: str, offset: int, records: list[bytes], nfield_hint: int) -> bytes:
    """One type block at byte ``offset`` (8-aligned): header, pointer table, records."""
    n = len(records)
    # Result tables as two dims (1, n): a single packed-looking dim on a type-2
    # table past 100000 records would trip the reader's stale-tail trim.
    dims = (1, n) if _TYPE_FLAGS.get(name) == 2 else (n,)
    payload = offset + 4 + 8
    pointer_table = payload + (4 + 2 * len(dims)) * _SLOT
    ptr_table_word = (pointer_table + 4) // _SLOT
    assert (pointer_table + 4) % _SLOT == 0
    head = struct.pack("<I", PREAMBLE) + name.ljust(8).encode("ascii")
    head += _u32_slot(0) + _u32_slot(nfield_hint) + _u32_slot(_TYPE_FLAGS.get(name, 21)) + _u32_slot(ptr_table_word)
    for d in dims:
        head += _u32_slot(d) + _u32_slot(d)
    records_start = pointer_table + (n + 1) * _SLOT
    pointers = [0]
    cursor = records_start
    for rec in records:
        # The pointer names the record's first DATA word; NFIELD sits one word before.
        pointers.append(cursor // 4 + 1)
        cursor += len(rec)
    table = b"".join(_u32_slot(p) for p in pointers)
    body = head + table + b"".join(records)
    pad = (-len(body)) % _SLOT
    return body + b"\x00" * pad


def write_sin(deck: SinDeck, path: os.PathLike) -> pathlib.Path:
    """Write ``deck`` as a SIN file at ``path`` and return the path."""
    path = pathlib.Path(path)
    blocks: list[tuple[str, list[bytes], int]] = []
    for name, rows in deck.numeric.items():
        if not rows:
            continue
        recs = [_record_words(r).tobytes() for r in rows]
        blocks.append((name, recs, min(len(r) for r in rows) + 1))
    for name, rows in deck.text.items():
        if not rows:
            continue
        blocks.append((name, [_text_record_bytes(p, t) for p, t in rows], 5))

    ptab_offset = _HEADER_AREA
    n_blocks = len(blocks)
    # PTAB: header slots 0..5, then 1 + n_blocks pointer slots (NORSAM first).
    ptab_size = 12 + (6 + 1 + n_blocks) * _SLOT
    offset = ptab_offset + ptab_size + ((-(ptab_offset + ptab_size)) % _SLOT)
    block_bytes: list[bytes] = []
    block_offsets: list[int] = []
    for name, recs, nfield_hint in blocks:
        data = _block_bytes(name, offset, recs, nfield_hint)
        block_offsets.append(offset)
        block_bytes.append(data)
        offset += len(data)

    ptab = struct.pack("<I", PREAMBLE) + b"PTAB    "
    ptab += _u32_slot(0) + _u32_slot(5) + _u32_slot(0) + _u32_slot((ptab_offset + 12 + 6 * _SLOT + 4) // _SLOT)
    ptab += _u32_slot(n_blocks) + _u32_slot(n_blocks)
    # Fortran 1-based 64-bit-word addresses: (ptr - 1) * 8 is the preamble offset.
    ptab += _u32_slot(1) + b"".join(_u32_slot(off // _SLOT + 1) for off in block_offsets)
    ptab += b"\x00" * ((-len(ptab)) % _SLOT)

    header = struct.pack("<I", PREAMBLE) + b"NORSAM  " + struct.pack("<4I", 7, 0, 1, 2)
    header += b"\x00" * (32 - len(header))
    results = struct.pack("<I", PREAMBLE) + b"RESULTS " + struct.pack("<5I", 5, 1, ptab_offset // _SLOT + 1, 0, 0)
    header += results + b"\x00" * (32 - len(results))
    header += struct.pack("<I", PREAMBLE) + b"IEND    " + struct.pack("<5I", 5, 0, 0, 0, 0)
    header += b"\x00" * (_HEADER_AREA - len(header))

    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(ptab)
        if block_offsets:
            assert _HEADER_AREA + len(ptab) == block_offsets[0]
        for data in block_bytes:
            fh.write(data)
    return path


__all__ = ["SinDeck", "write_sin"]
