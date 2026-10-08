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

import itertools
import math
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.read_sif import SifReader
from ada.fem.formats.sesam.results.result_catalog import semantic_name
from ada.fem.formats.sesam.results.sin_reader import (
    SinFile,
    SuperElementInfo,
    SuperElementSpec,
    open_sin,
)

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult

# Mirror the SIF reader's card-group lists so a record-type seen in
# the SIN block index is routed to the same bucket Sif2Mesh expects.
_OTHER_CARDS = (
    cards.UNITS,
    cards.BNDOF,
    cards.BNTRCOS,
    cards.GUNIVEC,
    cards.GELTH,
    cards.TDSECT,
    cards.TDMATER,
    cards.MISOSEL,
    cards.MORSMEL,
    cards.TDSETNAM,
    cards.GSETMEMB,
    cards.TDRESREF,
    cards.GBEAMG,
    # Beam eccentricities. Without these a stiffener is drawn on its element axis
    # instead of offset onto the plate it stiffens -- see ``element_eccentricities``.
    cards.GECCEN,
)
_SECTION_CARDS = (cards.GIORH, cards.GBOX, cards.GPIPE, cards.GLSEC)
_RESULT_CARDS = (
    cards.RVNODDIS,
    cards.RDNODREA,
    cards.RVNODREA,
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


def _records_for(sin: SinFile, card, *, step: int | None = None, elements: set[int] | None = None) -> list[list]:
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
    # ``elements`` narrows RVFORCES to the records whose IELNO (second data
    # word) the caller needs — a caller that only reads line forces for a small
    # subset of beam elements avoids decoding the whole model's forces.
    elem_filter = elements if type_name == cards.RVFORCES.name else None
    out_num: list[list] = []
    for rec in sin.iter_records(type_name, where_first_word=rec_filter, where_second_word=elem_filter):
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
    # How a COMPLEX result case (RDRESREF COMPLX=1) is presented: as its response
    # at this phase angle, in radians — R cos Φ − I sin Φ per component. The
    # default 0 is the real part; −π/2 gives the imaginary part. Combinations do
    # not use it: they read the raw real and imaginary words and apply their own
    # per-term phases. See :func:`_present_complex_rows`.
    complex_phase: float = 0.0

    def complex_cases(self) -> frozenset[int]:
        """Result IRES the deck flags complex (RDRESREF COMPLX), read once."""
        cached = getattr(self, "_complex_cases", None)
        if cached is None:
            cached = read_complex_result_cases(self.sin)
            self._complex_cases = cached
        return cached

    def combination_terms(self) -> dict[int, CombinationRecipe]:
        """The deck's RDRESCMB recipes, read once."""
        cached = getattr(self, "_combination_terms", None)
        if cached is None:
            cached = read_result_combination_terms(self.sin)
            self._combination_terms = cached
        return cached

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
        slicing (`sif.nodes[:, 0]` for the internal node numbers,
        `sif.nodes[:, 1:]` for xyz, `sif.node_ids` for the external
        numbers). ``self.elements`` is reshaped to the same
        ``(eltyp, elno, nids_list, elnox)`` tuple :meth:`SifReader.read_gelmnts`
        emits — without it, Sif2Mesh would group elements by ``elnox``
        and dereference the wrong fields as element type.
        """
        self._load_static()
        recipe = self.combination_terms().get(int(self.step)) if self.step is not None else None
        if recipe:
            self.load_combination(int(self.step), recipe)
        else:
            self.load_step(self.step)
            if self.step is None:
                # A full read is what the bake takes, and a deck that stores only
                # its basic cases would otherwise present its combinations as
                # names with no data behind them.
                self.append_unstored_combinations()

    def _load_static(self) -> None:
        """Read the step-invariant blocks once: mesh (GCOORD/GNODE/
        GELMNT1/GELREF1), sections, other, and the non-RV* result cards
        (e.g. the RDPOINTS super-headers). Idempotent-by-reuse: callers
        that stream many steps run this once, then :meth:`load_step` per
        step only re-reads the RV* tables."""
        gcoord_rows = _records_for(self.sin, cards.GCOORD)
        if gcoord_rows:
            self.nodes = np.array(gcoord_rows, dtype=float)
        # GNODE records: SifReader truncates to [nodex, nodeno] (external,
        # internal), and so does this.
        gnode_rows = _records_for(self.sin, cards.GNODE)
        if gnode_rows:
            self.node_ids = np.array([row[:2] for row in gnode_rows], dtype=float)
        # GELMNT1 records: reshape to (eltyp, elno, nids, elnox) — the
        # ``cards.GELMNT1`` field order is (elnox, elno, eltyp,
        # eltyad, nids…), so eltyp is at index 2 and the node-ref
        # list starts at index 4.
        elnox_idx, elno_idx, eltyp_idx, nids_idx = cards.GELMNT1.get_indices_from_names(
            ["elnox", "elno", "eltyp", "nids"],
        )
        gelmnt_rows = _records_for(self.sin, cards.GELMNT1)
        if gelmnt_rows:
            self.elements = [(row[eltyp_idx], row[elno_idx], row[nids_idx:], row[elnox_idx]) for row in gelmnt_rows]
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

    def load_step(self, step: int | None, cards: "set[str] | None" = None) -> None:
        """(Re)materialise just the per-step RV* result tables for
        ``step`` on top of the static blocks. ``step is None`` reads every
        step (the full-materialise path). Cheap to call repeatedly: the
        mesh/section/RDPOINTS blocks are not touched.

        ``cards`` restricts which RV blocks are gathered — e.g. just
        ``{"RVNODDIS"}`` when only the nodal field is being emitted. The bake
        iterates one field at a time, so loading only that field's card avoids
        re-gathering the other cards (and, on a range-backed byte source,
        re-fetching their pages) once per field."""
        if not getattr(self, "_static_loaded", False):
            self._load_static()
        self.step = step
        want = None if cards is None else set(cards)
        # Fresh per-step results = static (RDPOINTS …) + this step's RV*.
        self.results = list(self._static_results)
        for card in _RESULT_CARDS:
            if card.name not in _RV_TYPE_NAMES:
                continue
            if want is not None and card.name not in want:
                continue
            rec = self._read_result_card(card, step=step)
            if rec is not None:
                self.results.append(rec)

    def load_combination(
        self,
        step: int,
        recipe: "CombinationRecipe | dict[int, float]",
        cards: "set[str] | None" = None,
    ) -> None:
        """Materialise a linear ``RDRESCMB`` case from its stored basic cases.

        ``recipe`` is a :class:`CombinationRecipe` (factors AND phases, as
        :func:`read_result_combination_terms` returns them) or the legacy
        ``{basic: factor}`` dict, which means a real combination at zero phase.

        Combination is deliberately performed on the raw RV component values,
        before :class:`Sif2Mesh` computes any nonlinear derived quantities such
        as principal or von Mises stress.  The entity columns (node or element,
        result point, descriptor, transformation flag) must be identical across
        contributing cases; a drift is an input/schema error and is surfaced.
        """

        if not getattr(self, "_static_loaded", False):
            self._load_static()
        self.step = int(step)
        want = None if cards is None else set(cards)
        self.results = list(self._static_results)
        for card in _RESULT_CARDS:
            if card.name not in _RV_TYPE_NAMES:
                continue
            if want is not None and card.name not in want:
                continue
            combined = self._combine_card(card, int(step), CombinationRecipe.coerce(recipe))
            if combined is not None:
                self.results.append((card.name, combined))

    def _combine_card(self, card, step: int, recipe: CombinationRecipe):
        """Superpose one RV card of ``recipe``'s basic cases → the combination's table (or None)."""
        complex_cases = self.complex_cases()
        combined = None
        reference = None
        for basic_step, factor, phase in recipe.terms:
            # Raw: the combination needs a complex case's real AND imaginary words.
            rec = self._read_result_card(card, step=int(basic_step), raw=True)
            if rec is None or len(rec[1]) <= 1:
                continue
            combined = _accumulate_rv_combination(
                card,
                combined,
                rec[1],
                float(factor),
                combination_step=int(step),
                phase=float(phase),
                basic_complex=int(basic_step) in complex_cases,
                combination_complex=recipe.complex,
                reference_step=reference,
            )
            if reference is None:
                reference = int(basic_step)
        if combined is not None and recipe.complex:
            # A complex combination is presented like any complex case.
            combined = _present_complex_rows(card, combined, frozenset({int(step)}), self.complex_phase)
        return combined

    def stored_steps(self) -> set[int]:
        """Result-case ids the SIN physically stores, from the RV* tables."""
        steps: set[int] = set()
        for rv in _RV_TYPE_NAMES:
            if rv not in self.sin.type_blocks:
                continue
            ires = self.sin.gather_first_words(rv)
            if ires.size:
                steps.update(int(x) for x in np.unique(ires.astype(np.int64)).tolist())
        return steps

    def append_unstored_combinations(self, cards: "set[str] | None" = None) -> None:
        """Add every ``RDRESCMB`` case the SIN does not already store.

        SESTRA's "smart load combinations" store only the basic cases and leave
        the combinations as recipes; other decks store the combined results
        outright. Both are normal, and only the first needs anything done — so
        this superposes exactly the ids that are missing and leaves a deck that
        already has them untouched.

        Same superposition as :meth:`load_combination`, on the raw RV component
        values before :class:`Sif2Mesh` derives anything nonlinear from them.
        Doing it here rather than after the fact is what makes the combined case
        an ordinary step to everything downstream: von Mises and principal
        stresses are computed from the combined components, which is what the reference postprocessor
        does, and not combined after the fact, which would be wrong.
        """
        combinations = self.combination_terms()
        if not combinations:
            return
        stored = self.stored_steps()
        missing = {i: r for i, r in combinations.items() if r and i not in stored}
        if not missing:
            return

        want = None if cards is None else set(cards)
        index_of = {name: i for i, (name, _) in enumerate(self.results)}
        for ires in sorted(missing):
            for card in _RESULT_CARDS:
                if card.name not in _RV_TYPE_NAMES:
                    continue
                if want is not None and card.name not in want:
                    continue
                combined = self._combine_card(card, int(ires), missing[ires])
                if combined is None:
                    continue
                at = index_of.get(card.name)
                if at is None:
                    index_of[card.name] = len(self.results)
                    self.results.append((card.name, combined))
                else:
                    name, existing = self.results[at]
                    self.results[at] = (name, _concat_rv_rows(existing, combined))

    def _read_result_card(self, card, step, *, raw: bool = False):
        """Read one result card → ``(name, rows)`` (or None if the block
        is absent), step-filtered for RV* cards.

        Complex result cases' RV* rows are presented at :attr:`complex_phase`
        (one word per component, like a real case) unless ``raw`` — the
        combination path needs their real and imaginary words.

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
            elements = getattr(self, "_forces_elements", None) if card.name == cards.RVFORCES.name else None
            rows = _records_for(self.sin, card, step=step, elements=elements)
            rows = [super_header, *rows]
        # The card's record bytes are now copied into ``rows`` — drop the
        # mmap pages so the next (often equally large) RV* table doesn't
        # stack its resident pages on top of this one's.
        self.sin.release_record_pages(card.name)
        if not raw and card.name in _RV_TYPE_NAMES:
            complex_cases = self.complex_cases()
            if step is not None:
                complex_cases = complex_cases & {int(step)}
            rows = _present_complex_rows(card, rows, complex_cases, self.complex_phase)
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

    ``combinations`` maps a combination result IRES → ``{basic IRES:
    factor}`` (read from RDRESCMB); ``result_names`` maps any result
    IRES → its label (from TDRESREF). A combination IRES may *or may
    not* also appear in ``field_steps`` — SESTRA "smart load
    combinations" usually store only the basic cases, so combination
    results must be reconstructed by superposing the basic fields.

    ``combinations`` drops each term's phase and so is exact only for real
    cases at zero phase; ``combination_terms`` is the full recipe (factor AND
    phase in radians per term, and whether the combination is complex).
    ``complex_cases`` are the result IRES whose results are complex (RDRESREF
    COMPLX=1), e.g. quasi-static wave cases.
    """

    types: list[str]
    node_count: int
    element_count: int
    field_steps: dict[str, list[int]]
    combinations: dict[int, dict[int, float]] = None
    result_names: dict[int, str] = None
    combination_terms: dict[int, CombinationRecipe] = None
    complex_cases: frozenset[int] = None
    #: Every superelement in the file (see :meth:`SinFile.hierarchy`).
    super_elements: tuple[SuperElementInfo, ...] = ()
    #: On a superelement assembly, the label (``SEL10.IND1``) of the one read;
    #: None for a SIN that holds one superelement.
    super_element: str | None = None

    def __post_init__(self) -> None:
        if self.combinations is None:
            self.combinations = {}
        if self.result_names is None:
            self.result_names = {}
        if self.combination_terms is None:
            self.combination_terms = {}
        if self.complex_cases is None:
            self.complex_cases = frozenset()

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

    @property
    def combination_ids(self) -> list[int]:
        """All defined combination result IRES, sorted."""
        return sorted(self.combinations)

    @property
    def selectable_cases(self) -> list[int]:
        """Every result case a check can request: basic RV* steps plus any
        defined combinations (which superpose those basic steps)."""
        return sorted(set(self.steps) | set(self.combinations))


@dataclass(frozen=True)
class CombinationRecipe:
    """One ``RDRESCMB`` record (Results Interface File 4.3.1.11), in file order.

    ``terms`` are ``(basic IRES, FACT, PHASE)``: the basic result case, its
    factor, and its phase angle Φ in RADIANS, as the results file stores it
    (Prepost takes the angle in degrees at its command line; the file holds
    radians). Terms whose factor is zero are left out — they contribute nothing
    and their case need not be read. The same basic case may appear more than
    once at different phases, and each occurrence is its own term.

    ``complex`` is the record's COMPLX flag: whether the COMBINED case is complex
    (a real and an imaginary part) or real. Whether a BASIC case is complex is
    not on this record; it is ``RDRESREF``'s COMPLX (see
    :func:`read_complex_result_cases`).

    Each term contributes by the RBLODCMB formulae (4.3.1.1, Table 4.1), which
    4.3.1.11 says apply to RDRESCMB too. For a real basic case ``[R]`` and a
    complex one ``[R, I]``::

        real combination:     [R cos Φ]·FACT              [R cos Φ − I sin Φ]·FACT
        complex combination:  [R cos Φ, R sin Φ]·FACT     [R cos Φ − I sin Φ, I cos Φ + R sin Φ]·FACT

    So a phase selects which instant of a complex (e.g. quasi-static wave)
    response enters a real design combination.
    """

    complex: bool
    terms: tuple[tuple[int, float, float], ...]

    def __bool__(self) -> bool:
        # An all-zero recipe superposes nothing, and callers have always treated
        # an empty ``{basic: factor}`` dict as "no combination to build".
        return bool(self.terms)

    @property
    def factors(self) -> dict[int, float]:
        """``{basic IRES: summed factor}``, the shape :func:`read_result_combinations`
        has always returned. It ignores the phases, so it describes the
        combination exactly only when every phase is zero and every basic case
        is real."""
        out: dict[int, float] = {}
        for basic, factor, _phase in self.terms:
            out[basic] = out.get(basic, 0.0) + factor
        return out

    @classmethod
    def coerce(cls, recipe: "CombinationRecipe | dict[int, float]") -> "CombinationRecipe":
        """Accept the legacy ``{basic: factor}`` recipe: a real combination at zero phase."""
        if isinstance(recipe, CombinationRecipe):
            return recipe
        return cls(complex=False, terms=tuple((int(b), float(f), 0.0) for b, f in recipe.items()))


def read_result_combination_terms(sin: SinFile) -> dict[int, CombinationRecipe]:
    """Read every ``RDRESCMB`` record → ``{combination IRES: CombinationRecipe}``.

    A record is ``[ires, complx, nres, *triplets]`` with ``nres`` triplets
    ``(basic IRES, FACT, PHASE)`` (Results Interface File 4.3.1.11); PHASE is in
    radians. Basic IRES values are the first RV* data word of the basic case's
    records, so they double as the streaming step id.
    """
    if "RDRESCMB" not in sin.type_blocks:
        return {}
    out: dict[int, CombinationRecipe] = {}
    for rec in sin.iter_records("RDRESCMB"):
        if len(rec) < 3:
            continue
        ires = int(round(rec[0]))
        nres = int(round(rec[2]))
        triplets = rec[3:]
        terms: list[tuple[int, float, float]] = []
        for i in range(nres):
            base = 3 * i
            if base + 1 >= len(triplets):
                break
            basic = int(round(triplets[base]))
            factor = float(triplets[base + 1])
            phase = float(triplets[base + 2]) if base + 2 < len(triplets) else 0.0
            if factor != 0.0:
                terms.append((basic, factor, phase))
        out[ires] = CombinationRecipe(complex=int(round(rec[1])) != 0, terms=tuple(terms))
    return out


def read_result_combinations(sin: SinFile) -> dict[int, dict[int, float]]:
    """Read result-case combination definitions from a SIN as ``{combination
    IRES: {basic IRES: factor}}``.

    The long-standing shape, kept for callers that only list or label
    combinations. It drops each term's PHASE (the third RDRESCMB word, an angle
    in radians — not an imaginary factor), so it is only a faithful recipe for a
    real combination of real cases at zero phase. Anything that superposes
    results must use :func:`read_result_combination_terms`. Zero factors are
    dropped (the basic case contributes nothing and need not be read).
    """
    return {ires: recipe.factors for ires, recipe in read_result_combination_terms(sin).items()}


def read_complex_result_cases(sin: SinFile) -> frozenset[int]:
    """Result IRES whose ``RDRESREF`` COMPLX flag is set (4.3.1.12).

    Their RV* records carry a real AND an imaginary word per component —
    interleaved, ``R1, I1, R2, I2, …`` — which doubles the value words of the
    record (4.3.1.29 RVFORCES, 4.3.1.35 RVNODDIS, 4.3.1.36 RVNODREA,
    4.3.1.49 RVSTRESS). Quasi-static linear cases (ICALTY 6) are the usual
    source. A reader that did not know would take ``R1, I1, R2`` for three
    components.
    """
    if "RDRESREF" not in sin.type_blocks:
        return frozenset()
    out: set[int] = set()
    for rec in sin.iter_records("RDRESREF"):
        # [ires, irno, ieres, icalty, complx, numtyp, ...] — NFIELD stripped.
        if len(rec) >= 5 and int(round(rec[4])) != 0:
            out.add(int(round(rec[0])))
    return frozenset(out)


def read_result_names(sin: SinFile) -> dict[int, str]:
    """Map result IRES → label from the SIN's TDRESREF text records."""
    if "TDRESREF" not in sin.type_blocks:
        return {}
    out: dict[int, str] = {}
    for prefix, text in sin.iter_text_records("TDRESREF"):
        if not prefix or not text:
            continue
        out[int(round(prefix[0]))] = text
    return out


