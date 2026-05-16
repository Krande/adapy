# Sesam SIN (Norsam) binary format — reverse-engineering notes

This document captures the on-disk structure of Sesam's **SIN
direct-access binary** result file ("Norsam format"), worked out by
comparing a known SIF (text) cantilever model against the SIN produced
by `dnv-sifio` (the official DNV Python wrapper around `SifIO.dll`).
The goal is a pure-Python reader so adapy doesn't have to shell out to
`Prepost.exe` (current behaviour — see `sin2sif.py`) or take a hard
dependency on the .NET-backed `dnv-sifio` (an optional gated backend
is fine; a default hard dep is not).

The official binary spec isn't on the public web; DNV's own
`SESAM Manager / Framework / Prepost` PDFs document the *user-facing*
side and the *SIF text* layout, but not the on-disk Norsam record
encoding. Everything below is verified empirically.

## Top-level file layout

```
+--------------------------------------------------+ offset 0
| File header — sequence of 4 named control blocks |
|   "NORSAM  "  "ALLOCATE"  "RESULTS "  "IEND    " |
+--------------------------------------------------+ ~0xC0
| Zero-padding to the next 4-KB / 8-KB boundary    |
+--------------------------------------------------+ 0x2000
| Per-type data blocks, one per registered data    |
| type (GNODE, GCOORD, GELMNT1, …, RVNODDIS …)     |
|                                                  |
|   For each type:                                 |
|     +------------------------------------------+ |
|     | Type-block header (preamble, name, dims, |
|     | record count, … — see below)             |
|     +------------------------------------------+ |
|     | Pointer table (n_records × 64-bit word-  |
|     | offsets into the file, low-32-used)      |
|     +------------------------------------------+ |
|     | Tightly-packed float32 records           |
|     | (each NFIELD words wide, padded to even  |
|     | word count)                              |
|     +------------------------------------------+ |
+--------------------------------------------------+ EOF
```

## Constants

* **Byte order**: little-endian throughout (`0x803` literal at byte 0
  decodes as `08 03 00 00`).
* **Word size**: 32 bits (4 bytes). Pointers stored in **64-bit slots**
  with the value in the low 32 bits; header control fields use the
  same 64-bit-slot convention. Data records are densely packed 32-bit
  floats — no per-value padding.
* **Preamble marker**: `0x00000803` precedes every named block (file
  header records and per-type blocks alike). Looks like a type/length
  tag — not yet fully decoded.
* **Name field**: 8 ASCII bytes, space-padded. Examples seen:
  `"NORSAM  "`, `"ALLOCATE"`, `"RESULTS "`, `"IEND    "`,
  `"GNODE   "`, `"GCOORD  "`, `"GELMNT1 "`, `"RVNODDIS"`.

## File-header records

Bytes 0..~0xC0 of the sample SIN (`cant.SIN`, 253 520 bytes):

```
offset  preamble  name        following data (u32 LE, 0-padded to 64-bit slot)
------- --------- ----------- ----------------------------------------------
0x0000  0x803     "NORSAM  "  | 7 | 0 | 1 | 2 | 31691 | …
0x002C  0x803     "ALLOCATE"  | 21 | 2 | 0 | 15 | 0 | 0 | 0 | 0 | 0 | 31692 | -511 (0xFFFFFE01) | 31 | 1025 | …
0x0084  0x803     "RESULTS "  | 5 | 1 | 31667 | 0 | 0
0x00A8  0x803     "IEND    "  | 5 | 0 | 0 | 0 | 0
```

The exact field meanings are TBD; the structural finding is that the
file *opens* with these four control blocks, each prefixed by the same
`0x803` marker and a name.

## Per-type block layout

Verified on **GNODE** (offset `0x2000`), **GCOORD** (`0x52A4`),
**RVNODDIS** (`0x2ADD4`). Reading values as 32-bit LE while skipping
the high-32 zero pad of each 64-bit slot:

```
offset  field           example values
------- --------------- --------------------------
+0x00   preamble (u32)  0x803
+0x04   name (8 bytes)  "GNODE   "
+0x0C   unknown (3 ×    0, 0, 0
        u32, zero)
+0x18   NFIELD (u32)    5 (GNODE), 5 (GCOORD), 7 (RVNODDIS)
+0x20   type_flag (u32) Norsam type-class enum (see table below)
+0x28   ptr_table_word  64-bit-word offset of slot[6]'s value field
        (u32)           — i.e. the first entry of the pointer table.
                        Redundant cross-check; lets the decoder derive
                        NDIM deterministically (see below).
+0x30   cap[0]  (u32)   allocated capacity of dim 0
+0x38   pop[0]  (u32)   populated count of dim 0 (≤ cap[0])
+0x40   cap[1]  (u32)   only present for 2-D types (RVNODDIS,
                        RVSTRESS, RDPOINTS); 1-D types (GNODE,
                        GCOORD …) skip straight to the pointer table.
+0x48   pop[1]  (u32)
+...    pointer table   n × u64 word-offsets (low-32 used);
                        n = prod(pop[i])
+...    record stream   tightly packed float32 records,
                        each NFIELD wide + 1 pad word to even count
```

