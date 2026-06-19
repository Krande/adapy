"""Memory-bounded ``FEAStreamReader`` for Sesam SIF result decks.

The default SIF bake path materialises the whole multi-step ``FEAResult`` in
RAM (``FEAResultStreamAdapter(read_sif_file(path))``) — fine for small decks,
but a many-mode deck holds every step at once and blows the worker's memory
budget. This reader is the SIF analogue of :class:`SinStreamReader`: it parses
the step-invariant part of the deck (mesh, sections, RDPOINTS and the RV*
control rows) **once**, then reads only one step's RV* bytes at a time using
the byte-offset index (:mod:`sif_index`). At most ~2 steps are ever resident.

Like the SIN streamer this is a memory-for-time trade — the bake calls
``iter_field_steps`` once per field, so each step's RV bytes are re-read per
field — so it is gated off by default (``ADA_FEA_SIF_STREAMER``) and only
turned on for decks that would otherwise OOM. The SIF→artefact mapping itself
is not reimplemented: each step is mapped through the validated
:class:`Sif2Mesh` + ``FEAResultStreamAdapter`` path; only the per-step
orchestration and the global ``(n_steps, step_values)`` of the specs are new.
"""

from __future__ import annotations

import copy
import io
import pathlib

from ada.fem.formats.sesam.results.read_sif import SifReader, Sif2Mesh, _RV_STEP_CARDS
from ada.fem.formats.sesam.results.sif_index import SifStepIndex, assemble_reduced_local, build_sif_index

# Read RV cards in a stable order so each step's block is rebuilt the same way.
_RV_ORDER = ("RVNODDIS", "RVSTRESS", "RVFORCES")

# Element field name (as the adapter advertises it) → the RV card it comes from.
# Lets ``iter_element_field_steps`` load only that field's card block per step
# instead of every step's whole record set (the SIF layout is columnar by card,
# so each field is one contiguous block — reading just it keeps the bake to a
# single pass with no per-field re-reads).
_ELEM_FIELD_TO_CARD = {"STRESS": "RVSTRESS", "FORCES": "RVFORCES"}

# Sentinel appended when parsing a step's RV bytes so the line-based reader hits
# a clean block end (a non-card, non-numeric line) instead of running off the
# end of the buffer (which a generator turns into a RuntimeError under PEP 479).
_END_SENTINEL = "ZZZEND\n"


