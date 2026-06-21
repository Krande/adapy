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
_SECTION_CARDS = (cards.GIORH, cards.GBOX, cards.GPIPE, cards.GLSEC)
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
        SifReader-shaped state for ``self.step`` (all steps when
        ``step is None``).

        Equivalent to :meth:`_load_static` followed by
        :meth:`load_step` — the streaming reader (:class:`SinStreamReader`)
        splits the two so the step-invariant mesh/section/RDPOINTS blocks
        are read once across many steps and only the per-step RV* tables
        re-read.

        ``self.nodes`` / ``self.node_ids`` are kept as raw record
        arrays for compatibility with :meth:`Sif2Mesh.get_sif_mesh`'s
        slicing (`sif.nodes[:, 1:]` for xyz, `sif.node_ids[:, 0]` for
        identifiers). ``self.elements`` is reshaped to the same
        ``(eltyp, elno, nids_list)`` triple :meth:`SifReader.read_gelmnts`
        emits — without it, Sif2Mesh would group elements by ``elnox``
        and dereference the wrong fields as element type.
        """
        self._load_static()
        self.load_step(self.step)

    def _load_static(self) -> None:
        """Read the step-invariant blocks once: mesh (GCOORD/GNODE/
        GELMNT1/GELREF1), sections, other, and the non-RV* result cards
        (e.g. the RDPOINTS super-headers). Idempotent-by-reuse: callers
        that stream many steps run this once, then :meth:`load_step` per
        step only re-reads the RV* tables."""
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

        # Non-RV* result cards (e.g. RDPOINTS) are step-invariant — read
        # them once and stash so load_step can prepend them to each step's
        # results without re-reading.
        self._static_results = []
        for card in _RESULT_CARDS:
            if card.name in _RV_TYPE_NAMES:
                continue
            rec = self._read_result_card(card, step=None)
            if rec is not None:
                self._static_results.append(rec)
        self._static_loaded = True

    def load_step(self, step: int | None) -> None:
        """(Re)materialise just the per-step RV* result tables for
        ``step`` on top of the static blocks. ``step is None`` reads every
        step (the full-materialise path). Cheap to call repeatedly: the
        mesh/section/RDPOINTS blocks are not touched."""
        if not getattr(self, "_static_loaded", False):
            self._load_static()
        self.step = step
        # Fresh per-step results = static (RDPOINTS …) + this step's RV*.
        self.results = list(self._static_results)
        for card in _RESULT_CARDS:
            if card.name not in _RV_TYPE_NAMES:
                continue
            rec = self._read_result_card(card, step=step)
            if rec is not None:
                self.results.append(rec)

    def _read_result_card(self, card, step):
        """Read one result card → ``(name, rows)`` (or None if the block
        is absent), step-filtered for RV* cards.

        SifReader keeps the first record as the type-block "super-header"
        (`[-ndim, ndim, dim0, …]`) and consumers do ``records[1:]`` to skip
        it; SIN stores the shape in the block header, not as a record, so
        we synthesise the super-header. Always emit it even when the block
        has no rows — Sif2Mesh does ``get_result(name)[0]`` and would crash
        on a card missing from results."""
        block = self.sin.type_blocks.get(card.name)
        if block is None:
            return None
        super_header = [-float(block.ndim), float(block.ndim)] + [float(d) for d in block.dims]
        # Big RV* tables (RVNODDIS/RVSTRESS/RVFORCES — up to tens of
        # millions of rows) dominate the read's heap. Materialise them as
        # one contiguous float64 ndarray via the vectorised gather instead
        # of a per-record list[float] (≈80 B/row vs ≈376 B), padding the
        # synthetic super-header into row 0 — downstream consumers only ever
        # do ``rows[1:]``, so the pad is never read. ``gather_records``
        # returns None for variable-width tables, which fall through to the
        # per-record path.
        wfw = step if (step is not None and card.name in _RV_TYPE_NAMES) else None
        arr = self.sin.gather_records(card.name, where_first_word=wfw)
        if arr is not None and arr.ndim == 2 and arr.shape[1] >= len(super_header):
            sh = np.zeros((1, arr.shape[1]), dtype=np.float64)
            sh[0, : len(super_header)] = super_header
            rows = np.vstack((sh, arr)) if arr.shape[0] else sh
        else:
            rows = _records_for(self.sin, card, step=step)
            rows = [super_header, *rows]
        # The card's record bytes are now copied into ``rows`` — drop the
        # mmap pages so the next (often equally large) RV* table doesn't
        # stack its resident pages on top of this one's.
        self.sin.release_record_pages(card.name)
        return (card.name, rows)


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

    # ``sin_file`` may be a local path or an s3://, http(s):// URI — let
    # open_sin pick the backend. Don't Path()-mangle a URI; use the
    # source's display name (basename) for the FEAResult / convert path.
    sin = open_sin(sin_file)
    name_path = sin.path if sin.path is not None else pathlib.Path(str(sin_file))
    reader = SinReader(sin=sin, step=step)
    reader.load()
    s2m = Sif2Mesh(reader)
    return s2m.convert(name_path)


def iter_sin_step_results(sin_file: str | pathlib.Path, steps):
    """Yield ``(step, FEAResult)`` reading the SIN once and reusing the mesh.

    On large multi-step SINs this is dramatically faster than calling
    :func:`read_sin_file` per step: the file is opened and its type blocks
    decoded **once**, the step-invariant static blocks and the mesh are read
    **once**, and only each step's RV* tables are re-read. Each yielded
    ``FEAResult`` shares the same cached :class:`~ada.fem.results.common.Mesh`
    instance. No LIS/MLG enrichment (not needed for value extraction).
    """
    sin = open_sin(sin_file)
    with SinStreamReader(sin) as reader:
        for step in steps:
            yield int(step), reader._load_step(int(step))


class SinStreamReader:
    """Memory-bounded ``FEAStreamReader`` for Sesam SIN.

    Reads one step at a time from a single, warm-cached :class:`SinFile`, so
    the whole multi-step ``FEAResult`` is never resident — at most ~2 steps
    (the representative first step kept for geometry/specs, plus the step
    currently being emitted). This is what lets a many-mode deck *bake* in
    the browser without the full result blowing the wasm32 ceiling, on top
    of the source itself being range-streamed.

    The SIN→artefact mapping is **not** reimplemented: each step is mapped
    through the validated :class:`Sif2Mesh` + ``FEAResultStreamAdapter``
    path; only the per-step orchestration and the global ``(n_steps,
    step_values)`` of the specs are new. Step labels are the RV* IRES
    indices (a remote SIN has no ``SESTRA.LIS`` eigen-frequency sidecar, so
    the index *is* the label); the server/path bake keeps the full-
    materialise adapter, which can enrich labels from LIS.

    Accepts a :class:`SinFile` or any ``ByteSource`` (wrapped into one).
    """

    def __init__(self, source) -> None:
        from ada.fem.formats.sesam.results.sin_reader import SinFile

        self.sin = source if isinstance(source, SinFile) else SinFile(source=source)
        self._steps = self._discover_steps()
        self._rep = None  # FEAResultStreamAdapter over the first step (geometry/specs/beams)
        self._mesh = None  # step-invariant Mesh, built once and reused across steps
        self._reader = None  # one SinReader; static blocks read once, RV* re-read per step

    # ── lifecycle ─────────────────────────────────────────────────────
    def close(self) -> None:
        self.sin.close()

    def __enter__(self) -> "SinStreamReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── helpers ───────────────────────────────────────────────────────
    def _discover_steps(self) -> list[int]:
        steps: set[int] = set()
        for rv in _RV_TYPE_NAMES:
            if rv in self.sin.type_blocks:
                ires = self.sin.gather_first_words(rv)
                if ires.size:
                    steps.update(int(x) for x in np.unique(ires.astype(np.int64)).tolist())
        return sorted(steps)

    def _step_values(self) -> list[float]:
        return [float(s) for s in self._steps]

    def _load_step(self, step: int):
        """Materialise just one step's FEAResult from the shared SinFile.

        The mesh topology is step-invariant, so :meth:`Sif2Mesh.get_sif_mesh`
        — the dominant per-step cost (a pure-Python pass over every element:
        groupby, type resolution, node-order reconciliation) — is run **once**
        and the built ``Mesh`` reused for every subsequent step. Only the
        per-step RV* field extraction (``get_sif_results``) re-runs. This is
        what keeps the per-step bake from regressing badly on speed vs the
        full-materialise path while staying memory-bounded. (No LIS/MLG
        enrichment here — same as before; the source is a bare SinFile.)"""
        from ada.fem.formats.sesam.results.read_sif import Sif2Mesh
        from ada.fem.results.common import FEAResult, FEATypes

        # One persistent reader: read the step-invariant mesh/section/
        # RDPOINTS blocks once, then only re-read this step's RV* tables.
        if self._reader is None:
            self._reader = SinReader(sin=self.sin)
            self._reader._load_static()
        reader = self._reader
        reader.load_step(int(step))
        s2m = Sif2Mesh(reader)
        if self._mesh is None:
            self._mesh = s2m.get_sif_mesh()
        # Reuse the cached mesh; get_sif_results() needs it set (RVFORCES
        # resolves element ids against it) but never rebuilds it.
        s2m.mesh = self._mesh
        results = s2m.get_sif_results()
        return FEAResult(
            "remote",
            FEATypes.SESAM,
            results=results,
            mesh=self._mesh,
            results_file_path=pathlib.Path("remote.sin"),
            step_name_map=s2m.get_result_name_map(),
            software_version="N/A",
        )

    def _adapter_for(self, idx: int):
        """``FEAResultStreamAdapter`` over step ``idx``; step 0 is cached as
        the representative (its result stays resident for geometry/specs)."""
        from ada.fem.results.artefacts import FEAResultStreamAdapter

        if idx == 0:
            if self._rep is None:
                if not self._steps:
                    raise RuntimeError("SIN result has no RV* result steps to bake")
                self._rep = FEAResultStreamAdapter(self._load_step(self._steps[0]))
            return self._rep
        return FEAResultStreamAdapter(self._load_step(self._steps[idx]))

    def _with_global_steps(self, specs):
        import dataclasses

        labels = self._step_values()
        return [dataclasses.replace(s, n_steps=len(labels), step_values=labels) for s in specs]

    # ── FEAStreamReader protocol ──────────────────────────────────────
    def read_mesh_geometry(self):
        return self._adapter_for(0).read_mesh_geometry()

    def field_specs(self):
        return self._with_global_steps(self._adapter_for(0).field_specs())

    def element_field_specs(self):
        return self._with_global_steps(self._adapter_for(0).element_field_specs())

    def iter_field_steps(self, field_name: str):
        import dataclasses

        labels = self._step_values()
        for i in range(len(self._steps)):
            ad = self._adapter_for(i)
            emitted = 0
            for sv in ad.iter_field_steps(field_name):
                yield dataclasses.replace(sv, step_index=i, step_value=labels[i])
                emitted += 1
            if emitted != 1:
                raise RuntimeError(
                    f"SIN nodal field {field_name!r} yielded {emitted} steps at step "
                    f"index {i} (expected 1) — field missing or duplicated for a step"
                )

    def iter_element_field_steps(self, spec):
        import dataclasses

        labels = self._step_values()
        for i in range(len(self._steps)):
            ad = self._adapter_for(i)
            ad_spec = next(
                (s for s in ad.element_field_specs() if s.name == spec.name and s.elem_type == spec.elem_type),
                None,
            )
            if ad_spec is None:
                raise RuntimeError(f"SIN element field {spec.name!r}/{spec.elem_type} missing at step index {i}")
            if ad_spec.element_labels != spec.element_labels:
                # The bake writes every step against spec.element_labels (step 0's
                # order); a reordered step would silently mis-correlate values.
                raise RuntimeError(f"SIN element field {spec.name!r} element order drifted at step index {i}")
            for esv in ad.iter_element_field_steps(ad_spec):
                yield dataclasses.replace(esv, step_index=i, step_value=labels[i])

    def try_solid_beams(self):
        return self._adapter_for(0).try_solid_beams()

    def try_history_records(self):
        return None

    def try_fem_concepts(self):
        return None

    def try_groups(self):
        return None


__all__ = ["SinMetadata", "SinReader", "SinStreamReader", "read_sin_file", "read_sin_metadata"]
