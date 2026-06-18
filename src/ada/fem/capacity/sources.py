"""Panel-group *grouping* sources.

A capacity model is a panel group = a set of plate elements bound by a set of
stiffener (beam) elements. Genie's Capacity Manager *identifies* these groups
geometrically from the mesh. That identification is **not** reproduced here yet
(deferred milestone); instead the grouping is supplied by a pluggable source
behind the :class:`PanelGroupSource` interface, so a future ``SinConceptSource``
(parsing the SIN ``SCONCEPT`` records) or ``GeometricSource`` can replace it
without touching the model builder.

Milestone 1 ships :class:`ModelJsonSource`, which reads the element-id grouping
(and the stiffener section parameters adapy cannot yet recover for bulb flats)
from a Genie ``model.json``. This is what lets the whole pipeline be validated
against the matched reference dataset.
"""

from __future__ import annotations

import json
import pathlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ada.fem.capacity.model import CapSection


@dataclass(frozen=True)
class PlateSpec:
    name: str
    element_ids: tuple[int, ...]


@dataclass(frozen=True)
class StiffenerSpec:
    name: str
    element_ids: tuple[int, ...]
    continuous: bool = True
    # Section params the SIN reader cannot recover for bulb flats. ``None`` means
    # "derive from the SIN section" (works for I/box profiles adapy parses).
    section: CapSection | None = None
    eccentricity: float | None = None


@dataclass(frozen=True)
class PanelGroupSpec:
    """A panel group's membership: which plate/stiffener elements belong to it."""

    name: str
    plates: tuple[PlateSpec, ...] = ()
    stiffeners: tuple[StiffenerSpec, ...] = ()


class PanelGroupSource(ABC):
    """Yields the panel-group membership for a model.

    ``mesh`` is the SIN :class:`~ada.fem.results.common.Mesh`; a source may use it
    (e.g. :class:`SinSource`) or ignore it (e.g. :class:`ModelJsonSource`).
    """

    @abstractmethod
    def groups(self, mesh) -> list[PanelGroupSpec]:  # pragma: no cover - interface
        ...


# --------------------------------------------------------------------------- #
# Genie model.json grouping source (milestone 1)
# --------------------------------------------------------------------------- #
_DBL = -1.7976931348623157e308  # Genie's "unset" sentinel for section params


def _section_from_genie(sec: dict) -> CapSection:
    params = sec.get("SectionParameters", {})

    def _val(key: str) -> float:
        v = params.get(key, 0.0)
        return 0.0 if (v is None or v <= _DBL) else float(v)

    return CapSection(
        name=sec.get("SectionName", ""),
        section_type=int(sec.get("SectionType", -1)),
        height=_val("Height"),
        web_thickness=_val("WebThickness"),
        flange_width=_val("FlangeWidth"),
        flange_thickness=_val("FlangeThickness"),
    )


# Genie criterion-id → continuous?
_CONTINUOUS_BY_SUPPORT = {0: True, 1: False}


@dataclass
class ModelJsonSource(PanelGroupSource):
    """Read panel-group membership from a Genie ``model.json``."""

    model_json: str | pathlib.Path
    _data: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._data = json.loads(pathlib.Path(self.model_json).read_text(encoding="utf-8"))

    def groups(self, mesh=None) -> list[PanelGroupSpec]:
        out: list[PanelGroupSpec] = []
        for bm in self._data.get("BucklingModels", []):
            plates = tuple(
                PlateSpec(
                    name=p.get("Name", ""),
                    element_ids=tuple(int(e) for e in p.get("FiniteElements", [])),
                )
                for p in bm.get("Plates", [])
            )
            stiffeners = tuple(
                StiffenerSpec(
                    name=s.get("Name", ""),
                    element_ids=tuple(int(e) for e in s.get("FiniteElements", [])),
                    continuous=self._is_continuous(s),
                    section=_section_from_genie((s.get("Sections") or [{}])[0]),
                )
                for s in bm.get("Stiffeners", [])
            )
            out.append(PanelGroupSpec(name=bm.get("Name", ""), plates=plates, stiffeners=stiffeners))
        return out

    @staticmethod
    def _is_continuous(stiffener: dict) -> bool:
        # A stiffener supported at both ends is treated as simply supported
        # ([6.10.1]); otherwise continuous ([6.10.2]). Genie encodes support at
        # each cross-section; default to continuous when unknown.
        s1 = int(stiffener.get("SupportAtFirstCrossSection", 0) or 0)
        s2 = int(stiffener.get("SupportAtSecondCrossSection", 0) or 0)
        return not (s1 and s2)


