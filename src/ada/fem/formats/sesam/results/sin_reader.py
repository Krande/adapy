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

import mmap
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Iterator

PREAMBLE = 0x803
NAME_LEN = 8
SLOT_STRIDE = 8  # bytes between consecutive header values
SLOT_VALUE_OFFSET = 4  # high 4 bytes of each 8-byte slot hold the value
# Hard cap on records per type-block. SIF schema reality: the largest
# real Sesam result tables we've seen are <10 M records (large eigen
# RVFORCES blocks land near ~800 K). A block whose decoded
# ``prod(dims)`` exceeds this is rejected as garbage rather than
# allocating billions of Python ints into the pointer table — that
# path caused 15 GiB heap allocations on a 5 GB SIN and froze the
# host machine.
_MAX_RECORDS_PER_BLOCK = 50_000_000
# Hard cap on individual dim values for the same reason. A single dim
# > 10^8 is almost certainly a junk u32 read.
_MAX_DIM_VALUE = 100_000_000
# Upper bound on a single record's byte size. Real SIF records are
# NFIELD words wide; even outliers like GELMNT1 with 20 node-ids cap
# out at < 200 bytes. 4 KiB gives generous padding for variable-NFIELD
# rows + Fortran direct-access alignment when we jump past a block's
# records section during _discover_blocks.
_MAX_RECORD_BYTES = 4096


_SCAN_WINDOW = 64 * 1024 * 1024  # 64 MiB per find() chunk


def _validate_first_record(data: Any, block: "TypeBlock") -> bool:
    """Read NFIELD of the first non-zero pointer and check it's sane.

    Real Sesam records start with a NFIELD count stored as a float;
    valid values are in [2, 64] (the per-type-block NFIELD is the
    minimum, individual records can be larger e.g. GELMNT1 with 20-
    node bricks → NFIELD=24). A false-positive preamble inside
    record data tends to point to garbage NFIELDs (huge negative
    values, NaN, ints reinterpreted as floats giving 1e38 etc.) —
    catching these here keeps phantom blocks from shadowing real
    ones in :attr:`SinFile.type_blocks`.
    """
    file_end = len(data)
    for word_ptr in block.pointer_table:
        if word_ptr <= 0:
            continue
        nfield_byte = (word_ptr - 1) * 4
        if nfield_byte < 0 or nfield_byte + 4 > file_end:
            return False
        try:
            nfield_f = struct.unpack_from("<f", data, nfield_byte)[0]
        except struct.error:
            return False
        # NaN/inf compare False. Cast via int() to surface huge floats
        # as out-of-range too. Accept NFIELD >= 1: some Sesam result
        # types (RDPOINTS on cantilever) write a leading "marker"
        # record with NFIELD=1.0 — real data, just a different shape
        # than the type-block's schema NFIELD. Real-record NFIELDs
        # observed up to ~64 (20-node solid elements via GELMNT1);
        # 256 is a generous upper bound for future formats.
        if not (nfield_f == nfield_f):  # NaN check
            return False
        if not (1.0 <= nfield_f <= 256.0):
            return False
        try:
            nfield_i = int(nfield_f)
        except (ValueError, OverflowError):
            return False
        if not (1 <= nfield_i <= 256):
            return False
        return True
    # Empty / all-zero pointer table — treat as valid (rare but
    # happens for capacity-only blocks like BNBCD with count=0).
    return True


