"""Sesam SIN (Norsam) binary file probe.

Walks a `.sin` file and prints the file-header records + per-type
blocks (name, NFIELD, count, first/last pointer, first record bytes
decoded as float32). Used as a scratch tool while reverse-engineering
the format — see ``src/ada/fem/formats/sesam/results/SIN_FORMAT.md``.

Run via::

    python scripts/sin_probe.py path/to/file.SIN

Pure stdlib; no adapy or DNV dependency.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

PREAMBLE = 0x803
NAME_LEN = 8


def _read_u32_after_slot(data: bytes, off: int) -> tuple[int, int]:
    """Read one 4-byte LE u32 at ``off`` and skip its 4-byte high-half
    zero pad (per-type-block control fields are stored in 64-bit slots
    with the value in the low 32 bits). Returns ``(value, next_offset)``."""
    if off + 8 > len(data):
        return 0, off + 8
    return struct.unpack_from("<I", data, off)[0], off + 8


def _find_named_blocks(data: bytes) -> list[tuple[int, str]]:
    """Locate every offset where ``PREAMBLE`` is followed by a printable
    8-byte ASCII name — these are the file-header records and the
    per-type block markers."""
    hits: list[tuple[int, str]] = []
    needle = struct.pack("<I", PREAMBLE)
    i = 0
    while True:
        i = data.find(needle, i)
        if i < 0:
            break
        if i + 4 + NAME_LEN <= len(data):
            raw = data[i + 4 : i + 4 + NAME_LEN]
            if all(32 <= b < 127 for b in raw):
                name = raw.decode("ascii").rstrip()
                if name:
                    hits.append((i, name))
        i += 1
    return hits


def _decode_type_block(data: bytes, off: int, next_off: int) -> dict:
    """Best-effort decode of one per-type block header.

    ``off`` points at the PREAMBLE; ``next_off`` is the start of the
    following block (used to clamp the pointer-table walk). Returns a
    dict with ``name``, ``nfield``, ``count``, ``ndim``, ``pointers``
    (first few + last) and ``first_record`` (decoded as float32).
    """
    name = data[off + 4 : off + 4 + NAME_LEN].decode("ascii").rstrip()
    # Skip preamble (4 bytes) + name (8 bytes) + 12 bytes of zeros that
    # consistently precede NFIELD across the GNODE / GCOORD / RVNODDIS
    # samples. The "12 bytes" is empirical — three 4-byte zero slots.
    p = off + 4 + NAME_LEN + 12
    nfield, p = _read_u32_after_slot(data, p)
    _ctrl_a, p = _read_u32_after_slot(data, p)  # 21/31/2 — TBD
    _ctrl_b, p = _read_u32_after_slot(data, p)  # size/offset — TBD
    # Dimension entries: read pairs of (value, value) until we hit what
    # looks like a pointer (a 32-bit value >> count of records in the
    # file's word-stride). Heuristic, but works for the cases tested.
    dims: list[int] = []
    count_candidates: list[int] = []
    while True:
        v, p_next = _read_u32_after_slot(data, p)
        # The repeated-dim pattern: read pairs of equal u32s. When the
        # next u32 differs from the previous, we've crossed into the
        # pointer table.
        if dims and v != count_candidates[-1]:
            break
        if v == 0 or v > 10_000_000:
            break
        if dims and v == count_candidates[-1]:
            dims.append(v)
            p = p_next
            continue
        # First value of a new dim pair.
        count_candidates.append(v)
        p = p_next
        # Read the "repeat" copy
        v2, p2 = _read_u32_after_slot(data, p)
        if v2 == v:
            dims.append(v)
            p = p2
        else:
            # Single-occurrence; rewind so the next iteration sees v2.
            count_candidates.pop()
            break
        if len(dims) >= 4:
            break
    count = dims[-1] if dims else 0
    # Pointer table: ``count`` 64-bit slots (low-32 = word offset).
    ptrs: list[int] = []
    for j in range(count):
        po = p + 8 * j
        if po + 4 > len(data):
            break
        ptr = struct.unpack_from("<I", data, po)[0]
        ptrs.append(ptr)
    # First record decode (NFIELD + 1 even-padded floats from the
    # pointer's word offset).
    first_record: list[float] = []
    if ptrs and nfield > 0:
        rec_words = nfield + (nfield & 1)
        rec_byte = ptrs[0] * 4
        if rec_byte + rec_words * 4 <= len(data):
            first_record = list(struct.unpack_from(f"<{rec_words}f", data, rec_byte))
    return {
        "offset": off,
        "name": name,
        "nfield": nfield,
        "_ctrl_a": _ctrl_a,
        "_ctrl_b": _ctrl_b,
        "dims": dims,
        "count": count,
        "first_ptr_word_offset": ptrs[0] if ptrs else None,
        "first_record": first_record,
        "n_ptrs_recovered": len(ptrs),
    }


def probe(path: Path) -> None:
    data = path.read_bytes()
    print(f"file: {path}  size={len(data)} bytes")

    blocks = _find_named_blocks(data)
    print(f"\nfound {len(blocks)} named blocks")

    # Sort by offset, classify into file-header vs per-type.
    file_header_names = {"NORSAM", "ALLOCATE", "RESULTS", "IEND"}
    header_blocks = [b for b in blocks if b[1] in file_header_names]
    type_blocks = [b for b in blocks if b[1] not in file_header_names]

    print("\nFile header records:")
    for off, name in header_blocks:
        print(f"  0x{off:08x}  {name!r}")

    print("\nPer-type blocks (count, NFIELD, dims, first record):")
    for idx, (off, name) in enumerate(type_blocks):
        next_off = type_blocks[idx + 1][0] if idx + 1 < len(type_blocks) else len(data)
        info = _decode_type_block(data, off, next_off)
        rec_preview = ", ".join(f"{v:g}" for v in info["first_record"][:6]) if info["first_record"] else "—"
        print(
            f"  0x{info['offset']:08x}  {info['name']:9s}  "
            f"NFIELD={info['nfield']:>3d}  count={info['count']:>5d}  "
            f"dims={info['dims']}  first=[{rec_preview}]"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} path/to/file.SIN", file=sys.stderr)
        raise SystemExit(2)
    probe(Path(sys.argv[1]))
