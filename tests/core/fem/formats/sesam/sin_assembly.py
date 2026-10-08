"""Build a superelement assembly SIN at test time from the committed cantilever.

A SIN written for a superelement assembly holds the whole assembly: one RESULTS
entry per superelement that has data, each with its own PTAB, plus the
hierarchy records (HIERARCH, HSUPSTAT, HSUPTRAN) on the top level's entry.
adapy has no SIN writer and no such file is committed, so these helpers splice
one together from the single-superelement cantilever:

* the header area (NORSAM and ALLOCATE copied verbatim, then one RESULTS record
  per entry and IEND);
* entry 1, the top level: a PTAB and the three hierarchy blocks, no mesh;
* entries 2 and 3, the first-level superelements: the cantilever's type blocks
  copied whole and rebased to where they now sit (each block's
  ``ptr_table_word`` and every record pointer), an IDENT block naming the
  superelement type, and a fresh PTAB — the second one leaving out the blocks
  named in ``drop_blocks_in_second``.

Layout facts relied on are the reader's own (``sin_reader.py``): a header slot's
value lives in the high four bytes of an 8-byte slot; a record pointer is the
32-bit word index of the record's first data word, its NFIELD one word before;
a PTAB pointer is the 64-bit word index of a block's preamble plus one; a
RESULTS record's IPFILE is the PTAB's 64-bit word index plus one.
"""

from __future__ import annotations

import pathlib
import struct
from typing import Iterable, Sequence

_FIXTURE = pathlib.Path(__file__).resolve().parents[5] / "files/fem_files/cantilever/sesam/static/shell"
SIN_PATH = _FIXTURE / "STATIC_SHELL_CANTILEVER_SESAMR1.SIN"

PAGE = 8192
PREAMBLE = 0x803
NAME_LEN = 8
SLOT = 8

IDENTITY_4X4 = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