_RV_TYPE_NAMES = ("RVNODDIS", "RVNODREA", "RVSTRESS", "RVFORCES")

# Element field name (as the adapter advertises it) → its RV card, so the
# streaming reader gathers only that field's card per step instead of all of
# them once per field. Nodal fields use their card name directly (RVNODDIS).
_ELEM_FIELD_TO_CARD = {"STRESS": "RVSTRESS", "FORCES": "RVFORCES"}
for _position in ("resultpoints", "elements", "element_average"):
    for _attribute in ("G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "R-STRESS"):
        _ELEM_FIELD_TO_CARD[semantic_name(_position, _attribute)] = "RVSTRESS"
    for _attribute in ("G-FORCE", "B-STRESS"):
        _ELEM_FIELD_TO_CARD[semantic_name(_position, _attribute)] = "RVFORCES"
_NODAL_FIELD_TO_CARD = {
    "RVNODDIS": "RVNODDIS",
    semantic_name("nodes", "DISPLACEMENT"): "RVNODDIS",
    "REACTION-FORCE": "RVNODREA",
}
for _attribute in ("G-STRESS", "P-STRESS", "PM-STRESS", "D-STRESS", "R-STRESS"):
    _NODAL_FIELD_TO_CARD[semantic_name("nodes", _attribute)] = "RVSTRESS"


_RV_VALUE_START = {
    "RVNODDIS": cards.RVNODDIS.get_indices_from_names(["U1|"]),
    "RVNODREA": cards.RVNODREA.get_indices_from_names(["F1|"]),
    "RVSTRESS": cards.RVSTRESS.get_indices_from_names(["irstrs"]) + 1,
    "RVFORCES": cards.RVFORCES.get_indices_from_names(["irforc|"]) + 1,
}


def _concat_rv_rows(existing, extra):
    """Append one RV table's data rows to another's, keeping row 0.

    Row 0 is the synthesised super-header (``[-ndim, ndim, *dims]``); every
    consumer does ``rows[1:]``, and ``dims`` describes the block's declared
    shape rather than how many rows were materialised — the step-filtered read
    already returns fewer rows than it claims, so leaving it alone here is the
    same contract, not a new liberty.
    """
    if isinstance(existing, np.ndarray) and isinstance(extra, np.ndarray):
        if extra.shape[0] <= 1:
            return existing
        if existing.shape[1] != extra.shape[1]:
            raise ValueError(f"cannot append combination rows: width {extra.shape[1]} into {existing.shape[1]}")
        return np.vstack((existing, extra[1:]))
    return [*list(existing), *list(extra)[1:]]


# The columns that say WHICH entity a row belongs to, per RV card. Two basic
# cases superpose row by row, so these must agree; the other header words are
# per-case descriptors and may differ. RVNODREA's IRBOC is the case in point: it
# references the boundary-condition description RDNODBOC (4.3.1.36), which a
# deck may number per basic case while the node, its reaction components and
# its transformation are the same.
_RV_ENTITY_COLUMNS = {
    "RVNODDIS": ("inod", "irdva|", "itrans|"),
    "RVNODREA": ("inod", "irrea|", "itrans|"),
    "RVSTRESS": ("iielno", "ispalt", "irstrs"),
    "RVFORCES": ("ielno", "ispalt", "irforc|"),
}


def _combination_values(values, factor: float, phase: float, *, basic_complex: bool, combination_complex: bool):
    """One basic case's contribution to a combination, per value word.

    ``values`` is a float32 array whose last axis holds value words — one row's
    components, or several rows' concatenated, which is the same thing as long
    as every row of a complex case has an even count. A complex case's words are
    interleaved ``R1, I1, R2, I2, …`` (the RDIS/IDIS, STRESS/ISTRESS pairs of
    4.3.1.35 / 4.3.1.49 sit INSIDE the per-component repeat).

    The contribution is Table 4.1 of the Results Interface File (4.3.1.1,
    applied to RDRESCMB by 4.3.1.11), with ``phase`` in radians::

        combination real:     R cos Φ − I sin Φ                     (I = 0 for a real case)
        combination complex:  R cos Φ − I sin Φ,  I cos Φ + R sin Φ  (interleaved again)

    all times FACT. The result has the COMBINATION's shape: half the words of a
    complex case when the combination is real, twice a real case's when it is
    complex. The minus sign on I sin Φ, and the interleaving, were also checked
    against Sesam Xtract's own evaluation of a deck's stored recipes: they agree
    to float32 noise, where a plus sign is off by about half the displacement.
    A complex combination (RDRESCMB COMPLX=1) follows the same table but has
    not been checked against a reference.

    FACT·cos Φ and FACT·sin Φ are formed in double and rounded to float32 once,
    then applied in float32 like the rest of the superposition. At Φ = 0 that is
    exactly ``float32(FACT)``, so a real term at zero phase is bit-for-bit the
    long-standing ``value * float32(FACT)``; the rounding order for a non-zero
    phase is our choice, not something the manual specifies.
    """
    c = np.float32(factor * math.cos(phase))
    s = np.float32(factor * math.sin(phase))
    if basic_complex:
        re, im = values[..., 0::2], values[..., 1::2]
    else:
        re, im = values, None
    real = re * c
    if im is not None and s != 0:
        real = real - im * s
    if not combination_complex:
        return real
    imag = re * s
    if im is not None:
        imag = im * c + imag
    out = np.empty(real.shape[:-1] + (2 * real.shape[-1],), dtype=np.float32)
    out[..., 0::2] = real
    out[..., 1::2] = imag
    return out


def _check_rv_entities(card, reference, current, *, combination_step, current_step, reference_step) -> None:
    """Raise when two contributors' rows do not describe the same entities.

    ``reference`` / ``current`` are ``(rows, n_entity_columns)`` arrays of the
    card's :data:`_RV_ENTITY_COLUMNS`. Superposing rows that belong to different
    nodes or elements would produce numbers that look valid and mean nothing.
    """
    differ = reference != current
    if not differ.any():
        return
    row, col = (int(x) for x in np.argwhere(differ)[0])
    name = _RV_ENTITY_COLUMNS[card.name][col].rstrip("|").upper()
    earlier = f"basic case {reference_step}" if reference_step is not None else "an earlier basic case"
    raise ValueError(
        f"{card.name} combination {combination_step}: basic case {current_step} and {earlier} disagree on "
        f"{name} at data row {row + 1} ({reference[row, col]:g} vs {current[row, col]:g}); "
        "their rows do not describe the same entities, so they cannot be superposed"
    )


def _accumulate_rv_combination(
    card,
    accumulated,
    rows,
    factor: float,
    *,
    combination_step: int,
    phase: float = 0.0,
    basic_complex: bool = False,
    combination_complex: bool = False,
    reference_step: int | None = None,
):
    """Accumulate one basic RV table into a synthetic combination table.

    ``rows`` is the basic case's table as read (row 0 the super-header), either
    one ndarray or a list of rows. ``phase`` (radians), ``basic_complex`` (the
    basic case's RDRESREF COMPLX) and ``combination_complex`` (the RDRESCMB
    COMPLX) select the Table 4.1 formula — see :func:`_combination_values`. The
    accumulated table has the combination's shape, and each row's NFIELD word
    counts the words it now holds. ``reference_step`` names the first
    contributor in an error message.
    """

    value_start = int(_RV_VALUE_START[card.name])
    ires_i = int(card.get_indices_from_names(["ires"]))
    entity_i = card.get_indices_from_names(list(_RV_ENTITY_COLUMNS[card.name]))
    kind = dict(basic_complex=basic_complex, combination_complex=combination_complex)

    # One case can arrive vectorised and another per record (a table is ragged
    # when its cases differ in width); meet on the per-record form.
    if accumulated is not None and isinstance(accumulated, np.ndarray) != isinstance(rows, np.ndarray):
        if isinstance(accumulated, np.ndarray):
            accumulated = accumulated.tolist()
        else:
            rows = rows.tolist()

    if isinstance(rows, np.ndarray):
        current = np.asarray(rows, dtype=np.float64)
        current_step = int(current[1, ires_i]) if current.shape[0] > 1 else None
        values = np.asarray(current[1:, value_start:], dtype=np.float32)
        if basic_complex and values.shape[1] % 2:
            raise ValueError(f"{card.name} case {current_step} is flagged complex but stores an odd count of values")
        # NORSAM result values and factors are IEEE float32 words. The reference postprocessor
        # superposes them in that precision, one contributor at a time.
        # Keeping the accumulator in float64 changes cancellation-heavy
        # combinations by visible amounts (several tenths for stresses of
        # order 1e6), even though every input word is identical.
        contribution = _combination_values(values, factor, phase, **kind)
        if accumulated is None:
            width = value_start + contribution.shape[1]
            if width == current.shape[1]:
                out = current.copy()
            else:
                out = np.zeros((current.shape[0], width), dtype=np.float64)
                out[:, :value_start] = current[:, :value_start]
                keep = min(width, current.shape[1])
                out[0, :keep] = current[0, :keep]
                # NFIELD counts the words the record holds (4.3.1.35: NFIELD-5 =
                # NDIS·(COMPLX+1)); a consumer that trusts it must see the new width.
                out[1:, 0] = float(width)
            out[1:, value_start:] = contribution
            out[1:, ires_i] = combination_step
            return out
        out = accumulated
        if out.shape[0] != current.shape[0]:
            raise ValueError(f"{card.name} combination contributors have different row counts")
        if out.shape[1] != value_start + contribution.shape[1]:
            raise ValueError(
                f"{card.name} combination {combination_step}: basic case {current_step} has "
                f"{contribution.shape[1]} values per row where the combination holds {out.shape[1] - value_start}"
            )
        _check_rv_entities(
            card,
            out[1:, entity_i],
            current[1:, entity_i],
            combination_step=combination_step,
            current_step=current_step,
            reference_step=reference_step,
        )
        accumulated_values = np.asarray(out[1:, value_start:], dtype=np.float32)
        accumulated_values += contribution
        out[1:, value_start:] = accumulated_values
        return out

    # Per-record tables (RVSTRESS / RVFORCES widths vary with the element's
    # descriptor): the value words of all rows are handled as one flat array so
    # the arithmetic is vectorised, then cut back into rows.
    current_rows = list(rows)
    data = current_rows[1:]
    current_step = int(data[0][ires_i]) if data else None
    lengths = np.fromiter((len(r) - value_start for r in data), dtype=np.int64, count=len(data))
    flat = np.fromiter(
        itertools.chain.from_iterable(r[value_start:] for r in data), dtype=np.float64, count=int(lengths.sum())
    ).astype(np.float32)
    if basic_complex and np.any(lengths % 2):
        raise ValueError(f"{card.name} case {current_step} is flagged complex but stores an odd count of values")
    contribution = _combination_values(flat, factor, phase, **kind)
    out_lengths = lengths
    if basic_complex and not combination_complex:
        out_lengths = lengths // 2
    elif combination_complex and not basic_complex:
        out_lengths = lengths * 2
    offsets = np.concatenate(([0], np.cumsum(out_lengths))).tolist()

    if accumulated is None:
        resized = bool(np.any(out_lengths != lengths))
        out = [list(current_rows[0])]
        for k, row in enumerate(data):
            head = list(row[:value_start])
            head[ires_i] = float(combination_step)
            if resized:
                head[0] = float(value_start + int(out_lengths[k]))
            out.append(head + contribution[offsets[k] : offsets[k + 1]].tolist())
        return out

    out = accumulated
    if isinstance(out, np.ndarray) or len(out) != len(current_rows):
        raise ValueError(f"{card.name} combination contributors have different row counts")
    acc_rows = out[1:]
    acc_lengths = np.fromiter((len(r) - value_start for r in acc_rows), dtype=np.int64, count=len(acc_rows))
    if not np.array_equal(acc_lengths, out_lengths):
        bad = int(np.argmax(acc_lengths != out_lengths))
        raise ValueError(
            f"{card.name} combination {combination_step}: basic case {current_step} has {int(out_lengths[bad])} "
            f"values at data row {bad + 1} where the combination holds {int(acc_lengths[bad])}"
        )
    _check_rv_entities(
        card,
        np.array([[r[i] for i in entity_i] for r in acc_rows], dtype=np.float64).reshape(len(acc_rows), -1),
        np.array([[r[i] for i in entity_i] for r in data], dtype=np.float64).reshape(len(data), -1),
        combination_step=combination_step,
        current_step=current_step,
        reference_step=reference_step,
    )
    acc = np.fromiter(
        itertools.chain.from_iterable(r[value_start:] for r in acc_rows), dtype=np.float64, count=int(acc_lengths.sum())
    ).astype(np.float32)
    acc += contribution
    for k, row in enumerate(acc_rows):
        row[value_start:] = acc[offsets[k] : offsets[k + 1]].tolist()
    return out


def _present_complex_rows(card, rows, complex_steps: frozenset[int], phase: float):
    """Reduce the complex cases' rows of an RV table to one real value per component.

    Everything downstream of the reader (:class:`Sif2Mesh`, the mesh and result
    adapters) expects one word per component: handed a complex row it would
    read ``R1, I1, R2`` as three components, or fail to reshape a stress record.
    A complex case is therefore PRESENTED as its response at ``phase`` (radians):
    ``R cos Φ − I sin Φ`` (Table 4.1, a real combination of the one case at
    factor 1). Φ = 0 gives the real part exactly, Φ = −π/2 the imaginary part.
    Real cases' rows pass through untouched.
    """
    if not complex_steps:
        return rows
    value_start = int(_RV_VALUE_START[card.name])
    ires_i = int(card.get_indices_from_names(["ires"]))

    if isinstance(rows, np.ndarray):
        if rows.shape[0] <= 1:
            return rows
        is_complex = np.isin(rows[1:, ires_i].astype(np.int64), np.fromiter(complex_steps, dtype=np.int64))
        if not is_complex.any():
            return rows
        if not is_complex.all():
            return _present_complex_rows(card, rows.tolist(), complex_steps, phase)
        values = np.asarray(rows[1:, value_start:], dtype=np.float32)
        if values.shape[1] % 2:
            raise ValueError(f"{card.name}: a case flagged complex stores an odd count of values")
        real = _combination_values(values, 1.0, phase, basic_complex=True, combination_complex=False)
        width = value_start + real.shape[1]
        out = np.zeros((rows.shape[0], width), dtype=np.float64)
        out[:, :value_start] = rows[:, :value_start]
        keep = min(width, rows.shape[1])
        out[0, :keep] = rows[0, :keep]
        out[1:, 0] = float(width)
        out[1:, value_start:] = real
        return out

    out = [rows[0]]
    for row in rows[1:]:
        if int(row[ires_i]) not in complex_steps:
            out.append(row)
            continue
        values = np.asarray(row[value_start:], dtype=np.float32)
        if values.size % 2:
            raise ValueError(
                f"{card.name}: case {int(row[ires_i])} is flagged complex but stores an odd count of values"
            )
        real = _combination_values(values, 1.0, phase, basic_complex=True, combination_complex=False)
        head = list(row[:value_start])
        head[0] = float(value_start + real.size)
        out.append(head + real.tolist())
    return out


def read_sin_metadata(sin_file: str | pathlib.Path, *, super_element: SuperElementSpec | None = None) -> SinMetadata:
    """Enumerate steps + fields in a SIN without loading any values.

    Walks each RV* type's pointer table reading only the first data
    word per record (= IRES, the step index). For million-record-
    scale eigen files this touches a few MB of mmap pages, not the
    multi-GB record streams. The full record materialisation lives
    in :func:`read_sin_file` and only runs when a caller asks for
    actual values.

    ``super_element``: on a superelement assembly SIN, which superelement to
    describe (see :func:`~.sin_reader.open_sin`). The result lists every
    superelement in ``super_elements`` either way.
    """
    sin = open_sin(sin_file, super_element=super_element)
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
        terms = read_result_combination_terms(sin)
        return SinMetadata(
            types=types,
            node_count=node_count,
            element_count=element_count,
            field_steps=field_steps,
            combinations={ires: recipe.factors for ires, recipe in terms.items()},
            result_names=read_result_names(sin),
            combination_terms=terms,
            complex_cases=read_complex_result_cases(sin),
            super_elements=sin.hierarchy(),
            super_element=selected.label if (selected := sin.selected) is not None else None,
        )
    finally:
        sin.close()


def read_sin_file(
    sin_file: str | pathlib.Path,
    *,
    step: int | None = None,
    complex_phase: float = 0.0,
    super_element: SuperElementSpec | None = None,
) -> "FEAResult":
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

    ``complex_phase``: the phase angle, in radians, at which a COMPLEX result
    case (RDRESREF COMPLX=1) is presented — ``R cos Φ − I sin Φ`` per
    component. 0 (the default) is the real part, −π/2 the imaginary part.
    Combinations are unaffected: their terms carry their own phases.

    ``super_element``: on a superelement assembly SIN, which superelement to
    read (see :func:`~.sin_reader.open_sin`). None reads the entry with a mesh
    and results.
    """
    # ``sin_file`` may be a local path or an s3://, http(s):// URI — let
    # open_sin pick the backend. Don't Path()-mangle a URI; use the
    # source's display name (basename) for the FEAResult / convert path.
    from ada.fem.formats.sesam.results.case_names import (
        result_case_names,
        selectable_result_cases,
    )
    from ada.fem.formats.sesam.results.read_sif import Sif2Mesh
    from ada.fem.formats.sesam.results.sets import manifest_groups

    sin = open_sin(sin_file, super_element=super_element)
    if "GELMNT1" not in sin.type_blocks and sin.is_assembly():
        # Defensive: the default pick lands on an entry with elements, so this
        # takes an assembly whose chosen entry lost its mesh.
        rows = sin.hierarchy()
        sin.close()
        readable = [r.label for r in rows if r.iref is not None and r.has_mesh]
        listing = "\n".join(f"  {r.describe()}" for r in rows)
        raise ValueError(
            f"this SIN holds {len(rows)} superelements and the one opened has no mesh; "
            f"pass super_element=<label>, one of {readable}:\n{listing}"
        )
    name_path = sin.path if sin.path is not None else pathlib.Path(str(sin_file))
    reader = SinReader(sin=sin, step=step, complex_phase=complex_phase)
    reader.load()
    s2m = Sif2Mesh(reader)
    result = s2m.convert(name_path)
    # The deck's named sets travel with the result. They are decoded from records
    # this reader has already parsed, and the reader is discarded on return — so
    # not carrying them here means the only way to get them back is a second full
    # parse of the file, which on a 400 MB SIN is not a thing to do for a picker.
    result.sesam_groups = manifest_groups(reader)
    # And what the deck calls its result cases. Same reasoning as the sets: read
    # from records this parse already walked, and unreachable afterwards without
    # opening the file again.
    result.sesam_case_names = result_case_names(sin)
    result.sesam_result_cases = selectable_result_cases(sin)
    return result


def iter_sin_step_results(
    sin_file: str | pathlib.Path,
    steps,
    *,
    forces_elements: set[int] | None = None,
    complex_phase: float = 0.0,
    super_element: SuperElementSpec | None = None,
):
    """Yield ``(step, FEAResult)`` reading the SIN once and reusing the mesh.

    On large multi-step SINs this is dramatically faster than calling
    :func:`read_sin_file` per step: the file is opened and its type blocks
    decoded **once**, the step-invariant static blocks and the mesh are read
    **once**, and only each step's RV* tables are re-read. Each yielded
    ``FEAResult`` shares the same cached :class:`~ada.fem.results.common.Mesh`
    instance. No LIS/MLG enrichment (not needed for value extraction).

    ``forces_elements``: when given, the per-step RVFORCES decode is narrowed to
    these element ids (IELNO). A caller that only reads line forces for a small
    subset of beam elements avoids decoding the whole model's forces every step.
    Leave ``None`` (the bake / full-materialise paths) to read all.

    ``complex_phase``, ``super_element``: see :func:`read_sin_file`.
    """
    sin = open_sin(sin_file, super_element=super_element)
    with SinStreamReader(sin, forces_elements=forces_elements, complex_phase=complex_phase) as reader:
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

    ``steps``: when given, only these result cases (IRES, stored or combined)
    are offered and read -- the others are never touched, which is what makes a
    bake of one case out of hundreds cost one case. See :meth:`select_steps`.

    ``super_element``: on a superelement assembly SIN, which superelement to
    read (see :meth:`SinFile.resolve_super_element`); None keeps the source's
    active one.
    """

    #: Each step is decoded once per RV card and every field of that card is
    #: derived from it (see :meth:`_field_adapter`), so a bake that feeds all its
    #: fields a step at a time reads each step once; field by field, it would
    #: read the step again for every field.
    step_major_bake = True

    def __init__(
        self,
        source,
        *,
        forces_elements: set[int] | None = None,
        complex_phase: float = 0.0,
        steps: "Iterable[int] | None" = None,
        super_element: SuperElementSpec | None = None,
    ) -> None:
        from ada.fem.formats.sesam.results.sin_reader import SinFile

        self.sin = source if isinstance(source, SinFile) else SinFile(source=source)
        if super_element is not None:
            self.sin.select_super_element(super_element)
        self._combinations = read_result_combination_terms(self.sin)
        # Phase (radians) at which complex result cases are presented; see
        # :attr:`SinReader.complex_phase`.
        self._complex_phase = float(complex_phase)
        self._steps = sorted(set(self._discover_steps()) | set(self._combinations))
        self._offered = list(self._steps)  # every case, before any narrowing
        self._rep = None  # FEAResultStreamAdapter over the first step (geometry/specs/beams)
        self._mesh = None  # step-invariant Mesh, built once and reused across steps
        self._reader = None  # one SinReader; static blocks read once, RV* re-read per step
        # Optional RVFORCES element-id narrowing for callers that read line
        # forces for only a small subset of beam elements.
        self._forces_elements = set(forces_elements) if forces_elements is not None else None
        # The step whose RV cards were decoded last, as (step index, {card: adapter}).
        self._step_cache: "tuple[int, dict[str | None, object]] | None" = None
        # No step chosen on purpose (``steps=[]``): geometry only, no RV* record read.
        self._geometry_only = False
        if steps is not None:
            self.select_steps(steps)

    def select_steps(self, steps: "Iterable[int | float]") -> None:
        """Offer and read only these result cases (IRES), in ascending order.

        Must come before the first read: the representative step, which the
        geometry and the field specs come from, is the first chosen one. A case
        the deck neither stores nor defines as a combination is a ValueError.

        No case at all offers the geometry only: the mesh, sections, sets and
        property fields come from the step-invariant blocks, and no RV* record is
        read, so the cost is the model's and not a case's.
        """
        wanted: set[int] = set()
        for s in steps:
            value = float(s)
            if not value.is_integer():
                raise ValueError(f"SIN result steps are case numbers; got {s!r}")
            wanted.add(int(value))
        missing = sorted(wanted - set(self._steps))
        if missing:
            raise ValueError(f"result cases not in this SIN: {missing}")
        self._steps = sorted(wanted)
        self._geometry_only = not wanted
        self._rep = None
        self._step_cache = None

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

    def _load_step(
        self,
        step: int | None,
        cards: "set[str] | None" = None,
        requested_fields: "set[str] | None" = None,
    ):
        """Materialise just one step's FEAResult from the shared SinFile.

        The mesh topology is step-invariant, so :meth:`Sif2Mesh.get_sif_mesh`
        — the dominant per-step cost (a pure-Python pass over every element:
        groupby, type resolution, node-order reconciliation) — is run **once**
        and the built ``Mesh`` reused for every subsequent step. Only the
        per-step RV* field extraction (``get_sif_results``) re-runs. ``cards``
        restricts which RV blocks are gathered so a per-field bake pass reads
        only that field's card, not every field's records. (No LIS/MLG
        enrichment here — same as before; the source is a bare SinFile.)
        ``step=None`` with ``cards=set()`` is the geometry-only load: the
        static blocks and no RV* card."""
        from ada.fem.formats.sesam.results.read_sif import Sif2Mesh
        from ada.fem.results.common import FEAResult, FEATypes

        # One persistent reader: read the step-invariant mesh/section/
        # RDPOINTS blocks once, then only re-read this step's RV* tables.
        if self._reader is None:
            self._reader = SinReader(sin=self.sin, complex_phase=self._complex_phase)
            self._reader._forces_elements = self._forces_elements
            self._reader._load_static()
        reader = self._reader
        recipe = self._combinations.get(int(step)) if step is not None else None
        if recipe:
            reader.load_combination(int(step), recipe, cards=cards)
        else:
            reader.load_step(None if step is None else int(step), cards=cards)
        s2m = Sif2Mesh(reader)
        if self._mesh is None:
            self._mesh = s2m.get_sif_mesh()
        # Reuse the cached mesh; get_sif_results() needs it set (RVFORCES
        # resolves element ids against it) but never rebuilds it.
        s2m.mesh = self._mesh
        results = s2m.get_sif_results(requested_fields=requested_fields)
        return FEAResult(
            "remote",
            FEATypes.SESAM,
            results=results,
            mesh=self._mesh,
            results_file_path=pathlib.Path("remote.sin"),
            step_name_map=s2m.get_result_name_map(),
            software_version="N/A",
        )

    def _load_geometry(self):
        """The model without a case: the static blocks, no RV* card, and the
        property fields (thickness, material, section), which a loaded case
        would otherwise have carried. They are stamped with the deck's first
        case, as a bake of every case stamps them."""
        from ada.fem.formats.sesam.results.property_fields import build_property_fields

        result = self._load_step(None, cards=set())
        step = self._offered[0] if self._offered else 1
        result.results = [*result.results, *build_property_fields(self._mesh, self._reader, step=step)]
        return result

    def _adapter_for(self, idx: int):
        """``FEAResultStreamAdapter`` over step ``idx``; step 0 is cached as
        the representative (its result stays resident for geometry/specs)."""
        from ada.fem.results.artefacts import FEAResultStreamAdapter

        if idx == 0:
            if self._rep is None:
                if self._geometry_only:
                    self._rep = FEAResultStreamAdapter(self._load_geometry())
                    return self._rep
                if not self._steps:
                    raise RuntimeError("SIN result has no RV* result steps to bake")
                self._rep = FEAResultStreamAdapter(self._load_step(self._steps[0]))
            return self._rep
        return FEAResultStreamAdapter(self._load_step(self._steps[idx]))

    def _field_adapter(self, idx: int, card: str | None, field_name: str):
        """Adapter over step ``idx`` holding every field of ``card``'s block.

        Reuses the cached step-0 representative when available; every other
        step gathers just the one RV card instead of all of them — so iterating
        a field does not re-read (and, on a range source, re-fetch) the other
        cards' records.

        The decoded card is kept until the next step is asked for. One RV card
        feeds many fields — RVSTRESS alone gives some thirty (every stress
        attribute at result points, element nodes and element averages, per
        element type, plus the nodal averages) — and decoding it is the dominant
        cost of a step, so a bake that visits every field of a step before
        moving on (``step_major_bake``) decodes each card once per step rather
        than once per field. ``field_name`` is no longer used to narrow the
        decode: narrowing saves only the derivation, which is small beside the
        decode that would then be repeated per field.
        """
        from ada.fem.results.artefacts import FEAResultStreamAdapter

        if idx == 0 and self._rep is not None:
            return self._rep
        if self._step_cache is None or self._step_cache[0] != idx:
            # Drop the previous step's tables before decoding the next.
            self._step_cache = None
            self._step_cache = (idx, {})
        by_card = self._step_cache[1]
        adapter = by_card.get(card)
        if adapter is None:
            cards = {card} if card else None
            adapter = FEAResultStreamAdapter(self._load_step(self._steps[idx], cards=cards))
            by_card[card] = adapter
        return adapter

    def _with_global_steps(self, specs):
        import dataclasses

        labels = self._step_values()
        # A real-valued RDRESCMB recipe is conclusive static-case metadata.
        # Do not let the generic monotonic-step heuristic reinterpret case
        # numbers 1..N as eigen frequencies merely because they increase.
        analysis_kind = "static" if self._combinations else None
        return [
            (
                s
                if s.category == "property"
                # Property fields do not vary by load case: one step, however
                # many cases the deck stores. Expanding them to the global step
                # list would bake ten identical copies of a constant.
                else dataclasses.replace(
                    s,
                    n_steps=len(labels),
                    step_values=labels,
                    analysis_kind=analysis_kind or s.analysis_kind,
                )
            )
            for s in specs
        ]

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
        # A nodal field's name is its RV card (RVNODDIS) — gather only that one.
        card = _NODAL_FIELD_TO_CARD.get(field_name)
        for i in range(len(self._steps)):
            ad = self._field_adapter(i, card, field_name)
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

        if spec.category == "property":
            # Single-step by construction (see _with_global_steps); the values
            # come from the deck's property tables, not any step's RV block.
            ad = self._adapter_for(0)
            ad_spec = next(
                (s for s in ad.element_field_specs() if s.name == spec.name and s.elem_type == spec.elem_type),
                None,
            )
            if ad_spec is None:
                raise RuntimeError(f"SIN property field {spec.name!r}/{spec.elem_type} missing")
            yield from ad.iter_element_field_steps(ad_spec)
            return

        labels = self._step_values()
        card = _ELEM_FIELD_TO_CARD.get(spec.name)
        for i in range(len(self._steps)):
            ad = self._field_adapter(i, card, spec.name)
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

    def try_solid_beams(self, **kwargs):
        return self._adapter_for(0).try_solid_beams(**kwargs)

    def try_history_records(self):
        return None

    def try_fem_concepts(self):
        return None

    def try_groups(self):
        """The deck's named sets, for the manifest's ``groups`` block.

        Reads through the persistent reader, which already holds the parsed
        ``TDSETNAM`` / ``GSETMEMB`` records — the step-invariant blocks are read
        once for the whole bake, so this costs nothing beyond the decode.
        """
        from ada.fem.formats.sesam.results.sets import manifest_groups

        if self._reader is None:
            self._reader = SinReader(sin=self.sin, complex_phase=self._complex_phase)
            self._reader._load_static()
        return manifest_groups(self._reader)

    def try_step_names(self):
        """What the deck calls each result case, for the manifest's steps.

        Straight off the open file — these are text records, not the numeric
        blocks the reader indexes, so no reader needs to exist for them.
        """
        from ada.fem.formats.sesam.results.case_names import result_case_names

        return result_case_names(self.sin)

    def try_result_cases(self):
        """Every result case the deck OFFERS, which is not the same as its steps.

        A "smart load combination" deck stores only its basic cases as RV*
        records, so the manifest's steps miss the combinations — which are the
        design cases anyone actually checks.
        """
        from ada.fem.formats.sesam.results.case_names import selectable_result_cases

        return selectable_result_cases(self.sin)


__all__ = [
    "CombinationRecipe",
    "SinMetadata",
    "SinReader",
    "SinStreamReader",
    "iter_sin_step_results",
    "read_complex_result_cases",
    "read_result_combination_terms",
    "read_result_combinations",
    "read_result_names",
    "read_sin_file",
    "read_sin_metadata",
]