# --------------------------------------------------------------------------- #
# SIN-native grouping source (no Genie model.json required)
# --------------------------------------------------------------------------- #
@dataclass
class SinSource(PanelGroupSource):
    """Identify panel groups straight from the SIN mesh.

    Each stiffener (a beam element) becomes a capacity model together with the
    plate elements that border it (the shells sharing the beam's nodes). This
    reproduces, per stiffener, the same tributary the Genie ``model.json``
    grouping yields — so the stiffened-plate check runs without a Genie capacity
    run. Section dimensions come from the raw section cards via
    :class:`~ada.fem.capacity.extract.AuxRecords` (so bulb flats resolve too).

    ``group`` optionally restricts the stiffeners to a named Sesam set/group
    present in the SIN; bordering plates are always included. ``continuous``
    sets the support assumption ([6.10.2] vs [6.10.1]) for every stiffener.

    Note: stiffener/plate names are synthesised from element ids (the SIN carries
    no per-stiffener concept names); multi-element stiffener chains are treated
    one beam element at a time.
    """

    group: str | None = None
    continuous: bool = True
    classify_secondary: bool = True

    def groups(self, mesh) -> list[PanelGroupSpec]:
        from ada.fem.shapes.definitions import LineShapes, ShellShapes

        beam_ids: list[int] = []
        shell_ids: list[int] = []
        for block in mesh.elements:
            etype = block.elem_info.type
            ids = [int(x) for x in block.identifiers]
            if isinstance(etype, LineShapes):
                beam_ids.extend(ids)
            elif isinstance(etype, ShellShapes):
                shell_ids.extend(ids)

        if self.group is not None:
            members = self._set_members(mesh, self.group)
            beam_ids = [b for b in beam_ids if b in members]

        from ada.fem.capacity.extract import tributary_plate_ids

        trib_by_beam: dict[int, list[int]] = {}
        for beam in beam_ids:
            plate_els = tributary_plate_ids(mesh, (beam,), shell_ids)
            if not plate_els:
                continue  # a free beam (no bordering plate) is not a stiffener
            trib_by_beam[beam] = plate_els

        if self.classify_secondary:
            beam_ids = self._secondary_stiffener_ids(mesh, list(trib_by_beam))
        else:
            beam_ids = list(trib_by_beam)

        out: list[PanelGroupSpec] = []
        for beam in beam_ids:
            plate_els = trib_by_beam[beam]
            out.append(
                PanelGroupSpec(
                    name=f"stiffener_el{beam}",
                    plates=tuple(PlateSpec(name=f"plate_el{p}", element_ids=(p,)) for p in plate_els),
                    stiffeners=(StiffenerSpec(name=f"el{beam}", element_ids=(beam,), continuous=self.continuous),),
                )
            )
        return out

    @staticmethod
    def _set_members(mesh, group: str) -> set[int]:
        sets = mesh.sets or {}
        fs = sets.get(group)
        if fs is None:
            available = ", ".join(sorted(sets)) or "(none)"
            raise KeyError(f"set/group {group!r} not found in SIN. Available: {available}")
        ids = getattr(fs, "_member_ids", None)
        if ids:
            return {int(x) for x in ids}
        return {int(getattr(m, "id", m)) for m in getattr(fs, "members", []) or []}

    @staticmethod
    def _secondary_stiffener_ids(mesh, beam_ids: list[int]) -> list[int]:
        """Filter out primary girders when several beam profiles share a set."""
        if not beam_ids:
            return []

        from ada.fem.capacity.extract import geono_of

        beams_by_geono: dict[int, list[int]] = {}
        for beam in beam_ids:
            beams_by_geono.setdefault(geono_of(mesh, beam), []).append(beam)
        if len(beams_by_geono) == 1:
            return beam_ids

        depths = {geono: SinSource._section_depth(mesh, geono) for geono in beams_by_geono}
        known = {geono: depth for geono, depth in depths.items() if depth > 0.0}
        if not known:
            return beam_ids

        min_depth = min(known.values())
        keep_geonos = {geono for geono, depth in known.items() if depth <= min_depth * 1.05}
        return [beam for beam in beam_ids if geono_of(mesh, beam) in keep_geonos]

    @staticmethod
    def _section_depth(mesh, geono: int) -> float:
        sec = mesh.sections.get(geono)
        for attr in ("h", "r", "w_top", "w_btn"):
            value = getattr(sec, attr, None)
            if value:
                scale = 2.0 if attr == "r" else 1.0
                return float(value) * scale
        return 0.0