class SinBuffer:
    """A growing SIN image: type blocks, PTABs and the header area."""

    def __init__(self, size: int = 0) -> None:
        self.buf = bytearray(size)

    @property
    def end(self) -> int:
        return len(self.buf)

    def pad_to(self, multiple: int) -> int:
        """Zero-pad the image to a multiple of ``multiple`` bytes; return the end."""
        rem = len(self.buf) % multiple
        if rem:
            self.buf.extend(b"\x00" * (multiple - rem))
        return len(self.buf)

    def u32(self, at: int, value: int) -> None:
        struct.pack_into("<I", self.buf, at, value & 0xFFFFFFFF)

    def get_u32(self, at: int) -> int:
        return struct.unpack_from("<I", self.buf, at)[0]

    def f32(self, at: int, value: float) -> None:
        struct.pack_into("<f", self.buf, at, value)

    def _preamble(self, at: int, name: str) -> int:
        self.u32(at, PREAMBLE)
        self.buf[at + 4 : at + 4 + NAME_LEN] = name.encode("ascii").ljust(NAME_LEN)
        return at + 4 + NAME_LEN  # the payload (slot stream)

    def _slots(self, payload: int, values: Sequence[int]) -> None:
        for i, value in enumerate(values):
            self.u32(payload + i * SLOT + 4, value)

    def write_block(self, name: str, records: Iterable[Sequence[float]], *, type_flag: int = 1) -> int:
        """Append one 1-D type block holding ``records`` (data words only; each
        record's NFIELD is written before it). Returns the preamble offset.

        Header slots ``[0, NFIELD, type flag, ptr_table_word, n, n]``, then one
        pointer per record and a terminating slot whose pointer lies past the
        end of any file, so the table ends where it was built to end.
        """
        records = [tuple(r) for r in records]
        at = self.pad_to(SLOT)
        payload = at + 4 + NAME_LEN
        n = len(records)
        header = [0, max(len(r) + 1 for r in records), type_flag, 0, n, n]
        header[3] = (payload + len(header) * SLOT + 4) // SLOT
        table_from = payload + len(header) * SLOT
        records_from = table_from + (n + 1) * SLOT
        records_from += -records_from % SLOT  # NFIELD on an even word
        words: list[int] = []
        word = records_from // 4
        for rec in records:
            words.append(word)
            word += 1 + len(rec)
            word += word & 1
        self.buf.extend(b"\x00" * (word * 4 - at))
        self._preamble(at, name)
        self._slots(payload, header + [w + 1 for w in words] + [0xFFFFFFF0])
        for w, rec in zip(words, records):
            self.f32(w * 4, float(len(rec) + 1))
            for i, value in enumerate(rec):
                self.f32((w + 1 + i) * 4, float(value))
        return at

    def write_ptab(self, preambles: Sequence[int], *, allocated: int | None = None) -> int:
        """Append a PTAB listing NORSAM and then each block preamble; return its
        offset. Slot 4 holds the slots allocated after NORSAM (``allocated``,
        by default one per block); the ones past the blocks stay empty."""
        at = self.pad_to(SLOT)
        payload = at + 4 + NAME_LEN
        count = len(preambles) if allocated is None else allocated
        assert count >= len(preambles)
        header = [0, 5, 0, (payload + 6 * SLOT + 4) // SLOT, count, count]
        self.buf.extend(b"\x00" * (4 + NAME_LEN + (len(header) + 1 + count) * SLOT))
        self._preamble(at, "PTAB")
        self._slots(payload, header + [1] + [p // SLOT + 1 for p in preambles])
        return at

    def write_header(self, prefix: bytes, ptabs: Sequence[tuple[int, int]]) -> None:
        """Write the header area at byte 0: ``prefix`` (the NORSAM and ALLOCATE
        records), one RESULTS record per ``(iref, ptab offset)`` and IEND."""
        at = len(prefix)
        assert at % SLOT == 0 and at + 32 * (len(ptabs) + 1) <= PAGE
        self.buf[:at] = prefix
        for iref, ptab in ptabs:
            payload = self._preamble(at, "RESULTS")
            ipfile = ptab // SLOT + 1
            for i, value in enumerate((5, iref, ipfile & 0xFFFFFFFF, ipfile >> 32, 0)):
                self.u32(payload + 4 * i, value)
            at += 32
        payload = self._preamble(at, "IEND")
        self.u32(payload, 5)

    def write_bytes(self, out: pathlib.Path) -> pathlib.Path:
        out.write_bytes(bytes(self.buf))
        return out


def _copy_rebased(image: SinBuffer, source: bytes, blocks, first: int, last: int) -> int:
    """Append ``source[first:last]`` page-aligned and rebase every block in it;
    return the byte shift."""
    at = image.pad_to(PAGE)
    shift = at - first
    assert shift % SLOT == 0
    image.buf.extend(source[first:last])
    for block in blocks:
        # The anchor (slot 3) is an absolute 64-bit word index.
        anchor = block.preamble_offset + shift + 4 + NAME_LEN + 3 * SLOT + 4
        image.u32(anchor, image.get_u32(anchor) + shift // SLOT)
        # Record pointers are absolute 32-bit word indices.
        for i in range(int(block.pointer_table.size)):
            slot = block.pointer_table_offset + shift + i * SLOT + 4
            value = image.get_u32(slot)
            if value:
                image.u32(slot, value + shift // 4)
    return shift


def write_assembly_sin(
    out: pathlib.Path,
    source: pathlib.Path = SIN_PATH,
    *,
    seltyps: tuple[int, int] = (10, 11),
    top_seltyp: int = 100,
    drop_blocks_in_second: Iterable[str] = ("RVSTRESS",),
) -> pathlib.Path:
    """Write a three-entry superelement assembly SIN to ``out``.

    Entry 1 is the top level (type ``top_seltyp``, level 2) holding only
    HIERARCH, HSUPSTAT and HSUPTRAN. Entries 2 and 3 are copies of ``source``'s
    single superelement as types ``seltyps[0]`` and ``seltyps[1]``, instance 1,
    level 1; the second without the blocks in ``drop_blocks_in_second``.
    """
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    data = pathlib.Path(source).read_bytes()
    with open_sin(source) as sf:
        assert len(sf.super_element_refs) == 1, "the source must hold one superelement"
        blocks = list(sf.type_blocks.values())
        source_ptab = sf.super_element_refs[0][1]
        n_elements = sf.get_count("GELMNT1")
        prefix_end = next(off for off, name in sf.header_blocks if name == "RESULTS")
    first = min(b.preamble_offset for b in blocks)
    assert max(b.records_start for b in blocks) <= source_ptab, "the source's PTAB must follow its blocks"
    drop = set(drop_blocks_in_second)

    image = SinBuffer(PAGE)
    # Entry 1: the top level, hierarchy only.
    first_sub, second_sub = seltyps
    image.pad_to(PAGE)
    top_blocks = [
        image.write_block(
            "HIERARCH",
            [
                (1, top_seltyp, 1, 2, 0, 0, 2, 2, 3),
                (2, first_sub, 1, 1, 2, 1, 0),
                (3, second_sub, 1, 1, 3, 1, 0),
            ],
        ),
        image.write_block(
            "HSUPSTAT",
            [
                (top_seltyp, 0, 0, 0, 0, 0, 0, -1),
                (first_sub, 0, 0, 0, n_elements, 0, 0, -1),
                (second_sub, 0, 0, 0, n_elements, 0, 0, -1),
            ],
        ),
        image.write_block("HSUPTRAN", [(2, *IDENTITY_4X4), (3, *IDENTITY_4X4)]),
    ]
    ptabs = [(1, image.write_ptab(top_blocks))]

    # Entries 2 and 3: the source's superelement, rebased, typed by an IDENT.
    for iref, seltyp in ((2, first_sub), (3, second_sub)):
        shift = _copy_rebased(image, data, blocks, first, source_ptab)
        listed = [b.preamble_offset + shift for b in blocks if iref == 2 or b.name not in drop]
        # IDENT: SLEVEL, SELTYP, SELMOD, 0.
        listed.append(image.write_block("IDENT", [(1, seltyp, 3, 0)], type_flag=31))
        ptabs.append((iref, image.write_ptab(listed)))
    image.pad_to(SLOT)

    image.write_header(data[:prefix_end], ptabs)
    return image.write_bytes(pathlib.Path(out))


def write_header_only_sin(out: pathlib.Path, *, allocated: int = 8) -> pathlib.Path:
    """Two RESULTS entries whose PTABs allocate the same number of slots: the
    first lists only a HIERARCH block, the second a GNODE, a GELMNT1, an
    RVNODDIS and an IDENT block (one record each). The old default pick, by allocated slots
    with the first of a tie winning, opened the first."""
    image = SinBuffer(PAGE)
    image.pad_to(PAGE)
    hier = image.write_block("HIERARCH", [(1, 100, 1, 2, 0, 0, 1, 2), (2, 10, 1, 1, 0, 1, 0)])
    ptab1 = image.write_ptab([hier], allocated=allocated)
    mesh = [
        image.write_block("GNODE", [(1, 1, 6, 123456)]),
        image.write_block("GELMNT1", [(1, 1, 15, 0, 1, 1)]),
        image.write_block("RVNODDIS", [(1, 1, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)], type_flag=2),
        image.write_block("IDENT", [(1, 10, 3, 0)], type_flag=31),
    ]
    ptab2 = image.write_ptab(mesh, allocated=allocated)
    image.pad_to(SLOT)
    # A NORSAM record to open the header area (its fields are not read).
    norsam = SinBuffer(40)
    norsam._preamble(0, "NORSAM")
    image.write_header(bytes(norsam.buf), [(1, ptab1), (2, ptab2)])
    return image.write_bytes(pathlib.Path(out))
