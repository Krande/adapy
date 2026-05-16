"""Pure-Python reader for Sesam SIN (Norsam) binary result files.

Walks the file's per-type blocks and exposes:

* :func:`iter_named_blocks` — every preamble-marked block (file header
  records + per-type blocks), as ``(offset, name)`` tuples.
* :class:`TypeBlock` — decoded header for one data type
  (``name``, ``nfield``, ``ndim``, ``dims``, ``count``,
  ``pointer_table``, ``records_start``).
* :func:`open_sin` — entry point; returns a :class:`SinFile` with
  ``types`` (list of TypeBlock keyed by name) and ``iter_records``
  for record-by-record float access.

The binary format spec was reverse-engineered against ``dnv-sifio``
output; see ``SIN_FORMAT.md`` in this directory.

stdlib-only: only ``struct`` + ``pathlib`` are needed. Holds the whole
file in memory (typical Sesam result files are <100 MB and the layout
is random-access by design).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

PREAMBLE = 0x803
NAME_LEN = 8
SLOT_STRIDE = 8  # bytes between consecutive header values
SLOT_VALUE_OFFSET = 4  # high 4 bytes of each 8-byte slot hold the value

# Names of the four file-header records (in order) that open every
# SIN file. They aren't "data" types — pure control directives.
_HEADER_NAMES = ("NORSAM", "ALLOCATE", "RESULTS", "IEND")


def _read_u32_slot(data: bytes, off: int) -> int:
    """Return the u32 value stored in the high 4 bytes of an 8-byte slot."""
    if off + SLOT_STRIDE > len(data):
        return 0
    return struct.unpack_from("<I", data, off + SLOT_VALUE_OFFSET)[0]


def iter_named_blocks(data: bytes) -> Iterator[tuple[int, str]]:
    """Yield ``(preamble_offset, name)`` for every block in ``data``.

    A block is a 4-byte preamble equal to :data:`PREAMBLE` followed
    by an 8-byte ASCII name (space-padded). Catches the four file-
    header records (NORSAM / ALLOCATE / RESULTS / IEND) plus every
    per-type data block.
    """
    needle = struct.pack("<I", PREAMBLE)
    i = 0
    while True:
        i = data.find(needle, i)
        if i < 0:
            return
        if i + 4 + NAME_LEN <= len(data):
            raw = data[i + 4 : i + 4 + NAME_LEN]
            # ASCII-printable name → real block; rules out the same
            # 0x803 byte sequence appearing inside numeric record data.
            if all(32 <= b < 127 for b in raw):
                yield i, raw.decode("ascii").rstrip()
        i += 1


@dataclass
class TypeBlock:
    """Decoded header for one data-type block.

    ``records_start`` is the byte offset of the first record's first
    field (NFIELD). ``pointer_table`` lists the word offset of each
    record — multiply by 4 for byte offset. Records with a zero
    pointer represent unused capacity slots (the SIF spec allows
    pre-allocated tables with ``populated < capacity``).

    ``type_flag`` (slot[2], "+0x20") is a Norsam type-class enum: 31
    for "all-int" tables (GNODE, GELMNT1), 21 for mixed-int/float
    tables with header id (GCOORD, GELREF1, GELTH, BNBCD), 20 for
    material/section scalars (MISOSEL), 2 for 2-D result-vector
    tables (RVNODDIS, RVSTRESS, RDPOINTS), 1 for result-definition
    records (RDSTRESS, RDIELCOR, RDRESREF), 41 for text-tagged
    records (TDMATER, TDRESREF), 0 for the PTAB pointer table itself.
    Stored for diagnostics; the reader doesn't consume it.

    ``ptr_table_word`` (slot[3], "+0x28") is a redundant cross-check
    that holds the 8-byte-word offset of slot[6] (== the first
    pointer slot's value field). The decoder uses this to derive
    ``ndim`` deterministically instead of relying on a cap-vs-pop
    heuristic.
    """

    preamble_offset: int
    name: str
    nfield: int
    type_flag: int
    ptr_table_word: int
    ndim: int
    dims: tuple[int, ...]  # populated count per dimension
    capacity: tuple[int, ...]  # allocated capacity per dimension
    pointer_table_offset: int
    pointer_table: list[int]
    records_start: int

    @property
    def count(self) -> int:
        """Number of populated (non-zero pointer) records."""
        return sum(1 for p in self.pointer_table if p != 0)

    @property
    def record_words(self) -> int:
        """Per-record stride in 32-bit words (NFIELD + even-word pad)."""
        return self.nfield + (self.nfield & 1)


def _decode_type_block(
    data: bytes, preamble_off: int, next_preamble: int | None
) -> TypeBlock:
    """Decode one per-type block. ``next_preamble`` clamps the pointer
    table walk so a malformed header can't read past the next block."""
    name = data[preamble_off + 4 : preamble_off + 4 + NAME_LEN].decode("ascii").rstrip()
    # Payload (slot stream) starts right after the 8-byte name.
    payload = preamble_off + 4 + NAME_LEN

    # Header slot layout:
    #   slot[0]: zero pad (always 0)
    #   slot[1]: NFIELD — base record width in 32-bit words
    #   slot[2]: type-flag enum (see TypeBlock.type_flag)
    #   slot[3]: ptr_table_word — 8-byte-word offset of slot[6]'s
    #            value field == first pointer entry. Lets us derive
    #            NDIM deterministically:
    #              NDIM = ((ptr_table_word*8 - 4 - payload) / 8 - 4) / 2
    #   slot[4..4+2*NDIM-1]: (capacity, populated) pairs
    #   slot[4+2*NDIM..]:    pointer table (NDIM-flattened)
    nfield = _read_u32_slot(data, payload + 1 * SLOT_STRIDE)
    type_flag = _read_u32_slot(data, payload + 2 * SLOT_STRIDE)
    ptr_table_word = _read_u32_slot(data, payload + 3 * SLOT_STRIDE)

    # Derive NDIM from ptr_table_word — slot[6+2*NDIM-2]'s value field
    # sits at ptr_table_word*8 bytes; that anchors where dims stop.
    ptr_value_off = ptr_table_word * SLOT_STRIDE
    pointer_table_offset = ptr_value_off - SLOT_VALUE_OFFSET
    # ((pointer_table_offset - payload) - 4*SLOT_STRIDE) / SLOT_STRIDE
    # = number of dim slots = 2 * NDIM
    dim_bytes = pointer_table_offset - payload - 4 * SLOT_STRIDE
    if ptr_table_word > 0 and dim_bytes >= 0 and dim_bytes % (2 * SLOT_STRIDE) == 0:
        ndim = dim_bytes // (2 * SLOT_STRIDE)
        if ndim > 4:
            # Defensive — well-formed SIF types have ndim ≤ 2.
            ndim = 0
    else:
        # ptr_table_word missing or implausible — fall back to the
        # cap/pop heuristic so malformed/older SIN files still parse.
        ndim = 0

    if ndim == 0:
        # Fallback path: walk consecutive (cap, pop) equal-u32 pairs
        # until we hit the first pointer (which varies record-to-
        # record). cap == pop is the dim invariant even when
        # capacity > population (BNBCD: cap=200, pop=200, count=13).
        dim_slot = 4
        while True:
            cap = _read_u32_slot(data, payload + dim_slot * SLOT_STRIDE)
            pop = _read_u32_slot(data, payload + (dim_slot + 1) * SLOT_STRIDE)
            if cap == 0 or cap != pop:
                break
            dim_slot += 2
            if (dim_slot - 4) // 2 >= 4:
                break
        ndim = (dim_slot - 4) // 2
        pointer_table_offset = payload + dim_slot * SLOT_STRIDE

    caps: list[int] = []
    dims: list[int] = []
    for d in range(ndim):
        caps.append(_read_u32_slot(data, payload + (4 + 2 * d) * SLOT_STRIDE))
        dims.append(_read_u32_slot(data, payload + (4 + 2 * d + 1) * SLOT_STRIDE))

    total_records = 1
    for d in dims:
        total_records *= d

    pointer_table: list[int] = []
    for i in range(total_records):
        po = pointer_table_offset + i * SLOT_STRIDE
        if next_preamble is not None and po + SLOT_STRIDE > next_preamble:
            break
        pointer_table.append(_read_u32_slot(data, po))

    records_start = pointer_table_offset + total_records * SLOT_STRIDE

    return TypeBlock(
        preamble_offset=preamble_off,
        name=name,
        nfield=nfield,
        type_flag=type_flag,
        ptr_table_word=ptr_table_word,
        ndim=ndim,
        dims=tuple(dims),
        capacity=tuple(caps),
        pointer_table_offset=pointer_table_offset,
        pointer_table=pointer_table,
        records_start=records_start,
    )


@dataclass
class SinFile:
    """Top-level handle for an opened ``.sin`` file.

    The file is read fully into ``_data`` (typical SIN files are well
    under 100 MB and random access by design). Use :meth:`types` to
    list every data type present and :meth:`iter_records` to walk a
    type's records as flat float32 tuples (one per record, length
    ``nfield``).
    """

    path: Path
    _data: bytes = field(repr=False)
    header_blocks: list[tuple[int, str]] = field(default_factory=list)
    type_blocks: dict[str, TypeBlock] = field(default_factory=dict)

    def __post_init__(self) -> None:
        blocks = list(iter_named_blocks(self._data))
        for off, name in blocks:
            if name in _HEADER_NAMES:
                self.header_blocks.append((off, name))
        type_offsets = [(o, n) for o, n in blocks if n not in _HEADER_NAMES]
        for idx, (off, name) in enumerate(type_offsets):
            next_off = (
                type_offsets[idx + 1][0]
                if idx + 1 < len(type_offsets)
                else len(self._data)
            )
            try:
                block = _decode_type_block(self._data, off, next_off)
            except Exception:
                continue
            # Drop false-positive matches: real type blocks always have
            # a sensible NFIELD (≥ 2) and at least one dim.
            if block.nfield < 2 or not block.dims:
                continue
            # Last writer wins on duplicate names — shouldn't happen in
            # well-formed SIN files but guard against junk matches.
            self.type_blocks[block.name] = block

    @property
    def types(self) -> list[str]:
        return list(self.type_blocks.keys())

    def get_count(self, name: str) -> int:
        block = self.type_blocks.get(name)
        return block.count if block is not None else 0

    def iter_records(self, name: str) -> Iterator[tuple[float, ...]]:
        """Yield one tuple of float32 values per populated record (the
        SIF "data" fields).

        Each on-disk record is ``[NFIELD, …data…, opt pad]``. The
        per-record pointer points to the first data field; the NFIELD
        prefix sits one word earlier. Some data types have a *fixed*
        NFIELD (GNODE = 5, GCOORD = 5) and others vary per record
        (GELMNT1 carries 4/8/20 node-ids per element depending on
        element type, all under the same type-block) — so we read
        NFIELD per record rather than trusting the type-block header.
        Records with a zero pointer are skipped silently.
        """
        block = self.type_blocks.get(name)
        if block is None:
            return
        for word_ptr in block.pointer_table:
            if word_ptr == 0:
                continue
            nfield_byte = (word_ptr - 1) * 4
            if nfield_byte < 0 or nfield_byte + 4 > len(self._data):
                continue
            nfield = int(struct.unpack_from("<f", self._data, nfield_byte)[0])
            n_data = nfield - 1
            if n_data <= 0:
                continue
            data_byte = word_ptr * 4
            if data_byte + n_data * 4 > len(self._data):
                continue
            yield struct.unpack_from(f"<{n_data}f", self._data, data_byte)

    def iter_text_records(self, name: str) -> Iterator[tuple[tuple[float, ...], str]]:
        """Yield ``(numeric_prefix, text)`` per record for text-typed
        data (``TDMATER``, ``TDRESREF``, ``TDSECT``, …).

        SIN encodes a TD* record as ``[NFIELD, ID, NCHAR_LEN, ?, len_word,
        char_word, char_word, …]``: a few numeric fields followed by a
        length-prefix word and ``ceil(nchars/4)`` words of packed ASCII.
        We split that into the numeric prefix (everything up to the
        length word) and the text payload (decoded ASCII, trailing
        spaces / nulls stripped — matches what SIF's text continuation
        line produces).
        """
        block = self.type_blocks.get(name)
        if block is None:
            return
        for word_ptr in block.pointer_table:
            if word_ptr == 0:
                continue
            nfield_byte = (word_ptr - 1) * 4
            if nfield_byte < 0 or nfield_byte + 4 > len(self._data):
                continue
            nfield = int(struct.unpack_from("<f", self._data, nfield_byte)[0])
            n_total = nfield - 1
            if n_total <= 0:
                continue
            data_byte = word_ptr * 4
            if data_byte + n_total * 4 > len(self._data):
                continue
            # Layout: 3 numeric prefix words (ID, NCHAR_LEN, flag),
            # 1 length-prefix word, then text words. Cantilever
            # samples confirm this for TDMATER (NFIELD=6: 3 num + 1
            # len + 1 text) and TDRESREF (NFIELD=7: 3 num + 1 len +
            # 2 text). The "length" word's low byte is the flag,
            # next 3 bytes are the char count.
            n_numeric_prefix = 3
            n_text_words = n_total - n_numeric_prefix - 1
            if n_text_words <= 0:
                # Defensive — fall through with empty text on
                # malformed/short records rather than blow up.
                yield (
                    struct.unpack_from(f"<{n_total}f", self._data, data_byte),
                    "",
                )
                continue
            numeric_prefix = struct.unpack_from(
                f"<{n_numeric_prefix}f", self._data, data_byte,
            )
            len_word = struct.unpack_from(
                "<I", self._data, data_byte + n_numeric_prefix * 4,
            )[0]
            n_chars = (len_word >> 8) & 0xFFFFFF
            text_bytes = self._data[
                data_byte + (n_numeric_prefix + 1) * 4
                : data_byte + (n_numeric_prefix + 1) * 4 + n_text_words * 4
            ]
            if n_chars and n_chars <= len(text_bytes):
                text = text_bytes[:n_chars].decode("ascii", errors="replace").rstrip()
            else:
                text = text_bytes.decode("ascii", errors="replace").rstrip("\x00 ")
            yield (numeric_prefix, text)


def open_sin(path: str | Path) -> SinFile:
    """Open a ``.sin`` file and decode its block index."""
    p = Path(path)
    return SinFile(path=p, _data=p.read_bytes())


__all__ = [
    "PREAMBLE",
    "NAME_LEN",
    "TypeBlock",
    "SinFile",
    "iter_named_blocks",
    "open_sin",
]