class SifStreamReader:
    """Per-step streaming reader over a SIF deck, backed by a byte-offset index."""

    def __init__(self, path: str | pathlib.Path, index: SifStepIndex | None = None) -> None:
        self.path = pathlib.Path(path)
        self.index = index if index is not None else build_sif_index(self.path)
        self._steps = self.index.steps

        self._static: SifReader | None = None  # mesh/sections/RDPOINTS + RV control rows
        self._rd_results: list | None = None  # non-RV result cards (RDPOINTS, RDSTRESS, ...)
        self._control: dict | None = None  # card -> control row (block row 0)
        self._mesh = None  # step-invariant Mesh, built once
        self._rep = None  # FEAResultStreamAdapter over the first step (geometry/specs/beams)
        self._eig = None  # EigenDataSummary from a sibling SESTRA.LIS, if present
        self._eig_loaded = False

    # ── lifecycle ─────────────────────────────────────────────────────
    def close(self) -> None:
        pass

    def __enter__(self) -> "SifStreamReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── static (step-invariant) parse, once ───────────────────────────
    def _ensure_static(self) -> None:
        if self._static is not None:
            return
        # The header is the deck minus every step's RV data — it still carries
        # the mesh, sections, RDPOINTS and each RV block's control row.
        import tempfile

        hdr = pathlib.Path(tempfile.mkstemp(suffix=".SIF")[1])
        try:
            assemble_reduced_local(self.path, self.index.header_ranges(), hdr)
            with open(hdr, "r") as f:
                sif = SifReader(f)
                sif.load()
        finally:
            try:
                hdr.unlink()
            except OSError:
                pass

        self._static = sif
        self._rd_results = [(c, r) for (c, r) in sif.results if c not in _RV_STEP_CARDS]
        self._control = {c: r[0] for (c, r) in sif.results if c in _RV_STEP_CARDS and len(r) > 0}

    def _read_step_rows(self, card: str, step: int) -> list:
        """Parse ``card``'s data rows for ``step`` from their byte spans.

        Returns the data rows only (no control row); the caller prepends the
        cached control row so the ``[1:]`` contract every consumer relies on
        still holds."""
        spans = self.index.step_spans_for(card, step)
        if not spans:
            return []
        chunks: list[bytes] = []
        with open(self.path, "rb") as f:
            for s, e in spans:
                f.seek(s)
                chunks.append(f.read(e - s))
        text = b"".join(chunks).decode("latin-1")
        if not text.endswith("\n"):
            text += "\n"
        text += _END_SENTINEL

        it = io.StringIO(text)
        reader = SifReader(it)
        try:
            first = next(it)
        except StopIteration:
            return []
        # read_results yields every record it sees (the [1:] drop is the
        # consumer's job), so feeding it data rows returns all of them.
        return list(reader.read_results(card, first.strip()))

    def _load_step(self, step: int, cards: "set[str] | None" = None):
        """Materialise one step's FEAResult: cached static mesh + this step's RV.

        ``cards`` restricts which RV blocks are read — e.g. just ``{"RVNODDIS"}``
        when only the nodal displacement field is being emitted — so a per-field
        bake pass reads only that field's block, not every field's records."""
        from ada.fem.results.common import FEAResult, FEATypes

        self._ensure_static()
        assert self._static is not None and self._control is not None and self._rd_results is not None

        want = set(_RV_ORDER) if cards is None else set(cards)
        rv_results = []
        for card in _RV_ORDER:
            if card not in want:
                continue
            control = self._control.get(card)
            if control is None:
                continue
            data = self._read_step_rows(card, step)
            if not data:
                continue
            rv_results.append((card, [control, *data]))

        # Shallow copy shares the big mesh/section arrays; only results swap.
        reader = copy.copy(self._static)
        reader.results = list(self._rd_results) + rv_results

        s2m = Sif2Mesh(reader)
        if self._mesh is None:
            self._mesh = s2m.get_sif_mesh()
        s2m.mesh = self._mesh  # reuse; get_sif_results resolves element ids against it
        results = s2m.get_sif_results()
        return FEAResult(
            self.path.stem,
            FEATypes.SESAM,
            results=results,
            mesh=self._mesh,
            results_file_path=self.path,
            step_name_map=s2m.get_result_name_map(),
            software_version="N/A",
        )

    def _adapter_for(self, idx: int):
        """Adapter over step ``idx`` with *all* fields — for geometry/specs only.
        Step 0 is cached as the representative; per-field iteration uses the
        narrower :meth:`_field_adapter` so it never re-reads other fields."""
        from ada.fem.results.artefacts import FEAResultStreamAdapter

        if idx == 0:
            if self._rep is None:
                if not self._steps:
                    raise RuntimeError("SIF result has no RV* result steps to bake")
                self._rep = FEAResultStreamAdapter(self._load_step(self._steps[0]))
            return self._rep
        return FEAResultStreamAdapter(self._load_step(self._steps[idx]))

    def _field_adapter(self, idx: int, card: str | None):
        """Adapter over step ``idx`` loading only ``card``'s block (one field).

        Reusing the cached step-0 representative when it's available avoids a
        redundant re-read of step 0; every other step loads just the one card."""
        from ada.fem.results.artefacts import FEAResultStreamAdapter

        if idx == 0 and self._rep is not None:
            return self._rep
        cards = {card} if card else None
        return FEAResultStreamAdapter(self._load_step(self._steps[idx], cards=cards))

    def _ensure_lis(self) -> None:
        """Load a sibling SESTRA.LIS eigen-frequency table once, if present —
        the same enrichment the full-materialise path applies."""
        if self._eig_loaded:
            return
        self._eig_loaded = True
        lis = self.path.parent / "SESTRA.LIS"
        if not lis.exists():
            return
        try:
            from ada.fem.formats.sesam.results._results import get_eigen_data

            self._eig = get_eigen_data(lis)
        except Exception:
            self._eig = None

    def _plain_labels(self) -> list[float]:
        return [float(s) for s in self._steps]

    def _nodal_labels(self) -> list[float]:
        """Step labels for nodal fields: eigen frequency per step from
        SESTRA.LIS, falling back to the IRES step index. Matches the full path,
        which sets ``NodalFieldData.eigen_freq`` from LIS so the adapter's
        ``step_values`` become frequencies for nodal fields only."""
        self._ensure_lis()
        if self._eig is None:
            return self._plain_labels()
        out = []
        for s in self._steps:
            em = self._eig.get_eigenmode(int(s))
            out.append(float(em.f_hz) if em is not None and em.f_hz is not None else float(s))
        return out

    def _labels_for(self, support: str) -> list[float]:
        # Only nodal fields are LIS-enriched (mirrors the convert loop, which
        # skips non-NodalFieldData); element/gauss fields keep the step index.
        return self._nodal_labels() if support == "nodal" else self._plain_labels()

    def _with_global_steps(self, specs):
        import dataclasses

        out = []
        for s in specs:
            labels = self._labels_for(s.support)
            out.append(dataclasses.replace(s, n_steps=len(labels), step_values=labels))
        return out

    # ── FEAStreamReader protocol ──────────────────────────────────────
    def read_mesh_geometry(self):
        return self._adapter_for(0).read_mesh_geometry()

    def field_specs(self):
        return self._with_global_steps(self._adapter_for(0).field_specs())

    def element_field_specs(self):
        return self._with_global_steps(self._adapter_for(0).element_field_specs())

    def iter_field_steps(self, field_name: str):
        import dataclasses

        labels = self._nodal_labels()
        # A nodal field's name is its RV card (RVNODDIS) — read only that block.
        card = field_name if field_name in _RV_ORDER else None
        for i in range(len(self._steps)):
            ad = self._field_adapter(i, card)
            emitted = 0
            for sv in ad.iter_field_steps(field_name):
                yield dataclasses.replace(sv, step_index=i, step_value=labels[i])
                emitted += 1
            if emitted != 1:
                raise RuntimeError(
                    f"SIF nodal field {field_name!r} yielded {emitted} steps at step "
                    f"index {i} (expected 1) — field missing or duplicated for a step"
                )

    def iter_element_field_steps(self, spec):
        import dataclasses

        labels = self._plain_labels()  # element fields aren't LIS-enriched
        card = _ELEM_FIELD_TO_CARD.get(spec.name)
        for i in range(len(self._steps)):
            ad = self._field_adapter(i, card)
            ad_spec = next(
                (s for s in ad.element_field_specs() if s.name == spec.name and s.elem_type == spec.elem_type),
                None,
            )
            if ad_spec is None:
                raise RuntimeError(f"SIF element field {spec.name!r}/{spec.elem_type} missing at step index {i}")
            if ad_spec.element_labels != spec.element_labels:
                raise RuntimeError(f"SIF element field {spec.name!r} element order drifted at step index {i}")
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