def _truncate_pointer_table(data: Any, ptrs: Any) -> int:
    """Return the index of the first non-zero invalid pointer in *ptrs*.

    On multi-super-element files (multi-GB decks with a dozen+ SEs),
    huge RV* tables encode ``dims`` as a CAP (~20 M) rather than the
    real populated count. Walking all 20 M slots reads record bytes
    that happen to encode small floats (NFIELD=11.0 → 0x41300000 ≈
    1.09 GB as an int → still in-file) as if they were pointers,
    producing millions of phantom records.

    The cutoff is detectable cheaply: real pointers either are 0
    (sparse slot) or point to a byte whose float32 is a sane NFIELD
    in [1, 1024]. The first non-zero pointer that fails this check
    marks the end of the real table; everything after is record-data
    being misread.

    Numpy-vectorised so a million-row-by-hundreds-of-modes RVNODDIS
    table (~150 MiB of pointer bytes) validates in well under a
    second without per-entry Python overhead.

    Scanned in chunks with an early exit at the first invalid pointer.
    The real records are a contiguous prefix, so the cutoff is found
    near the start — but the garbage cap-slots beyond it point into
    record-data bytes spread across the whole multi-GB file. Validating
    the full table at once (one ``as_f32[word_idx]`` over all ~20 M
    slots) faults every one of those scattered pages, pinning ~2.4 GiB
    of RSS on a 5 GB SIN just to find a cutoff that sits in the first
    chunk. Stopping at the first invalid pointer touches only the real
    records' pages.
    """
    import numpy as np

    if ptrs.size == 0:
        return 0
    file_end = len(data)
    as_f32 = np.frombuffer(data, dtype=np.float32)
    chunk = 1 << 16  # 64 K slots / pass — bounds the pages faulted per step
    total = int(ptrs.size)
    for start in range(0, total, chunk):
        seg = ptrs[start : start + chunk]
        nonzero = seg != 0
        nfield_bytes = (seg - 1) * 4
        in_bounds = (nfield_bytes >= 0) & (nfield_bytes + 4 <= file_end)
        # Word index 0 is a safe placeholder for out-of-bounds rows so
        # the fancy-index can't raise — they get masked out below.
        word_idx = np.where(in_bounds & nonzero, nfield_bytes // 4, 0).astype(np.int64)
        nfield_at = as_f32[word_idx]
        # NFIELD must be a positive integer in [1, 1024]. NaN/inf compare
        # False; casting to int32 then back catches non-integer floats.
        # The cast warns on NaN/inf — they're expected (garbage tail of
        # the table) and get masked out by the range check anyway.
        with np.errstate(invalid="ignore"):
            nfield_int = nfield_at.astype(np.int32).astype(np.float32)
        nfield_like = (nfield_at == nfield_int) & (nfield_at >= 1.0) & (nfield_at <= 1024.0)
        invalid = nonzero & ~(in_bounds & nfield_like)
        if invalid.any():
            return start + int(np.argmax(invalid))
    return total


def _find_preamble(data: Any, start: int, stop: int) -> int:
    """Return offset of the next 0x803 preamble in ``data[start:stop]``,
    or -1 if not found. Wraps the bytes/mmap ``.find`` API."""
    needle = struct.pack("<I", PREAMBLE)
    return data.find(needle, start, stop)


def _find_preamble_chunked(data: Any, start: int, stop: int) -> int:
    """Scan for the next preamble in fixed-size windows, dropping each
    window's pages from the page cache after the chunk is searched.

    ``mmap.find`` is implemented in C and touches every page in its
    [start, stop) range. On a multi-GB SIN with blocks scattered
    across the file (Sesam writes some sections near offset 0, some
    near 5 GB), a single unbounded ``.find`` faults *every* page in
    the gap and pushes RSS past the 4 GiB cgroup limit. Chunking the
    scan + ``madvise(MADV_DONTNEED)`` after each chunk keeps the
    touched-but-resident set bounded to ``_SCAN_WINDOW``.

    NAME_LEN bytes of overlap between chunks guarantee a preamble
    straddling a chunk boundary is still found.
    """
    needle = struct.pack("<I", PREAMBLE)
    pos = start
    while pos < stop:
        win_end = min(pos + _SCAN_WINDOW, stop)
        hit = data.find(needle, pos, win_end)
        if hit >= 0:
            return hit
        if isinstance(data, mmap.mmap):
            try:
                page = mmap.ALLOCATIONGRANULARITY
                aligned_start = (pos // page) * page
                aligned_end = ((win_end + page - 1) // page) * page
                length = aligned_end - aligned_start
                if length > 0:
                    data.madvise(mmap.MADV_DONTNEED, aligned_start, length)
            except (AttributeError, OSError, ValueError):
                pass
        # Advance — but always strictly forward, so we don't spin if
        # the overlap brings us back to the same position (which
        # happens when the remaining range is smaller than NAME_LEN).
        new_pos = win_end - NAME_LEN
        if new_pos <= pos:
            new_pos = win_end
        pos = new_pos
    return -1


# Names of the four file-header records (in order) that open every
# SIN file. They aren't "data" types — pure control directives.
_HEADER_NAMES = ("NORSAM", "ALLOCATE", "RESULTS", "IEND")


def _read_u32_slot(data: bytes, off: int) -> int:
    """Return the u32 value stored in the high 4 bytes of an 8-byte slot."""
    if off + SLOT_STRIDE > len(data):
        return 0
    return struct.unpack_from("<I", data, off + SLOT_VALUE_OFFSET)[0]


_NAME_BODY = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_NAME_FIRST = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _is_block_name(raw: bytes) -> bool:
    """Return True iff ``raw`` looks like a real SIF type name.

    Real names are uppercase ASCII letters + optional trailing digits,
    space-padded to ``NAME_LEN`` (e.g. ``"GNODE   "``, ``"RVNODDIS"``,
    ``"GELMNT1 "``). The 4-byte preamble pattern (``0x00000803``) does
    occur inside record data; the previous "any printable ASCII"
    filter let those false positives through (``"FILENAME"`` appeared
    inside the NORSAM record on real-world files). This filter rules
    them out by requiring the first byte to be an uppercase letter
    and disallowing embedded spaces / lowercase / punctuation.
    """
    if not raw or raw[0] not in _NAME_FIRST:
        return False
    saw_space = False
    for b in raw:
        if b == 0x20:  # trailing-space pad — only valid after the name body
            saw_space = True
            continue
        if saw_space:
            return False
        if b not in _NAME_BODY:
            return False
    return True


def iter_named_blocks(data: Any) -> Iterator[tuple[int, str]]:
    """Yield ``(preamble_offset, name)`` for every block in ``data``.

    A block is a 4-byte preamble equal to :data:`PREAMBLE` followed
    by an 8-byte ASCII name (space-padded). Catches the four file-
    header records (NORSAM / ALLOCATE / RESULTS / IEND) plus every
    per-type data block.

    Accepts any object that supports ``.find(needle, pos)`` and
    ``__getitem__`` with byte slices — both ``bytes`` and
    ``mmap.mmap`` qualify.
    """
    needle = struct.pack("<I", PREAMBLE)
    i = 0
    while True:
        i = data.find(needle, i)
        if i < 0:
            return
        if i + 4 + NAME_LEN <= len(data):
            raw = bytes(data[i + 4 : i + 4 + NAME_LEN])
            if _is_block_name(raw):
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
    # numpy int64 array of word-offsets (one per record slot). Stored
    # as numpy rather than ``list[int]`` because large eigen tables
    # have up to 20 M entries — Python int + list overhead would push
    # 600 MiB+ per block, blowing past the 4 GiB worker cap on the
    # biggest multi-super-element decks. int64 storage is 8 B/entry →
    # 160 MiB for the biggest table.
    pointer_table: Any
    records_start: int

    @property
    def count(self) -> int:
        """Number of populated (non-zero pointer) records."""
        # numpy fast path; falls back to Python iteration for lists.
        import numpy as np

        if isinstance(self.pointer_table, np.ndarray):
            return int((self.pointer_table != 0).sum())
        return sum(1 for p in self.pointer_table if p != 0)

    @property
    def record_words(self) -> int:
        """Per-record stride in 32-bit words (NFIELD + even-word pad)."""
        return self.nfield + (self.nfield & 1)


def _decode_type_block(data: bytes, preamble_off: int, next_preamble: int | None) -> TypeBlock:
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

    # Reject obvious-garbage dims before they balloon the pointer table.
    # A junk u32 read can put 2^31 in a dim slot; allocating a list of
    # 2 billion Python ints is what froze the host machine.
    if any(d > _MAX_DIM_VALUE for d in dims):
        raise ValueError(f"dim value > {_MAX_DIM_VALUE} in block {name!r}: {dims} — likely junk header")

    total_records = 1
    for d in dims:
        total_records *= d
    if total_records > _MAX_RECORDS_PER_BLOCK:
        raise ValueError(
            f"block {name!r} dims {dims} → {total_records} records "
            f"exceeds {_MAX_RECORDS_PER_BLOCK} cap — likely junk header"
        )

    # Bulk-read the pointer table as numpy. Each slot is 8 bytes,
    # value (low 32 bits of the 64-bit pointer) in the +4 half. We
    # read both halves as u32 then take every other element — for
    # huge tables (RVFORCES at 20 M entries) this is 80 MiB of
    # int64 vs 600 MiB of Python ints.
    #
    # The pointer table is 1-based: slot[0] is a zero sentinel and
    # records live at slots[1..N]. dims=(N,) therefore needs N+1
    # actual slots, otherwise the last record (id N) falls off the
    # end. Without this, multi-super-element files lose one
    # node/element per SE — and the missing element references a
    # node that then looks out-of-bounds to the trimesh builder.
    import numpy as np

    file_end = len(data)
    slot_count = total_records + 1
    n_words = slot_count * 2  # 2 u32 per slot
    max_bytes = pointer_table_offset + n_words * 4
    if max_bytes > file_end:
        n_words = max(0, (file_end - pointer_table_offset) // 4)
        n_words -= n_words & 1  # even number of u32s
        slot_count = n_words // 2
    total_records = slot_count
    if n_words > 0:
        u32_pairs = np.frombuffer(
            data,
            dtype=np.uint32,
            count=n_words,
            offset=pointer_table_offset,
        )
        pointer_table = u32_pairs[1::2].astype(np.int64).copy()
    else:
        pointer_table = np.empty(0, dtype=np.int64)

    # Truncate the pointer table at the first non-zero invalid pointer.
    # ``dims`` is a CAP for huge multi-SE RV* tables (some eigen decks
    # report ~20 M but the real count is n_modes × N nodes/elements).
    # Without this, walking the table yields millions of phantom
    # records pulled from record-data bytes being misread as pointers.
    real_count = _truncate_pointer_table(data, pointer_table)
    if real_count < pointer_table.size:
        pointer_table = pointer_table[:real_count].copy()
        total_records = real_count

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

    Backed by an :class:`mmap.mmap` so the OS pages bytes in on demand
    — a 5 GB SIN doesn't pin 5 GB of RSS; cold pages get reclaimed
    under memory pressure. Use :meth:`types` to list every data type
    present and :meth:`iter_records` to walk a type's records as flat
    float32 tuples (one per record, length ``nfield``).

    Treat ``_data`` as opaque bytes-like; it satisfies the buffer
    protocol that ``struct.unpack_from`` and ``bytes.find`` need.

    Multi-super-element files: A Sesam SIN can carry data for multiple
    "first level super-elements" — each is an independent mesh + result
    set (e.g. tens of separate load cases in a large eigen deck). They
    appear as ``RESULTS`` records in the header. ``super_element_refs``
    lists every one; ``super_elements[iref]`` lazily decodes one on
    first access (each can hold a 20 M-entry pointer table — decoding
    every SE upfront would blow past a 4 GiB cgroup limit).
    ``type_blocks`` aliases the active super-element's blocks; defaults
    to the first super-element, override via :meth:`use_super_element`.
    """

    path: Path
    # mmap.mmap satisfies the buffer protocol that struct.unpack_from
    # and bytes.find expect; we keep the type hint loose so the type
    # checker doesn't object to .find / slicing on either bytes or mmap.
    _data: Any = field(repr=False)
    _fh: IO[bytes] | None = field(default=None, repr=False)
    header_blocks: list[tuple[int, str]] = field(default_factory=list)
    # Cheap directory: every RESULTS record's (IREF, PTAB byte offset).
    # Walking this is O(num_super_elements) and touches only header
    # pages — safe even on multi-GB files.
    super_element_refs: list[tuple[int, int]] = field(default_factory=list)
    # Lazily-populated dict of {iref: {type_name: TypeBlock}}. Filled
    # on first access via :meth:`get_super_element`.
    super_elements: dict[int, dict[str, TypeBlock]] = field(default_factory=dict)
    # Aliased view of the currently-active super-element's TypeBlocks;
    # populated by :meth:`use_super_element` (called automatically in
    # ``__post_init__`` for the first super-element).
    type_blocks: dict[str, TypeBlock] = field(default_factory=dict)
    _active_iref: int | None = field(default=None, repr=False)

    def close(self) -> None:
        """Release the mmap + file handle.

        Idempotent — safe to call multiple times. After ``close()`` the
        :class:`SinFile` is unusable; reads will raise ``ValueError``.
        """
        if isinstance(self._data, mmap.mmap):
            try:
                self._data.close()
            except (ValueError, BufferError):
                pass
        self._data = b""
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def __enter__(self) -> "SinFile":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __post_init__(self) -> None:
        self._discover_blocks()

    def _discover_blocks(self) -> None:
        """Use the file's own directory structure (NORSAM → RESULTS →
        PTAB) to find every type-block per super-element.

        Per Sesam Interface File spec Appendix B (DNV GL, 2014):

        * The header area starts at byte 0 with NORSAM, ALLOCATE,
          (optionally FILENAME / IEXT), one RESULTS record **per
          super-element that carries result data**, and IEND.
        * Each RESULTS.IPFILE (a Fortran 1-indexed 64-bit-word
          address) points to that super-element's PTAB section.
        * Each PTAB is itself a type-block whose pointer table holds
          ``(ptr - 1) * 8 = preamble byte offset`` for every data
          type-block in the super-element (NORSAM, GNODE, GCOORD,
          GELMNT1, …, RVNODDIS, RVSTRESS, …).

        This replaces the earlier "scan the whole file for 0x803
        preambles" approach, which was both O(file_size) — fatal on
        multi-GB files — and prone to false positives (record float
        data occasionally contains the byte pattern ``03 08 00 00``
        followed by 8 printable ASCII bytes, which shadowed real
        type-blocks). The PTAB-driven walk is O(num_super_elements ×
        num_types) and visits only meaningful pages.
        """
        data = self._data
        if isinstance(data, mmap.mmap):
            try:
                data.madvise(mmap.MADV_RANDOM)
            except (AttributeError, OSError):
                pass

        # Step 1 — walk the header area to collect NORSAM / ALLOCATE /
        # RESULTS / IEND. Header records are packed contiguously starting
        # at byte 0; each starts with the 0x803 preamble.
        self.super_element_refs = self._walk_header_area()
        if not self.super_element_refs:
            # Defensive: SIN with no RESULTS records carries no super-
            # element data. The file is structurally invalid for our
            # purposes; leave type_blocks empty rather than guessing.
            return

        # Step 2 — pick the super-element with the most type-blocks as
        # the default, and decode only that one. The first RESULTS entry
        # is typically a summary-only super-element (IREF=1 on multi-SE
        # eigen decks has many type-block stubs but zero GELMNT1 records
        # — useless for rendering). The "main" data lives in whichever
        # super-element carries the densest PTAB. Touching each PTAB's
        # slot[4] is cheap (~64 bytes per super-element) and avoids
        # materialising the heavy pointer tables for the wrong default.
        default_iref = self._pick_default_super_element()
        if default_iref is not None:
            self.use_super_element(default_iref)

    def _pick_default_super_element(self) -> int | None:
        """Return the IREF whose PTAB lists the most type-blocks, as a
        heuristic for "main" super-element (carries the full mesh +
        results). Caller can override via :meth:`use_super_element`.
        """
        data = self._data
        file_end = len(data)
        best_iref = None
        best_count = -1
        for iref, ptab_byte in self.super_element_refs:
            if ptab_byte + 12 + 5 * SLOT_STRIDE + 4 > file_end:
                continue
            if struct.unpack_from("<I", data, ptab_byte)[0] != PREAMBLE:
                continue
            name = bytes(data[ptab_byte + 4 : ptab_byte + 12]).decode("ascii", errors="replace").rstrip()
            if name != "PTAB":
                continue
            payload = ptab_byte + 4 + NAME_LEN
            count = _read_u32_slot(data, payload + 4 * SLOT_STRIDE)
            if count > best_count:
                best_count = count
                best_iref = iref
        return best_iref

    def use_super_element(self, iref: int) -> None:
        """Set ``iref`` as the active super-element. Decodes its PTAB
        + type-block headers if not already cached. Updates
        :attr:`type_blocks` to point at this super-element's blocks.
        """
        if iref not in self.super_elements:
            self.super_elements[iref] = self._decode_super_element(iref)
        self._active_iref = iref
        self.type_blocks = self.super_elements[iref]

    def _decode_super_element(self, iref: int) -> dict[str, TypeBlock]:
        """Walk one super-element's PTAB and decode every listed
        type-block. Caller is responsible for memory bookkeeping —
        each call materialises that super-element's per-block pointer
        tables (numpy int64 arrays, ~8 B per entry)."""
        ptab_byte = next((b for ir, b in self.super_element_refs if ir == iref), None)
        if ptab_byte is None:
            raise KeyError(f"super-element IREF={iref} not in this SIN")
        data = self._data
        file_end = len(data)
        out: dict[str, TypeBlock] = {}
        for preamble_off in self._walk_ptab(ptab_byte):
            if preamble_off <= 0 or preamble_off + 12 > file_end:
                continue
            # PTAB pointers can include NORSAM (preamble at byte 0)
            # — that's a header record, not a type-block. Skip.
            if preamble_off == 0:
                continue
            # Reject anything that doesn't actually start with 0x803
            # — guard against PTAB corruption.
            if struct.unpack_from("<I", data, preamble_off)[0] != PREAMBLE:
                continue
            raw = bytes(data[preamble_off + 4 : preamble_off + 4 + NAME_LEN])
            if not _is_block_name(raw):
                continue
            try:
                block = _decode_type_block(data, preamble_off, None)
            except Exception:
                continue
            # PTAB-sourced blocks are authoritative — skip the
            # post-decode NFIELD/type_flag sanity check that the
            # old scan path needed against false positives.
            out[block.name] = block
            # _decode_type_block (via _truncate_pointer_table) just
            # faulted this block's record pages reading each NFIELD
            # word; the decoded TypeBlock retains everything we need,
            # so drop those pages before the next block stacks its own.
            self._release_block_pages(block)
        return out

    def _walk_header_area(self) -> list[tuple[int, int]]:
        """Walk packed header records from byte 0 until IEND. Side-
        effect: populates ``self.header_blocks``. Returns the list of
        ``(IREF, PTAB byte offset)`` for every RESULTS record.
        """
        data = self._data
        file_end = len(data)
        out: list[tuple[int, int]] = []
        i = 0
        # Cap header walk to a generous prefix — the header area is
        # pre-allocated (~280 bytes per spec, larger when many RESULTS
        # records are present). 1 MiB is enough for any realistic file.
        max_header = min(file_end, 1024 * 1024)
        while i < max_header:
            if struct.unpack_from("<I", data, i)[0] != PREAMBLE:
                break
            raw = bytes(data[i + 4 : i + 4 + NAME_LEN])
            if not _is_block_name(raw):
                break
            name = raw.decode("ascii").rstrip()
            self.header_blocks.append((i, name))
            if name == "RESULTS":
                # RESULTS layout (32 bytes total, per spec B.3.4):
                #   preamble (4) + "RESULTS " (8) + NFIELD (4) +
                #   IREF (4) + IPFILE_low (4) + IPFILE_high (4) +
                #   Not Used (4). IPFILE is a Fortran 1-indexed
                #   64-bit-word address; byte_offset = (IPFILE-1)*8.
                iref = struct.unpack_from("<I", data, i + 16)[0]
                ipfile_lo = struct.unpack_from("<I", data, i + 20)[0]
                ipfile_hi = struct.unpack_from("<I", data, i + 24)[0]
                ipfile = ipfile_lo | (ipfile_hi << 32)
                if ipfile > 0:
                    ptab_byte = (ipfile - 1) * 8
                    if 0 <= ptab_byte + 12 < file_end:
                        out.append((iref, ptab_byte))
            if name == "IEND":
                break
            # Header records are packed densely; the next preamble
            # follows immediately. Locate it via a small bounded
            # search so we don't depend on per-type record-size math.
            next_i = data.find(struct.pack("<I", PREAMBLE), i + 4, min(i + 4096, max_header))
            if next_i < 0:
                break
            i = next_i
        return out

    def _walk_ptab(self, ptab_byte: int) -> list[int]:
        """Decode one PTAB section and return preamble byte offsets
        for every type-block it lists.

        PTAB layout (verified empirically against cantilever fixtures
        plus large multi-super-element eigen decks):

            slot[1]  NFIELD       (= 5 on all PTABs seen)
            slot[2]  type_flag    (= 0 or 1)
            slot[3]  ptr_table_word — same convention as a normal
                     type-block header: pointer table starts at
                     ptr_table_word * 8 - 4
            slot[4]  count        (# of pointer entries)
            slot[5]  count-repeat (low half non-zero on some PTABs;
                     not fully understood, but slot[4] is the
                     authoritative count)
            slot[6..6+count-1]   8-byte pointer entries

        Each pointer is a Fortran 1-indexed 64-bit-word address into
        the file; ``(ptr - 1) * 8`` is the type-block's preamble byte
        offset.
        """
        data = self._data
        file_end = len(data)
        if ptab_byte + 12 > file_end:
            return []
        if struct.unpack_from("<I", data, ptab_byte)[0] != PREAMBLE:
            return []
        name = (
            bytes(data[ptab_byte + 4 : ptab_byte + 4 + NAME_LEN])
            .decode(
                "ascii",
                errors="replace",
            )
            .rstrip()
        )
        if name != "PTAB":
            return []
        payload = ptab_byte + 4 + NAME_LEN
        # slot[4] holds the count of type-block pointers *excluding* the
        # mandatory leading NORSAM reference. Verified on cantilever:
        # slot[4]=15, actual pointer table = slot[6..21] = 16 entries
        # (1 NORSAM + 15 type-blocks including TDMATER, TDRESREF). The
        # same +1 offset is needed on large multi-SE eigen decks —
        # e.g. slot[4]=48 lists 49 pointers each.
        count = _read_u32_slot(data, payload + 4 * SLOT_STRIDE) + 1
        if not (1 < count <= 1024):
            # PTAB with absurd count → likely corrupted header; bail.
            return []
        offsets: list[int] = []
        for idx in range(count):
            slot = payload + (6 + idx) * SLOT_STRIDE
            if slot + SLOT_STRIDE > file_end:
                break
            ptr_lo = struct.unpack_from("<I", data, slot)[0]
            ptr_hi = struct.unpack_from("<I", data, slot + 4)[0]
            # 64-bit pointer reconstruction: SLOT layout puts the LOW
            # 32 bits in bytes [+4..+7] (matching the rest of the
            # NSPI=2 convention). For values ≤ 2^32, ptr_hi=0 and
            # the value is ptr_lo at +4. For huge files the value
            # comes from the +0 word (verified on multi-SE eigen
            # decks where PTAB pointers stay well under 2^32).
            ptr = ptr_hi if ptr_lo == 0 else ptr_lo
            if ptr <= 0:
                continue
            offsets.append((ptr - 1) * 8)
        return offsets

    @property
    def types(self) -> list[str]:
        return list(self.type_blocks.keys())

    def get_count(self, name: str) -> int:
        block = self.type_blocks.get(name)
        return block.count if block is not None else 0

    def iter_record_first_word(self, name: str) -> Iterator[float]:
        """Yield just the first data word (4 bytes) of every populated
        record.

        For RV* result types (RVNODDIS, RVSTRESS, RVFORCES, …) the
        first data word is ``IRES`` — the step / result-reference
        index. Use :meth:`gather_first_words` instead when you need
        all values at once — that path is ~50× faster and uses
        ~100× less peak Python heap on big tables (numpy bulk read
        vs per-record float yield).
        """
        block = self.type_blocks.get(name)
        if block is None:
            return
        data = self._data
        file_end = len(data)
        for word_ptr in block.pointer_table:
            wp = int(word_ptr)
            if wp == 0:
                continue
            data_byte = wp * 4
            if data_byte + 4 > file_end:
                continue
            yield struct.unpack_from("<f", data, data_byte)[0]

    def gather_first_words(self, name: str):
        """Return a numpy ``float32`` array of every populated record's
        first data word — the bulk-IRES gather for RV* result types.

        For large RVFORCES tables (tens of millions of records) this
        materialises a single 80 MiB numpy buffer instead of yielding
        20 M Python float objects (each 28 B + GC churn). The bulk
        read is essential for staying under the worker's 4 GiB heap
        budget on real-world SINs.

        Returns an empty array if the type isn't present.
        """
        import numpy as np

        block = self.type_blocks.get(name)
        if block is None:
            return np.empty(0, dtype=np.float32)
        # pointer_table is already a numpy int64 array (see
        # _decode_type_block). Filter out unused slots + EOF-overrun
        # pointers via vectorised masks.
        ptrs = block.pointer_table
        file_end = len(self._data)
        valid = (ptrs > 0) & (ptrs * 4 + 4 <= file_end)
        ptrs = ptrs[valid]
        # Float view over the whole file, then fancy-index by word
        # offset. Numpy issues one read per page, kernel pages in
        # only the bytes we hit (and they're 4-aligned), so for a
        # densely-packed records section this touches every page
        # once and no more.
        as_f32 = np.frombuffer(self._data, dtype=np.float32)
        return as_f32[ptrs].copy()

    def _release_block_pages(self, block: "TypeBlock") -> None:
        """``MADV_DONTNEED`` a type-block's record region so its mmap
        pages stop counting against RSS.

        The dominant resident set on a multi-GB eigen SIN is the RV*
        record streams, faulted in two phases: once at decode time (
        :func:`_truncate_pointer_table` reads each record's NFIELD word)
        and again at read time (:meth:`gather_records`). Dropping a
        block's pages as soon as each phase finishes with it caps peak
        RSS at a single block's resident span instead of the cumulative
        sum across all 45 blocks. Correctness is unaffected — a later
        re-read simply re-faults the pages from the backing file.

        Reclaims [NFIELD-prefix of the first record .. _MAX_RECORD_BYTES
        past the last]. No-op on non-mmap buffers / platforms without
        ``madvise``.
        """
        data = self._data
        if not isinstance(data, mmap.mmap) or block.pointer_table.size == 0:
            return
        nz = block.pointer_table[block.pointer_table > 0]
        if nz.size == 0:
            return
        file_end = len(data)
        lo = max(0, (int(nz.min()) - 1) * 4)
        hi = min(file_end, int(nz.max()) * 4 + _MAX_RECORD_BYTES)
        if hi <= lo:
            return
        try:
            page = mmap.ALLOCATIONGRANULARITY
            aligned_start = (lo // page) * page
            aligned_end = min(file_end, ((hi + page - 1) // page) * page)
            length = aligned_end - aligned_start
            if length > 0:
                data.madvise(mmap.MADV_DONTNEED, aligned_start, length)
        except (AttributeError, OSError, ValueError):
            pass

    def release_record_pages(self, name: str) -> None:
        """Reclaim a type-block's record pages by name (see
        :meth:`_release_block_pages`). No-op if the type isn't present."""
        block = self.type_blocks.get(name)
        if block is not None:
            self._release_block_pages(block)

    def gather_records(self, name: str, *, where_first_word: int | None = None):
        """Bulk-read every populated *fixed-width* record into a single
        ``(count, nfield)`` float64 array — the vectorised analogue of
        :meth:`iter_records`.

        Column 0 holds the constant NFIELD (matching the SIF-style row
        ``[nfield, *data]``); columns 1.. hold the record's data words.
        One ``np.frombuffer`` view + a broadcast fancy-index does the
        whole table, so a million-row RVNODDIS materialises as one ~80
        B/row ndarray instead of a million Python ``list[float]`` rows
        (~376 B each) — the heap saving the streaming bake needs.

        Returns ``None`` when the records are **not** uniform width
        (e.g. GELMNT1, whose NFIELD varies per element), signalling the
        caller to fall back to the per-record :meth:`iter_records` path.
        Returns an empty ``(0, 0)`` array when the type has no populated
        records.

        ``where_first_word`` mirrors :meth:`iter_records`: keep only
        records whose first data word (IRES for RV* types) equals it.
        """
        import numpy as np

        block = self.type_blocks.get(name)
        if block is None:
            return None
        ptrs = block.pointer_table
        data = self._data
        file_end = len(data)
        empty = np.empty((0, 0), dtype=np.float64)
        if ptrs.size == 0:
            return empty
        nz = ptrs[ptrs > 0]
        # Drop pointers whose NFIELD prefix word ((wp-1)*4) is out of
        # bounds before we touch the buffer.
        nz = nz[(nz >= 1) & (nz <= file_end // 4)]
        if nz.size == 0:
            return empty
        as_f32 = np.frombuffer(data, dtype=np.float32)
        nfields = as_f32[nz - 1]
        # Only vectorise truly fixed-width tables; a varying NFIELD
        # means the per-record path is the only correct reader.
        if not np.all(nfields == nfields[0]):
            return None
        nfield = int(nfields[0])
        n_data = nfield - 1
        if n_data <= 0:
            return empty
        # Each record needs n_data data words at [wp, wp+n_data).
        nz = nz[(nz * 4 + n_data * 4) <= file_end]
        if nz.size == 0:
            return empty
        # Per-step pre-filter: narrow the *pointer table* to the matching
        # records before the heavy gather. The first data word (word idx
        # ``wp``) is IRES for RV* types; reading just that column is one
        # small array, so the big ``(count, n_data)`` allocation that
        # follows is sized to a single step rather than to all 200 steps
        # then sliced. On a per-mode streaming bake this keeps the heap
        # bounded to one mode instead of re-allocating the whole table on
        # every step.
        if where_first_word is not None:
            nz = nz[as_f32[nz].astype(np.int64) == where_first_word]
            if nz.size == 0:
                return np.empty((0, nfield), dtype=np.float64)
        # (count, nfield) output: NFIELD constant in col 0, data in 1..
        # Assign the float32 fancy-index straight into the float64 buffer
        # (numpy casts on store) — no separate float64 ``recs`` temporary.
        word_idx = nz[:, None] + np.arange(n_data, dtype=np.int64)[None, :]
        out = np.empty((nz.size, nfield), dtype=np.float64)
        out[:, 0] = float(nfield)
        out[:, 1:] = as_f32[word_idx]
        return out

    def iter_records(self, name: str, *, where_first_word: int | None = None) -> Iterator[tuple[float, ...]]:
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

        ``where_first_word`` (optional): if provided, only records whose
        first data word equals this int are yielded. For RV* result
        types the first data word is ``IRES`` (the step / mode index),
        so passing ``where_first_word=step`` slices to that step
        without materialising any record outside it. On large eigen
        decks this typically cuts a single-step RVNODDIS slice from
        a million-plus records to a few thousand.
        """
        block = self.type_blocks.get(name)
        if block is None:
            return
        data = self._data
        file_end = len(data)
        for word_ptr in block.pointer_table:
            wp = int(word_ptr)
            if wp == 0:
                continue
            nfield_byte = (wp - 1) * 4
            if nfield_byte < 0 or nfield_byte + 4 > file_end:
                continue
            nfield = int(struct.unpack_from("<f", data, nfield_byte)[0])
            n_data = nfield - 1
            if n_data <= 0:
                continue
            data_byte = wp * 4
            if data_byte + n_data * 4 > file_end:
                continue
            if where_first_word is not None:
                # Cheap pre-filter: read just the first data word and
                # skip mismatches before paying for the full unpack.
                first = int(struct.unpack_from("<f", data, data_byte)[0])
                if first != where_first_word:
                    continue
            yield struct.unpack_from(f"<{n_data}f", data, data_byte)

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
                f"<{n_numeric_prefix}f",
                self._data,
                data_byte,
            )
            len_word = struct.unpack_from(
                "<I",
                self._data,
                data_byte + n_numeric_prefix * 4,
            )[0]
            n_chars = (len_word >> 8) & 0xFFFFFF
            text_bytes = self._data[
                data_byte + (n_numeric_prefix + 1) * 4 : data_byte + (n_numeric_prefix + 1) * 4 + n_text_words * 4
            ]
            if n_chars and n_chars <= len(text_bytes):
                text = text_bytes[:n_chars].decode("ascii", errors="replace").rstrip()
            else:
                text = text_bytes.decode("ascii", errors="replace").rstrip("\x00 ")
            yield (numeric_prefix, text)


def open_sin(path: str | Path) -> SinFile:
    """Open a ``.sin`` file (memory-mapped) and decode its block index.

    Uses ``mmap`` so a multi-GB SIN doesn't load resident — the OS
    pages bytes in on access. The file handle and mapping live on the
    returned :class:`SinFile`; call :meth:`SinFile.close` (or use it
    as a context manager) to release them.
    """
    p = Path(path)
    fh = open(p, "rb")
    try:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    except Exception:
        fh.close()
        raise
    return SinFile(path=p, _data=mm, _fh=fh)


__all__ = [
    "PREAMBLE",
    "NAME_LEN",
    "TypeBlock",
    "SinFile",
    "iter_named_blocks",
    "open_sin",
]
