"""History output (time-series at monitored nodes / elements / model) records and manifest payload."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Literal

from .specs import FieldCategory

# ---------------------------------------------------------------------------
# History output (time-series at monitored nodes / elements / model)
# ---------------------------------------------------------------------------
#
# Field outputs are dense 3D paintings; history outputs are a sparse
# time series at a hand-picked set of points the analyst requested.
# Their natural axes differ (region × variable × step × time), so the
# manifest carries them in a separate ``history`` section rather than
# trying to share the field machinery.
#
# v1 of the section embeds the (times, values) arrays directly in the
# manifest JSON. That fits typical sizes (a few thousand frames × a
# few dozen variables × a few regions ≈ low-MB JSON); if a future
# analysis pushes past that, the shape leaves room to swap inline
# arrays for a binary blob without changing the surrounding keys.

HistoryRegionKind = Literal["node", "element", "model", "set"]


HistoryDomain = Literal["time", "frequency", "mode"]


@dataclass
class HistoryRegion:
    """A monitored point or set the source's history output reports on.

    ``id`` is the bake-local key the series rows refer back to; the
    other fields drive the picker UI. ``instance`` is the part /
    instance name ("ASSEMBLY" for model-scoped output). ``coords`` is
    optional metadata — present for node regions where the reader can
    look the position up, omitted otherwise."""

    id: str
    kind: HistoryRegionKind
    instance: str
    label: str
    display_name: str = ""
    coords: tuple[float, float, float] | None = None


@dataclass
class HistoryVariable:
    """A variable in the history section.

    ``name_native`` is what the source called it (Abaqus ``U1``, Sesam
    ``DISPL_X``, Code_Aster ``DX``); ``name_canonical`` is the cross-
    solver equivalent the frontend can compare across analyses. For
    a v1 Abaqus-only bake the two are equal; later readers fill in
    the canonical mapping. ``group`` clusters related variables in
    the picker (e.g. ``U`` for the displacement triplet)."""

    name_native: str
    name_canonical: str
    category: FieldCategory = "other"
    component: str = ""
    group: str = ""
    unit: str = ""


@dataclass
class HistoryStep:
    """One source-side step. The history series rows are partitioned by
    step index because Abaqus restarts the frame clock at each step;
    sharing one x-axis across steps would misplace the samples."""

    i: int
    name: str
    procedure: str = ""
    domain: HistoryDomain = "time"


@dataclass
class HistorySeries:
    """A single time-series row: one (region, variable, step) tuple's
    samples. ``times`` and ``values`` line up index-for-index."""

    region_id: str
    variable: str
    step_idx: int
    times: list[float]
    values: list[float]


@dataclass
class HistoryRecords:
    """Bake-side container the reader hands back from
    ``try_history_records``. The bake serialises this into the
    manifest's ``history`` section verbatim."""

    regions: list[HistoryRegion] = dc_field(default_factory=list)
    variables: list[HistoryVariable] = dc_field(default_factory=list)
    steps: list[HistoryStep] = dc_field(default_factory=list)
    series: list[HistorySeries] = dc_field(default_factory=list)


def build_history_payload(history: "HistoryRecords") -> dict:
    """Serialise a :class:`HistoryRecords` to the manifest's ``history``
    section. Plain dict / list / float — no numpy types — so
    ``json.dumps`` accepts it without a custom encoder."""

    return {
        "regions": [
            {
                "id": r.id,
                "kind": r.kind,
                "instance": r.instance,
                "label": r.label,
                "display_name": r.display_name or r.label,
                **({"coords": list(r.coords)} if r.coords is not None else {}),
            }
            for r in history.regions
        ],
        "variables": [
            {
                "name_native": v.name_native,
                "name_canonical": v.name_canonical or v.name_native,
                "category": v.category,
                "component": v.component,
                "group": v.group,
                "unit": v.unit,
            }
            for v in history.variables
        ],
        "steps": [
            {
                "i": s.i,
                "name": s.name,
                "procedure": s.procedure,
                "domain": s.domain,
            }
            for s in history.steps
        ],
        "series": [
            {
                "region_id": s.region_id,
                "variable": s.variable,
                "step_idx": s.step_idx,
                "times": [float(t) for t in s.times],
                "values": [float(v) for v in s.values],
            }
            for s in history.series
        ],
    }
