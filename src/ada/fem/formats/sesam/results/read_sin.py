"""Direct Sesam SIN (Norsam binary) → :class:`FEAResult` reader.

Builds the same internal state shape :class:`SifReader` produces
(``nodes``, ``node_ids``, ``elements``, ``_other``, ``_sections``,
``_gelref1``, ``results``), but populated *directly* from the binary
records via :mod:`sin_reader`. No SIF text round-trip — the streaming
bake feeds ``MeshData`` and ``FieldArtefactMeta`` straight from this
adapter via the unchanged :class:`Sif2Mesh` consumer.

A separate :mod:`sin_to_sif` module still emits SIF text for
debugging / interop, but isn't on the read path.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.read_sif import SifReader
from ada.fem.formats.sesam.results.sin_reader import SinFile, open_sin

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult

# Mirror the SIF reader's card-group lists so a record-type seen in
# the SIN block index is routed to the same bucket Sif2Mesh expects.
_OTHER_CARDS = (
    cards.GUNIVEC,
    cards.TDSECT,
    cards.TDMATER,
    cards.MISOSEL,
    cards.MORSMEL,
    cards.TDSETNAM,
    cards.GSETMEMB,
    cards.TDRESREF,
    cards.GBEAMG,
)
_SECTION_CARDS = (cards.GIORH, cards.GBOX, cards.GPIPE)
_RESULT_CARDS = (
    cards.RVNODDIS,
    cards.RVSTRESS,
    cards.RDPOINTS,
    cards.RDSTRESS,
    cards.RDIELCOR,
    cards.RDRESREF,
    cards.RVFORCES,
    cards.RDFORCES,
)

# Text-typed records: the numeric payload ends with a label string
# that ``iter_text_records`` extracts separately. SifReader's
# ``iter_card`` appends that string as the last list element for
# these names — mirror exactly so ``Sif2Mesh.get_materials`` etc.
# can do ``x[-1]`` to recover the name.
_TEXT_CARDS = {"TDSECT", "TDSETNAM", "TDMATER", "TDRESREF"}


def _records_for(sin: SinFile, card, *, step: int | None = None) -> list[list]:
    """Pull every record of one type into the SifReader-compatible
    list shape.

    SifReader's ``iter_card`` keeps NFIELD as the first list element
    whenever the card's ``components[0] == "nfield"`` (which is true
    for every result card plus the TD* text cards) — downstream
    consumers like ``cards.RVSTRESS.get_indices_from_names`` resolve
    field names *against the components list*, so the NFIELD prefix
    must be in the data array to keep their indices in sync. For
    non-nfield cards (GNODE, GCOORD, GELMNT1) we omit the prefix to
    match SIF reader's behaviour there too.

    ``step``: when not None, only RV* records whose first data word
    (IRES) equals ``step`` are returned. Non-RV cards ignore the
    filter (mesh / section / material data is shared across steps).
    """
    type_name = card.name
    if type_name not in sin.type_blocks:
        return []
    has_nfield = card.components and card.components[0] == "nfield"
    if type_name in _TEXT_CARDS:
        out: list[list] = []
        for prefix, text in sin.iter_text_records(type_name):
            # Numeric fields after NFIELD = len(prefix); +1 for NFIELD
            # itself gives the SIF-style record-length count.
            row: list = [float(len(prefix) + 1), *prefix] if has_nfield else list(prefix)
            if text:
                row.append(text)
            out.append(row)
        return out
    # Only RV* records carry IRES in their first data word — apply the
    # step filter just there.
    rec_filter = step if (step is not None and type_name in _RV_TYPE_NAMES) else None
    out_num: list[list] = []
    for rec in sin.iter_records(type_name, where_first_word=rec_filter):
        # NFIELD is len(rec) + 1 (the count includes itself); +1 for
        # the implicit prefix word that SIN stores but iter_records
        # strips.
        row = [float(len(rec) + 1), *rec] if has_nfield else list(rec)
        out_num.append(row)
    return out_num


@dataclass
class SinReader(SifReader):
    """Drop-in replacement for :class:`SifReader` populated from a
    SIN binary file rather than SIF text.

    Inherits every helper :class:`Sif2Mesh` calls (``get_sections``,
    ``get_sets``, ``get_materials``, ``get_tdsect_map``, …) — those
    only look at ``self._other`` / ``self._sections`` / ``self._gelref1``
    dicts, which we populate from the SIN binary in :meth:`load`. The
    base ``file`` field is unused on this path (no text iteration);
    it's set to ``None`` to satisfy the dataclass shape.
    """

    sin: SinFile = None
    file: object = None  # unused — kept for SifReader dataclass shape
    step: int | None = None  # when set, only this IRES is materialised

    def load(self) -> None:
        """Walk every SIN type block and populate the internal
        SifReader-shaped state.

        ``self.nodes`` / ``self.node_ids`` are kept as raw record
        arrays for compatibility with :meth:`Sif2Mesh.get_sif_mesh`'s
        slicing (`sif.nodes[:, 1:]` for xyz, `sif.node_ids[:, 0]` for
        identifiers). ``self.elements`` is reshaped to the same
        ``(eltyp, elno, nids_list)`` triple :meth:`SifReader.read_gelmnts`
        emits — without it, Sif2Mesh would group elements by ``elnox``
        and dereference the wrong fields as element type.
        """
        gcoord_rows = _records_for(self.sin, cards.GCOORD)
        if gcoord_rows:
            self.nodes = np.array(gcoord_rows, dtype=float)
        # GNODE records: SifReader truncates to [nodex, nodeno] —
        # the get_sif_mesh path reads `node_ids[:, 0]` as the
        # identifier column, so keeping the same width here means
        # the array indexing stays valid.
        gnode_rows = _records_for(self.sin, cards.GNODE)
        if gnode_rows:
            self.node_ids = np.array([row[:2] for row in gnode_rows], dtype=float)
        # GELMNT1 records: reshape to (eltyp, elno, nids) — the
        # ``cards.GELMNT1`` field order is (elnox, elno, eltyp,
        # eltyad, nids…), so eltyp is at index 2 and the node-ref
        # list starts at index 4.
        elno_idx, eltyp_idx, nids_idx = cards.GELMNT1.get_indices_from_names(
            ["elno", "eltyp", "nids"],
        )
        gelmnt_rows = _records_for(self.sin, cards.GELMNT1)
        if gelmnt_rows:
            self.elements = [(row[eltyp_idx], row[elno_idx], row[nids_idx:]) for row in gelmnt_rows]
        gelref_rows = _records_for(self.sin, cards.GELREF1)
        if gelref_rows:
            self._gelref1 = gelref_rows

        for card in _OTHER_CARDS:
            rows = _records_for(self.sin, card)
            if rows:
                self._other[card.name] = rows

        for card in _SECTION_CARDS:
            rows = _records_for(self.sin, card)
            if rows:
                self._sections[card.name] = rows

        # Result cards: SifReader keeps the first record as the
        # type-block "super-header" (a `[-ndim, ndim, dim0, …]` line
        # that documents the table's shape), and Sif2Mesh consumers
        # do `records[1:]` to skip past it. SIN doesn't store that
        # line as a record — the shape lives in the per-type block
        # header — so synthesise one from the block's metadata so
        # the downstream slice doesn't drop the first real record.
        for card in _RESULT_CARDS:
            block = self.sin.type_blocks.get(card.name)
            if block is None:
                continue
            # Always emit the super-header — Sif2Mesh's RDPOINTS map
            # (and other shape-driven consumers) do
            # ``self.get_result(card.name)[0]`` and crash if the card
            # is missing from results. Some eigen decks have RDPOINTS
            # present as a type-block but empty (no record rows) in
            # certain super-elements, so without this guard the whole
            # convert fails.
            super_header = [-float(block.ndim), float(block.ndim)] + [float(d) for d in block.dims]
            # Big RV* tables (RVNODDIS/RVSTRESS/RVFORCES — up to tens of
            # millions of rows) dominate the read's heap. Materialise
            # them as one contiguous float64 ndarray via the vectorised
            # gather instead of a per-record list[float] (≈80 B/row vs
            # ≈376 B), padding the synthetic super-header into row 0 —
            # downstream consumers only ever do ``rows[1:]``, so the pad
            # is never read. ``gather_records`` returns None for
            # variable-width tables, which fall through to the per-record
            # path below.
            wfw = self.step if (self.step is not None and card.name in _RV_TYPE_NAMES) else None
            arr = self.sin.gather_records(card.name, where_first_word=wfw)
            if arr is not None and arr.ndim == 2 and arr.shape[1] >= len(super_header):
                sh = np.zeros((1, arr.shape[1]), dtype=np.float64)
                sh[0, : len(super_header)] = super_header
                rows = np.vstack((sh, arr)) if arr.shape[0] else sh
                self.results.append((card.name, rows))
            else:
                rows = _records_for(self.sin, card, step=self.step)
                rows = [super_header, *rows]
                self.results.append((card.name, rows))
            # The card's record bytes are now copied into ``rows`` — drop
            # the mmap pages so the next (often equally large) RV* table
            # doesn't stack its resident pages on top of this one's.
            self.sin.release_record_pages(card.name)


@dataclass
class SinMetadata:
    """Cheap, RSS-bounded enumeration of what a SIN contains.

    Built by :func:`read_sin_metadata` in time proportional to the
    pointer-table sizes (not the record-stream sizes) — touches just
    the IRES word of each RV* record. Suitable for the GLB convert
    picker and any UI that needs to list ``(step, field)`` choices
    before the user commits to a render.

    ``field_steps`` keys are SIN type names (``"RVNODDIS"``,
    ``"RVSTRESS"``, ``"RVFORCES"``) — the GLB/picker layer maps them
    to display names. Values are sorted unique IRES (step / result-
    reference id) values seen in that type's records.
    """

    types: list[str]
    node_count: int
    element_count: int
    field_steps: dict[str, list[int]]

    @property
    def steps(self) -> list[int]:
        """All step IDs seen across any RV* type, sorted."""
        seen: set[int] = set()
        for ids in self.field_steps.values():
            seen.update(ids)
        return sorted(seen)

    @property
    def fields(self) -> list[str]:
        return list(self.field_steps.keys())


_RV_TYPE_NAMES = ("RVNODDIS", "RVSTRESS", "RVFORCES")


def read_sin_metadata(sin_file: str | pathlib.Path) -> SinMetadata:
    """Enumerate steps + fields in a SIN without loading any values.

    Walks each RV* type's pointer table reading only the first data
    word per record (= IRES, the step index). For million-record-
    scale eigen files this touches a few MB of mmap pages, not the
    multi-GB record streams. The full record materialisation lives
    in :func:`read_sin_file` and only runs when a caller asks for
    actual values.
    """
    sin = open_sin(sin_file)
    try:
        types = list(sin.types)
        node_count = sin.get_count("GCOORD")
        element_count = sin.get_count("GELMNT1")
        field_steps: dict[str, list[int]] = {}
        for rv_name in _RV_TYPE_NAMES:
            if rv_name not in sin.type_blocks:
                continue
            # Bulk-read every record's IRES as a numpy float32 array,
            # then np.unique → cast to int. On large RVFORCES blocks
            # (tens of millions of records) the per-record Python
            # yield path allocates ~600 MiB of transient float objects
            # before GC catches up; the bulk gather caps that at
            # ~80 MiB and runs in <1s.
            ires_floats = sin.gather_first_words(rv_name)
            if ires_floats.size == 0:
                field_steps[rv_name] = []
                continue
            unique = np.unique(ires_floats.astype(np.int64))
            field_steps[rv_name] = [int(x) for x in unique.tolist()]
        return SinMetadata(
            types=types,
            node_count=node_count,
            element_count=element_count,
            field_steps=field_steps,
        )
    finally:
        sin.close()


def read_sin_file(sin_file: str | pathlib.Path, *, step: int | None = None) -> "FEAResult":
    """Read a Sesam ``.sin`` (Norsam binary) result file → :class:`FEAResult`.

    Pure-Python — no Prepost.exe shell-out, no on-disk SIF
    intermediate, no .NET dependency. Reuses :class:`Sif2Mesh` for
    the record → mesh / FEAResult mapping so any SIF schema additions
    land in the SIN path for free.

    ``step``: when given, only RV* records whose IRES equals this
    value are materialised. Streaming-bake workers use this to load
    one mode/load-case at a time, capping per-step heap at
    ``n_nodes × n_components × 8 B`` instead of the full
    ``n_steps × …`` materialisation that hundreds-of-modes /
    millions-of-RVNODDIS-rows decks won't fit under the 4 GiB
    worker budget.
    """
    from ada.fem.formats.sesam.results.read_sif import Sif2Mesh

    sin_path = pathlib.Path(sin_file)
    sin = open_sin(sin_path)
    reader = SinReader(sin=sin, step=step)
    reader.load()
    s2m = Sif2Mesh(reader)
    return s2m.convert(sin_path)


__all__ = ["SinMetadata", "SinReader", "read_sin_file", "read_sin_metadata"]