### NDIM derivation (from `ptr_table_word`)

The pointer-table offset is `ptr_table_word * 8` (bytes). Working
backwards through the slot layout:

```
pointer_table_offset = ptr_table_word * 8 - 4   # slot value lives at slot_off+4
dim_slots            = (pointer_table_offset - payload) / 8 - 4
NDIM                 = dim_slots / 2
```

Verified on the cantilever fixture (`GNODE → NDIM=1`,
`RVNODDIS → NDIM=2`). The earlier cap-vs-pop walk is retained as a
fallback for malformed/older files where `ptr_table_word` is zero.

### `type_flag` (slot at +0x20) — empirical enum

The value at +0x20 is a per-type class tag. Pattern observed across
the cantilever fixture:

| value | types                                         | rough semantics                |
|-------|-----------------------------------------------|--------------------------------|
|  0    | `PTAB`                                        | pointer table for the file itself |
|  1    | `RDSTRESS`, `RDIELCOR`, `RDRESREF`            | result-definition records (1-D, NFIELD=5) |
|  2    | `RDPOINTS`, `RVNODDIS`, `RVSTRESS`            | 2-D result-vector tables       |
| 20    | `MISOSEL`                                     | material/section scalars       |
| 21    | `GCOORD`, `GELREF1`, `GELTH`, `BNBCD`         | mixed-int/float tables with id header |
| 31    | `GNODE`, `GELMNT1`                            | all-int tables (variable per-record NFIELD) |
| 41    | `TDMATER`, `TDRESREF`                         | text-tagged records            |

It's clearly a small enum, not a bitmask in any obvious sense (none
of the bit patterns line up with NFIELD or with the int/float field
makeup). The reader stores it on `TypeBlock.type_flag` for
diagnostics but never *consumes* it — pointer-table walking + per-
record NFIELD is sufficient to read every type encountered so far.

### Capacity vs population

The `cap[i]` / `pop[i]` pair distinguishes allocated table size from
written rows: `BNBCD` shows `cap=200, pop=200, count=13` (the table
was sized for 200 boundary cards but only 13 records have been
populated — the rest of the pointer table is zeros).

### Pointer table → record decode

For GNODE (NFIELD = 5):

```
pointer[0] = 0x0B37 = 2871   (word offset)
byte offset = 2871 × 4 = 11484 = 0x2CDC
```

The bytes there decode as **6 × float32 LE**:

```
0x2CDC: 5.0 1.0 1.0 6.0 123456.0 0.0
```

Matches the first SIF line exactly:

```
GNODE     1.00000000E+00  1.00000000E+00  6.00000000E+00  1.23456000E+05
```

Encoding rule confirmed:

* `record[0]` = `NFIELD` (= 5 for GNODE), stored as float32 even though
  it's conceptually a count — SIF stores it as a float too.
* `record[1 .. NFIELD-1]` = the SIF data fields (also float32 LE).
* One trailing 0.0 padding float aligns the record to an even word
  count (Fortran direct-access convention).

So each record stride in words = `NFIELD + (NFIELD & 1)` and the
pointers index a flat 32-bit-word array.

## Text records (TDNODE, TDRESREF, TDMATER…)

Not yet decoded; the `dnv-sifio` reader exposes them via `ReadText()`
returning `List[str]` per id, and `GetTabDimensions()` returns an
empty array for text types. Layout TBD — likely a length-prefixed
ASCII payload following the same per-type block header convention.

## Next steps

1. Pin down the +0x20 / +0x28 header fields by emitting a SIN file
   with `dnv-sifio` for many distinct (NFIELD, ndim, count) shapes
   and diffing the resulting bytes.
2. Decode text records (TDNODE, TDELEM, TDMATER, TDRESREF, TDSUPNAM).
3. Decode multi-D table layout (e.g. `RVNODDIS` with `dims=[1, 403]`
   stores 403 records but the pointer table semantics under multi-D
   need verification — does the 2D case nest pointers).
4. Verify the assumption that the four file-header records (NORSAM /
   ALLOCATE / RESULTS / IEND) are fixed-shape across all SIN files.
5. Lift the working bits into a streaming reader in
   `read_sin.py` that returns the same `MeshData` /
   `FieldArtefactMeta` shape `read_sif.py` already produces.
6. Add a checked-in tiny `STATIC_SHELL_CANTILEVER_SESAMR1.SIN`
   (regenerable from the corresponding SIF via the `dnv-sifio`
   round-trip in `scripts/regen_sin_fixtures.py`) so the reader has
   a stable test fixture.

## Tooling

* `scripts/sin_probe.py` (this repo) — dump the file structure of any
  SIN: header records, per-type blocks with NFIELD / count / pointer
  table sample. Used to validate decode hypotheses against
  `dnv-sifio`-generated SINs.
* `scripts/regen_sin_fixtures.py` — runs in an *isolated* env with
  `dnv-sifio` installed and re-generates the SIN test fixtures from
  the existing SIF files in `files/fem_files/cantilever/sesam/`. Not
  a runtime dep of adapy; only invoked by maintainers when test
  fixtures need refreshing.
