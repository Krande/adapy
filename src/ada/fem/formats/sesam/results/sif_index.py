"""Byte-offset index for Sesam SIF result decks → cheap single-step reads.

A SIF deck holds every result step's RV* records (RVNODDIS / RVSTRESS /
RVFORCES) in one contiguous block per card, and those blocks are 70%+ of a
large file. ``read_sif_file(step=...)`` already avoids *materialising* the
other steps, but it still has to *scan* the whole file line-by-line to find
the target step's records, and the worker still downloads the whole file.

This module records, in one cheap byte scan, the byte span of every step
inside each RV* block. With that index, the bytes that belong to steps other
than the target can be skipped entirely:

* :meth:`SifStepIndex.include_ranges` returns the byte ranges to read for a
  given step — the whole file *minus* the other steps' spans. Control rows,
  mesh, sections, RDPOINTS and inter-block gaps fall in the kept gaps
  automatically (they're never inside a step span), so a reader fed the
  concatenated ranges sees a valid, smaller SIF.
* The async worker range-fetches those ranges from object storage
  (``storage.get_range``) into a reduced tempfile — so a re-pick of one mode
  reads ~⅓ of a 969 MB deck instead of all of it, and the *download* shrinks
  by the same factor. The reduced file is parsed by the normal
  :func:`read_sif_file`; no S3 client is needed inside the conversion child.

The index is a small JSON sidecar (``_derived/<src>.sifindex.json``) built the
first time a deck is converted and reused on every later pick.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

# RV* result cards whose rows carry an ``ires`` (step id) at raw token index 2
# (card name + ``nfield`` precede it). Mirrors read_sif._RV_STEP_CARDS, kept
# local so this module has no import cycle with the parser.
_RV_CARDS = frozenset({"RVNODDIS", "RVSTRESS", "RVFORCES"})

# Index format version — bump if the build logic changes meaning so a stale
# sidecar is rejected rather than silently misread.
INDEX_VERSION = 1


@dataclass
class SifStepIndex:
    """Byte-span index of a SIF deck's per-step RV* records.

    ``step_spans`` is a flat list of ``(ires, start, end)`` byte ranges, one
    per contiguous same-step run inside an RV* block. Everything *not* covered
    by a span (control rows, mesh, RDPOINTS, gaps) is step-invariant and always
    kept. Offsets are into the *uncompressed* file, so range reads only work
    against identity-stored objects.
    """

    version: int
    size: int
    steps: list[int]
    fields: list[str]
    step_spans: list[tuple[int, int, int]]

    def include_ranges(self, step: int) -> list[tuple[int, int]]:
        """Byte ranges to read for ``step`` — the file minus every other
        step's spans. Adjacent/abutting kept regions are merged so the caller
        issues as few range requests as possible."""
        exclude = sorted((s, e) for (ires, s, e) in self.step_spans if ires != step)
        include: list[tuple[int, int]] = []
        cur = 0
        for s, e in exclude:
            if s > cur:
                include.append((cur, s))
            cur = max(cur, e)
        if cur < self.size:
            include.append((cur, self.size))
        return include

    def default_step(self) -> int:
        if not self.steps:
            raise ValueError("SIF index has no result steps")
        return self.steps[0]

    # ── serialization ──────────────────────────────────────────────
    def to_json(self) -> bytes:
        return json.dumps(
            {
                "version": self.version,
                "size": self.size,
                "steps": self.steps,
                "fields": self.fields,
                # JSON has no tuples; store as flat triples.
                "step_spans": [[i, s, e] for (i, s, e) in self.step_spans],
            }
        ).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes | str) -> "SifStepIndex":
        d = json.loads(data)
        if int(d.get("version", -1)) != INDEX_VERSION:
            raise ValueError(f"unsupported SIF index version {d.get('version')!r}")
        return cls(
            version=int(d["version"]),
            size=int(d["size"]),
            steps=[int(x) for x in d["steps"]],
            fields=[str(x) for x in d["fields"]],
            step_spans=[(int(i), int(s), int(e)) for (i, s, e) in d["step_spans"]],
        )


def build_sif_index(path: str | pathlib.Path) -> SifStepIndex:
    """Scan a SIF file once (no float parsing) and record per-step RV byte spans.

    The scan is pure byte/line work — ``startswith`` + ``tell`` — so it's a
    fraction of the cost of a full parse. The first record of each RV block is
    the control row (every consumer skips it via ``[1:]``); it is left out of
    the step spans so it stays in the kept region.
    """
    path = pathlib.Path(path)
    size = path.stat().st_size

    spans: list[tuple[int, int, int]] = []
    steps: set[int] = set()
    fields: set[str] = set()

    cur_card: str | None = None  # card name of the block currently being scanned
    cur_ires: int | None = None  # step of the open span, or None if no span open
    span_start = 0

    def close_span(end: int) -> None:
        nonlocal cur_ires, span_start
        if cur_ires is not None:
            spans.append((cur_ires, span_start, end))
            cur_ires = None

    off = 0
    with open(path, "rb") as f:
        for line in f:
            n = len(line)
            s = line.lstrip()
            head = s[:1]
            if head.isalpha():
                tok = s.split(None, 1)[0].decode("ascii", "replace")
                if tok in _RV_CARDS:
                    fields.add(tok)
                    if tok != cur_card:
                        # New RV block. This first record is the control row —
                        # leave it unspanned (kept in the gap). Close any span
                        # left open by the previous block.
                        close_span(off)
                        cur_card = tok
                    else:
                        # Data row: its ires drives the span boundaries.
                        parts = s.split()
                        ires = int(float(parts[2])) if len(parts) > 2 else None
                        if ires != cur_ires:
                            close_span(off)
                            if ires is not None:
                                cur_ires = ires
                                span_start = off
                                steps.add(ires)
                else:
                    # Non-RV card starts: any open RV span ends here.
                    if cur_card in _RV_CARDS:
                        close_span(off)
                    cur_card = tok
            # else: numeric continuation line — extends the open span implicitly
            # (we only close on a card-start), so nothing to do.
            off += n

    close_span(size)

    return SifStepIndex(
        version=INDEX_VERSION,
        size=size,
        steps=sorted(steps),
        fields=sorted(fields),
        step_spans=spans,
    )


def assemble_reduced_local(path: str | pathlib.Path, ranges: list[tuple[int, int]], out: str | pathlib.Path) -> int:
    """Concatenate ``ranges`` of ``path`` into ``out`` (a reduced SIF on disk).

    Local-file analogue of the worker's range-fetch-and-assemble — used by
    tests and any non-storage caller. Returns the byte count written.
    """
    path = pathlib.Path(path)
    out = pathlib.Path(out)
    written = 0
    with open(path, "rb") as fi, open(out, "wb") as fo:
        for start, end in ranges:
            fi.seek(start)
            remaining = end - start
            while remaining > 0:
                chunk = fi.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                fo.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
    return written
